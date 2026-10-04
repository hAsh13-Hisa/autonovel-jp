#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""strength_transplant.py — 改訂の向きを逆にする（天井を上げる）.

これまでの改訂は「最弱章・最弱の項目を直す」＝**平均点を上げる**方向だけだった。
本家の `reader_panel.py` は `best_scene`（最も良い場面）と `haunts_you`（残る一行）を
聞いているのに、改訂には worst / missing / thinnest しか使っていない（＝宝の持ち腐れ）。
平均点の底上げでは天井は動かないので、**良い場面で効いている書き方を型として言語化し、
弱い章の同種の場面へ移植する**指示書を作る。

流れ:
  1. パネル結果（best_scene / 1問パネルの tier=upper の理由）から、うまくいっている場面を集める
  2. そこから「効いている型」を最大5つ言語化する（LLM 1回）→ edit_logs/strength_patterns.{json,md}
  3. 対象章（既定は tier=lower の章）ごとに、型を当てると効く場面を1つ選び、
     直し方の手順をブリーフにする（LLM 1回/章）→ briefs/chNN_cycleC_strength.md

作ったブリーフは run_pipeline の改訂ループが拾って `gen_revision.py` に渡す
（章ゲート→修復→判定→一対比較の安全網はそのまま効く）。

使い方:
  python strength_transplant.py --panel <panel.json> --patterns
  python strength_transplant.py --panel <panel.json> --briefs --max-briefs 2 --cycle 1
  python strength_transplant.py --panel <panel.json> --patterns --briefs --chapters 3,16

  --panel は次の2形式のどちらでもよい:
    1) 1問パネルの出力      {"chapters": {"3": {"tier": "lower", "reason": "..."}}}
    2) reader_panel の出力  [{"best_scene": "...", "haunts_you": "..."}, ...]

exit 0 = 生成できた／exit 2 = 材料が足りない（パネルに良い場面が無い等）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPO = BASE_DIR      # main() で --dir に合わせて差し替える（llm.py の探索に使う）
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except Exception:  # noqa: BLE001
    pass

PATTERNS_SYSTEM = (
    "あなたは日本語小説の編集者です。うまくいっている場面の引用を読み、"
    "そこに共通して効いている書き方だけを「型」として言語化します。"
    "抽象論や一般論は書かず、その作品の本文に即して書きます。JSONのみで答えます。"
)

PATTERNS_PROMPT = """次は、読者役の審査で「うまくいっている」と挙げられた場面の引用です。

{quotes}

# してほしいこと
これらの場面に共通して効いている書き方を、最大5つ「型」として言語化してください。
- 抽象的な一般論（「感情を丁寧に描く」等）は書かないでください。
- それぞれ、この作品の本文に即した書き方にしてください。
- 各型に、上の引用から短い例を添えてください。

書き方（厳守）:
- JSON 以外は何も書かない。
- name は15字以内。what は60字以内。example は40字以内（引用そのまま）。

出力例: {{"patterns": [{{"name": "短い型の名前", "what": "何がどう効くか", "example": "本文からの短い引用"}}]}}
"""

BRIEF_SYSTEM = (
    "あなたは日本語小説の編集者です。良い章で効いている「型」を、別の章の同じ種類の場面へ"
    "移植する具体的な指示を書きます。あらすじ・文体・伏線は変えません。JSONのみで答えます。"
)

PROSE_SYSTEM = (
    "あなたは日本語小説の校正者です。うまくいっている場面の引用を読み、"
    "そこに共通して効いている**文章の作り方**（文の長さ・運び、語の選び方、間の取り方、"
    "情報の出し方）だけを「型」として言語化します。"
    "人物・筋・設定には触れません。抽象論や一般論は書かず、本文に即します。JSONのみで答えます。"
)

PROSE_PROMPT = """次は、読者役の審査で「うまくいっている」と挙げられた場面の引用です。

{quotes}

# してほしいこと
これらの場面の**文章そのもの**（＝どう書いているか）に共通して効いている型を、最大5つ挙げてください。
- 対象: 文の長さと運び／体言止めや倒置の使い方／情報を伏せる順番／会話と地の文の継ぎ方／
  語の選び方（抽象語か具体物か）／間（ま）の取り方。
- 対象外: 人物・関係・筋・設定・伏線の話は書かないでください。
- 「感情を丁寧に描く」のような一般論は書かないでください。この本文の書き方に即してください。
- 各型に、上の引用から短い例を添えてください。

書き方（厳守）:
- JSON 以外は何も書かない。
- name は15字以内。what は60字以内。example は40字以内（引用そのまま）。

出力例: {{"patterns": [{{"name": "短い型の名前", "what": "どう書くのか", "example": "本文からの短い引用"}}]}}
"""

BRIEF_PROMPT = """次の章に、別の章で効いていた「型」を移植したい。

# 移植できる型
{patterns}

# 移植先の章（第{ch}章）
{text}

# してほしいこと
この章の中で、上の型を当てると効く場面を1つだけ選び、直し方を日本語で書いてください。
- 場面は本文から40字以内でそのまま引用してください（実在する文であること）。
- 手順は3つまで。**その場面の書き方**の指示にしてください（あらすじを変えない）。
- 型に無いことを新しく持ち込まないでください。

書き方（厳守）:
- JSON 以外は何も書かない。
- pattern は型の名前（上の一覧から）。why は30字以内。

出力例: {{"scene_quote": "本文からの短い引用", "pattern": "型の名前", "plan": ["手順1", "手順2"], "why": "30字以内"}}
"""


# ---------------------------------------------------------------- 材料を読む

def read(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""


def load_best_scenes(panel: Path) -> tuple:
    """パネル結果から「うまくいっている場面」と「弱い章」を返す。

    戻り値: (quotes:list[str], weak_chapters:list[int], note:str)
    """
    if not panel.exists():
        return [], [], f"パネル結果が見つかりません: {panel}"
    try:
        data = json.loads(panel.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [], [], f"パネル結果を読めません: {e}"

    quotes, weak = [], []
    # 形式1: 1問パネル {"chapters": {"3": {"tier": "lower", "reason": ...}}}
    if isinstance(data, dict) and isinstance(data.get("chapters"), dict):
        for key, val in data["chapters"].items():
            if not isinstance(val, dict):
                continue
            tier = str(val.get("tier", "")).lower()
            reason = str(val.get("reason", "")).strip()
            quote = str(val.get("quote", "")).strip()
            try:
                num = int(key)
            except ValueError:
                continue
            if tier == "lower":
                weak.append(num)
            if tier == "upper" and (reason or quote):
                text = f"（第{num}章・上の層）{reason}"
                if quote and val.get("quote_ok"):
                    text += f" — 引用「{quote}」"
                quotes.append(text)
        return quotes, sorted(weak), "1問パネル形式"
    # 形式2: reader_panel の出力（配列・best_scene / haunts_you）
    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            for key in ("best_scene", "haunts_you"):
                val = entry.get(key)
                if isinstance(val, str) and 10 <= len(val) <= 600:
                    quotes.append(f"（{key}）{val.strip()}")
            worst = entry.get("worst_scene")
            if isinstance(worst, str):
                nums = [int(x) for x in re.findall(r"(?:Ch|第)\s*(\d+)", worst)]
                weak.extend(nums[:1])
        return quotes, sorted(set(weak)), "reader_panel 形式"
    return [], [], "パネル結果の形式が想定外"


def load_chapters(repo: Path, only: list) -> dict:
    chdir = repo / "chapters" if (repo / "chapters").is_dir() else repo
    out = {}
    for f in sorted(chdir.glob("ch_*.md")):
        m = re.search(r"(\d+)", f.stem)
        if not m:
            continue
        num = int(m.group(1))
        if only and num not in only:
            continue
        out[num] = f.read_text(encoding="utf-8")
    return out


# ---------------------------------------------------------------- LLM

def _llm(prompt: str, system: str, max_tokens: int = 12000) -> dict:
    for p in (REPO, BASE_DIR):   # llm.py はリポジトリ直下にある
        sys.path.insert(0, str(p))
    import llm  # noqa: E402

    raw = llm.call("judge", prompt, system=system, max_tokens=max_tokens)
    m = re.search(r"\{.*\}", raw or "", flags=re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _norm_patterns(data: dict, lens: str) -> list:
    out = []
    for p in (data.get("patterns") or []):
        if isinstance(p, dict) and p.get("name"):
            out.append({"name": str(p["name"])[:30],
                        "what": str(p.get("what", ""))[:120],
                        "example": str(p.get("example", ""))[:80],
                        "lens": lens})
    return out


def make_patterns(quotes: list, lens: str = "both") -> list:
    """効いている型を言語化する。lens=structure（仕掛け）／prose（文章）／both。"""
    body = "\n".join(f"- {q}" for q in quotes[:12])
    out = []
    if lens in ("structure", "both"):
        out += _norm_patterns(_llm(PATTERNS_PROMPT.format(quotes=body), PATTERNS_SYSTEM),
                              "structure")
    if lens in ("prose", "both"):
        out += _norm_patterns(_llm(PROSE_PROMPT.format(quotes=body), PROSE_SYSTEM), "prose")
    return out


def make_brief(ch: int, text: str, patterns: list) -> dict:
    def line(p):
        lens = {"structure": "仕掛け", "prose": "文章"}.get(p.get("lens", ""), "")
        head = f"[{lens}] " if lens else ""
        return f"- {head}{p['name']}: {p['what']}（例「{p['example']}」）"

    body = "\n".join(line(p) for p in patterns)
    return _llm(BRIEF_PROMPT.format(patterns=body, ch=ch, text=text[:6000]),
                BRIEF_SYSTEM)


def brief_markdown(ch: int, brief: dict, patterns: list) -> str:
    plan = brief.get("plan") or []
    if isinstance(plan, str):
        plan = [plan]
    lines = [
        f"# 強みの移植（自動生成）: 第{ch}章",
        "",
        "狙い: 平均点を上げるのではなく、**天井を上げる**。良い章で効いている型を、",
        "この章の同じ種類の場面へ移植して、この章の「一番良い場面」の高さを上げる。",
        "あらすじ・文体・伏線・会話の運びは変えないこと。",
        "",
        f"- 移植する型: **{brief.get('pattern', '')}**",
        f"- 対象の場面（本文から）: 「{brief.get('scene_quote', '')}」",
    ]
    for i, step in enumerate(plan, 1):
        lines.append(f"- 手順{i}: {step}")
    if brief.get("why"):
        lines.append(f"- なぜ: {brief['why']}")
    lines += ["", "## 参考: 型の一覧", ""]
    for p in patterns:
        lens = {"structure": "仕掛け", "prose": "文章"}.get(p.get("lens", ""), "")
        head = f"[{lens}] " if lens else ""
        lines.append(f"- {head}{p['name']}: {p['what']}（例「{p['example']}」）")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="強みの移植（改訂の向きを逆にする）")
    ap.add_argument("--dir", default=str(BASE_DIR))
    ap.add_argument("--panel", default="", help="パネル結果の JSON（1問パネル／reader_panel）")
    ap.add_argument("--patterns", action="store_true", help="効いている型を言語化する")
    ap.add_argument("--briefs", action="store_true", help="対象章の移植ブリーフを作る")
    ap.add_argument("--chapters", default="", help="対象章（例 3,16）。既定はパネルの弱い章")
    ap.add_argument("--lens", default="both",
                    choices=["structure", "prose", "both"],
                    help="型をどの観点で言語化するか（仕掛け／文章／両方）")
    ap.add_argument("--max-briefs", type=int, default=2, help="1回に作るブリーフの上限")
    ap.add_argument("--cycle", type=int, default=1, help="改訂サイクル番号（ファイル名に使う）")
    ap.add_argument("--out-dir", default="briefs", help="ブリーフの置き場")
    ap.add_argument("--patterns-out", default="edit_logs/strength_patterns", help="型の保存先（拡張子なし）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo = Path(args.dir).resolve()
    globals()["REPO"] = repo
    panel = Path(args.panel) if args.panel else repo / "edit_logs" / "reader_panel.json"
    quotes, weak, note = load_best_scenes(panel)
    print(f"パネル: {panel}（{note}）")
    print(f"  うまくいっている場面: {len(quotes)}件 / 弱い章: {weak or 'なし'}")
    if not quotes:
        print("  良い場面の引用がパネルにありません（best_scene か tier=upper が必要）")
        return 2

    patterns_path = Path(args.patterns_out)
    if not patterns_path.is_absolute():
        patterns_path = repo / patterns_path
    patterns = []
    if args.patterns or not Path(f"{patterns_path}.json").exists():
        patterns = make_patterns(quotes, args.lens)
        if patterns:
            patterns_path.parent.mkdir(parents=True, exist_ok=True)
            Path(f"{patterns_path}.json").write_text(
                json.dumps({"patterns": patterns, "source": str(panel)},
                           ensure_ascii=False, indent=1), encoding="utf-8")
            md = ["# 効いている型（自動生成）", "", f"材料: {panel}", ""]
            for lens_key, lens_label in (("structure", "仕掛けの型"), ("prose", "文章の型")):
                group = [p for p in patterns if p.get("lens") == lens_key]
                if not group:
                    continue
                md += [f"# {lens_label}", ""]
                for p in group:
                    md += [f"## {p['name']}", f"- 何が効くか: {p['what']}",
                           f"- 例: 「{p['example']}」", ""]
            Path(f"{patterns_path}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
            print(f"  型を {len(patterns)}件 言語化 → {patterns_path}.json / .md")
    else:
        try:
            patterns = json.loads(
                Path(f"{patterns_path}.json").read_text(encoding="utf-8")).get("patterns", [])
            print(f"  既存の型を使う: {len(patterns)}件（{patterns_path}.json）")
        except Exception:  # noqa: BLE001
            patterns = []
    for p in patterns:
        print(f"    - {p['name']}: {p['what']}")

    if not args.briefs:
        return 0
    if not patterns:
        print("型が作れなかったので、ブリーフは作りません")
        return 2

    target = [int(x) for x in args.chapters.split(",") if x.strip().isdigit()] or weak
    if not target:
        print("対象章が決まりません（--chapters で指定してください）")
        return 2
    target = target[:max(1, args.max_briefs)]
    texts = load_chapters(repo, target)
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = repo / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    made = []
    for num in target:
        text = texts.get(num)
        if not text:
            print(f"  第{num}章の本文が見つかりません（chapters/ch_{num:02d}.md が要ります）")
            continue
        print(f"  移植先を検討中: 第{num}章 …", flush=True)
        brief = make_brief(num, text, patterns)
        if not brief.get("scene_quote"):
            print(f"    第{num}章: 場面を選べませんでした（見送り）")
            continue
        path = out_dir / f"ch{num:02d}_cycle{args.cycle}_strength.md"
        path.write_text(brief_markdown(num, brief, patterns), encoding="utf-8")
        made.append({"chapter": num, "path": str(path), "brief": brief})
        print(f"    第{num}章: 「{brief['scene_quote'][:30]}」に型「{brief.get('pattern', '')}」"
              f" → {path.name}")

    if args.json:
        print(json.dumps({"patterns": patterns, "briefs": made}, ensure_ascii=False, indent=1))
    print(f"ブリーフ: {len(made)}件（{out_dir}）")
    return 0 if made else 2


if __name__ == "__main__":
    sys.exit(main())
