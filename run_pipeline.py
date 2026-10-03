#!/usr/bin/env python3
"""
run_pipeline.py — Fully automated novel pipeline orchestrator.

Runs the complete autonovel pipeline from seed concept to finished novel.
Manages state, git commits, evaluation, and retry logic.

Usage:
  python run_pipeline.py                    # run from current state
  python run_pipeline.py --from-scratch     # start fresh from seed.txt
  python run_pipeline.py --phase foundation # run only foundation
  python run_pipeline.py --phase drafting   # run only drafting
  python run_pipeline.py --phase revision   # run only revision
  python run_pipeline.py --phase export     # run only export
  python run_pipeline.py --max-cycles 4     # limit revision cycles
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
RESULTS_FILE = BASE_DIR / "results.tsv"
CHAPTERS_DIR = BASE_DIR / "chapters"
BRIEFS_DIR = BASE_DIR / "briefs"
EDIT_LOGS_DIR = BASE_DIR / "edit_logs"
EVAL_LOGS_DIR = BASE_DIR / "eval_logs"

FOUNDATION_THRESHOLD = 7.5
CHAPTER_THRESHOLD = 4.0
MAX_FOUNDATION_ITERS = 20
MAX_CHAPTER_ATTEMPTS = 5
MIN_REVISION_CYCLES = 3
MAX_REVISION_CYCLES = 6
PLATEAU_DELTA = 0.3

# 章ゲート（verify_chapter_gates.py）の文字数帯。
# 自律実行では上限を見ない（初稿は下限だけ強制する。改稿・挿入で章は伸びるため）。
# 帯を変えたいときは env AUTONOVEL_CHAPTER_GATE_BAND="6000 99999" のように上書きする。
CHAPTER_GATE_BAND = os.environ.get("AUTONOVEL_CHAPTER_GATE_BAND", "3000 99999")

# 章を書くドラフター。fork 推奨の分割ドラフター（セリフ比率ゲート内蔵）を既定にする。
# 本家の draft_chapter_beat.py に戻すなら env AUTONOVEL_DRAFTER=draft_chapter_beat.py。
DRAFTER = os.environ.get("AUTONOVEL_DRAFTER", "draft_chapter_split.py")
# 分割ドラフターは1章を約1,200字×5チャンクで作る（各チャンク最大3回再試行）ため、
# 単発ドラフターより遥かに長い。10分では足りない。
DRAFT_TIMEOUT = int(os.environ.get("AUTONOVEL_DRAFT_TIMEOUT", "3600"))

# 初稿（drafting）の章ゲートで「記録のみ＝破棄条件にしない」項目。
# 破棄条件に残すのは 文字数・重複・記号・比喩の型（＝修正が効く欠陥）。
# セリフ比・最小窓は「完成稿の物差し」で、生原稿は落ちるのが普通（実測 2026-10-03:
# 生原稿は6項目中3項目FAIL／人手ポリッシュ後の採用版は全PASS）。
# 初稿でこれらを破棄条件にすると1章あたり最大15回の章生成を消費して無駄になる。
# 手動で完成稿を検査するときは未指定（＝全項目が合否対象）にする。
GATE_WARN_ITEMS = os.environ.get("AUTONOVEL_GATE_WARN_ITEMS", "dialogue_ratio,min_window")

# 章ゲートに落ちたとき、まず**修復**を試すか（2026-10-03 Hisa決定）。
# 修復 = repair_chapter.py。落ちた箇所だけを直す（重複・記号はコードで、比喩・最小窓はその箇所だけLLMで）。
# 修復版がゲートを通れば採用、通らなければ破棄して次の attempt で書き直す。
# 0 にすると従来どおり「落ちたら即破棄」。
REPAIR_ON_FAIL = os.environ.get("AUTONOVEL_REPAIR_ON_FAIL", "1") == "1"
# 分割ドラフターの章を1章ぶん修復する（言い換えは十数回のLLM呼び出し）。実測 約14秒。
REPAIR_TIMEOUT = int(os.environ.get("AUTONOVEL_REPAIR_TIMEOUT", "1800"))

PHASE_ORDER = ["foundation", "drafting", "revision", "export"]


# ---------------------------------------------------------------------------
# Helpers: state management
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load pipeline state from state.json, creating defaults if missing."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return default_state()


def default_state() -> dict:
    return {
        "phase": "foundation",
        "current_focus": "planning",
        "iteration": 0,
        "foundation_score": 0.0,
        "lore_score": 0.0,
        "chapters_drafted": 0,
        "chapters_total": 0,
        "novel_score": 0.0,
        "revision_cycle": 0,
        "debts": [],
    }


def save_state(state: dict):
    """Write state to state.json."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Helpers: logging
# ---------------------------------------------------------------------------

def log_result(commit: str, phase: str, score, word_count: int,
               status: str, description: str):
    """Append a row to results.tsv."""
    header = "commit\tphase\tscore\tword_count\tstatus\tdescription\n"
    if not RESULTS_FILE.exists():
        RESULTS_FILE.write_text(header)
    elif RESULTS_FILE.stat().st_size == 0:
        RESULTS_FILE.write_text(header)
    with open(RESULTS_FILE, "a") as f:
        f.write(f"{commit}\t{phase}\t{score}\t{word_count}\t{status}\t{description}\n")


def banner(text: str, char: str = "=", width: int = 60):
    """Print a visible phase/step banner."""
    print(f"\n{char * width}")
    print(f"  {text}")
    print(f"{char * width}")


def step(text: str):
    """Print a step indicator."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] {text}")


# ---------------------------------------------------------------------------
# Helpers: subprocess execution
# ---------------------------------------------------------------------------

def run_tool(cmd: str, timeout: int = 600, check: bool = False) -> subprocess.CompletedProcess:
    """
    Run a tool as a subprocess, capturing output.
    Uses shell=True so callers can pass full command strings.
    Returns CompletedProcess; never raises unless check=True.
    """
    step(f"RUN: {cmd}")
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=str(BASE_DIR),
        )
        if result.returncode != 0:
            print(f"    WARN: exit code {result.returncode}")
            stderr_preview = (result.stderr or "")[:300]
            if stderr_preview:
                print(f"    stderr: {stderr_preview}")
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr)
        return result
    except subprocess.TimeoutExpired:
        print(f"    ERROR: timed out after {timeout}s")
        # Return a fake CompletedProcess for graceful handling
        fake = subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="TIMEOUT")
        return fake


def uv_run(script: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """Shorthand for 'uv run python <script>' from project root."""
    return run_tool(f"uv run python {script}", timeout=timeout)


# ---------------------------------------------------------------------------
# Helpers: git operations
# ---------------------------------------------------------------------------

def git_add_commit(message: str) -> str:
    """Stage all changes and commit. Returns short hash or empty string."""
    run_tool("git add -A")
    result = run_tool(f'git commit -m "{message}" --allow-empty')
    if result.returncode == 0:
        hash_result = run_tool("git rev-parse --short HEAD")
        commit_hash = hash_result.stdout.strip()
        step(f"GIT COMMIT: {commit_hash} — {message}")
        return commit_hash
    else:
        step("GIT: nothing to commit or commit failed")
        return ""


def git_reset_hard(ref: str = "HEAD~1"):
    """Hard reset to discard bad changes."""
    step(f"GIT RESET: {ref}")
    run_tool(f"git reset --hard {ref}")


def git_short_hash() -> str:
    """Get current HEAD short hash."""
    r = run_tool("git rev-parse --short HEAD")
    return r.stdout.strip() if r.returncode == 0 else "unknown"


# ---------------------------------------------------------------------------
# Helpers: score parsing
# ---------------------------------------------------------------------------

def parse_score(stdout: str, key: str = "overall_score") -> float:
    """
    Parse a score from evaluate.py YAML-like stdout output.
    Looks for lines like 'overall_score: 8.0' or 'novel_score: 7.5'.
    """
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith(f"{key}:"):
            val = line.split(":", 1)[1].strip()
            try:
                return float(val)
            except ValueError:
                continue
    return -1.0


def parse_lore_score(stdout: str) -> float:
    """Parse lore_score from foundation evaluation output."""
    return parse_score(stdout, "lore_score")


def count_words_in_chapters() -> int:
    """Sum word count across all chapter files."""
    total = 0
    if CHAPTERS_DIR.exists():
        for f in CHAPTERS_DIR.glob("ch_*.md"):
            total += len(f.read_text().split())
    return total


def count_chapter_files() -> int:
    """Count the number of chapter files."""
    if not CHAPTERS_DIR.exists():
        return 0
    return len(list(CHAPTERS_DIR.glob("ch_*.md")))


def get_total_chapters(state: dict) -> int:
    """Determine total chapter count from state or outline."""
    if state.get("chapters_total", 0) > 0:
        return state["chapters_total"]
    # Try to infer from outline.md
    outline = BASE_DIR / "outline.md"
    if outline.exists():
        text = outline.read_text()
        matches = re.findall(r'###\s*Ch(?:apter)?\s*(\d+)', text)
        if not matches:
            matches = re.findall(r'###\s*第(\d+)章', text)
        if matches:
            return max(int(m) for m in matches)
    return 24  # sensible default


# ---------------------------------------------------------------------------
# PHASE 1 — FOUNDATION
# ---------------------------------------------------------------------------

def run_foundation(state: dict) -> dict:
    """
    Build planning documents (world, characters, outline, voice, canon).
    Loop until foundation_score > threshold or max iterations reached.
    """
    banner("PHASE 1: FOUNDATION", "=")

    best_score = state.get("foundation_score", 0.0)
    iteration = state.get("iteration", 0)

    for i in range(iteration + 1, MAX_FOUNDATION_ITERS + 1):
        banner(f"Foundation Iteration {i}", "-")
        state["iteration"] = i

        # 1. Generate planning documents
        step("Generating world bible...")
        uv_run("gen_world.py", timeout=300)

        step("Generating characters...")
        uv_run("gen_characters.py", timeout=300)

        step("Generating outline (part 1)...")
        uv_run("gen_outline.py", timeout=300)

        step("Generating outline (part 2 — foreshadowing)...")
        uv_run("gen_outline_part2.py", timeout=300)

        step("Generating canon...")
        uv_run("gen_canon.py", timeout=300)

        step("Running voice fingerprint...")
        uv_run("voice_fingerprint.py", timeout=300)

        # 1.5. Character consistency gate — キャラ登録簿 ↔ outline の整合を検証
        #      （モデルが characters.md のキャラを使わず独自に追加する逸脱を検出）
        step("Verifying character consistency (characters.md ↔ outline)...")
        cc = uv_run("verify_character_consistency.py --outline", timeout=120)
        if cc.returncode != 0:
            step("WARNING: character consistency check FAILED — "
                 "outline に未定義キャラ or characters.md に未登場キャラがあります。"
                 "この iteration は破棄します。")
            git_reset_hard("HEAD")
            log_result("discarded", "foundation", 0.0, 0, "discard",
                       f"Iteration {i}: character consistency FAILED")
            continue

        # 2. Evaluate (retry on parse failure / judge model returning bad JSON)
        step("Evaluating foundation...")
        score = -1.0
        lore = -1.0
        for eval_attempt in range(1, 4):
            eval_result = uv_run("evaluate.py --phase=foundation", timeout=300)
            score = parse_score(eval_result.stdout, "overall_score")
            lore = parse_lore_score(eval_result.stdout)
            if score >= 0:
                break
            step(f"Evaluation parse failed (score {score}), retrying ({eval_attempt}/3)...")

        step(f"Foundation score: {score}  (lore: {lore}, prev best: {best_score})")

        # 3. Keep or discard
        if score > best_score:
            commit_hash = git_add_commit(
                f"foundation iter {i}: score {score} (lore {lore})")
            log_result(commit_hash, "foundation", score, 0, "keep",
                       f"Iteration {i}: score improved {best_score} -> {score}")
            best_score = score
            state["foundation_score"] = score
            state["lore_score"] = lore
            save_state(state)
        else:
            step(f"Score did not improve ({score} <= {best_score}), discarding")
            git_reset_hard("HEAD")
            log_result("discarded", "foundation", score, 0, "discard",
                       f"Iteration {i}: no improvement ({score} <= {best_score})")

        # 4. Check exit condition
        if best_score >= FOUNDATION_THRESHOLD:
            step(f"Foundation score {best_score} >= {FOUNDATION_THRESHOLD} — PASSED")
            break
    else:
        step(f"WARNING: max iterations ({MAX_FOUNDATION_ITERS}) reached "
             f"with score {best_score}")

    # Determine total chapters from outline
    total = get_total_chapters(state)
    state["chapters_total"] = total
    state["phase"] = "drafting"
    state["current_focus"] = "chapter_drafting"
    save_state(state)

    banner(f"FOUNDATION COMPLETE — score {best_score}, {total} chapters planned")
    return state


# ---------------------------------------------------------------------------
# PHASE 2 — DRAFTING
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 試作1章テスト（設定が通った直後に、1章だけ書いて判定する）
# ---------------------------------------------------------------------------
# 目的: 24章を書き始める前に、設定（世界観・人物・アウトライン）の穴を見つける。
#   1章だけ試作 → 章ゲート（落ちたら修復） → 判定スコア → 通れば第1章として採用し、
#   2章以降を書き続ける。通らなければ数行のブリーフを残して止める（既定）。
TRIAL_TEST = os.environ.get("AUTONOVEL_TRIAL_TEST", "1") != "0"
TRIAL_STOP = os.environ.get("AUTONOVEL_TRIAL_STOP", "1") != "0"
TRIAL_ONLY = os.environ.get("AUTONOVEL_TRIAL_ONLY", "0") != "0"
TRIAL_BRIEF = BASE_DIR / os.environ.get(
    "AUTONOVEL_TRIAL_BRIEF", "briefs/trial_chapter_brief.md")


def _trial_fail(state: dict, ch_file: Path, gate_out: str,
                repair_out: str, score: float, verdict: str) -> tuple:
    """試作テストの不合格を数行のブリーフに残し、設定を直すために止める（既定）。"""
    fail_lines = [l.strip() for l in gate_out.splitlines()
                  if "[FAIL]" in l or "[WARN]" in l or l.strip().startswith("RESULT")]
    fix_lines = [l.strip() for l in repair_out.splitlines() if l.strip().startswith("- ")]

    body = [
        "# 試作1章テスト：不合格",
        "",
        "設定（世界観・人物・アウトライン）が通った直後に、**1章だけ試作して判定**した結果です。",
        "**24章を書き始める前に**、設計の穴を見つけるためのテストです。",
        "",
        f"- 判定: {verdict}",
        f"- 判定スコア: {score}（合格ライン {CHAPTER_THRESHOLD}）",
        f"- 試作した章: {ch_file}（読めるようにそのまま残してあります）",
        "",
        "## 章ゲート（文体6項目）の結果",
        "",
    ]
    body += ([f"- {l}" for l in fail_lines] if fail_lines
             else ["- （ゲートの出力を取得できませんでした）"])
    body += ["", f"## 修復で直そうとした箇所（{len(fix_lines)}件）", ""]
    body += ([f"- {l}" for l in fix_lines[:6]] if fix_lines
             else ["- （修復は走っていません）"])
    body += [
        "",
        "## 次にやること（設計側を直す）",
        "",
        "- `outline.md`: 幕構成と各章の事件（中盤に山、40〜60%地点で取り返しのつかない損失、対立面を終盤まで持続）",
        "- `characters.md`: 主人公と対立する人物が、終盤まで働いているか",
        "- `voice.md` / `style_exemplar.md`: 比喩の供給源と予算（型の反復を減らす）",
        "- 直したら `AUTONOVEL_TRIAL_ONLY=1 uv run python run_pipeline.py --phase drafting` で再試行",
        "  （1章だけ書いて判定。24章は書きません）",
        "",
    ]
    TRIAL_BRIEF.parent.mkdir(parents=True, exist_ok=True)
    TRIAL_BRIEF.write_text("\n".join(body), encoding="utf-8")

    banner("試作1章テスト 不合格（設定を直してください）", "!")
    print(f"  判定: {verdict}")
    for l in fail_lines[:5]:
        print(f"    {l}")
    print(f"  詳しい結果: {TRIAL_BRIEF}")
    print(f"  試作した章: {ch_file}")
    if TRIAL_STOP:
        print("  24章を書き始める前に止めます。設定（上のブリーフに書いたファイル）を直して再実行してください。")
        print("  このまま続ける場合は AUTONOVEL_TRIAL_STOP=0 を付けて再実行します。")
        save_state(state)
        sys.exit(3)
    print("  AUTONOVEL_TRIAL_STOP=0 なので記録だけして、2章以降を書きます。")
    log_result("discarded", "ch01", score, 0, "discard", f"試作1章テスト不合格: {verdict}")
    return False, verdict


def trial_chapter_test(state: dict, total: int) -> tuple:
    """設定の合格直後に1章だけ試作し、章ゲート（修復込み）と判定スコアで合否を出す。"""
    if not TRIAL_TEST:
        step("試作1章テストは無効（AUTONOVEL_TRIAL_TEST=0）")
        return True, ""
    if state.get("chapters_drafted", 0) >= 1:
        step("すでに第1章があるので試作テストは省略します")
        return True, ""

    ch = 1
    banner("試作1章テスト（第1章を1章だけ書いて判定）", "-")
    CHAPTERS_DIR.mkdir(exist_ok=True)
    ch_file = CHAPTERS_DIR / f"ch_{ch:02d}.md"

    draft_result = uv_run(f"{DRAFTER} {ch}", timeout=DRAFT_TIMEOUT)
    if draft_result.returncode != 0 or not ch_file.exists() or ch_file.stat().st_size < 100:
        step(f"試作のドラフトに失敗しました（exit {draft_result.returncode}）")
        return _trial_fail(state, ch_file, "", "", -1.0, "試作のドラフトに失敗")

    gate_cmd = (f'AUTONOVEL_GATE_WARN_ITEMS="{GATE_WARN_ITEMS}" '
                f"uv run python verify_chapter_gates.py --band {CHAPTER_GATE_BAND} ")
    gate = run_tool(gate_cmd + f"chapters/ch_{ch:02d}.md", timeout=180)
    repair_out, healed = "", False
    if gate.returncode != 0:
        if REPAIR_ON_FAIL:
            step("章ゲートに落ちたので修復します（落ちた箇所だけを直す）")
            rep = uv_run(f"repair_chapter.py chapters/ch_{ch:02d}.md "
                         f"--out chapters/ch_{ch:02d}_repaired.md "
                         f'--band "{CHAPTER_GATE_BAND}"', timeout=REPAIR_TIMEOUT)
            repair_out = rep.stdout or ""
            repaired = ch_file.with_name(f"ch_{ch:02d}_repaired.md")
            if repaired.exists():
                gate2 = run_tool(gate_cmd + f"chapters/ch_{ch:02d}_repaired.md", timeout=180)
                if gate2.returncode == 0:
                    run_tool(f"mv chapters/ch_{ch:02d}_repaired.md chapters/ch_{ch:02d}.md")
                    healed, gate = True, gate2
                    step("修復版がゲートを通過しました — 採用します")
                else:
                    run_tool(f"rm -f chapters/ch_{ch:02d}_repaired.md")
        else:
            step("修復は無効（AUTONOVEL_REPAIR_ON_FAIL=0）なので、そのまま判定します")
    gates_ok = healed or gate.returncode == 0

    score = -1.0
    for _ in range(3):
        ev = uv_run(f"evaluate.py --chapter={ch}", timeout=300)
        score = parse_score(ev.stdout, "overall_score")
        if score >= 0:
            break
    score_ok = score >= CHAPTER_THRESHOLD

    if gates_ok and score_ok:
        word_count = len(ch_file.read_text(encoding="utf-8"))
        commit_hash = git_add_commit(f"ch01 (試作テスト通過): score {score}, {word_count}字")
        log_result(commit_hash, "ch01", score, word_count, "keep", "試作1章テスト通過")
        state["chapters_drafted"] = 1
        save_state(state)
        step(f"試作1章テスト 通過（score {score}）— この章を第1章として採用し、2章以降を書きます")
        return True, ""

    verdict = ("章ゲートで不合格" if not gates_ok
               else f"章ゲートは通ったが判定スコアが低い（{score}）")
    return _trial_fail(state, ch_file, gate.stdout or "", repair_out, score, verdict)


def run_drafting(state: dict) -> dict:
    """
    Draft each chapter sequentially, evaluating and retrying as needed.
    """
    banner("PHASE 2: DRAFTING", "=")

    total = get_total_chapters(state)
    trial_chapter_test(state, total)          # 設定の合格直後に1章だけ試作して判定する
    if TRIAL_ONLY:
        step("試作1章テストだけを実行しました（AUTONOVEL_TRIAL_ONLY=1）")
        return state
    start_chapter = state.get("chapters_drafted", 0) + 1

    CHAPTERS_DIR.mkdir(exist_ok=True)

    for ch in range(start_chapter, total + 1):
        banner(f"Drafting Chapter {ch}/{total}", "-")
        drafted = False

        for attempt in range(1, MAX_CHAPTER_ATTEMPTS + 1):
            step(f"Attempt {attempt}/{MAX_CHAPTER_ATTEMPTS}")

            # Draft（既定は fork 推奨の分割ドラフター。セリフ比率ゲート内蔵）
            draft_result = uv_run(f"{DRAFTER} {ch}", timeout=DRAFT_TIMEOUT)
            if draft_result.returncode != 0:
                step(f"Draft failed (exit {draft_result.returncode}), retrying...")
                continue

            # Check the chapter file exists and has content
            ch_file = CHAPTERS_DIR / f"ch_{ch:02d}.md"
            if not ch_file.exists() or ch_file.stat().st_size < 100:
                step("Chapter file missing or too short, retrying...")
                continue

            word_count = len(ch_file.read_text().split())
            step(f"Drafted {word_count} words")

            # Evaluate (retry on parse failure / judge model returning bad JSON)
            score = -1.0
            for eval_attempt in range(1, 4):
                eval_result = uv_run(f"evaluate.py --chapter={ch}", timeout=300)
                score = parse_score(eval_result.stdout, "overall_score")
                if score >= 0:
                    break
                step(f"Chapter eval parse failed (score {score}), retrying ({eval_attempt}/3)...")
            step(f"Chapter {ch} score: {score}")

            if score >= CHAPTER_THRESHOLD:
                # Character consistency gate — この章に未定義キャラが登場していないか
                # （モデルが characters.md にないキャラを勝手に登場させる逸脱を検出）
                cc = uv_run("verify_character_consistency.py --chapters", timeout=120)
                if cc.returncode != 0:
                    step("WARNING: character consistency FAILED — "
                         "この章に未定義キャラが登場しています。破棄して再試行します。")
                    log_result("discarded", f"ch{ch:02d}", score, word_count,
                               "discard", f"Chapter {ch}: character consistency FAILED")
                    if ch_file.exists():
                        run_tool(f"git checkout -- chapters/ch_{ch:02d}.md 2>/dev/null || true")
                    continue
                # Chapter quality gates — 文体6項目（文字数/セリフ比/最小窓/重複/記号/比喩の型）
                # 破棄するのは GATE_WARN_ITEMS 以外の項目（既定: 文字数・重複・記号・比喩の型）。
                # セリフ比・最小窓は初稿では [WARN] 記録のみ（完成稿の物差し。2026-10-03 の実測で判断）
                gate_cmd = (f'AUTONOVEL_GATE_WARN_ITEMS="{GATE_WARN_ITEMS}" '
                            f"uv run python verify_chapter_gates.py --band {CHAPTER_GATE_BAND} ")
                gate = run_tool(gate_cmd + f"chapters/ch_{ch:02d}.md", timeout=180)
                healed = False
                if gate.returncode != 0:
                    step("WARNING: chapter quality gates FAILED — "
                         "破棄条件の項目に不合格（文字数・重複・記号・比喩の型）")
                    for line in (gate.stdout or "").splitlines():
                        if "[FAIL]" in line or "[WARN]" in line or line.startswith("RESULT"):
                            print(f"      {line.strip()}")
                    # まず修復を試す（落ちた箇所だけを直す。2026-10-03 Hisa決定）
                    if REPAIR_ON_FAIL:
                        step("修復パスを試します（落ちた箇所だけを直す）")
                        rep = uv_run(f"repair_chapter.py chapters/ch_{ch:02d}.md "
                                     f"--out chapters/ch_{ch:02d}_repaired.md "
                                     f'--band "{CHAPTER_GATE_BAND}"',
                                     timeout=REPAIR_TIMEOUT)
                        repaired = ch_file.with_name(f"ch_{ch:02d}_repaired.md")
                        if rep.returncode in (0, 1) and repaired.exists():
                            for line in (rep.stdout or "").splitlines():
                                if line.strip().startswith("- "):
                                    print(f"      {line.strip()}")
                            gate2 = run_tool(gate_cmd + f"chapters/ch_{ch:02d}_repaired.md",
                                             timeout=180)
                            if gate2.returncode == 0:
                                run_tool(f"mv chapters/ch_{ch:02d}_repaired.md "
                                         f"chapters/ch_{ch:02d}.md")
                                healed = True
                                word_count = len(ch_file.read_text(encoding="utf-8"))
                                step(f"修復版がゲートを通過しました — 採用します（{word_count}字）")
                            else:
                                print("      修復版もゲートを通りませんでした（破棄して書き直します）")
                        else:
                            print(f"      修復できませんでした（exit {rep.returncode}）")
                if gate.returncode != 0 and not healed:
                    log_result("discarded", f"ch{ch:02d}", score, word_count,
                               "discard", f"Chapter {ch}: quality gates FAILED")
                    if ch_file.exists():
                        run_tool(f"git checkout -- chapters/ch_{ch:02d}.md 2>/dev/null || true")
                    run_tool(f"rm -f chapters/ch_{ch:02d}_repaired.md")
                    continue
                commit_hash = git_add_commit(
                    f"ch{ch:02d}: score {score}, {word_count}w")
                log_result(commit_hash, f"ch{ch:02d}", score, word_count,
                           "keep", f"Chapter {ch} (attempt {attempt})")
                state["chapters_drafted"] = ch
                save_state(state)
                drafted = True
                break
            else:
                step(f"Score {score} < {CHAPTER_THRESHOLD}, discarding attempt")
                log_result("discarded", f"ch{ch:02d}", score, word_count,
                           "discard", f"Chapter {ch} attempt {attempt}")
                # Remove the bad chapter file so next attempt starts fresh
                if ch_file.exists():
                    run_tool(f"git checkout -- chapters/ch_{ch:02d}.md 2>/dev/null || true")

        if not drafted:
            step(f"WARNING: Chapter {ch} failed all {MAX_CHAPTER_ATTEMPTS} attempts, "
                 f"keeping last attempt and moving on")
            # Keep whatever we have and commit it
            ch_file = CHAPTERS_DIR / f"ch_{ch:02d}.md"
            if ch_file.exists():
                word_count = len(ch_file.read_text().split())
                commit_hash = git_add_commit(
                    f"ch{ch:02d}: best-effort after {MAX_CHAPTER_ATTEMPTS} attempts")
                log_result(commit_hash, f"ch{ch:02d}", "?", word_count,
                           "forced", f"Chapter {ch}: kept after max attempts")
                state["chapters_drafted"] = ch
                save_state(state)

    # All chapters drafted
    state["phase"] = "revision"
    state["current_focus"] = "full_novel"
    state["chapters_drafted"] = total
    state["revision_cycle"] = 0
    save_state(state)

    total_words = count_words_in_chapters()
    banner(f"DRAFTING COMPLETE — {total} chapters, {total_words} words")
    return state


# ---------------------------------------------------------------------------
# PHASE 3 — REVISION
# ---------------------------------------------------------------------------

def parse_panel_consensus(panel_path: Path) -> list[dict]:
    """
    Parse reader_panel.json to find chapters with consensus issues.
    Returns list of dicts: {chapter, question, flagged_by, details}
    sorted by number of readers who flagged (descending).
    """
    if not panel_path.exists():
        return []
    with open(panel_path) as f:
        data = json.load(f)

    items = []

    # Look at disagreements — these are flagged by some but not all readers
    for d in data.get("disagreements", []):
        items.append({
            "chapter": d.get("chapter", 0),
            "question": d.get("question", ""),
            "flagged_by": d.get("flagged_by", []),
            "count": len(d.get("flagged_by", [])),
        })

    # Also scan readers for direct chapter mentions in key questions
    readers = data.get("readers", {})
    chapter_mentions = {}  # ch_num -> count of readers mentioning it

    for reader_key, answers in readers.items():
        for question in ["momentum_loss", "cut_candidate", "worst_scene",
                         "thinnest_character", "missing_scene"]:
            answer = answers.get(question, "")
            if not isinstance(answer, str):
                continue
            chs = re.findall(r'Ch(?:apter)?\s*(\d+)', answer, re.IGNORECASE)
            for ch_str in chs:
                ch_num = int(ch_str)
                key = (ch_num, question)
                if key not in chapter_mentions:
                    chapter_mentions[key] = {"chapter": ch_num, "question": question,
                                             "flagged_by": [], "count": 0}
                chapter_mentions[key]["flagged_by"].append(reader_key)
                chapter_mentions[key]["count"] += 1

    # Merge and deduplicate
    seen = set()
    for item in items:
        seen.add((item["chapter"], item["question"]))
    for key, item in chapter_mentions.items():
        if key not in seen:
            items.append(item)

    # Sort by count descending, take unique chapters
    items.sort(key=lambda x: -x["count"])

    # Deduplicate by chapter (keep highest-count issue per chapter)
    seen_chapters = set()
    unique = []
    for item in items:
        if item["chapter"] not in seen_chapters and item["chapter"] > 0:
            seen_chapters.add(item["chapter"])
            unique.append(item)

    return unique[:5]  # top 3-5 consensus items


REVISION_GATES = os.environ.get("AUTONOVEL_REVISION_GATES", "1") != "0"


def gate_cmd_for(path: str) -> str:
    """章ゲート（文体6項目）を1ファイルにかけるコマンド。"""
    return (f'AUTONOVEL_GATE_WARN_ITEMS="{GATE_WARN_ITEMS}" '
            f"uv run python verify_chapter_gates.py --band {CHAPTER_GATE_BAND} {path}")


def changed_chapters() -> list:
    """直前の改訂で書き換わった章ファイル（未コミットの差分）。"""
    res = run_tool("git diff --name-only -- chapters", timeout=60)
    return [BASE_DIR / p for p in (res.stdout or "").splitlines()
            if p.strip().endswith(".md")]


def revision_gate_check(changed: list) -> int:
    """本文を触った直後に章ゲートを回し、FAIL した章を元に戻す（2026-10-03 Hisa決定）。

    改訂は「スコア」だけで採否を決めていたので、文体が崩れても気づけなかった。
    本文を書き換えたら必ずゲートを通し、落ちた章は git で戻す。
    """
    if not REVISION_GATES or not changed:
        return 0
    reverted = 0
    for path in changed:
        res = run_tool(gate_cmd_for(path), timeout=300)
        if res.returncode == 0:
            continue
        step(f"  章ゲートFAIL → 元に戻します: {path.name}")
        for line in (res.stdout or "").splitlines():
            if "[FAIL]" in line:
                print(f"      {line.strip()}")
        run_tool(f"git checkout -- {path}")
        log_result("reverted", path.stem, "", 0, "discard",
                   "改訂で本文を触ったら章ゲートFAIL（元に戻した）")
        reverted += 1
    return reverted


def run_revision(state: dict, max_cycles: int = MAX_REVISION_CYCLES) -> dict:
    """
    Revision phase: adversarial editing, reader panel, targeted revisions.

    本文を書き換えるステップの直後には必ず章ゲート（文体6項目）を回し、
    落ちた章は元に戻す（`AUTONOVEL_REVISION_GATES=0` で無効化）。
    """
    banner("PHASE 3: REVISION", "=")

    BRIEFS_DIR.mkdir(exist_ok=True)
    EDIT_LOGS_DIR.mkdir(exist_ok=True)

    prev_score = state.get("novel_score", 0.0)
    start_cycle = state.get("revision_cycle", 0) + 1
    max_cycles = min(max_cycles, MAX_REVISION_CYCLES)

    for cycle in range(start_cycle, max_cycles + 1):
        banner(f"Revision Cycle {cycle}/{max_cycles}", "-")

        # -- Step 1: Adversarial editing pass --
        step("Running adversarial editing on all chapters...")
        uv_run("adversarial_edit.py all", timeout=900)
        revision_gate_check(changed_chapters())      # 本文を触ったら章ゲート

        # -- Step 2: Apply mechanical cuts (only if apply_cuts.py exists) --
        apply_cuts = BASE_DIR / "apply_cuts.py"
        if apply_cuts.exists():
            step("Applying mechanical cuts (OVER-EXPLAIN, REDUNDANT)...")
            run_tool("uv run python apply_cuts.py all "
                     "--types OVER-EXPLAIN REDUNDANT --min-fat 15", timeout=300)
            revision_gate_check(changed_chapters())  # 削った後も同じゲートを通す
        else:
            step("apply_cuts.py not found, skipping mechanical cuts")

        # -- Step 3: Reader panel --
        step("Running reader panel evaluation...")
        uv_run("reader_panel.py", timeout=600)

        # -- Step 4: Parse panel consensus --
        panel_path = EDIT_LOGS_DIR / "reader_panel.json"
        consensus_items = parse_panel_consensus(panel_path)

        if consensus_items:
            step(f"Found {len(consensus_items)} consensus items:")
            for item in consensus_items:
                print(f"    Ch {item['chapter']}: {item['question']} "
                      f"(flagged by {item['count']} readers)")
        else:
            step("No strong consensus items found from panel")

        # -- Step 5: Targeted revisions for consensus items --
        for idx, item in enumerate(consensus_items):
            ch_num = item["chapter"]
            question = item["question"]
            banner(f"  Revising Ch {ch_num} ({question}) [{idx+1}/{len(consensus_items)}]", ".")

            # Snapshot the current chapter score for comparison
            pre_eval = uv_run(f"evaluate.py --chapter={ch_num}", timeout=300)
            pre_score = parse_score(pre_eval.stdout, "overall_score")

            # Generate revision brief
            brief_file = BRIEFS_DIR / f"ch{ch_num:02d}_cycle{cycle}_{question}.md"
            gen_brief = BASE_DIR / "gen_brief.py"
            if gen_brief.exists():
                step(f"Generating brief for Ch {ch_num}...")
                run_tool(f"uv run python gen_brief.py --panel {ch_num}", timeout=300)
                # gen_brief.py may write to briefs/ — find the most recent brief
                brief_candidates = sorted(
                    BRIEFS_DIR.glob(f"ch{ch_num:02d}*.md"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
                if brief_candidates:
                    brief_file = brief_candidates[0]
            else:
                # Create a minimal brief from the panel data
                step(f"gen_brief.py not found, creating minimal brief for Ch {ch_num}...")
                brief_content = (
                    f"# Revision Brief: Chapter {ch_num}\n\n"
                    f"## Issue: {question}\n\n"
                    f"Panel consensus identified this chapter for revision.\n"
                    f"Focus: address the {question.replace('_', ' ')} issue.\n"
                    f"Preserve existing voice, character work, and essential beats.\n"
                )
                brief_file.write_text(brief_content)

            if not brief_file.exists():
                step(f"No brief file found for Ch {ch_num}, skipping")
                continue

            # Run revision
            step(f"Revising Ch {ch_num} with brief {brief_file.name}...")
            uv_run(f"gen_revision.py {ch_num} {brief_file}", timeout=600)

            # 本文を触ったので章ゲートを回す（FAIL なら修復 → それでも駄目なら元に戻す）
            ch_file = CHAPTERS_DIR / f"ch_{ch_num:02d}.md"
            gate_path = f"chapters/ch_{ch_num:02d}.md"
            if REVISION_GATES and run_tool(gate_cmd_for(gate_path), timeout=300).returncode != 0:
                step("  改訂版が章ゲートに落ちました — 修復を試します（落ちた箇所だけ直す）")
                healed = False
                if REPAIR_ON_FAIL and ch_file.exists():
                    uv_run(f"repair_chapter.py {gate_path} "
                           f"--out chapters/ch_{ch_num:02d}_repaired.md "
                           f'--band "{CHAPTER_GATE_BAND}"', timeout=REPAIR_TIMEOUT)
                    repaired = ch_file.with_name(f"ch_{ch_num:02d}_repaired.md")
                    if repaired.exists():
                        if run_tool(gate_cmd_for(f"chapters/ch_{ch_num:02d}_repaired.md"),
                                    timeout=300).returncode == 0:
                            run_tool(f"mv chapters/ch_{ch_num:02d}_repaired.md "
                                     f"chapters/ch_{ch_num:02d}.md")
                            healed = True
                            step("  修復版がゲートを通過しました — 採用します")
                        else:
                            run_tool(f"rm -f chapters/ch_{ch_num:02d}_repaired.md")
                if not healed:
                    step("  修復しても通らないので、この改訂は元に戻します")
                    run_tool(f"git checkout -- {gate_path}")
                    log_result("reverted", f"rev-ch{ch_num:02d}", -1, 0, "discard",
                               f"Cycle {cycle}: {question} 改訂版が章ゲートFAIL（元に戻した）")
                    continue

            # Evaluate revised chapter
            post_eval = uv_run(f"evaluate.py --chapter={ch_num}", timeout=300)
            post_score = parse_score(post_eval.stdout, "overall_score")

            ch_file = CHAPTERS_DIR / f"ch_{ch_num:02d}.md"
            word_count = len(ch_file.read_text().split()) if ch_file.exists() else 0

            step(f"Ch {ch_num}: {pre_score} -> {post_score}")

            if post_score >= pre_score:
                commit_hash = git_add_commit(
                    f"revision cycle {cycle}: ch{ch_num:02d} "
                    f"{question} {pre_score}->{post_score}")
                log_result(commit_hash, f"rev-ch{ch_num:02d}", post_score,
                           word_count, "keep",
                           f"Cycle {cycle}: {question} improved {pre_score}->{post_score}")
            else:
                step(f"Revision made it worse ({post_score} < {pre_score}), reverting")
                git_reset_hard("HEAD")
                log_result("reverted", f"rev-ch{ch_num:02d}", post_score,
                           word_count, "discard",
                           f"Cycle {cycle}: {question} regressed {pre_score}->{post_score}")

        # -- Step 6: Full novel evaluation --
        step("Running full novel evaluation...")
        full_eval = uv_run("evaluate.py --full", timeout=600)
        novel_score = parse_score(full_eval.stdout, "novel_score")

        if novel_score < 0:
            # Fallback: try overall_score
            novel_score = parse_score(full_eval.stdout, "overall_score")

        total_words = count_words_in_chapters()
        step(f"Novel score: {novel_score}  (prev: {prev_score}, words: {total_words})")

        # Commit cycle results
        commit_hash = git_add_commit(
            f"revision cycle {cycle} complete: novel_score {novel_score}")
        log_result(commit_hash, f"revision-cycle-{cycle}", novel_score,
                   total_words, "cycle",
                   f"Cycle {cycle}: novel_score {prev_score}->{novel_score}")

        state["novel_score"] = novel_score
        state["revision_cycle"] = cycle
        save_state(state)

        # -- Step 7: Plateau detection --
        if cycle >= MIN_REVISION_CYCLES and abs(novel_score - prev_score) < PLATEAU_DELTA:
            step(f"Plateau detected (delta {abs(novel_score - prev_score):.2f} "
                 f"< {PLATEAU_DELTA}) after {cycle} cycles — stopping")
            break

        prev_score = novel_score

    # =========================================================
    # PHASE 3b: OPUS REVIEW LOOP (deep, prose-level refinement)
    # =========================================================
    review_py = BASE_DIR / "review.py"
    if review_py.exists():
        banner("PHASE 3b: OPUS REVIEW LOOP", "=")
        
        max_review_rounds = 4
        for rnd in range(1, max_review_rounds + 1):
            banner(f"Opus Review Round {rnd}/{max_review_rounds}", "-")
            
            # Step 1: Generate the review
            step("Sending manuscript to Opus for review...")
            review_result = uv_run(
                f"review.py --output reviews.md", timeout=900)
            
            # Step 2: Parse the review
            step("Parsing review...")
            parse_result = run_tool(
                "uv run python review.py --parse", timeout=60)
            print(parse_result.stdout if parse_result else "")
            
            # Step 3: Check stopping condition
            review_logs = sorted(
                (EDIT_LOGS_DIR).glob("*_review.json"), reverse=True)
            if review_logs:

                review_data = json.loads(review_logs[0].read_text())
                stars = review_data.get("stars", 0) or 0
                total_items = review_data.get("total_items", 0)
                major_items = review_data.get("major_items", 0)
                qualified = review_data.get("qualified_items", 0)
                
                step(f"Stars: {stars}, Items: {total_items} "
                     f"({major_items} major, {qualified} qualified)")
                
                # Stop if: ≥4★, no major unqualified items, or >half qualified
                if stars >= 4.5 and major_items == 0:
                    step("★★★★½ with no major items — novel is ready.")
                    break
                if stars >= 4 and total_items > 0 and qualified / total_items > 0.5:
                    step(f"★{'★' * int(stars)} with majority qualified items — novel is ready.")
                    break
            
            # Step 4: Generate briefs from review items and fix
            step("Generating revision briefs from review...")
            gen_brief_py = BASE_DIR / "gen_brief.py"
            if gen_brief_py.exists():
                # Auto mode: picks weakest chapter, cross-references all sources
                run_tool("uv run python gen_brief.py --auto", timeout=300)
                
                # Find any generated briefs and apply the top one
                recent_briefs = sorted(
                    BRIEFS_DIR.glob("*_auto.md"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
                if recent_briefs:
                    brief = recent_briefs[0]
                    # Extract chapter number from filename
                    ch_match = re.search(r'ch(\d+)', brief.name)
                    if ch_match:
                        ch_num = int(ch_match.group(1))
                        step(f"Revising Ch {ch_num} from review brief...")
                        uv_run(f"gen_revision.py {ch_num} {brief}", timeout=600)
                        git_add_commit(
                            f"review round {rnd}: revise ch{ch_num:02d} from Opus feedback")
            
            # Step 5: Mechanical fixes from review
            # Run slop pass on any mentioned patterns
            step("Running mechanical cleanup pass...")
            apply_cuts_py = BASE_DIR / "apply_cuts.py"
            if apply_cuts_py.exists():
                run_tool(
                    "uv run python apply_cuts.py all --types OVER-EXPLAIN REDUNDANT --min-fat 15",
                    timeout=300)
                git_add_commit(f"review round {rnd}: mechanical cleanup")
            
            step(f"Review round {rnd} complete.")
        
        banner("OPUS REVIEW LOOP COMPLETE")
    
    state["phase"] = "export"
    state["current_focus"] = "export"
    save_state(state)

    banner(f"REVISION COMPLETE — {state.get('revision_cycle', 0)} cycles, "
           f"novel_score {state.get('novel_score', 0)}")
    return state


# ---------------------------------------------------------------------------
# PHASE 4 — EXPORT
# ---------------------------------------------------------------------------

def run_export(state: dict) -> dict:
    """
    Build final deliverables: outline, arc summary, manuscript, PDF.
    """
    banner("PHASE 4: EXPORT", "=")

    # 1. Rebuild outline from chapters
    build_outline = BASE_DIR / "build_outline.py"
    if build_outline.exists():
        step("Rebuilding outline from chapters...")
        uv_run("build_outline.py", timeout=300)

    # 2. Build arc summary
    build_arc = BASE_DIR / "build_arc_summary.py"
    if build_arc.exists():
        step("Building arc summary...")
        uv_run("build_arc_summary.py", timeout=300)

    # 3. Concatenate chapters into manuscript.md
    step("Building manuscript.md...")
    manuscript = BASE_DIR / "manuscript.md"
    chapter_files = sorted(CHAPTERS_DIR.glob("ch_*.md"))

    parts = []
    for ch_file in chapter_files:
        text = ch_file.read_text().strip()
        if text:
            parts.append(text)

    if parts:
        manuscript.write_text("\n\n---\n\n".join(parts) + "\n")
        word_count = sum(len(p.split()) for p in parts)
        step(f"Manuscript: {len(parts)} chapters, {word_count} words")
    else:
        step("WARNING: no chapter files found for manuscript")

    # 4. Build LaTeX
    build_tex = BASE_DIR / "typeset" / "build_tex.py"
    if build_tex.exists():
        step("Building LaTeX content...")
        run_tool(f"uv run python typeset/build_tex.py", timeout=120)

        # 5. Typeset with tectonic (if available)
        novel_tex = BASE_DIR / "typeset" / "novel.tex"
        if novel_tex.exists():
            tectonic_check = run_tool("which tectonic", timeout=10)
            if tectonic_check.returncode == 0:
                step("Typesetting PDF with tectonic...")
                result = run_tool("tectonic typeset/novel.tex", timeout=300)
                if result.returncode == 0:
                    step("PDF generated: typeset/novel.pdf")
                else:
                    step("WARNING: tectonic typesetting failed")
            else:
                step("tectonic not found, skipping PDF generation")
    else:
        step("typeset/build_tex.py not found, skipping LaTeX")

    # 6. Final commit
    commit_hash = git_add_commit("export: manuscript, outline, arc summary, PDF")
    total_words = count_words_in_chapters()
    log_result(commit_hash, "export", state.get("novel_score", "?"),
               total_words, "export", "Final export")

    state["phase"] = "complete"
    state["current_focus"] = "done"
    save_state(state)

    banner(f"EXPORT COMPLETE — {len(chapter_files)} chapters, {total_words} words")
    return state


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_pipeline(args):
    """Run the full pipeline or a specific phase."""

    # Load or initialize state
    if args.from_scratch:
        banner("STARTING FROM SCRATCH")
        seed_file = BASE_DIR / "seed.txt"
        if not seed_file.exists():
            print("ERROR: seed.txt not found. Cannot start from scratch without a seed.")
            sys.exit(1)
        state = default_state()
        save_state(state)
    else:
        state = load_state()

    # Ensure directories exist
    CHAPTERS_DIR.mkdir(exist_ok=True)
    BRIEFS_DIR.mkdir(exist_ok=True)
    EDIT_LOGS_DIR.mkdir(exist_ok=True)
    EVAL_LOGS_DIR.mkdir(exist_ok=True)

    # Apply max_cycles override
    max_cycles = args.max_cycles if args.max_cycles else MAX_REVISION_CYCLES

    # Determine which phases to run
    if args.phase:
        # Single phase mode
        phases = [args.phase]
    else:
        # Run from current state onward
        current = state.get("phase", "foundation")
        if current == "complete":
            print("Pipeline already complete. Use --from-scratch to restart "
                  "or --phase to run a specific phase.")
            return
        try:
            start_idx = PHASE_ORDER.index(current)
        except ValueError:
            start_idx = 0
        phases = PHASE_ORDER[start_idx:]

    banner(f"AUTONOVEL PIPELINE — phases: {', '.join(phases)}")
    print(f"  State: phase={state.get('phase')}, "
          f"foundation_score={state.get('foundation_score', 0)}, "
          f"chapters={state.get('chapters_drafted', 0)}/{state.get('chapters_total', '?')}, "
          f"novel_score={state.get('novel_score', 0)}")

    start_time = datetime.now()

    for phase in phases:
        try:
            if phase == "foundation":
                state = run_foundation(state)
            elif phase == "drafting":
                state = run_drafting(state)
            elif phase == "revision":
                state = run_revision(state, max_cycles=max_cycles)
            elif phase == "export":
                state = run_export(state)
            else:
                print(f"Unknown phase: {phase}")
                sys.exit(1)
        except KeyboardInterrupt:
            banner("INTERRUPTED — state saved")
            save_state(state)
            sys.exit(130)
        except Exception as e:
            print(f"\n  FATAL ERROR in {phase}: {e}")
            save_state(state)
            raise

    elapsed = datetime.now() - start_time
    hours = elapsed.total_seconds() / 3600

    banner("PIPELINE COMPLETE")
    print(f"  Time:       {hours:.1f} hours")
    print(f"  Phase:      {state.get('phase')}")
    print(f"  Foundation: {state.get('foundation_score', 0)}")
    print(f"  Chapters:   {state.get('chapters_drafted', 0)}/{state.get('chapters_total', '?')}")
    print(f"  Words:      {count_words_in_chapters()}")
    print(f"  Novel:      {state.get('novel_score', 0)}")
    print(f"  Cycles:     {state.get('revision_cycle', 0)}")


def main():
    parser = argparse.ArgumentParser(
        description="Autonovel pipeline orchestrator — seed to finished novel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python run_pipeline.py                     # resume from current state
  python run_pipeline.py --from-scratch      # start fresh from seed.txt
  python run_pipeline.py --phase foundation  # run only foundation
  python run_pipeline.py --phase drafting    # run only drafting
  python run_pipeline.py --phase revision    # run only revision
  python run_pipeline.py --phase export      # run only export
  python run_pipeline.py --max-cycles 4      # limit revision to 4 cycles
""")

    parser.add_argument(
        "--from-scratch", action="store_true",
        help="Reset state and start from seed.txt")
    parser.add_argument(
        "--phase", choices=PHASE_ORDER,
        help="Run only a specific phase")
    parser.add_argument(
        "--max-cycles", type=int, default=None,
        help=f"Maximum revision cycles (default: {MAX_REVISION_CYCLES})")

    args = parser.parse_args()
    run_pipeline(args)


if __name__ == "__main__":
    main()
