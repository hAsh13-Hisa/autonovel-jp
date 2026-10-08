#!/usr/bin/env python3
"""章をまたぐ編集（アーク編集）。

1章ずつの書き直しでは届かない指摘（中盤の反復・説明台詞の分散・結末の整理）に効かせる工程。
章数は変えずに、複数章のあいだで役割の重複を消し、空いた分量を新しい展開に充てる。

流れ:
  1) 範囲の章のあらすじ（arc_summary.md か本文）と章立てを読み、構成編集の計画を立てる（1回のLLM呼び出し）
  2) 計画を章ごとの指示書にする（briefs/arc_edit_chNN.md）
  3) 1章ずつ書き直す → 章ゲート → 痩せすぎ検査 → 通れば採用、落ちれば元に戻す
  4) 結果（章ごとの字数・ゲート・痩せ具合）を出す

使い方:
  uv run python edit_arc.py --range 7-16 --goal "同型の展開を削り、エピソードの役割を分けてテンポを上げる"
  uv run python edit_arc.py --range 7-16 --goal "..." --dry-run      # 計画だけ見る
  uv run python edit_arc.py --range 7-16 --goal "..." --max-chapters 3

環境変数:
  AUTONOVEL_ARC_MIN_RATIO  痩せてよい下限（元の字数に対する割合・既定0.9）
  AUTONOVEL_USAGE_LOG      使用量の記録先
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
CHAPTERS_DIR = BASE_DIR / "chapters"
BRIEFS_DIR = BASE_DIR / "briefs"
OUTLINE = BASE_DIR / "outline.md"
ARC_SUMMARY = BASE_DIR / "arc_summary.md"
MIN_RATIO = float(os.environ.get("AUTONOVEL_ARC_MIN_RATIO", "0.9"))

PLAN_SYSTEM = (
    "あなたは日本語長編小説の構成編集者です。全章を読んだうえで、"
    "章をまたいで起こっている問題（同じ型の展開の繰り返し・説明の重複・"
    "クライマックスの後に山が続く等）を指摘し、章ごとの具体的な直し方を組み立てます。"
    "思い付きではなく、本文にある事実だけを根拠にします。回答は日本語で、指定のJSONだけを返します。"
)

PLAN_PROMPT = """次の範囲の章を、**章の数を変えずに**作り直す計画を立ててください。

# 直したいこと（講評からの指摘）
{goal}

# 範囲
第{start}章〜第{end}章（全{count}章・合計{chars}字）

# 章ごとの今の内容（あらすじ）
{summaries}

# 章立て（この作品が各章でやるはずのこと）
{outline}

# 守ること
- **章の数は変えない。統合や削除もしない。** 代わりに、複数の章で役割が重なっている部分を片方から外し、
  その章には別の役割（別の事件・別の人物の動き・次の章への助走）を持たせる。
- 各章の「その章でやるはずのこと」（章立て）は変えない。章末フックも残す。
- 人物の生死を動かさない（死んだ人物を生き返らせない）。
- 設定の説明を人物の台詞にまとめない（主人公の視覚描写・体に伝わる振動へ移す）。
- 章の字数は元の字数の{min_ratio:.0%}以上を保つ（痩せさせない）。

# 出すもの（この形のJSONだけ）
{{
  "範囲全体の方針": "この範囲で何を揃え、何を削るか（2〜3文）",
  "章ごと": [
    {{"章": {first},
      "今の役割": "いまこの章が担っていること（20字程度）",
      "重なっている章": 0,
      "どう変えるか": "この章で具体的にすること（60字程度）",
      "残すもの": "絶対に残す場面・台詞（30字程度）",
      "削るもの": "削る反復・説明（30字程度）",
      "圧縮してよいか": false}}
  ]
}}
"""


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def jp_chars(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def chapter_path(ch: int) -> Path:
    return CHAPTERS_DIR / f"ch_{ch:02d}.md"


def chapter_text(ch: int) -> str:
    p = chapter_path(ch)
    return read(p) if p.exists() else ""


def arc_summaries(nums: list) -> str:
    """あらすじ（arc_summary.md）から、その範囲の章の要約を取る。無ければ本文の書き出しで代用。"""
    out = []
    if ARC_SUMMARY.exists():
        text = read(ARC_SUMMARY)
        for ch in nums:
            m = re.search(rf"###\s*第\s*{ch}\s*章[^\n]*\n(.*?)(?=\n###\s*第|\Z)", text, re.S)
            if m:
                body = m.group(1).strip()
                out.append(f"## 第{ch}章\n{body[:600]}")
                continue
            out.append(f"## 第{ch}章\n（あらすじ無し）書き出し: {chapter_text(ch)[:160]}")
    else:
        for ch in nums:
            out.append(f"## 第{ch}章\n書き出し: {chapter_text(ch)[:200]}")
    return "\n\n".join(out)


def outline_digest(nums: list) -> str:
    """章立てから、その範囲の章のビートを切り出す。"""
    if not OUTLINE.exists():
        return "（章立て無し）"
    text = read(OUTLINE)
    out = []
    for ch in nums:
        m = re.search(rf"###\s*第\s*{ch}\s*章[^\n]*\n(.*?)(?=\n###\s*第|\Z)", text, re.S)
        if not m:
            continue
        body = m.group(1)
        beats = re.findall(r"^\s*-\s*(.+)$", body, re.M)
        hook = re.search(r"章末フック[：:]\s*(.+)", body)
        out.append(f"第{ch}章: " + " / ".join(b.strip() for b in beats[:4])
                   + (f" ［章末フック］{hook.group(1).strip()}" if hook else ""))
    return "\n".join(out) if out else "（章立て無し）"


def build_plan(nums: list, goal: str) -> dict:
    sys.path.insert(0, str(BASE_DIR))
    import llm  # noqa: E402

    prompt = PLAN_PROMPT.format(
        goal=goal, start=nums[0], end=nums[-1], count=len(nums),
        chars=f"{sum(jp_chars(chapter_text(c)) for c in nums):,}",
        summaries=arc_summaries(nums), outline=outline_digest(nums),
        min_ratio=MIN_RATIO, first=nums[0])
    raw = llm.call("revision", prompt, system=PLAN_SYSTEM, max_tokens=8000)
    m = re.search(r"\{.*\}", raw or "", flags=re.S)
    if not m:
        sys.exit("ERROR: 計画のJSONが読めませんでした")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: 計画のJSONが壊れています: {e}")


def build_brief(ch: int, plan: dict) -> str:
    """計画の1章ぶんを、その章の改訂指示書にする。"""
    entry = next((e for e in plan.get("章ごと", []) if int(e.get("章", 0)) == ch), None)
    lines = [f"# 改訂ブリーフ：第{ch}章（章をまたぐ編集）", ""]
    lines.append("## 範囲全体の方針")
    lines.append(plan.get("範囲全体の方針", ""))
    if entry:
        lines.append("")
        lines.append("## この章ですること")
        if entry.get("今の役割"):
            lines.append(f"- いまの役割: {entry['今の役割']}")
        if entry.get("重なっている章"):
            lines.append(f"- 役割が重なっている章: 第{entry['重なっている章']}章"
                         "（同じことを繰り返さない。こちらは別の役割を持つ）")
        lines.append(f"- どう変えるか: {entry.get('どう変えるか', '')}")
        if entry.get("残すもの"):
            lines.append(f"- 必ず残す: {entry['残すもの']}")
        if entry.get("削るもの"):
            lines.append(f"- 削る: {entry['削るもの']}")
    lines.append("")
    lines.append("## 守ること")
    lines.append("- 章の数は変えない。この章を消さない。章の目的と章末フックは残す。")
    lines.append(f"- 字数は元の{MIN_RATIO:.0%}以上（下限3,000字）。いま: {jp_chars(chapter_text(ch)):,}字")
    lines.append("- 人物の生死を動かさない（死んだ人物を生き返らせない。回想・遺品として触れるのは可）。")
    lines.append("- 設定の説明を人物の台詞にまとめない（視覚描写・体に伝わる振動へ移す）。")
    lines.append("- 比喩（よう／まるで／みたい）と「……」を増やさない（章ゲートで不合格になる）。")
    lines.append("- 同じ内容の反復をしない。冒険活劇としての前進を作る。")
    return "\n".join(lines) + "\n"


def run(cmd: str, timeout: int = 600) -> subprocess.CompletedProcess:
    print(f"  RUN: {cmd}", flush=True)
    return subprocess.run(cmd, shell=True, cwd=BASE_DIR, capture_output=True,
                          text=True, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser(description="章をまたぐ編集（構成編集）")
    ap.add_argument("--range", required=True, help="章の範囲（例 7-16）")
    ap.add_argument("--goal", required=True, help="直したいこと（講評の指摘など）")
    ap.add_argument("--max-chapters", type=int, default=0, help="直す章数の上限（0=計画の全部）")
    ap.add_argument("--dry-run", action="store_true", help="計画を出すだけで本文を触らない")
    args = ap.parse_args()

    m = re.match(r"^(\d+)\s*-\s*(\d+)$", args.range.strip())
    if not m:
        sys.exit("ERROR: --range は 7-16 の形で指定してください")
    start, end = int(m.group(1)), int(m.group(2))
    nums = [c for c in range(start, end + 1) if chapter_path(c).exists()]
    if len(nums) < 2:
        sys.exit("ERROR: 範囲に章が2つ以上必要です")

    before = {c: jp_chars(chapter_text(c)) for c in nums}
    print(f"=== 章をまたぐ編集: 第{nums[0]}〜{nums[-1]}章（{len(nums)}章・{sum(before.values()):,}字）===")
    print(f"  直したいこと: {args.goal}")

    plan = build_plan(nums, args.goal)
    print(f"  方針: {plan.get('範囲全体の方針', '')}")
    BRIEFS_DIR.mkdir(exist_ok=True)
    targets = []
    for entry in plan.get("章ごと", []):
        try:
            ch = int(entry.get("章", 0))
        except (TypeError, ValueError):
            continue
        if ch in nums:
            targets.append(ch)
            (BRIEFS_DIR / f"arc_edit_ch{ch:02d}.md").write_text(build_brief(ch, plan), encoding="utf-8")
    if args.max_chapters:
        targets = targets[:args.max_chapters]
    print(f"  直す章: {targets}")
    if args.dry_run:
        print("  （--dry-run なので本文は触りません）")
        return 0

    results = []
    for ch in targets:
        old = before[ch]
        brief = BRIEFS_DIR / f"arc_edit_ch{ch:02d}.md"
        print(f"--- 第{ch}章（{old:,}字）")
        run(f"uv run python gen_revision.py {ch} {brief}")
        new = jp_chars(chapter_text(ch))
        ratio = new / old if old else 1.0
        gate_ok = run(f"uv run python verify_chapter_gates.py --band 3000 99999 "
                      f"chapters/ch_{ch:02d}.md").returncode == 0
        if not gate_ok or ratio < MIN_RATIO:
            reason = "章ゲート不合格" if not gate_ok else f"痩せすぎ（{ratio:.0%}）"
            print(f"  {reason} → 元に戻します")
            run(f"git checkout -- chapters/ch_{ch:02d}.md")
            results.append((ch, old, old, "差し戻し"))
        else:
            results.append((ch, old, new, "採用"))
            run(f"git add -A && git commit -q -m "
                f"'arc edit: ch{ch:02d} 章をまたぐ編集' --allow-empty")
        print(f"  結果: {old:,} → {new:,}字")

    print("\n=== 章をまたぐ編集の結果 ===")
    tot_before = tot_after = 0
    for ch, old, new, verdict in results:
        tot_before += old
        tot_after += new
        print(f"  第{ch:>2}章: {old:>6,} → {new:>6,}字 ({new/old if old else 1:.0%}) {verdict}")
    print(f"  合計: {tot_before:,} → {tot_after:,}字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
