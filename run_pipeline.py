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
# 設定が合格点に届かないまま何周続いたら止めるか。実測: 届かないまま20周（＝上限）まで
# 回り続け、判定だけで数十万トークンを捨てた。改善しないループは早く止める。
FOUNDATION_PLATEAU_ITERS = int(os.environ.get("AUTONOVEL_FOUNDATION_PLATEAU_ITERS", "4"))
CHAPTER_THRESHOLD = float(os.environ.get("AUTONOVEL_CHAPTER_THRESHOLD", "5.5"))
# 章の合格ライン。本家の仕様は 6.0、fork は 4.0 で走らせていた。
# **ラインは判定モデルの厳しさに合わせて実測で決める**（甘いモデル＋高い線＝作り直し地獄）。
# 2026-10-07: 手元のローカル12Bは点が甘く（却下された生原稿 4.7／仕上げた章 7.8／初稿 8.0）、
# 4.0 では弱い章も通った → 6.0 に上げた。
# 2026-10-08: 判定をクラウドの gemma4:31b-cloud に替えると**生原稿は 5.4〜5.8 に密集**し、
# 6.0 だと1章あたり3.3回の作り直し＝**56万トークン**を食った（23章で1000万トークン超）。
# 生原稿にかける線は **5.5** が妥当（品質は改訂フェーズで上げる。ドラフト段階で
# 完成稿の物差しを当てない）。上限（MAX_CHAPTER_ATTEMPTS）とセットで費用を読むこと。
# 判定（evaluate.py）1回に許す時間。既定300秒。
# 2026-10-08 実測: 手元のローカルモデルが**GPUに載らずCPUで動くと1回が数十分**かかり、
# 300秒で切れて「評価の失敗」として走行が止まった。遅い判定を使うときはここを上げる
# （例: AUTONOVEL_EVAL_TIMEOUT=900）。
EVAL_TIMEOUT = int(os.environ.get("AUTONOVEL_EVAL_TIMEOUT", "300"))
MAX_FOUNDATION_ITERS = 20
MAX_CHAPTER_ATTEMPTS = int(os.environ.get("AUTONOVEL_MAX_CHAPTER_ATTEMPTS", "5"))
# 同じ章を何回まで作り直すか。1回の作り直し＝約17万トークン（実測）。
# 2026-10-08 実測: 判定が厳しいモデル（クラウドのgemma4）だと生原稿が5.4〜5.8に密集し、
# 5回まで粘ると**1章あたり3.3回の作り直し＝56万トークン**になった。上限は費用の主レバー。
# 判定（judge）の応答が読めないときのやり直し回数。すべて失敗したら
# 「低い点」ではなく「評価の失敗」として止める（stop_for_eval_failure）。
MAX_JUDGE_TRIES = 3
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

# ── 全編の目標字数（作り直し後の「薄まり」を防ぐ）────────────────────
# なぜ必要か（実測 2026-10-13）: セリフ比の検査を入れたら、改訂が「説明を会話に
# 置き換えろ」と動いて地の文が減り、24章93,466字 → 17章60,520字（35%減）になった。
# 章ごとの下限（1章3,000字）は守られていても、総量はどこも見ていなかった。
# → 目標字数を置き、サイクルごとに達しなければ膨らませの指示を出す。
#   0 または未設定なら検査しない（従来どおり）。
TARGET_WORDS = int(os.environ.get("AUTONOVEL_TARGET_WORDS", "0"))
# 目標の何%を下回ったら警告するか（既定 0.9＝10%以上不足で警告）。
TARGET_WORDS_MIN_RATIO = float(os.environ.get("AUTONOVEL_TARGET_WORDS_MIN_RATIO", "0.9"))
# 目標字数が未設定のときの1章あたりの字数の目安。
# なぜ自動算出にするか: 目標字数は作品ごとに作者が決めるものだが、毎回決めるのは手間なので、
# 章数から出すデフォルトを用意した（実測 2026-10-13: 作り直し後の1章平均は3,560字で、
# これは薄まりすぎだった。元の24章は1章3,894字だった）。
#   0 を指定すると自動算出をやめる（＝検査しない）。
DEFAULT_WORDS_PER_CHAPTER = int(os.environ.get("AUTONOVEL_DEFAULT_WORDS_PER_CHAPTER", "5000"))


def thinnest_chapters(exclude: int = 0, limit: int = 3) -> list:
    """地の文（＝会話でない部分）の割合が大きい章を返す。

    なぜ: セリフ比の検査が効きすぎて地の文が減り全体が薄まった（実測 2026-10-13）。
    膨らませるときは「会話が薄い章」ではなく**地の文の厚い章**を足す先に選ぶ
    （会話が薄い章は既に地の文だらけなので、そこに足すと更に説明臭くなる）。
    """
    rows = []
    if not CHAPTERS_DIR.exists():
        return rows
    for f in sorted(CHAPTERS_DIR.glob("ch_*.md")):
        try:
            num = int(f.stem.split("_")[-1])
        except ValueError:
            continue
        if num == exclude:
            continue
        text = f.read_text(encoding="utf-8")
        total = jp_char_count(text)
        if total <= 0:
            continue
        dlg = sum(jp_char_count(m) for m in re.findall(r"「[^」]*」", text))
        rows.append((1 - dlg / total, num))
    rows.sort(reverse=True)
    return [n for _r, n in rows[:limit]]


def target_words_for(state: dict) -> int:
    """目標字数。解決順: state.json の target_words → env AUTONOVEL_TARGET_WORDS
    → 章数 × 1章あたりの目安（自動算出）。

    自動算出にする理由: 目標字数は本来作者が決めるものだが、毎回決めるのは手間。
    未設定でも薄まりすぎを検出できるように、章数から出す。実測 2026-10-13 では
    作り直し後の1章平均3,560字が薄まりすぎだった（元は3,894字）。
    """
    for src in (state or {}, os.environ):
        val = src.get("target_words") if isinstance(src, dict) else src.get("AUTONOVEL_TARGET_WORDS")
        if val:
            try:
                n = int(val)
                if n > 0:
                    return n
            except (TypeError, ValueError):
                pass
    if DEFAULT_WORDS_PER_CHAPTER > 0 and CHAPTERS_DIR.exists():
        n_ch = len(list(CHAPTERS_DIR.glob("ch_*.md")))
        if n_ch:
            return n_ch * DEFAULT_WORDS_PER_CHAPTER
    return TARGET_WORDS

# 初稿（drafting）の章ゲートで「記録のみ＝破棄条件にしない」項目。
# 破棄条件に残すのは 文字数・重複・記号・比喩の型（＝修正が効く欠陥）。
# セリフ比・最小窓は「完成稿の物さし」で、生原稿は落ちるのが普通（実測 2026-10-03:
# 生原稿は6項目中3項目FAIL／人手ポリッシュ後の採用版は全PASS）。
# 初稿でこれらを破棄条件にすると1章あたり最大15回の章生成を消費して無駄になる。
# 手動で完成稿を検査するときは未指定（＝全項目が合否対象）にする。
GATE_WARN_ITEMS = os.environ.get("AUTONOVEL_GATE_WARN_ITEMS", "dialogue_ratio,min_window")

# 改訂（revision）の章ゲートで「記録のみ」にする項目。**既定は空＝全項目が合否対象。**
#
# なぜ分けたか（実測 2026-10-08）: 講評の「説明を台詞でなく描写で」に従って章を
# 書き直すと、セリフ比が50%未満に落ちた章が6章・最小窓30%未満が11章に出た
# （初稿はそれぞれ1章・0章）。ところが改訂のゲートも GATE_WARN_ITEMS を共有して
# いたため、ヒサ品質ルール（セリフ比・最小窓）が改訂中は警告どまり＝抜け穴だった。
# 生原稿を落とさないための措置が、改訂の品質も落としていた。
#
# 改訂は「できあがった本文を直す」工程なので、完成稿の物さしをそのまま当てる。
# 落ちたら修復（repair_chapter.py が会話を足す処理を持つ）→ 通らなければ元に戻す。
# どうしても警告に戻したいときは AUTONOVEL_REVISION_GATE_WARN_ITEMS="dialogue_ratio" 等。
REVISION_GATE_WARN_ITEMS = os.environ.get("AUTONOVEL_REVISION_GATE_WARN_ITEMS", "")

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


def pyexe() -> str:
    """このリポジトリの .venv のインタプリタ。無ければシステムの python3。

    探す場所は**このファイルの隣**だけでなく、環境変数 `AUTONOVEL_PYTHON` と
    カレントディレクトリの `.venv` も見る。なぜか（実測 2026-10-13）:
    スクリプト一式を作業用ディレクトリへコピーして実行すると
    `Path(__file__).parent / ".venv"` は存在せず、システムの python3 に
    フォールバックして `ModuleNotFoundError: No module named 'dotenv'` で
    落ちた。本番の venv の場所を env で渡せるようにする。
    """
    env = os.environ.get("AUTONOVEL_PYTHON")
    if env and Path(env).exists():
        return env
    for base in (Path(__file__).parent, Path.cwd()):
        cand = base / ".venv" / "bin" / "python"
        if cand.exists():
            return str(cand)
    return "python3"


def uv_run(script: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """'python <script>' を .venv のインタプリタで実行する。

    なぜ `uv run` をやめたか（実測 2026-10-13）: `uv run` は
    `UV_PROJECT_ENVIRONMENT` を無視して uv が独自管理する python3.12 を
    使い、その環境には dotenv も httpx も入っていない。
    -> evaluate.py が ModuleNotFoundError で落ち、改訂ループが
    「評価が読めない」として止まった（低得点ではない）。
    .venv 直叩きなら本番と同じパッケージが見える。
    """
    return run_tool(f'"{pyexe()}" {script}', timeout=timeout)


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


QUOTA_MARKER = BASE_DIR / "edit_logs" / "STOP_QUOTA.txt"


def quota_stop_requested() -> bool:
    """llm.py が「429 が連続＝プロバイダが受け付けていない」と判断したときの合図を見る."""
    return QUOTA_MARKER.exists()


def stop_for_quota() -> bool:
    """上限で止めるときの表示。周回を空回りさせず、再開方法を示して止まる.

    実測（2026-10-04）: 429 が続いたまま 8時間・19周を空回りし、その間の生成は全滅した。
    周回を続けても回復しないので、合図が出たら**ここで止める**。
    """
    banner("STOPPED — model provider is refusing requests (HTTP 429)", "=")
    print(f"  詳細: {QUOTA_MARKER}")
    print("  再開: このファイルを消して、同じコマンドをもう一度実行してください。")
    print("        使用率を監視して自動で待機・再開させたい場合は quota_guard.py 経由で走らせます。")
    try:
        print("  ---")
        print("  " + QUOTA_MARKER.read_text(encoding="utf-8").strip().replace("\n", "\n  "))
    except OSError:
        pass
    raise SystemExit(0)      # 周回を続けても回復しないので、ここで止める


def stop_for_eval_failure(stage: str, detail: str = "") -> None:
    """評価そのものが成立しなかったときは止める（＝作品の出来とは無関係）.

    実測（2026-10-05）: 判定が解釈できないまま「スコア -1.0＝改善なし」と扱われ、
    生成物を毎回捨てながら19周・約100万トークンを空回りした。**壊れた評価を
    「低い点」と混同しない**のがこの関数の役目。
    """
    banner("STOPPED — the evaluation itself failed (this is NOT a low score)", "=")
    print(f"  段階: {stage}")
    if detail:
        print("  ---")
        print("  " + detail.strip()[-800:].replace("\n", "\n  "))
    print("  生成物は捨てていません（そのまま残っています）。")
    print("  直したら同じコマンドで再開できます（state.json から続きます）。")
    raise SystemExit(0)


def stop_for_foundation_plateau(no_gain: int, best_score: float) -> None:
    """設定が合格点に届かないまま改善しないときは止める（消費の空回りを防ぐ）."""
    banner("STOPPED — foundation is not improving", "=")
    print(f"  {no_gain} 周続けて合格点（{FOUNDATION_THRESHOLD}）に届きませんでした"
          f"（最高 {best_score}）。")
    print("  周回を重ねても改善しないのでここで止めます（無駄な消費を防ぐため）。")
    print("  見直す場所: 判定の較正（evaluate.py）か、設定の生成（gen_*.py）。")
    raise SystemExit(0)


def git_reset_hard(ref: str = "HEAD~1", keep_seed: bool = True):
    """Hard reset to discard bad changes.

    seed.txt だけは巻き戻さない（2026-10-04 修正）。seed.txt は「人間が書く唯一の入力」
    なので、`--from-scratch` で種を貼った直後に foundation の1周目が失敗して
    `git reset --hard` が走ると、**未コミットの種が消えて見本（サンプル作品）に戻る**。
    以降の周回は見本の物語を作り続け、整合チェックが永久に通らない。
    """
    seed_file = BASE_DIR / "seed.txt"
    seed_backup = seed_file.read_text(encoding="utf-8") if seed_file.exists() else None
    step(f"GIT RESET: {ref}")
    run_tool(f"git reset --hard {ref}")
    if keep_seed and seed_backup:
        now = seed_file.read_text(encoding="utf-8") if seed_file.exists() else ""
        if now != seed_backup:
            seed_file.write_text(seed_backup, encoding="utf-8")
            print("  SEED KEPT: seed.txt はリセットで戻さない（人間の入力を守る）")


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


def jp_char_count(text: str) -> int:
    """日本語の本文の長さ＝文字数。

    空白で区切って数えると、日本語は行や段落の数しか出ない（実測: 24章119,210字の小説が
    「2942 words」と表示され、桁が読めなかった）。本文の長さは**字数**で数える。
    **数え方は章ゲートの「文字数」と同じ（改行を含む生の文字数）** — 別々に数えると
    同じ章が「4,608」と「4,387」の2通りで表示され、どちらが合否の数字か分からなくなる。
    """
    return len(text)


def count_words_in_chapters() -> int:
    """全章の字数の合計（章ゲートの「文字数」と同じ数え方）。"""
    total = 0
    if CHAPTERS_DIR.exists():
        for f in CHAPTERS_DIR.glob("ch_*.md"):
            total += jp_char_count(f.read_text())
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
    no_gain = 0

    for i in range(iteration + 1, MAX_FOUNDATION_ITERS + 1):
        if quota_stop_requested():
            stop_for_quota()
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
            no_gain += 1
            continue

        # 2. Evaluate (retry on parse failure / judge model returning bad JSON)
        step("Evaluating foundation...")
        score = -1.0
        lore = -1.0
        eval_result = None
        for eval_attempt in range(1, MAX_JUDGE_TRIES + 1):
            eval_result = uv_run("evaluate.py --phase=foundation", timeout=EVAL_TIMEOUT)
            score = parse_score(eval_result.stdout, "overall_score")
            lore = parse_lore_score(eval_result.stdout)
            if score >= 0:
                break
            step(f"Evaluation parse failed (score {score}), retrying ({eval_attempt}/3)...")

        if score < 0:
            stop_for_eval_failure(
                f"foundation iteration {i} の評価（{MAX_JUDGE_TRIES}回試して解釈できず）",
                ((eval_result.stderr or "") + (eval_result.stdout or "")) if eval_result else "")

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
            no_gain = 0
        else:
            step(f"Score did not improve ({score} <= {best_score}), discarding")
            git_reset_hard("HEAD")
            log_result("discarded", "foundation", score, 0, "discard",
                       f"Iteration {i}: no improvement ({score} <= {best_score})")
            no_gain += 1

        # 4. Check exit condition
        if best_score >= FOUNDATION_THRESHOLD:
            step(f"Foundation score {best_score} >= {FOUNDATION_THRESHOLD} — PASSED")
            break
        # 合格点に届かないまま改善しないなら、周回を重ねず止める（消費を守る）
        if no_gain >= FOUNDATION_PLATEAU_ITERS:
            stop_for_foundation_plateau(no_gain, best_score)
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
# 段階実行: 1回の実行で何章まで書くか（0=残り全部）。2026-10-06 Hisa要望「大量消費の前に
# 段階実行（小さく実測→続行判断）」のためのノブ。章ごとに保存されるので、止めても同じ
# コマンドで続きから進む。
MAX_CHAPTERS = int(os.environ.get("AUTONOVEL_MAX_CHAPTERS", "0"))
# 指定した章だけを書く（穴の埋め戻し用）。例: AUTONOVEL_CHAPTERS_ONLY=3,5,6
# 使いどころ（2026-10-08 の実測）: 全試行が不合格になった章が**消えて穴が空いた**とき、
# その章だけを作り直す。state の「どこまで書いたか」は下げない（max で更新する）。
CHAPTERS_ONLY = [int(x) for x in
                 os.environ.get("AUTONOVEL_CHAPTERS_ONLY", "").replace(" ", "").split(",")
                 if x.strip().isdigit()]
TRIAL_BRIEF = BASE_DIR / os.environ.get(
    "AUTONOVEL_TRIAL_BRIEF", "briefs/trial_chapter_brief.md")


def _trial_fail(state: dict, ch_file: Path, gate_out: str,
                repair_out: str, score: float, verdict: str) -> tuple:
    """試作テストの不合格を数行のブリーフに残し、設定を直すために止める（既定）。"""
    fail_lines = [l.strip() for l in gate_out.splitlines()
                  if "[FAIL]" in l or "[WARN]" in l or l.strip().startswith("RESULT")]
    fix_lines = [l.strip() for l in repair_out.splitlines() if l.strip().startswith("- ")]
    err_lines = [l.strip() for l in repair_out.splitlines()
                 if "例外" in l or l.strip().startswith("Traceback")
                 or "Error" in l or "error:" in l][:3]

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
    body += [f"- {l}" for l in fix_lines[:6]] if fix_lines else (
        [f"- 修復が例外で止まりました: {l}" for l in err_lines]
        if err_lines else
        ["- （修復は走りましたが、直せた箇所は0件）",
         f"- 修復の出力末尾: {repair_out.strip().splitlines()[-1][:120]}"]
        if repair_out.strip() else
        ["- （修復は走っていません）"])
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
                f'"{pyexe()}" verify_chapter_gates.py --band {CHAPTER_GATE_BAND} ')
    gate = run_tool(gate_cmd + f"chapters/ch_{ch:02d}.md", timeout=180)
    repair_out, healed = "", False
    if gate.returncode != 0:
        if REPAIR_ON_FAIL:
            step("章ゲートに落ちたので修復します（落ちた箇所だけを直す）")
            rep = uv_run(f"repair_chapter.py chapters/ch_{ch:02d}.md "
                         f"--out chapters/ch_{ch:02d}_repaired.md "
                         f'--band "{CHAPTER_GATE_BAND}"', timeout=REPAIR_TIMEOUT)
            repair_out = (rep.stdout or "") + "\n" + (rep.stderr or "")
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
    ev = None
    for _ in range(MAX_JUDGE_TRIES):
        ev = uv_run(f"evaluate.py --chapter={ch}", timeout=EVAL_TIMEOUT)
        score = parse_score(ev.stdout, "overall_score")
        if score >= 0:
            break
    if score < 0:
        stop_for_eval_failure(f"試作1章（第{ch}章）の評価（{MAX_JUDGE_TRIES}回試して解釈できず）",
                              ((ev.stderr or "") + (ev.stdout or "")) if ev else "")
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


def discard_chapter_file(ch: int) -> None:
    """捨てると決めた章の原稿を確実に消す（2026-10-08）。

    `git checkout --` は**追跡されている**ファイルしか戻せない。まだ一度もコミットして
    いない章（最初の試行で落ちた章）は戻せず、その場に残る。残ると
    `AUTONOVEL_REUSE_DRAFT=1` が「捨てたはずの原稿」を拾い、同じ章を評価し続ける。
    """
    rel = f"chapters/ch_{ch:02d}.md"
    run_tool(f"git checkout -- {rel} 2>/dev/null || true")
    if (BASE_DIR / rel).exists():
        tracked = run_tool(f"git ls-files --error-unmatch {rel} 2>/dev/null").returncode == 0
        if not tracked:
            run_tool(f"rm -f {rel}")


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
    if CHAPTERS_ONLY:
        # 穴の埋め戻し（AUTONOVEL_CHAPTERS_ONLY=3,5,6）は章番号で直接指定して回す。
        # state の「どこまで書いたか」より手前の章を書くので、開始位置を先頭に戻す。
        start_chapter = 1
        step(f"指定された章だけを書きます: {CHAPTERS_ONLY}（AUTONOVEL_CHAPTERS_ONLY）")
    if MAX_CHAPTERS:
        stage_end = min(total, start_chapter + MAX_CHAPTERS - 1)
        step(f"段階実行: 今回の範囲は 第{start_chapter}章〜第{stage_end}章"
             f"（AUTONOVEL_MAX_CHAPTERS={MAX_CHAPTERS}）")

    CHAPTERS_DIR.mkdir(exist_ok=True)

    for ch in range(start_chapter, total + 1):
        if CHAPTERS_ONLY and ch not in CHAPTERS_ONLY:
            continue
        if MAX_CHAPTERS and ch > start_chapter + MAX_CHAPTERS - 1:
            save_state(state)
            banner(f"段階実行: {MAX_CHAPTERS}章ぶん書き終えたので一度止めます", "-")
            print(f"  書けた章: 第{state.get('chapters_drafted')}章まで / 全{total}章")
            print("  続けるには同じコマンドをもう一度実行してください（書けた章は保存済み）。")
            print("  全章まで一気に進めるなら AUTONOVEL_MAX_CHAPTERS=0 を付けて実行します。")
            sys.exit(0)
        if quota_stop_requested():
            stop_for_quota()
        banner(f"Drafting Chapter {ch}/{total}", "-")
        drafted = False
        # 全試行が不合格のときのために「いちばん良かった稿」を退避しておく（2026-10-08）。
        # 以前はここで消してしまい、**章そのものが消えて物語に穴が空いた**（実測: 5章が欠落）。
        best_draft = CHAPTERS_DIR / f".best_ch_{ch:02d}.md"
        run_tool(f"rm -f {best_draft}")
        best_score = -1.0

        def keep_if_best(score_now: float) -> None:
            """破棄する前に、いままでで一番良い稿なら退避しておく。"""
            nonlocal best_score
            if not ch_file.exists() or score_now <= best_score:
                return
            best_score = score_now
            run_tool(f"cp chapters/ch_{ch:02d}.md {best_draft}")

        for attempt in range(1, MAX_CHAPTER_ATTEMPTS + 1):
            step(f"Attempt {attempt}/{MAX_CHAPTER_ATTEMPTS}")

            # 既存の原稿を使う（判定側の障害で止まったとき、書けた原稿を捨てないため）。
            # 2026-10-07 の実測: ローカルの思考モデルを判定に使うと本文が空で返り、
            # 「評価が解釈できない」で停止 → 再開すると**同じ章を丸ごと書き直して**いた。
            # AUTONOVEL_REUSE_DRAFT=1 なら、ある原稿をそのまま評価から再開する。
            ch_file = CHAPTERS_DIR / f"ch_{ch:02d}.md"
            reuse = (os.environ.get("AUTONOVEL_REUSE_DRAFT", "") == "1"
                     and ch_file.exists() and ch_file.stat().st_size >= 100)
            if reuse:
                step("既存の原稿をそのまま使います（AUTONOVEL_REUSE_DRAFT=1・作り直しません）")
            else:
                # Draft（既定は fork 推奨の分割ドラフター。セリフ比率ゲート内蔵）
                draft_result = uv_run(f"{DRAFTER} {ch}", timeout=DRAFT_TIMEOUT)
                if draft_result.returncode != 0:
                    step(f"Draft failed (exit {draft_result.returncode}), retrying...")
                    continue

                # Check the chapter file exists and has content
                if not ch_file.exists() or ch_file.stat().st_size < 100:
                    step("Chapter file missing or too short, retrying...")
                    continue

            word_count = jp_char_count(ch_file.read_text())
            step(f"Drafted {word_count}字")

            # Evaluate (retry on parse failure / judge model returning bad JSON)
            score = -1.0
            eval_result = None
            for eval_attempt in range(1, MAX_JUDGE_TRIES + 1):
                eval_result = uv_run(f"evaluate.py --chapter={ch}", timeout=EVAL_TIMEOUT)
                score = parse_score(eval_result.stdout, "overall_score")
                if score >= 0:
                    break
                step(f"Chapter eval parse failed (score {score}), retrying ({eval_attempt}/3)...")

            if score < 0:
                stop_for_eval_failure(
                    f"第{ch}章の評価（{MAX_JUDGE_TRIES}回試して解釈できず）",
                    ((eval_result.stderr or "") + (eval_result.stdout or "")) if eval_result else "")
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
                    keep_if_best(score)
                    discard_chapter_file(ch)
                    continue
                # Chapter quality gates — 文体6項目（文字数/セリフ比/最小窓/重複/記号/比喩の型）
                # 破棄するのは GATE_WARN_ITEMS 以外の項目（既定: 文字数・重複・記号・比喩の型）。
                # セリフ比・最小窓は初稿では [WARN] 記録のみ（完成稿の物差し。2026-10-03 の実測で判断）
                gate_cmd = (f'AUTONOVEL_GATE_WARN_ITEMS="{GATE_WARN_ITEMS}" '
                            f'"{pyexe()}" verify_chapter_gates.py --band {CHAPTER_GATE_BAND} ')
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
                    keep_if_best(score)
                    discard_chapter_file(ch)
                    run_tool(f"rm -f chapters/ch_{ch:02d}_repaired.md")
                    continue
                commit_hash = git_add_commit(
                    f"ch{ch:02d}: score {score}, {word_count}w")
                log_result(commit_hash, f"ch{ch:02d}", score, word_count,
                           "keep", f"Chapter {ch} (attempt {attempt})")
                state["chapters_drafted"] = max(state.get("chapters_drafted", 0), ch)
                save_state(state)
                drafted = True
                break
            else:
                step(f"Score {score} < {CHAPTER_THRESHOLD}, discarding attempt")
                log_result("discarded", f"ch{ch:02d}", score, word_count,
                           "discard", f"Chapter {ch} attempt {attempt}")
                # Remove the bad chapter file so next attempt starts fresh
                keep_if_best(score)
                discard_chapter_file(ch)

        if not drafted:
            step(f"WARNING: Chapter {ch} failed all {MAX_CHAPTER_ATTEMPTS} attempts, "
                 f"keeping the best attempt and moving on")
            # いちばん良かった稿を戻して残す（穴を空けない）。
            # 残さないと次章の執筆が「前章なし」で走り、物語が繋がらなくなる（2026-10-08 実測）。
            ch_file = CHAPTERS_DIR / f"ch_{ch:02d}.md"
            if not ch_file.exists() and best_draft.exists():
                run_tool(f"mv {best_draft} chapters/ch_{ch:02d}.md")
                step(f"合格はしませんでしたが、いちばん良かった稿（{best_score}）を残します")
            if ch_file.exists():
                word_count = jp_char_count(ch_file.read_text())
                commit_hash = git_add_commit(
                    f"ch{ch:02d}: best-effort after {MAX_CHAPTER_ATTEMPTS} attempts")
                log_result(commit_hash, f"ch{ch:02d}", best_score if best_score >= 0 else "?",
                           word_count, "forced", f"Chapter {ch}: kept after max attempts")
                state["chapters_drafted"] = max(state.get("chapters_drafted", 0), ch)
                save_state(state)
        run_tool(f"rm -f {best_draft}")

    # All chapters drafted
    state["phase"] = "revision"
    state["current_focus"] = "full_novel"
    state["chapters_drafted"] = total
    state["revision_cycle"] = 0
    save_state(state)

    total_words = count_words_in_chapters()
    banner(f"DRAFTING COMPLETE — {total} chapters, {total_words}字")
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
            # 「Ch 5」「Chapter 5」「第5章」のどれでも拾う（日本語で答えさせているため）
            found = re.findall(r'Ch(?:apter)?\s*(\d+)|第\s*(\d+)\s*章', answer, re.IGNORECASE)
            chs = [a or b for a, b in found]
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
# 整合ゲート（消えた人物・設定矛盾・声の逸脱・伏線の放置）。0 で無効化。
CONSISTENCY = os.environ.get("AUTONOVEL_CONSISTENCY", "1") != "0"
# 整合チェック（judge モデル＝高い）を改訂サイクルごとに回すか。
# 既定は「最初のサイクルと最後のサイクルの2回」に絞る。実測 1回 約27,000トークンで、
# 最大6サイクル×1章＝162,000トークン/章が、54,000に減る（2026-10-06）。
CONSISTENCY_EVERY_CYCLE = os.environ.get("AUTONOVEL_CONSISTENCY_EVERY_CYCLE", "0") != "0"
CONSISTENCY_TIMEOUT = int(os.environ.get("AUTONOVEL_CONSISTENCY_TIMEOUT", "900"))
# 一対比較（Elo）で章の採否を決める。点が下がった改訂を、両順序で勝てば採用する。
ELO_DECIDE = os.environ.get("AUTONOVEL_ELO_DECIDE", "1") != "0"
ELO_TIMEOUT = int(os.environ.get("AUTONOVEL_ELO_TIMEOUT", "600"))
# 強みの移植（改訂の向きを逆にする＝天井を上げる）。0 で無効化。
STRENGTH = os.environ.get("AUTONOVEL_STRENGTH", "1") != "0"
STRENGTH_MAX_BRIEFS = int(os.environ.get("AUTONOVEL_STRENGTH_MAX_BRIEFS", "2"))
# 1周で直す章数の上限。指摘が多くても費用が暴走しないように、指摘の多い順に絞る
# （1章の改訂は 改訂前評価→指示書→整合→本文→ゲート→改訂後評価→一対比較 で約10万トークン）。
MAX_REVISIONS_PER_CYCLE = int(os.environ.get("AUTONOVEL_MAX_REVISIONS_PER_CYCLE", "6"))
STRENGTH_TIMEOUT = int(os.environ.get("AUTONOVEL_STRENGTH_TIMEOUT", "900"))

# 骨格（幕構成・章立て）を作り直す工程（章や範囲の書き直しでは消えない指摘に効かせる）。
# 章数を変えるので費用が大きい。既定は「止まって設計だけ出す」＝人間が設計を見て判断する。
#   plan（既定）: 骨格の設計を briefs/arc_rebuild_plan.json に出して止まる
#   apply:       設計を採用して骨格と本文を作り直す
#   off:         回さない
ARC_REBUILD = os.environ.get("AUTONOVEL_ARC_REBUILD", "plan")
ARC_REBUILD_TIMEOUT = int(os.environ.get("AUTONOVEL_ARC_REBUILD_TIMEOUT", "1800"))
# 骨格の作り直しを「何周目以降」に回すか。1周目は章の中の指摘を先に直したいので既定は2周目。
ARC_REBUILD_FROM_CYCLE = int(os.environ.get("AUTONOVEL_ARC_REBUILD_FROM_CYCLE", "2"))
# 骨格の指摘がこの周数だけ続いたら作り直す（同じ指摘が形を変えて出続ける＝骨格の問題）。
ARC_REBUILD_PERSIST_ROUNDS = int(os.environ.get("AUTONOVEL_ARC_REBUILD_PERSIST_ROUNDS", "2"))


def gate_cmd_for(path: str) -> str:
    """章ゲート（文体6項目）を1ファイルにかけるコマンド。

    既定ではセリフ比・最小窓は警告のみ（＝破棄条件にしない）。初稿の生原稿を
    落とさないための措置（実測 2026-10-03）。**改訂では使わない** —
    改訂は完成稿の物さしを当てるので `revision_gate_cmd_for` を使う。
    """
    return (f'AUTONOVEL_GATE_WARN_ITEMS="{GATE_WARN_ITEMS}" '
            f'"{pyexe()}" verify_chapter_gates.py --band {CHAPTER_GATE_BAND} {path}')


def append_exposition_note(extra: str, ch_num: int) -> str:
    """説明台詞の多い章のとき、**実際の台詞と現在値**を改訂ブリーフに足す。

    なぜ必要か（実測 2026-10-08）: 説明台詞を検出した3章（第5・6・9章）を書き直しても
    比率が 0.55→0.55 と動かなかった。原因はブリーフに実際の台詞が無く、
    「設定説明を地の文へ移す」という抽象的な指示だけだったため。
    → どの台詞が説明かを出し、現在値も書いて「直したか」が測れるようにする。

    検出は機械だけ（辞書ベース）なので、judge モデルの成否に関わらず動く。
    この章に何も無ければ extra をそのまま返す。
    """
    try:
        sys.path.insert(0, str(BASE_DIR))
        import verify_consistency as vc  # noqa: E402
        path = CHAPTERS_DIR / f"ch_{ch_num:02d}.md"
        if not path.exists():
            return extra
        hits = vc.check_exposition_dialogue([(ch_num, path, path.read_text(encoding="utf-8"))])
        if not hits:
            return extra
        it = hits[0]
        lines = [extra.strip()] if (extra or "").strip() else []
        lines += ["", "## 説明台詞（設定の説明を台詞から降ろす）",
                  f"この章の長い台詞 {it['long_lines']}本のうち {it['expository']}本が"
                  f"設定の説明です（比率 {it['ratio']}）。",
                  "",
                  "次の台詞が設定の説明になっています。1本ずつ、**視覚や体の感覚の描写に"
                  "置き換える**か、短いやり取りに分割してください。登場人物に世界の"
                  "仕組みを語らせないこと（読者は本文から読み取れる）。",
                  ""]
        for q in (it.get("quotes") or []):
            lines.append(f"- 「{q}」")
        lines += ["", f"直したあとの目標: 長い台詞のうち設定説明の割合を {it['ratio']} → "
                      "0.5 未満に落とす。"]
        return "\n".join(lines) + "\n"
    except Exception:
        return extra


def revision_gate_cmd_for(path: str) -> str:
    """改訂フェーズの章ゲート。**セリフ比・最小窓も合否対象**（既定）。

    実測（2026-10-08）: 改訂のゲートが会話比率を見ていなかったため、講評の
    「説明を台詞でなく描写で」に従って書き直した結果、セリフ比50%未満の章が
    6章・最小窓30%未満が11章に増えた（初稿はそれぞれ1章・0章）。
    生原稿を落とさない設定が改訂の品質も落とす抜け穴になっていた。
    """
    return (f'AUTONOVEL_GATE_WARN_ITEMS="{REVISION_GATE_WARN_ITEMS}" '
            f'"{pyexe()}" verify_chapter_gates.py --band {CHAPTER_GATE_BAND} {path}')


def changed_chapters() -> list:
    """直前の改訂で書き換わった章ファイル（未コミットの差分）。"""
    res = run_tool("git diff --name-only -- chapters", timeout=60)
    return [BASE_DIR / p for p in (res.stdout or "").splitlines()
            if p.strip().endswith(".md")]


def choose_review_targets(review_data: dict, max_n: int = 3) -> list:
    """講評の指摘を「効く章」に配る（章0＝全編の指摘にも対応する）。

    実測（2026-10-08）: 講評の重大な指摘は「章0＝全編」で返る（中盤の反復・説明台詞）。
    いままではそれを『最も弱い1章』にしか配れず、1章直して終わっていた。
    指摘の言葉と機械の測定（説明台詞の比率）から、効く章の組を選ぶ。
    """
    items = review_data.get("professor_items") or []
    nums = sorted(int(m.group(1)) for m in
                  (re.search(r"ch_(\d+)", p.name) for p in CHAPTERS_DIR.glob("ch_*.md")) if m)
    if not nums:
        return []
    n_ch = len(nums)
    early = [c for c in nums if c <= n_ch * 0.3]
    middle = [c for c in nums if n_ch * 0.3 < c <= n_ch * 0.75]
    late = [c for c in nums if c > n_ch * 0.75]
    exposition: list = []
    try:
        sys.path.insert(0, str(BASE_DIR))
        import verify_consistency as vc  # noqa: E402
        chapters = []
        for p in sorted(CHAPTERS_DIR.glob("ch_*.md")):
            m = re.search(r"ch_(\d+)", p.name)
            if m:
                chapters.append((int(m.group(1)), p, p.read_text(encoding="utf-8")))
        exposition = [x["chapter"] for x in vc.check_exposition_dialogue(chapters)]
    except Exception:
        exposition = []

    picked: list = []
    per_item: list = []

    def add(chs):
        for c in chs:
            if c not in picked:
                picked.append(c)

    for it in items:
        c = int(it.get("chapter") or 0)
        blob = f"{it.get('title', '')}{it.get('why', '')}{it.get('suggestion', '')}"
        if c:
            per_item.append([c])
        elif any(k in blob for k in ("説明", "設定", "口上", "台詞")):
            per_item.append(exposition or middle)
        elif any(k in blob for k in ("結末", "終盤", "エピローグ", "再建", "ラスト", "余韻")):
            per_item.append(late)
        elif any(k in blob for k in ("序盤", "前半", "導入", "冒頭")):
            per_item.append(early)
        elif any(k in blob for k in ("反復", "繰り返", "中盤", "テンポ", "単調", "同じ展開", "予想")):
            per_item.append(middle)
    # 1件の指摘で枠を使い切らないよう、指摘ごとに順番に1章ずつ配る
    # （実測: 中盤の指摘だけで枠が埋まり、説明台詞の章が選ばれなかった）
    for depth in range(4):
        for cands in per_item:
            if depth < len(cands):
                add([cands[depth]])
                if len(picked) >= max_n:
                    return picked[:max_n]
    if not picked:
        try:
            full = json.loads(sorted(EVAL_LOGS_DIR.glob("*_full.json"))[-1].read_text())
            weakest = full.get("weakest_chapter")
            if weakest:
                add([int(weakest)])
        except Exception:
            pass
    return picked[:max_n]


def revision_gate_check(changed: list) -> int:
    """本文を触った直後に章ゲートを回し、FAIL した章を元に戻す（2026-10-03 Hisa決定）。

    改訂は「スコア」だけで採否を決めていたので、文体が崩れても気づけなかった。
    本文を書き換えたら必ずゲートを通し、落ちた章は git で戻す。
    """
    if not REVISION_GATES or not changed:
        return 0
    reverted = 0
    for path in changed:
        res = run_tool(revision_gate_cmd_for(path), timeout=300)
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


def pairwise_verdict(ch_num: int, gate_path: str, cycle: int) -> tuple:
    """点が下がった改訂を、一対比較（Elo）で救うかどうかを決める。

    絶対点は実行ごとに最大1点動く（実測 2026-10-03）。「点が下がったから捨てる」と
    良い改訂を捨ててしまうので、旧版（git HEAD）と新版を**両方の順序で**比べさせ、
    両方で新版が勝ったときだけ採用する。順序で答えが変わる場合は現状維持。
    戻り値: ("adopt" | "keep", 理由)
    """
    tmp_old = EDIT_LOGS_DIR / f"ch{ch_num:02d}_cycle{cycle}_head.md"
    verdict_file = EDIT_LOGS_DIR / f"ch{ch_num:02d}_cycle{cycle}_verdict.txt"
    run_tool(f"git show HEAD:{gate_path} > {tmp_old}")
    if not tmp_old.exists() or not tmp_old.read_text(encoding="utf-8").strip():
        return "keep", "旧版を取り出せなかった（点が下がったので不採用）"
    uv_run(f"compare_versions.py --a {tmp_old} --b {gate_path} --chapter {ch_num} "
           f"--label-a ch{ch_num:02d}@HEAD --label-b ch{ch_num:02d}@revised "
           f"--ratings {EDIT_LOGS_DIR}/elo.tsv --verdict-file {verdict_file}",
           timeout=ELO_TIMEOUT)
    try:
        verdict = verdict_file.read_text(encoding="utf-8").strip()
    except OSError:
        verdict = ""
    if verdict == "adopt":
        return "adopt", "一対比較で新版が勝った（点は下がったが採用）"
    if verdict == "keep":
        return "keep", "一対比較でも旧版が勝った"
    return "keep", "一対比較は引き分け（順序で変わるため現状維持）"


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

        # -- Step 0: 整合ゲート（機械・無料）: 消えた人物などを記録 --
        if CONSISTENCY:
            step("Running consistency gate (mechanical)...")
            cons_report = EDIT_LOGS_DIR / f"consistency_report_cycle{cycle}.md"
            uv_run(f"verify_consistency.py --report {cons_report}", timeout=300)
            log_result("consistency", f"cycle{cycle}", "", 0, "note",
                       f"整合ゲート（機械）の結果: {cons_report.name}")

        # -- Step 1: Adversarial editing pass --
        step("Running adversarial editing on all chapters...")
        uv_run("adversarial_edit.py all", timeout=900)
        revision_gate_check(changed_chapters())      # 本文を触ったら章ゲート

        # -- Step 2: Apply mechanical cuts (only if apply_cuts.py exists) --
        apply_cuts = BASE_DIR / "apply_cuts.py"
        if apply_cuts.exists():
            step("Applying mechanical cuts (OVER-EXPLAIN, REDUNDANT)...")
            cuts_res = uv_run("apply_cuts.py all "
                                "--types OVER-EXPLAIN REDUNDANT --min-fat 15", timeout=300)
            if cuts_res is not None and cuts_res.returncode != 0:
                # apply_cuts は「適用に失敗した箇所がある」と exit 2 を返す。
                # 黙って先へ進むと、削れていない本文を「削った」前提で扱ってしまう。
                step(f"WARNING: 機械的な削除が一部失敗しました（exit {cuts_res.returncode}）。"
                     "本文はこのあとの章ゲートで守られますが、削れなかった箇所は残ります")
                log_result("mechanical_cuts", f"cycle{cycle}", "", 0, "warn",
                           f"apply_cuts が exit {cuts_res.returncode}")
            revision_gate_check(changed_chapters())  # 削った後も同じゲートを通す
        else:
            step("apply_cuts.py not found, skipping mechanical cuts")

        # -- Step 3: Reader panel --
        # 読者パネルは arc_summary.md（全編あらすじ）を読む。ところがそれを作るのは
        # 書き出しの工程なので、**改訂フェーズを単独で回すとあらすじが無くパネルが落ちる**
        # （2026-10-08 実測。落ちても「指摘なし」として静かに進んでいた）。
        # 無いとき・章より古いときは、ここで作り直す。
        arc_summary = BASE_DIR / "arc_summary.md"
        newest_ch = max((p.stat().st_mtime for p in CHAPTERS_DIR.glob("ch_*.md")), default=0)
        if not arc_summary.exists() or arc_summary.stat().st_mtime < newest_ch:
            if not arc_summary.exists():
                step("全編あらすじ（読者パネルの入力）が無いので作ります...")
            else:
                step("章が変わったので全編あらすじを作り直します...")
            uv_run("build_arc_summary.py", timeout=1800)

        step("Running reader panel evaluation...")
        panel_res = uv_run("reader_panel.py", timeout=600)
        if panel_res.returncode != 0:
            step("WARNING: 読者パネルが失敗しました（この周はパネルの指摘なしで続きます。"
                 "原因は上のログを見てください）")

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

        # -- Step 5b: 強みの移植（平均点の底上げではなく、天井を上げる）--
        # パネルが「最も良い場面」と答えた章から効いている型を言語化し、弱い章へ移植する
        # 指示書を作る。指示書は下の改訂ループが拾って gen_revision に渡す
        # （章ゲート→修復→判定→一対比較の安全網はそのまま効く）。
        if STRENGTH:
            step("Transplanting what works (best scenes → weak chapters)...")
            uv_run(f"strength_transplant.py --patterns --briefs --lens both "
                   f"--max-briefs {STRENGTH_MAX_BRIEFS} --cycle {cycle} "
                   f"--out-dir {BRIEFS_DIR}", timeout=STRENGTH_TIMEOUT)
            for sf in sorted(BRIEFS_DIR.glob(f"ch*_cycle{cycle}_strength.md")):
                sm = re.search(r"ch0*(\d+)", sf.name)
                if not sm:
                    continue
                s_num = int(sm.group(1))
                if not any(it["chapter"] == s_num for it in consensus_items):
                    consensus_items.append({"chapter": s_num, "question": "strength",
                                            "count": 1})
                    step(f"  移植の対象に追加: Ch {s_num}（{sf.name}）")

        # -- Step 5: Targeted revisions for consensus items --
        if len(consensus_items) > MAX_REVISIONS_PER_CYCLE:
            step(f"指摘が {len(consensus_items)} 章に及ぶので、この周は指摘の多い順に"
                 f"{MAX_REVISIONS_PER_CYCLE} 章まで直します")
            consensus_items = consensus_items[:MAX_REVISIONS_PER_CYCLE]

        for idx, item in enumerate(consensus_items):
            ch_num = item["chapter"]
            question = item["question"]
            banner(f"  Revising Ch {ch_num} ({question}) [{idx+1}/{len(consensus_items)}]", ".")

            # Snapshot the current chapter score for comparison
            pre_eval = uv_run(f"evaluate.py --chapter={ch_num}", timeout=300)
            pre_score = parse_score(pre_eval.stdout, "overall_score")
            if pre_score < 0:
                stop_for_eval_failure(f"改訂前 第{ch_num}章の評価（解釈できず）",
                                      (pre_eval.stderr or "") + (pre_eval.stdout or ""))

            # Generate revision brief
            brief_file = BRIEFS_DIR / f"ch{ch_num:02d}_cycle{cycle}_{question}.md"
            gen_brief = BASE_DIR / "gen_brief.py"
            strength_brief = BRIEFS_DIR / f"ch{ch_num:02d}_cycle{cycle}_strength.md"
            if strength_brief.exists():
                # 強みの移植の指示書（Step 5b が作る）を優先して使う
                brief_file = strength_brief
                step(f"Using the strength-transplant brief for Ch {ch_num}")
            elif gen_brief.exists():
                step(f"Generating brief for Ch {ch_num}...")
                uv_run(f"gen_brief.py --panel {ch_num}", timeout=300)
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

            # 整合の指摘（設定矛盾・声の逸脱・伏線の放置）も同じブリーフに足す。
            # 審査は judge モデルで1章1回（約1分半）。指摘が無ければ何も足さない。
            if CONSISTENCY and (CONSISTENCY_EVERY_CYCLE
                                or cycle == start_cycle or cycle == max_cycles):
                step(f"Checking consistency of Ch {ch_num} (judge model)...")
                cons_brief = BRIEFS_DIR / f"ch{ch_num:02d}_cycle{cycle}_consistency.md"
                uv_run(f"verify_consistency.py --llm --chapters {ch_num} "
                       f"--brief {cons_brief}", timeout=CONSISTENCY_TIMEOUT)
                try:
                    extra = cons_brief.read_text(encoding="utf-8").strip()
                except OSError:
                    extra = ""
                # 説明台詞は「整合の指摘」が出なくても機械だけで測れるので、
                # --llm の成否に関わらずこの章の分だけ必ず足す。
                # 実測（2026-10-08）: 説明台詞を検出した3章（第5・6・9章）を書き直しても
                # 比率が 0.55→0.55 と動かなかった。原因はブリーフに実際の台詞が無く、
                # 「同じ調子で書き直される」だけだったため。→ 台詞を出し、現在値も書く。
                extra = append_exposition_note(extra, ch_num)
                # 全編の字数が目標に届いていないとき、膨らませの指示を足す。
                # 実測 2026-10-13: セリフ比の検査が効きすぎて 35% 薄まった。
                # 不足分は総量なので1章で埋めるのではなく、薄い章から順に足す。
                short = state.get("word_shortfall")
                if short:
                    thin = thinnest_chapters(ch_num, limit=3)
                    hint = ["## 全編の字数が目標に届いていません（地の文を足して膨らませる）",
                            f"- 不足: 約{int(short):,}字（前の周の測定）",
                            "- 直し方: 会話を増やすのではなく**地の文・情景・内面の描写を足す**。",
                            "  （セリフ比は既に足りているので、会話を増やすと比率が崩れます）"]
                    if thin:
                        hint.append("- この章は地の文（情景・内面の描写）が厚い章の一つです。")
                        hint.append("  ここに情景や内面を足すと、説明臭くならずに字数を戻せます:")
                        for t in thin:
                            hint.append(f"  - 第{t}章")
                    extra = (extra + "\n\n" + "\n".join(hint)) if extra else "\n".join(hint)
                if extra:
                    with open(brief_file, "a", encoding="utf-8") as fh:
                        fh.write("\n\n" + extra + "\n")
                    step(f"  整合の指摘をブリーフに足しました（{len(extra)}字）")

            uv_run(f"gen_revision.py {ch_num} {brief_file}", timeout=600)

            # 本文を触ったので章ゲートを回す（FAIL なら修復 → それでも駄目なら元に戻す）
            ch_file = CHAPTERS_DIR / f"ch_{ch_num:02d}.md"
            gate_path = f"chapters/ch_{ch_num:02d}.md"
            if REVISION_GATES and run_tool(revision_gate_cmd_for(gate_path), timeout=300).returncode != 0:
                step("  改訂版が章ゲートに落ちました — 修復を試します（落ちた箇所だけ直す）")
                healed = False
                if REPAIR_ON_FAIL and ch_file.exists():
                    uv_run(f"repair_chapter.py {gate_path} "
                           f"--out chapters/ch_{ch_num:02d}_repaired.md "
                           f'--band "{CHAPTER_GATE_BAND}"', timeout=REPAIR_TIMEOUT)
                    repaired = ch_file.with_name(f"ch_{ch_num:02d}_repaired.md")
                    if repaired.exists():
                        if run_tool(revision_gate_cmd_for(f"chapters/ch_{ch_num:02d}_repaired.md"),
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
            if post_score < 0:
                stop_for_eval_failure(f"改訂後 第{ch_num}章の評価（解釈できず）",
                                      (post_eval.stderr or "") + (post_eval.stdout or ""))

            ch_file = CHAPTERS_DIR / f"ch_{ch_num:02d}.md"
            word_count = jp_char_count(ch_file.read_text()) if ch_file.exists() else 0

            step(f"Ch {ch_num}: {pre_score} -> {post_score}")

            # 採否の決定。絶対点（overall_score）は実行ごとに最大1点動く（実測 2026-10-03）。
            # 点が下がっただけでは捨てず、旧版と新版を一対比較にかける（両順序で勝てば採用）。
            verdict, why = "keep", f"regressed {pre_score}->{post_score}"
            if post_score >= pre_score:
                verdict, why = "adopt", f"improved {pre_score}->{post_score}"
            elif ELO_DECIDE:
                step(f"  点は下がりました（{pre_score} → {post_score}）。"
                     "旧版と新版を一対比較で比べます（両順序で勝ったときだけ採用）")
                verdict, why = pairwise_verdict(ch_num, gate_path, cycle)
                step(f"  一対比較の判定: {verdict}（{why}）")

            if verdict == "adopt":
                commit_hash = git_add_commit(
                    f"revision cycle {cycle}: ch{ch_num:02d} {question} {why}")
                log_result(commit_hash, f"rev-ch{ch_num:02d}", post_score,
                           word_count, "keep",
                           f"Cycle {cycle}: {question} {why}")
            else:
                step(f"Revision made it worse and lost the pairwise "
                     f"({post_score} < {pre_score}), reverting")
                git_reset_hard("HEAD")
                log_result("reverted", f"rev-ch{ch_num:02d}", post_score,
                           word_count, "discard",
                           f"Cycle {cycle}: {question} {why}")

        # -- Step 6: Full novel evaluation --
        step("Running full novel evaluation...")
        full_eval = uv_run("evaluate.py --full", timeout=600)
        novel_score = parse_score(full_eval.stdout, "novel_score")

        if novel_score < 0:
            # Fallback: try overall_score
            novel_score = parse_score(full_eval.stdout, "overall_score")
        if novel_score < 0:
            stop_for_eval_failure("全体評価（解釈できず）",
                                  (full_eval.stderr or "") + (full_eval.stdout or ""))

        total_words = count_words_in_chapters()
        step(f"Novel score: {novel_score}  (prev: {prev_score}, 字数: {total_words})")

        # 全編の目標字数に対する不足（作り直しで薄まりすぎていないか）
        tw = target_words_for(state)
        if tw:
            ratio = total_words / tw
            if ratio < TARGET_WORDS_MIN_RATIO:
                short = tw - total_words
                state["word_shortfall"] = short
                step(f"字数が目標に対して {short:,}字 不足（{total_words:,} / 目標 {tw:,} "
                     f"= {ratio*100:.0f}%）— 次の周のブリーフで膨らませます")
            else:
                state.pop("word_shortfall", None)
                step(f"字数は目標の {ratio*100:.0f}%（{total_words:,} / 目標 {tw:,}）")

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
        revised_in_review = set()   # 同じ章を何周も直さない（講評が同じ章を指し続けても痩せるだけ）
        stop_review_loop = False   # 直せる章が尽きたら止める（全編を読み直す費用が見合わない）
        for rnd in range(1, max_review_rounds + 1):
            if stop_review_loop:
                break
            banner(f"Opus Review Round {rnd}/{max_review_rounds}", "-")
            
            # Step 1: Generate the review
            step("Sending manuscript to Opus for review...")
            review_result = uv_run(
                f"review.py --output reviews.md", timeout=900)
            
            # Step 2: Parse the review
            step("Parsing review...")
            parse_result = uv_run("review.py --parse", timeout=60)
            print(parse_result.stdout if parse_result else "")
            
            # Step 3: Check stopping condition
            total_items = None
            review_data: dict = {}
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

            # 講評から指摘を1件も取れなかったら、ここで止まる。
            # 取れないまま先へ進むと「無い指摘」を作り話にして本文を削ってしまう
            # （2026-10-08 実測: 第14章が 4,680字 → 2,797字 まで痩せた）。
            if total_items == 0:
                step("講評から指摘を1件も取れなかったので、この周は何も直しません")
                break

            # -- Step 3.5: 骨格（幕構成・章立て）を作り直すべきかを判定する --
            # 講評の「全編の構成」の指摘（似た展開の反復・山場が1点に定まらない）は、
            # 章の書き直しでも章をまたぐ編集でも消えない。実測（2026-10-08）:
            # 3周まわっても同じ指摘が形を変えて出続け、★も 3.0〜3.5 のまま動かなかった。
            # → 骨格の指摘が続いた周で rebuild_arc.py を回す（章数を変える工程）。
            if ARC_REBUILD != "off":
                rebuild_py = BASE_DIR / "rebuild_arc.py"
                struct = []
                try:
                    sys.path.insert(0, str(BASE_DIR))
                    import rebuild_arc as _ra  # noqa: E402
                    struct = _ra.structural_items(review_data)
                except Exception:
                    struct = []
                if not rebuild_py.exists():
                    step("rebuild_arc.py が無いので骨格の作り直しは回しません")
                elif struct and rnd >= ARC_REBUILD_PERSIST_ROUNDS:
                    step(f"骨格（構成）の指摘が {rnd}周続いています（{len(struct)}件）。"
                         "章や範囲の書き直しでは消えない種類なので、骨格を作り直します")
                    plan_file = BRIEFS_DIR / "arc_rebuild_plan.json"
                    if ARC_REBUILD == "apply" and plan_file.exists():
                        # 設計が人手で確認ずみ（ファイルが存在する）なので採用して作り直す
                        res = uv_run(f"rebuild_arc.py "
                                       f"--plan-file {plan_file} --apply",
                                       timeout=ARC_REBUILD_TIMEOUT * 24)
                        step(f"骨格を作り直しました（exit {res.returncode}）")
                        print((res.stdout or "")[-2000:])
                        # rebuild_arc.py は章数を変えて state.json を書き直すので、
                        # サブプロセスの結果を読み直す（メモリの古い章数で上書きすると齟齬る）
                        reloaded = load_state()
                        if reloaded:
                            state = reloaded
                            print(f"  章数を読み直しました: "
                                  f"{count_chapter_files()}章 / 章立て {get_total_chapters(state)}章")
                        revised_in_review.clear()   # 章番号が変わったので記憶をリセット
                        stop_review_loop = True    # 作り直した後は改訂からやり直す
                    else:
                        # 設計だけ出して止まる（章数を変えるので人間の確認を取る）
                        res = uv_run("rebuild_arc.py --from-review",
                                       timeout=ARC_REBUILD_TIMEOUT)
                        print((res.stdout or "")[-2500:])
                        step("骨格の設計を出しました。この工程は章数を変えるので、"
                             f"設計（{plan_file}）を確認してから "
                             "AUTONOVEL_ARC_REBUILD=apply で実行してください")
                        stop_review_loop = True

            if stop_review_loop:
                break

            # Step 4: 講評の指摘を「効く章」に配って直す
            # 章0＝全編の指摘（中盤の反復・説明台詞）も、効く章の組に配って複数章を直す。
            # 実測（2026-10-08）: 1章しか直さない作りだったので、全編の指摘が残り続けていた。
            step("Generating revision briefs from review...")
            gen_brief_py = BASE_DIR / "gen_brief.py"
            round_revisions = int(os.environ.get("AUTONOVEL_REVIEW_ROUND_REVISIONS", "3"))
            targets = [c for c in choose_review_targets(review_data, round_revisions)
                       if c not in revised_in_review]
            step(f"講評の指摘から直す章: {targets if targets else 'なし'}")
            if not targets:
                step("直せる章がもう無いので、ここで講評ループを終えます"
                     "（全編を読み直す費用が見合わないため）")
                stop_review_loop = True
            elif gen_brief_py.exists():
                for ch_num in targets:
                    if ch_num in revised_in_review:
                        continue
                    if len(revised_in_review) >= MAX_REVISIONS_PER_CYCLE:
                        step(f"この周で直す章の上限（{MAX_REVISIONS_PER_CYCLE}章）に達したので残りは次周へ")
                        break
                    revised_in_review.add(ch_num)
                    uv_run(f"gen_brief.py --from-review {ch_num}", timeout=300)
                    brief = BRIEFS_DIR / f"ch{ch_num:02d}_review.md"
                    if not brief.exists():
                        step(f"Ch {ch_num}: 指示書が作れなかったので飛ばします")
                        continue
                    step(f"Revising Ch {ch_num} from review brief...")
                    uv_run(f"gen_revision.py {ch_num} {brief}", timeout=600)
                    revision_gate_check(changed_chapters())  # 直したら章ゲート
                    git_add_commit(
                        f"review round {rnd}: revise ch{ch_num:02d} from review")
            
            # Step 5: Mechanical fixes from review
            # Run slop pass on any mentioned patterns
            step("Running mechanical cleanup pass...")
            apply_cuts_py = BASE_DIR / "apply_cuts.py"
            if apply_cuts_py.exists():
                cuts_res = uv_run(
                    "apply_cuts.py all --types OVER-EXPLAIN REDUNDANT --min-fat 15",
                    timeout=300)
                if cuts_res is not None and cuts_res.returncode != 0:
                    step(f"WARNING: 機械的な削除が一部失敗しました（exit {cuts_res.returncode}）。"
                         "章ゲートで守られる範囲だけが反映されます")
                revision_gate_check(changed_chapters())  # 削った後も章ゲート
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
        word_count = sum(jp_char_count(p) for p in parts)
        step(f"Manuscript: {len(parts)} chapters, {word_count}字")
    else:
        step("WARNING: no chapter files found for manuscript")

    # 4. Build LaTeX
    build_tex = BASE_DIR / "typeset" / "build_tex.py"
    if build_tex.exists():
        step("Building LaTeX content...")
        uv_run("typeset/build_tex.py", timeout=120)

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

    banner(f"EXPORT COMPLETE — {len(chapter_files)} chapters, {total_words}字")
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
