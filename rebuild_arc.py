#!/usr/bin/env python3
"""rebuild_arc.py — 幕（構成）レベルを作り直す工程。

`edit_arc.py` は「章数・章立て・章末フック・人物の生死は変えない」約束の工程で、
章の中と章の間の指摘に効かせる。**こちらはその約束を外す。**

講評が「全編の構成」で返し続ける指摘（似た展開の反復・山場が1点に定まらない・
時系列のループ）は、章の書き直しでも範囲の編集でも消えない。骨格そのもの
（幕構成と章立て）から作り直す工程。**骨格が正**＝以後の本文は新しい章立てに合わせる。

流れ:
  1) いまの幕構成・章立て・各章のあらすじを集める
  2) 新しい幕構成と章立てを設計する（1回のLLM呼び出し）。
     統合・分割・新設・削除・山場の移動まで許す。人物の生死は新しい骨格で確定させる
  3) 設計を機械で検算する（取りこぼしの章ゼロ・章番号の連番・山場は1つだけ・
     静かな章は3まで・取り返しのつかない損失は40〜60%地点）
  4) `--apply` のときだけ本文に触る:
     outline.md を作り直し → 章を新しい章立てに合わせて並べ替え（統合した章は
     元の本文を材料に1本にまとめる）→ 1章ずつ書き直し → 章ゲート → 修復 →
     それでも落ちれば材料のまま戻す → 整合ゲート（生死の食い違い）を通して commit

使い方:
  uv run python rebuild_arc.py --goal "似た展開の反復をやめ、ゼノス戦を1つの頂点にする"
  uv run python rebuild_arc.py --from-review                 # 講評の「構成」の指摘をgoalにする
  uv run python rebuild_arc.py --from-review --plan-file briefs/arc_rebuild_plan.json
  uv run python rebuild_arc.py --from-review --apply         # 骨格を作り直して本文も書き換える
  uv run python rebuild_arc.py --plan-file <編集したJSON> --apply   # 設計を人手で直してから適用

環境変数:
  AUTONOVEL_ARC_REBUILD_GATE_WARN   章ゲートで警告のみにする項目（既定は空＝全項目合否対象。
                                    改訂と同じ物さし。生原稿を落とさない設定は使わない）
  AUTONOVEL_ARC_REBUILD_MIN_RATIO   書き直し後の字数の下限（元の材料に対する割合・既定0.9）
  AUTONOVEL_ARC_REBUILD_QUIET_MAX   静かな章の上限（既定3）
  AUTONOVEL_ARC_REBUILD_TIMEOUT     1章の書き直しの制限時間（既定1800秒）
  AUTONOVEL_USAGE_LOG               使用量の記録先
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
CHAPTERS_DIR = BASE_DIR / "chapters"
BRIEFS_DIR = BASE_DIR / "briefs"
EDIT_LOGS_DIR = BASE_DIR / "edit_logs"
OUTLINE = BASE_DIR / "outline.md"
ARC_SUMMARY = BASE_DIR / "arc_summary.md"
STATE = BASE_DIR / "state.json"

MIN_RATIO = float(os.environ.get("AUTONOVEL_ARC_REBUILD_MIN_RATIO", "0.9"))
QUIET_MAX = int(os.environ.get("AUTONOVEL_ARC_REBUILD_QUIET_MAX", "3"))
REWRITE_TIMEOUT = int(os.environ.get("AUTONOVEL_ARC_REBUILD_TIMEOUT", "1800"))
GATE_WARN = os.environ.get("AUTONOVEL_ARC_REBUILD_GATE_WARN", "")

# 講評の指摘のうち「骨格」のものを見分ける語。章や範囲の書き直しでは消えない種類。
STRUCTURAL_KEYS = (
    "構成", "反復", "繰り返", "同じ展開", "同様の展開", "クライマックス", "頂点",
    "時系列", "タイムライン", "ループ", "一本化", "統合", "再編", "骨格",
    "エピソード", "山場", "テンポ", "単調", "起伏", "構成要素",
)

PLAN_SYSTEM = (
    "あなたは日本語長編小説の構成 designer（ストーリー編集者）です。"
    "全編を読んだうえで、骨格（幕構成と章立て）そのものを作り直す設計をします。"
    "章を足す・統合する・分割する・順序を変える・山場を動かす、すべて許されます。"
    "ただし、読者が味わう人物の成長と主題は変えません。思い付きではなく、"
    "本文と章立てにある事実だけを根拠にします。回答は日本語で、指定のJSONだけを返します。"
)

PLAN_PROMPT = """この小説の**骨格（幕構成と章立て）を作り直す**設計をしてください。

# 直したいこと
{goal}

# いまの骨格
{act_overview}

# いまの章立てと各章の役割
{chapter_digest}

# 各章のあらすじ（要約）
{summaries}

# 許されること（ edit_arc との違い ）
- **章の数を変えてよい。** 統合（2章→1章）・分割（1章→2章）・新設・削除ができる
- **章の順序を変えてよい。** 山場（クライマックス）を1点に集め、そこへ向かう流れを作る
- **幕の区切りを変えてよい**
- 人物の生死は**この設計で確定させる**（骨格が正。以後の本文はこの設計に合わせる）
- ただし主題と人物の成長の筋は変えない。読後感（ピンチ→克服の落差）は保つ

# 守ること
- 各章にピンチ（危機・障害・損失の予感）と、その章なりの小さな克服を入れる
- 各章の末尾に「次をめくる理由」を1つ。静かな着地で終える章は全体で{quiet_max}章まで
- 全体の40〜60%地点に「取り返しのつかない損失」を1つ。以後の章で元に戻さない
- 対立面（敵・障害）を第II幕の終わりまで持続させる。敵が不在の章を3章以上続けない
- クライマックスは**1章だけ**に定め、そこを物語の頂点にする
- 削除する章がある場合は、その章にしかない要素（伏線・人物・出来事）を
  必ず別の章の「やること」に移す（取りこぼしをゼロにする）

# 出すもの（この形のJSONだけ）
{{
  "全体の方針": "骨格をどう作り直すか（3〜4文）",
  "変わったこと": ["統合: 旧第7・8章 → 新第7章", "山場: 旧第22章 → 新第21章"],
  "幕構成": [
    {{"幕": "第I幕", "章": "第1〜6章", "%位置": "0〜23%", "出来事": "この幕で起きること"}}
  ],
  "章ごと": [
    {{"新しい章": 1,
      "元の章": [1],
      "タイトル": "章の題",
      "役割": "この章が担うこと（20字程度）",
      "POV": "視点の人物",
      "場所": "舞台",
      "やること": "この章で具体的に起こること（60字程度。統合した章は両方の要素を書く）",
      "ビート": ["起こらなければならないこと1", "2", "3"],
      "章末フック": "次をめくる理由",
      "静かな章": false,
      "山場": false,
      "取り返しのつかない損失": false,
      "この章で起きる死": [],
      "残すもの": "絶対に残す場面・台詞",
      "削るもの": "削る反復・説明"}}
  ],
  "削除する章": [
    {{"章": 9, "理由": "なぜ不要か", "移すもの": "この章にしかない要素をどこへ移すか"}}
  ]
}}

いまの章は全{n_chapters}章。新しい章立ても{lo}〜{hi}章の範囲に収めること。
"""


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def jp_chars(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def chapter_nums() -> list:
    out = []
    for p in CHAPTERS_DIR.glob("ch_*.md"):
        m = re.search(r"ch_(\d+)", p.name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def chapter_path(ch: int) -> Path:
    return CHAPTERS_DIR / f"ch_{ch:02d}.md"


def chapter_text(ch: int) -> str:
    p = chapter_path(ch)
    return read(p) if p.exists() else ""


def act_overview() -> str:
    """いまの幕構成（表でも箇条書きでも拾う）。"""
    if not OUTLINE.exists():
        return "（章立て無し）"
    text = read(OUTLINE)
    out = []
    for m in re.finditer(r"^##\s*幕構成\s*$(.*?)(?=^##\s|\Z)", text, re.S | re.M):
        out.append(m.group(1).strip())
    return "\n---\n".join(out) if out else "（幕構成の記載無し）"


def outline_chapter_entries() -> dict:
    """章立てから `## 第N章：...` の見出しと本文を取り出す。"""
    if not OUTLINE.exists():
        return {}
    text = read(OUTLINE)
    entries = {}
    for m in re.finditer(r"^##\s*第\s*(\d+)\s*章\s*[:：]?\s*(.*)$", text, re.M):
        num = int(m.group(1))
        start = m.end()
        nxt = re.search(r"^##\s*(?:第\s*\d+\s*章|幕構成|伏線台帳|前半|後半)", text[start:], re.M)
        body = text[start:start + nxt.start()] if nxt else text[start:]
        entries[num] = {"title": m.group(2).strip(), "body": body.strip()}
    return entries


def chapter_digest(entries: dict) -> str:
    """各章の役割を1行に圧縮（プロンプトを膨らませない）。"""
    out = []
    for num in sorted(entries):
        body = entries[num]["body"]
        beat = re.search(r"-\s*\*\*ビート：\*\*(.*?)(?=-\s*\*\*|\Z)", body, re.S)
        stc = re.search(r"-\s*\*\*Save the Catビート：\*\*\s*(.+)", body)
        hook = re.search(r"-\s*\*\*章末フック：\*\*\s*(.+)", body)
        pos = re.search(r"-\s*\*\*%位置：\*\*\s*(.+)", body)
        bits = []
        if stc:
            bits.append(stc.group(1).strip())
        if pos:
            bits.append(f"位置{pos.group(1).strip()}")
        if beat:
            first = [b.strip() for b in re.split(r"\n", beat.group(1)) if b.strip()][:2]
            bits.append("／".join(re.sub(r"^[\d.\s-]+", "", b)[:40] for b in first))
        if hook:
            bits.append(f"フック: {hook.group(1).strip()[:40]}")
        out.append(f"第{num}章 {entries[num]['title']}: " + " ｜ ".join(bits))
    return "\n".join(out) if out else "（章立て無し）"


def summaries(nums: list) -> str:
    """あらすじ（arc_summary.md）が無ければ本文の書き出しで代用。"""
    out = []
    if ARC_SUMMARY.exists():
        text = read(ARC_SUMMARY)
        for ch in nums:
            m = re.search(rf"###\s*第\s*{ch}\s*章[^\n]*\n(.*?)(?=\n###\s*第|\Z)", text, re.S)
            if m:
                out.append(f"## 第{ch}章\n{m.group(1).strip()[:400]}")
                continue
            out.append(f"## 第{ch}章\n{chapter_text(ch)[:200]}")
    else:
        for ch in nums:
            out.append(f"## 第{ch}章\n{chapter_text(ch)[:200]}")
    return "\n\n".join(out)


def structural_items(review: dict) -> list:
    """講評の指摘のうち、骨格（構成）に効くものだけを拾う。"""
    items = []
    for it in (review or {}).get("professor_items") or []:
        blob = f"{it.get('title', '')}{it.get('why', '')}{it.get('suggestion', '')}"
        if any(k in blob for k in STRUCTURAL_KEYS):
            items.append(it)
    return items


def latest_review() -> tuple:
    candidates = sorted(EDIT_LOGS_DIR.glob("*_review.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    for f in candidates:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("professor_items"):
            return f, d
    return (None, None)


def goal_from_review() -> str:
    path, review = latest_review()
    if review is None:
        sys.exit("ERROR: 講評の記録（edit_logs/*_review.json）が見つかりません")
    items = structural_items(review)
    if not items:
        sys.exit("ERROR: 講評に骨格（構成）の指摘がありません。"
                 "章や範囲の書き直しで消える指摘なので、この工程は回しません")
    print(f"  講評: {path.name}（指摘 {len(items)}件が骨格に効く種類）", flush=True)
    lines = []
    for it in items:
        lines.append(f"- [{it.get('severity', '')}] {it.get('title', '')}")
        if it.get("why"):
            lines.append(f"  なぜ: {it['why']}")
        if it.get("suggestion"):
            lines.append(f"  直し方: {it['suggestion']}")
    return "\n".join(lines)


def build_plan(goal: str) -> dict:
    sys.path.insert(0, str(BASE_DIR))
    import llm  # noqa: E402

    nums = chapter_nums()
    entries = outline_chapter_entries()
    n = len(nums)
    prompt = PLAN_PROMPT.format(
        goal=goal,
        act_overview=act_overview(),
        chapter_digest=chapter_digest(entries) if entries else
        "\n".join(f"第{c}章: {chapter_text(c)[:80]}" for c in nums),
        summaries=summaries(nums),
        n_chapters=n,
        lo=max(8, int(n * 0.75)),
        hi=int(n * 1.3),
        quiet_max=QUIET_MAX,
    )
    raw = llm.call("revision", prompt, system=PLAN_SYSTEM, max_tokens=16000)
    m = re.search(r"\{.*\}", raw or "", flags=re.S)
    if not m:
        sys.exit("ERROR: 設計のJSONが読めませんでした")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: 設計のJSONが壊れています: {e}")


# ---------------------------------------------------------------------------
# 設計の機械検算（LLMに任せると取りこぼしと章番号の重なりが出る）
# ---------------------------------------------------------------------------

def validate_plan(plan: dict, old_nums: list) -> list:
    errors = []
    chapters = plan.get("章ごと") or []
    if not chapters:
        return ["設計に「章ごと」がありません"]

    new_nums = []
    for e in chapters:
        try:
            new_nums.append(int(e.get("新しい章", 0)))
        except (TypeError, ValueError):
            errors.append(f"章番号が読めない項目: {e}")
    if new_nums != list(range(1, len(new_nums) + 1)):
        errors.append(f"新しい章番号が 1〜{len(new_nums)} の連番になっていません: {new_nums}")
    if len(set(new_nums)) != len(new_nums):
        errors.append("新しい章番号が重複しています")

    # 元の章の取りこぼし（内容がどこにも行かない＝物語に穴が空く）
    dropped = {int(d.get("章", 0)) for d in (plan.get("削除する章") or [])}
    used = []
    for e in chapters:
        for src in (e.get("元の章") or []):
            try:
                used.append(int(src))
            except (TypeError, ValueError):
                errors.append(f"元の章が読めない項目: 新第{e.get('新しい章')}章")
    missing = [c for c in old_nums if c not in used and c not in dropped]
    if missing:
        errors.append(f"元の章の取りこぼし（どこにも行かない章）: {missing}"
                      " — 統合先の「元の章」に入れるか、削除する章に理由つきで入れてください")
    unknown = [c for c in used if c not in old_nums]
    if unknown:
        errors.append(f"存在しない元の章を参照しています: {unknown}")

    # 山場は1つだけ・静かな章は上限まで
    peaks = [int(e.get("新しい章", 0)) for e in chapters if e.get("山場")]
    if len(peaks) != 1:
        errors.append(f"山場（クライマックス）は1章だけに定めてください（現在 {len(peaks)}件: {peaks}）")
    elif chapters and peaks[0] < len(chapters) * 0.7:
        errors.append(f"山場が第{peaks[0]}章にあり早すぎます（全体の70%以降に置くこと）")
    quiet = [int(e.get("新しい章", 0)) for e in chapters if e.get("静かな章")]
    if len(quiet) > QUIET_MAX:
        errors.append(f"静かな章が {len(quiet)}章（上限 {QUIET_MAX}）: {quiet}")

    losses = [int(e.get("新しい章", 0)) for e in chapters if e.get("取り返しのつかない損失")]
    if len(losses) != 1:
        errors.append(f"「取り返しのつかない損失」は1つに定めてください（現在 {len(losses)}件）")
    elif not (len(chapters) * 0.4 <= losses[0] <= len(chapters) * 0.6):
        errors.append(f"「取り返しのつかない損失」が第{losses[0]}章にあり、"
                      f"40〜60%地点（第{int(len(chapters)*0.4)}〜{int(len(chapters)*0.6)}章）から外れています")

    # 幕構成の網羅
    acts = plan.get("幕構成") or []
    if len(acts) < 3:
        errors.append(f"幕構成が {len(acts)}つしかありません（第I・II・III幕の3つ以上）")
    return errors


# ---------------------------------------------------------------------------
# 設計の表示と保存
# ---------------------------------------------------------------------------

def show_plan(plan: dict) -> None:
    print(f"\n  全体の方針: {plan.get('全体の方針', '')}")
    print("\n  幕構成:")
    for a in (plan.get("幕構成") or []):
        print(f"    {a.get('幕', '')}（{a.get('%位置', '')}）: {a.get('章', '')} — {a.get('出来事', '')}")
    print("\n  変わったこと:")
    for c in (plan.get("変わったこと") or []):
        print(f"    - {c}")
    print("\n  章ごと:")
    for e in (plan.get("章ごと") or []):
        src = e.get("元の章") or []
        marks = []
        if e.get("山場"):
            marks.append("山場")
        if e.get("静かな章"):
            marks.append("静かな章")
        if e.get("取り返しのつかない損失"):
            marks.append("取り返しのつかない損失")
        if e.get("この章で起きる死"):
            marks.append("死: " + "・".join(e["この章で起きる死"]))
        tag = f" ［{'／'.join(marks)}］" if marks else ""
        merge = f"（旧{ '・'.join(str(s) for s in src) }を統合）" if len(src) > 1 else ""
        print(f"    新第{e.get('新しい章')}章「{e.get('タイトル', '')}」{merge}{tag}")
        print(f"      役割: {e.get('役割', '')}")
        print(f"      やること: {e.get('やること', '')}")
    for d in (plan.get("削除する章") or []):
        print(f"    削除: 旧第{d.get('章')}章 — {d.get('理由', '')} / 移す: {d.get('移すもの', '')}")


def save_plan(plan: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n  設計を保存しました: {path}")


# ---------------------------------------------------------------------------
# 適用（--apply のときだけ本文に触る）
# ---------------------------------------------------------------------------

def run(cmd: str, timeout: int = 600) -> subprocess.CompletedProcess:
    print(f"  RUN: {cmd}", flush=True)
    return subprocess.run(cmd, shell=True, cwd=BASE_DIR, capture_output=True,
                          text=True, timeout=timeout)


def write_new_outline(plan: dict, old_entries: dict) -> Path:
    """新しい骨格を outline.md の同じ書式で書く（ドラフターとパーサーが読む形式）。"""
    lines = ["# Outline（構成を作り直した版）", "",
             "## 幕構成", "", "| 幕 | 章 | %位置 | 主な出来事 |", "|---|---|---|---|"]
    for a in (plan.get("幕構成") or []):
        lines.append(f"| {a.get('幕', '')} | {a.get('章', '')} | {a.get('%位置', '')} "
                     f"| {a.get('出来事', '')} |")
    total = len(plan.get("章ごと") or [])
    for e in (plan.get("章ごと") or []):
        num = int(e.get("新しい章", 0))
        pos = f"{int((num - 1) / total * 100)}〜{int(num / total * 100)}%"
        lines += ["", f"## 第{num}章：{e.get('タイトル', '')}", "",
                  f"- **POV：** {e.get('POV', '')}",
                  f"- **場所：** {e.get('場所', '')}",
                  f"- **%位置：** {pos}",
                  f"- **役割：** {e.get('役割', '')}",
                  "- **ビート：**"]
        for i, b in enumerate(e.get("ビート") or [], 1):
            lines.append(f"  {i}. {b}")
        lines += [f"- **この章でやること：** {e.get('やること', '')}",
                  f"- **章末フック：** {e.get('章末フック', '')}"]
        if e.get("この章で起きる死"):
            lines.append(f"- **この章で起きる死：** {'・'.join(e['この章で起きる死'])}")
        if e.get("静かな章"):
            lines.append("- **静かな章：** はい")
        if e.get("山場"):
            lines.append("- **山場：** はい（物語の頂点）")
        if e.get("取り返しのつかない損失"):
            lines.append("- **取り返しのつかない損失：** はい")
    # 伏線台帳は骨格を変えても糸自体は残す（埋まらなくなるので持ち越す）
    if OUTLINE.exists():
        m = re.search(r"^##\s*伏線台帳\s*$(.*)\Z", read(OUTLINE), re.S | re.M)
        if m:
            lines += ["", "## 伏線台帳", "", m.group(1).strip(), ""]
    backup = OUTLINE.with_name("outline_pre_rebuild.md")
    if OUTLINE.exists() and not backup.exists():
        shutil.copy2(OUTLINE, backup)
        print(f"  元の章立てを退避しました: {backup.name}")
    OUTLINE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  章立てを作り直しました: {OUTLINE.name}（{total}章）")
    return OUTLINE


def build_brief(entry: dict, plan: dict, material_chars: int) -> str:
    """1章ぶんの作り直し指示書（統合した章は元の本文が材料）。"""
    num = int(entry.get("新しい章", 0))
    lines = [f"# 作り直しブリーフ：第{num}章（骨格を作り直した）", "",
             "## 骨格全体の方針", plan.get("全体の方針", ""), "",
             "## この章の新しい役割", f"- {entry.get('役割', '')}", "",
             "## この章でやること", entry.get("やること", ""), ""]
    if entry.get("ビート"):
        lines.append("## 必ず入れるビート")
        lines += [f"{i}. {b}" for i, b in enumerate(entry["ビート"], 1)]
        lines.append("")
    if entry.get("章末フック"):
        lines.append(f"## 章末フック（必ず最後に置く）\n{entry['章末フック']}\n")
    if entry.get("この章で起きる死"):
        lines.append(f"## この章で起きる死（骨格が正。本文でも死なせる）\n"
                     f"{'・'.join(entry['この章で起きる死'])}\n")
    if entry.get("残すもの"):
        lines.append(f"## 必ず残す\n- {entry['残すもの']}\n")
    if entry.get("削るもの"):
        lines.append(f"## 削る\n- {entry['削るもの']}\n")
    src = entry.get("元の章") or []
    if len(src) > 1:
        lines.append(f"## この章は旧第{'・'.join(str(s) for s in src)}章を1本にまとめたもの\n"
                     "材料（THE EXISTING DRAFT）に両方の本文が入っています。"
                     "要素を取りこぼさず、1つの章の流れにまとめてください。\n")
    lines += ["## 守ること",
              f"- 字数は材料の{MIN_RATIO:.0%}以上（いまの材料: {material_chars:,}字）。"
              "薄い章にしない",
              "- 設定の説明を人物の台詞にまとめない（視覚描写・体に伝わる振動へ移す）",
              "- 比喩（よう／まるで／みたい）は章の中で合計3回まで。"
              "同じ型を2回以上使わない",
              "- 「……」は2回まで。「——」は1回まで",
              "- 会話を厚くする（地の文だけの章にしない）",
              "- 同じ内容の反復をしない。ピンチ→克服の落差を作る"]
    return "\n".join(lines) + "\n"


def gate_cmd(path: str) -> str:
    return (f'AUTONOVEL_GATE_WARN_ITEMS="{GATE_WARN}" '
            f"uv run python verify_chapter_gates.py --band 3000 99999 {path}")


def apply_plan(plan: dict) -> int:
    old_nums = chapter_nums()
    entries = plan.get("章ごと") or []
    new_nums = [int(e.get("新しい章", 0)) for e in entries]

    # 1) 材料を組み立てる（統合した章は元の本文を連結）。元の章は上書き前に読み切る
    material = {}
    for e in entries:
        num = int(e.get("新しい章", 0))
        src = [int(s) for s in (e.get("元の章") or [])]
        parts = []
        for s in src:
            t = chapter_text(s)
            if t:
                parts.append(t)
        material[num] = "\n\n".join(parts)
        if not parts:
            print(f"  WARNING: 新第{num}章に材料がありません（新規の章として書きます）")

    # 2) 骨格を書き直してから、章を新しい章立てに並べ替える
    write_new_outline(plan, outline_chapter_entries())
    staging = BASE_DIR / "chapters_rebuild"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    for num in new_nums:
        (staging / f"ch_{num:02d}.md").write_text(material[num], encoding="utf-8")
    for p in CHAPTERS_DIR.glob("ch_*.md"):
        p.unlink()
    for p in sorted(staging.glob("ch_*.md")):
        shutil.move(str(p), str(CHAPTERS_DIR / p.name))
    staging.rmdir()
    print(f"  章を新しい章立てに並べ替えました: {len(old_nums)}章 → {len(new_nums)}章")

    # 3) 1章ずつ書き直す → 章ゲート → 修復 → だめなら材料のまま
    results = []
    for e in entries:
        num = int(e.get("新しい章", 0))
        mat = material[num]
        if not mat.strip():
            results.append((num, 0, 0, "材料なし（そのまま）"))
            continue
        before = jp_chars(mat)
        brief = BRIEFS_DIR / f"rebuild_ch{num:02d}.md"
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text(build_brief(e, plan, before), encoding="utf-8")
        print(f"\n--- 新第{num}章「{e.get('タイトル', '')}」（材料 {before:,}字）")
        run(f"uv run python gen_revision.py {num} {brief}", timeout=REWRITE_TIMEOUT)
        gate = run(gate_cmd(f"chapters/ch_{num:02d}.md"), timeout=300)
        if gate.returncode != 0:
            print("  章ゲートFAIL → 修復を試します")
            run(f"uv run python repair_chapter.py chapters/ch_{num:02d}.md "
                f"--out chapters/ch_{num:02d}_repaired.md --band 3000 99999",
                timeout=REWRITE_TIMEOUT)
            rep = CHAPTERS_DIR / f"ch_{num:02d}_repaired.md"
            if rep.exists():
                if run(gate_cmd(f"chapters/ch_{num:02d}_repaired.md"), timeout=300).returncode == 0:
                    shutil.move(str(rep), str(CHAPTERS_DIR / f"ch_{num:02d}.md"))
                    print("  修復版がゲートを通過しました — 採用します")
                else:
                    rep.unlink()
                    chapter_path(num).write_text(mat, encoding="utf-8")
                    print("  修復しても通らないので材料のまま戻します")
            else:
                chapter_path(num).write_text(mat, encoding="utf-8")
                print("  修復が動かなかったので材料のまま戻します")
        after = jp_chars(chapter_text(num))
        ratio = after / before if before else 1.0
        verdict = "採用" if ratio >= MIN_RATIO else f"薄すぎ（{ratio:.0%}）→ 材料に戻す"
        if ratio < MIN_RATIO:
            chapter_path(num).write_text(mat, encoding="utf-8")
            after = jp_chars(mat)
            verdict = f"薄すぎ（{ratio:.0%}）→ 材料のまま"
        print(f"  結果: {before:,} → {after:,}字 {verdict}")
        results.append((num, before, after, verdict))

    # 4) 整合ゲート（生死の食い違いは骨格を変えたときが一番出る）
    print("\n=== 整合ゲート（骨格を作り直したので必ず回す）===")
    report = EDIT_LOGS_DIR / "consistency_report_rebuild.md"
    EDIT_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    cons = run(f"uv run python verify_consistency.py --report {report}", timeout=600)
    print((cons.stdout or "")[-1500:])

    # 5) state の章数を新しい骨格に合わせる
    if STATE.exists():
        try:
            st = json.loads(read(STATE))
        except Exception:
            st = {}
        st["chapters_total"] = len(new_nums)
        st["chapters_drafted"] = len(new_nums)
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  state.json を {len(new_nums)}章に更新しました")

    print("\n=== 骨格を作り直した結果 ===")
    for num, before, after, verdict in results:
        print(f"  第{num:>2}章: {before:>6,} → {after:>6,}字  {verdict}")
    total_before = sum(b for _, b, _, _ in results)
    total_after = sum(a for _, _, a, _ in results)
    print(f"  合計: {total_before:,} → {total_after:,}字")
    print(f"\n  整合ゲートの結果: {report}")
    print("  次は `uv run python run_pipeline.py --phase revision` で改訂に戻せます")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="幕（構成）レベルを作り直す工程")
    ap.add_argument("--goal", default="", help="直したいこと（骨格の指摘）")
    ap.add_argument("--from-review", action="store_true",
                    help="講評の「構成」の指摘を goal にする")
    ap.add_argument("--plan-file", default="",
                    help="設計JSONの保存先／読み取り先（既定 briefs/arc_rebuild_plan.json）")
    ap.add_argument("--apply", action="store_true",
                    help="設計を採用して骨格と本文を作り直す（省略時は設計だけ出す）")
    args = ap.parse_args()

    if not args.goal and not args.from_review and not args.plan_file:
        sys.exit("ERROR: --goal か --from-review か --plan-file を指定してください")

    old_nums = chapter_nums()
    if len(old_nums) < 2:
        sys.exit("ERROR: 章が2つ以上必要です")

    plan = None
    if args.plan_file:
        p = Path(args.plan_file)
        if p.exists():
            plan = json.loads(p.read_text(encoding="utf-8"))
            print(f"  設計を読み込みました: {p}")
    if plan is None:
        goal = args.goal or (goal_from_review() if args.from_review else "")
        if not goal:
            sys.exit("ERROR: goal が空です")
        print(f"=== 骨格を作り直す設計（いま {len(old_nums)}章）===")
        print(f"  直したいこと: {goal.splitlines()[0]}")
        plan = build_plan(goal)
        default_plan = BRIEFS_DIR / "arc_rebuild_plan.json"
        save_plan(plan, default_plan)

    show_plan(plan)
    errors = validate_plan(plan, old_nums)
    if errors:
        print("\n  [設計の検算で見つかった問題]")
        for e in errors:
            print(f"    - {e}")
        print(f"\n  設計を直してから適用してください: {BRIEFS_DIR / 'arc_rebuild_plan.json'}")
        return 2
    print("\n  設計の検算: OK（取りこぼしゼロ・章番号の連番・山場1つ・静かな章の数・損失の位置）")

    if not args.apply:
        print("\n  本文は触っていません。適用するには --apply を付けてください。")
        print("  （骨格を作り直すと統合された章は書き直しになるので、費用がかかります。"
              "設計を人手で直してから適用することもできます）")
        return 0

    print("\n=== 適用します（骨格と本文を作り直す）===")
    return apply_plan(plan)


if __name__ == "__main__":
    raise SystemExit(main())
