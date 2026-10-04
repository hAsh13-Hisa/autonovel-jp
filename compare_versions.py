#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compare_versions.py — 同じ章の2つの版を一対比較して、採用・不採用を決める.

なぜ必要か（実測 2026-10-03）:
  絶対点（evaluate.py の overall_score）は**同じ章でも実行ごとに最大1点動く**。
  「点が下がったから捨てる」と決めると、良い改訂を捨ててしまう。
  一対比較（どちらが良いか）は絶対点より安定するので、採否の決め手に使う。

順序バイアス対策（実測: 一対比較は順序で答えが変わりうる）:
  同じ組を**両方の順序で**審査し、**両方で同じ版が勝ったときだけ**勝者を確定する。
  順序で答えが変わる場合は「引き分け」扱い＝**現状を維持**（ノイズで入れ替えない）。

使い方:
  python compare_versions.py --a old.md --b new.md
  python compare_versions.py --a old.md --b new.md --chapter 3 --json
  python compare_versions.py --a old.md --b new.md --verdict-file /tmp/verdict.txt
      → 判定ファイルに adopt（Bを採用）/ keep（Aのまま）/ draw（順序で変わる・同等）

Elo 表（--ratings、既定 edit_logs/elo.tsv）に版ごとの強さを貯める:
  1行 = ラベル<TAB>レート<TAB>対戦数<TAB>勝ち数<TAB>更新時刻
  比較1回につき1試合（引き分けは0.5勝）として更新する。

exit 0 = 判定できた（adopt/keep/draw のいずれか）／exit 2 = 判定できなかった（審査の失敗）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
try:  # .env があれば読む（run_pipeline 経由でなく単体で叩いても動くように）
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except Exception:  # noqa: BLE001
    pass
CLIP = 3500          # 1版あたりの本文上限（文字）
K_FACTOR = 16.0      # Elo の K
DEFAULT_RATING = 1500.0

JUDGE_SYSTEM = (
    "あなたは日本語小説の編集者です。同じ章の2つの版を読み、"
    "どちらが「続きを読みたい」かを必ず一方に決めます。"
    "良い点・悪い点は本文から短く引用して示します。回答は日本語で書きます。"
)

PROMPT = """同じ章の2つの版です。どちらが良いかを1つに決めてください。

# 版A
{a}

# 版B
{b}

# 見る観点
- 文章が具体的か（説明くさくないか）
- 会話が話し言葉として自然か（誰が言ったか分かるか）
- 先を読みたいと思わせるか
- 読者を信頼しているか（説明しすぎていないか）
- AIらしい癖が少ないか

引き分けは認めません。どちらかに決めてください。

書き方（厳守）:
- JSON 以外は何も書かない。前置き・説明・感想は書かない。
- decisive は勝った版から20字以内でそのまま抜く。why は40字以内。

出力例: {{"winner": "A", "margin": "clear", "decisive": "短い引用", "why": "40字以内"}}
"""


def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def clip(text: str, n: int = CLIP) -> str:
    return text if len(text) <= n else text[:n] + "\n［以下略］"


def parse_choice(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", flags=re.S)
    if not m:
        return {"winner": "", "raw": (raw or "")[:300]}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"winner": "", "raw": (raw or "")[:300], "error": "JSONの解析に失敗"}
    winner = str(data.get("winner", "")).strip().upper()[:1]
    if winner not in ("A", "B"):
        return {"winner": "", "raw": (raw or "")[:300], "error": f"winner が A/B でない: {winner}"}
    return {"winner": winner,
            "margin": str(data.get("margin", ""))[:20],
            "decisive": str(data.get("decisive", ""))[:120],
            "why": str(data.get("why", ""))[:200]}


def ask(text_a: str, text_b: str) -> dict:
    """審査モデルに1回聞く（AとBのどちらか）。"""
    sys.path.insert(0, str(BASE_DIR))
    import llm  # noqa: E402

    prompt = PROMPT.format(a=text_a, b=text_b)
    return parse_choice(llm.call("judge", prompt, system=JUDGE_SYSTEM, max_tokens=12000))


# ---------------------------------------------------------------- Elo

def load_ratings(path: Path) -> dict:
    table = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                try:
                    table[parts[0]] = {"rating": float(parts[1]),
                                       "games": int(parts[2]), "wins": float(parts[3])}
                except ValueError:
                    continue
    return table


def expected_score(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def apply_elo(table: dict, label_a: str, label_b: str, score_a: float) -> tuple:
    """1試合ぶんレートを更新する（score_a = 1.0 勝ち / 0.5 引き分け / 0.0 負け）。"""
    ra = table.setdefault(label_a, {"rating": DEFAULT_RATING, "games": 0, "wins": 0.0})["rating"]
    rb = table.setdefault(label_b, {"rating": DEFAULT_RATING, "games": 0, "wins": 0.0})["rating"]
    ea = expected_score(ra, rb)
    ra2 = ra + K_FACTOR * (score_a - ea)
    rb2 = rb + K_FACTOR * ((1.0 - score_a) - (1.0 - ea))
    for label, rating, score in ((label_a, ra2, score_a), (label_b, rb2, 1.0 - score_a)):
        row = table[label]
        row["rating"], row["games"], row["wins"] = rating, row["games"] + 1, row["wins"] + score
    return ra, ra2, rb, rb2


def save_ratings(path: Path, table: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["# ラベル\tレート\t対戦数\t勝ち数\t更新"]
    for label, row in sorted(table.items(), key=lambda kv: -kv[1]["rating"]):
        lines.append(f"{label}\t{row['rating']:.1f}\t{row['games']}\t{row['wins']:.1f}\t{now}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 判定

def compare(text_a: str, text_b: str) -> dict:
    """両方の順序で比べ、両方で同じ版が勝ったときだけ勝者を確定する。"""
    if clip(text_a).strip() == clip(text_b).strip():
        return {"verdict": "draw", "reason": "2つの版が同じ", "orders": []}
    first = ask(clip(text_a), clip(text_b))     # A=旧, B=新
    second = ask(clip(text_b), clip(text_a))    # A=新, B=旧（順序を入れ替える）
    new_first = first.get("winner") == "B"
    new_second = second.get("winner") == "A"
    old_first = first.get("winner") == "A"
    old_second = second.get("winner") == "B"
    failed = [x for x in (first, second) if not x.get("winner")]
    if failed:
        return {"verdict": "draw", "reason": "審査の出力が読めなかった（判定しない）",
                "orders": [first, second], "failed": len(failed)}
    if new_first and new_second:
        verdict, reason, score_a = "adopt", "両方の順序で新版が勝った", 0.0
    elif old_first and old_second:
        verdict, reason, score_a = "keep", "両方の順序で旧版が勝った", 1.0
    else:
        verdict, reason, score_a = "draw", "順序で答えが変わった（ノイズ。現状を維持）", 0.5
    return {"verdict": verdict, "reason": reason, "score_a": score_a,
            "orders": [{"order": "旧→新", **first}, {"order": "新→旧", **second}]}


def main() -> int:
    ap = argparse.ArgumentParser(description="同じ章の2つの版を一対比較して採否を決める")
    ap.add_argument("--a", required=True, help="旧版（現状の本文）")
    ap.add_argument("--b", required=True, help="新版（採用したい候補）")
    ap.add_argument("--chapter", default="", help="章番号（記録用）")
    ap.add_argument("--label-a", default="", help="Elo 表での名前（既定はファイル名）")
    ap.add_argument("--label-b", default="", help="Elo 表での名前（既定はファイル名）")
    ap.add_argument("--ratings", default="", help="Elo 表（既定 edit_logs/elo.tsv）")
    ap.add_argument("--verdict-file", default="", help="adopt/keep/draw を書き出す")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    text_a, text_b = read_text(Path(args.a)), read_text(Path(args.b))
    result = compare(text_a, text_b)
    label_a = args.label_a or Path(args.a).stem
    label_b = args.label_b or Path(args.b).stem

    ratings_path = Path(args.ratings) if args.ratings else BASE_DIR / "edit_logs" / "elo.tsv"
    table = load_ratings(ratings_path)
    if "score_a" in result:
        ra, ra2, rb, rb2 = apply_elo(table, label_a, label_b, result["score_a"])
        save_ratings(ratings_path, table)
        result["elo"] = {label_a: [round(ra), round(ra2)], label_b: [round(rb), round(rb2)]}

    ch = f"第{args.chapter}章 " if args.chapter else ""
    print(f"{ch}一対比較: {result['verdict']} — {result['reason']}")
    for entry in result.get("orders", []):
        if entry.get("winner"):
            print(f"  [{entry['order']}] 勝ち={entry['winner']} "
                  f"({entry.get('margin', '')}) 「{entry.get('decisive', '')}」 {entry.get('why', '')}")
        else:
            print(f"  [{entry['order']}] 判定できず: {entry.get('error', entry.get('raw', ''))}")
    if result.get("elo"):
        for label, (before, after) in result["elo"].items():
            print(f"  Elo: {label} {before} → {after}")
    if args.verdict_file:
        Path(args.verdict_file).write_text(result["verdict"] + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["verdict"] in ("adopt", "keep", "draw") else 2


if __name__ == "__main__":
    sys.exit(main())
