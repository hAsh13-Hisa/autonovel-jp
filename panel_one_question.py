# -*- coding: utf-8 -*-
"""panel_one_question.py — 「面白さ」の実測器（読者パネル。質問は1つだけ）.

質問（どのモードでも1つ）: この章を読んで、続きを読みたいと思ったか。
渡すのは要約ではなく**本文**（章頭300字 ＋ 会話が最も密な500字の窓＝山場の近似 ＋ 章末300字）。

## 2つのモード（既定は tiers。実測で選んだ）

- `--mode tiers`（既定・推奨）: **全章の抜粋を1回の呼び出しで渡し、必ず3層に分けさせる**（相対評価）。
  実測（2026-10-03、全24章・人手ポリッシュ済みの採用版）: 1回5秒で 上層6／中層12／下層6 に分かれ、
  下層に「世界観の説明が多く停滞」「精神的な対話中心でアクションに欠ける」など具体的な理由と引用がついた。
  第1章は上層に入り、人間の実感（第1章は良い／中盤に波がない）と整合した。
- `--mode per-chapter`: 章を1つずつ 0〜10 で採点。**識別力なしと実測済み**
  （つまらないと言われた最初期稿も採用版も 8/10）。単章の印象・引用取りにのみ使う。

**単純な一対比較（A/B）も使えない**（実測: 順序を入れ替えても「先に読まされた方」を選ぶ位置バイアス）。
相対で見たいときは tiers を使う。

## 出力（人間の時間を減らすための形）

- 標準出力: 層ごとの一覧（下層は理由と引用つき）
- `--md <path>`: **人間用ブリーフ**。下層＝**読むべき章**なので、人間はそこだけ読めばよい
- `--out <path.tsv>` / `--json <path.json>`: 機械可読
- 引用は本文に実在するか機械照合する（モデルの作話対策）

## 使い方

  uv run python panel_one_question.py --all                        # <repo>/chapters/ を章順に
  uv run python panel_one_question.py --dir <本文フォルダ> --all    # 作品フォルダを章順に
  uv run python panel_one_question.py --dir <本文フォルダ> --all --repeat 3 \
      --md panel_1q.md --json panel_1q.json
  uv run python panel_one_question.py chapters/ch_01.md            # 単章（per-chapter 扱い）

層は**最初は記録のみ**（run_pipeline はこれで破棄しない）。実測で線を校正してから昇格する
（比喩ゲートと同じ順序: 警告 → 実測 → FAIL）。`--fail-if-lower` で下層があれば exit 1。
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
# .env はカレント（＝作り直し版のコピー先）を優先し、無ければリポジトリ側。
# なぜ: 作り直しの作業はコピー先で行うので、エンドポイントの設定も
# コピー先の .env を使うべき。実測 2026-10-14: BASE_DIR 固定だと
# リポジトリ側の .env を読み、API に繋がらず接続拒否になっていた。
_cwd_env = Path.cwd() / ".env"
load_dotenv(_cwd_env if _cwd_env.exists() else BASE_DIR / ".env")

import llm  # noqa: E402

HEAD_CHARS = 300
PEAK_CHARS = 500
TAIL_CHARS = 300
PEAK_STEP = 100

TIERS = ("upper", "middle", "lower")
TIER_JA = {"upper": "上層（特に続きを読みたい）",
           "middle": "中層（読める）",
           "lower": "下層（退屈した・読み飛ばした）"}

SYSTEM = (
    "あなたは小説を読む1人の読者です。編集者でも評論家でもありません。"
    "読んでいて続きを読みたいと思ったか、退屈したかを正直に答えます。"
    "JSONのみで答えます。"
)

TIERS_PROMPT = """以下は同じ作品の全{total}章の抜粋です（各章、章頭300字＋会話が最も密な500字＋章末300字）。

{corpus}

あなたは1人の読者です。**「続きを読みたいか」という観点**で、{total}章を必ず3層に分けてください。
  upper  = 特に続きを読みたい（{n_upper}章程度）
  middle = 読める（{n_middle}章程度）
  lower  = 退屈した・読み飛ばした（{n_lower}章程度）

必ず全{total}章を**ちょうど1回ずつ**いずれかの層に入れてください（重複・抜けは不可）。
各章に40字以内の理由をつけてください。lower の章には、その章の抜粋から本文を**そのまま引用**した一文もつけてください。

JSONのみで答えてください:
{{"upper": [{{"ch": 1, "reason": "..."}}],
  "middle": [{{"ch": 2, "reason": "..."}}],
  "lower": [{{"ch": 3, "reason": "...", "quote": "..."}}]}}
"""

ONE_PROMPT = """以下はとあるライトノベルの第{n}章の抜粋です（{chars}字の章から、章頭・山場の近似・章末を抜き出したもの）。

【章頭】
{head}

【山場（会話が最も密な500字）】
{peak}

【章末】
{tail}

質問は1つだけです。**この章を読んで、続きを読みたいと思いましたか。**

0〜10の整数で答えてください。目盛りの目安:
  0-3 = 途中で読むのをやめた / 4-6 = 読めるが退屈した / 7-8 = 続きが気になる / 9-10 = 誰かに勧めたい
※ 実測では全章が7〜8に張り付き識別しない。参考値としてのみ使うこと。

理由は、上の抜粋の中の一文を**そのまま引用**して説明してください。

JSONのみで答えてください:
{{"score": <0-10の整数>, "quote": "<抜粋からそのまま引用した一文>", "reason": "<40〜80字>", "want_next": "はい|いいえ|どちらでも"}}
"""


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def build_excerpt(text: str) -> dict:
    """章頭・山場の近似（会話密度最大の窓）・章末を切り出す。"""
    body = strip_comments(text).strip()
    n = len(body)
    if n <= HEAD_CHARS + PEAK_CHARS + TAIL_CHARS:
        return {"head": body, "peak": "", "tail": "", "chars": n}
    head, tail = body[:HEAD_CHARS], body[-TAIL_CHARS:]
    inner_start, inner_end = HEAD_CHARS, n - TAIL_CHARS
    best, best_i = -1, inner_start
    for i in range(inner_start, max(inner_start + 1, inner_end - PEAK_CHARS + 1), PEAK_STEP):
        w = body[i:i + PEAK_CHARS]
        score = w.count("「") + w.count("」")
        if score > best:
            best, best_i = score, i
    return {"head": head, "peak": body[best_i:best_i + PEAK_CHARS], "tail": tail, "chars": n}


def chapter_number(path: Path) -> int:
    """ファイル名の**最後**の数字の塊を章番号にする（ch_07 / 2-07_鏡都 / 1-01_二つの影 → 7, 7, 1）。"""
    nums = re.findall(r"\d+", path.stem)
    return int(nums[-1]) if nums else 0


def parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    start = raw.find("{")
    if start >= 0:
        depth, in_string, escape = 0, False, False
        for i in range(start, len(raw)):
            c = raw[i]
            if escape:
                escape = False
                continue
            if c == "\\" and in_string:
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(raw[start:i + 1], strict=False)
    return json.loads(raw, strict=False)


def norm(s: str) -> str:
    return re.sub(r"[\s\u3000]", "", s)


def item_for(run: dict, ch: int) -> dict:
    """1回分の応答から、その章の項目（tier も返す）を取り出す。"""
    for tier in TIERS:
        for it in run.get(tier) or []:
            if isinstance(it, dict) and str(it.get("ch", "")).strip() == str(ch):
                return {"tier": tier, "reason": str(it.get("reason") or "").strip(),
                        "quote": str(it.get("quote") or "").strip()}
    return {}


def collect(args) -> list:
    files = []
    if args.all or args.dir:
        base = Path(args.dir) if args.dir else BASE_DIR / "chapters"
        files = sorted(base.glob("*.md"), key=chapter_number)
    files += [Path(p) for p in args.files]
    out, seen = [], set()
    for p in files:
        if not p.exists() or p.name in seen:
            continue
        seen.add(p.name)
        out.append(p)
    return out


# --------------------------------------------------------------------------- tiers

def run_tiers(paths, args) -> dict:
    texts, corpus = {}, []
    for p in paths:
        ch = chapter_number(p)
        text = p.read_text(encoding="utf-8")
        if ch <= 0:                    # afterword.md など章番号の無いものは除外
            continue
        texts[ch] = text
        ex = build_excerpt(text)
        corpus.append(f"【第{ch}章】{p.stem.split('_', 1)[-1]}\n{ex['head']}\n{ex['peak']}\n{ex['tail']}")
    chs = sorted(texts)
    if len(chs) < 4:
        print("ERROR: tiers モードは4章以上で使ってください（相対評価のため）")
        return {}
    total = len(chs)
    prompt = TIERS_PROMPT.format(
        total=total, corpus="\n\n".join(corpus),
        n_upper=max(2, round(total * 0.25)),
        n_middle=max(2, round(total * 0.5)),
        n_lower=max(2, round(total * 0.25)))

    runs = []
    for i in range(max(1, args.repeat)):
        raw = llm.call(args.role, prompt, system=SYSTEM,
                       max_tokens=args.max_tokens, temperature=args.temperature)
        runs.append(parse_json(raw))
        print(f"  （判定 {i + 1}/{max(1, args.repeat)} 回目を受領）", file=sys.stderr)

    chapters = {}
    for ch in chs:
        items = [item_for(run, ch) for run in runs]
        items = [it for it in items if it]
        if not items:
            chapters[ch] = {"tier": "unknown", "reason": "(モデルが層に含めなかった)",
                            "quote": "", "quote_ok": False, "history": []}
            continue
        votes = {}
        for it in items:
            votes[it["tier"]] = votes.get(it["tier"], 0) + 1
        tier = max(TIERS, key=lambda t: votes.get(t, 0))
        chosen = next((it for it in items if it["tier"] == tier), items[0])
        quote = chosen["quote"]
        chapters[ch] = {
            "tier": tier,
            "reason": chosen["reason"],
            "quote": quote,
            "quote_ok": bool(quote) and norm(quote) in norm(texts[ch]),
            "history": [it["tier"] for it in items],
        }
    unstable = [ch for ch in chs if len(set(chapters[ch].get("history") or [])) > 1]
    return {"chapters": chapters, "unstable": unstable, "runs": len(runs),
            "total": total, "role": args.role, "temperature": args.temperature}


def report_tiers(res: dict) -> None:
    chapters = res.get("chapters") or {}
    for tier in ("lower", "upper", "middle"):
        members = [(ch, v) for ch, v in sorted(chapters.items()) if v["tier"] == tier]
        print(f"\n== {TIER_JA[tier]} — {len(members)}章 ==")
        for ch, v in members:
            print(f"  ch{ch:02d}  {v['reason']}")
            if v.get("quote"):
                flag = "" if v.get("quote_ok") else "  ⚠引用が本文に見つかりません"
                print(f"        引用: {v['quote'][:60]}{flag}")
    miss = [ch for ch, v in chapters.items() if v["tier"] == "unknown"]
    if miss:
        print(f"\n⚠ モデルが層に含めなかった章: {miss}")
    if res.get("unstable"):
        print(f"⚠ 反復で層が変わった章: {res['unstable']}（判定が不安定。割り引いて読む）")


def write_brief(res: dict, path: str) -> None:
    chapters = res.get("chapters") or {}
    lines = ["# 読者パネル（1問・3層）結果", "",
             f"実行: {datetime.now().strftime('%Y-%m-%d %H:%M')} / 判定 role={res.get('role')} / "
             f"温度 {res.get('temperature')} / 反復 {res.get('runs')}回 / 対象 {res.get('total')}章",
             "", "## 下層＝**読むべき章**（人間の時間はここに集中させる）"]
    for ch, v in sorted(chapters.items()):
        if v["tier"] == "lower":
            q = f"／引用「{v['quote']}」" if v.get("quote") else ""
            lines.append(f"- **ch{ch:02d}**: {v['reason']}{q}")
    lines += ["", "## 上層（効いている章＝他の章へ移植する元）"]
    for ch, v in sorted(chapters.items()):
        if v["tier"] == "upper":
            lines.append(f"- ch{ch:02d}: {v['reason']}")
    lines += ["", "## 中層"]
    for ch, v in sorted(chapters.items()):
        if v["tier"] == "middle":
            lines.append(f"- ch{ch:02d}: {v['reason']}")
    if res.get("unstable"):
        lines += ["", f"⚠ 反復で層が変わった章: {res['unstable']}（判定が不安定）"]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"ブリーフ: {path}")


# --------------------------------------------------------------------------- per-chapter

def ask_one(text: str, ch: int, args) -> dict:
    ex = build_excerpt(text)
    prompt = ONE_PROMPT.format(n=ch, chars=ex["chars"], head=ex["head"],
                               peak=ex["peak"] or "（この章は短いため省略）",
                               tail=ex["tail"] or "（省略）")
    raw = llm.call(args.role, prompt, system=SYSTEM,
                   max_tokens=args.max_tokens, temperature=args.temperature)
    data = parse_json(raw)
    score = data.get("score")
    try:
        score = int(round(float(str(score if score is not None else ""))))
    except (TypeError, ValueError):
        score = -1
    quote = str(data.get("quote") or "").strip()
    return {"score": score, "quote": quote,
            "quote_ok": bool(quote) and len(quote) >= 8 and norm(quote) in norm(text),
            "reason": str(data.get("reason") or "").strip(),
            "want_next": str(data.get("want_next") or "").strip()}


def run_per_chapter(paths, args) -> list:
    rows = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        ch = chapter_number(path)
        results = [ask_one(text, ch, args) for _ in range(max(1, args.repeat))]
        score = round(sum(r["score"] for r in results) / len(results))
        first = results[0]
        ok = all(r["quote_ok"] for r in results)
        rows.append({"file": path.name, "ch": ch, "score": score,
                     "scores": [r["score"] for r in results], "quote_ok": ok,
                     "quote": first["quote"], "reason": first["reason"],
                     "want_next": first["want_next"]})
        print(f"  ch{ch:02d}  {score:>2}/10  {first['want_next']:<6}  {first['reason']}"
              f"{'' if ok else '  ⚠引用が本文に見つかりません'}")
        print(f"         引用: {first['quote'][:60]}")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="読者パネル（面白さの実測器・質問は1つ）")
    ap.add_argument("files", nargs="*", help="章ファイル")
    ap.add_argument("--all", action="store_true", help="フォルダ内の章を章順に全部")
    ap.add_argument("--dir", default="", help="--all の対象フォルダ（既定 <repo>/chapters）")
    ap.add_argument("--mode", default="tiers", choices=["tiers", "per-chapter"])
    ap.add_argument("--role", default="review", help="使うロール（既定 review）")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--repeat", type=int, default=1, help="複数回まわして多数決（安定性も見る）")
    ap.add_argument("--out", default="", help="TSV の保存先")
    ap.add_argument("--json", default="", help="JSON の保存先")
    ap.add_argument("--md", default="", help="人間用ブリーフ（tiers のみ）")
    ap.add_argument("--fail-if-lower", action="store_true",
                    help="下層があれば exit 1（既定は記録のみ＝exit 0）")
    ap.add_argument("--fail-under", type=int, default=None,
                    help="per-chapter モードで、この点数未満があれば exit 1")
    args = ap.parse_args()

    paths = collect(args)
    if not paths:
        print(__doc__)
        return 2

    mode = args.mode
    if mode == "tiers" and len(paths) < 4:
        print("（対象が4章未満なので per-chapter に切り替えます）")
        mode = "per-chapter"

    print(f"モード: {mode} / 対象 {len(paths)} ファイル / role={args.role} / 反復 {max(1, args.repeat)}")

    if mode == "tiers":
        res = run_tiers(paths, args)
        if not res:
            return 2
        report_tiers(res)
        if args.md:
            write_brief(res, args.md)
        if args.out:
            with Path(args.out).open("w", encoding="utf-8") as f:
                f.write("ch\ttier\treason\tquote_ok\tquote\n")
                for ch, v in sorted(res["chapters"].items()):
                    f.write(f"{ch}\t{v['tier']}\t{v['reason']}\t"
                            f"{'ok' if v.get('quote_ok') else 'NG'}\t{v.get('quote', '')}\n")
            print(f"TSV: {args.out}")
        if args.json:
            Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"JSON: {args.json}")
        lower = sorted(ch for ch, v in res["chapters"].items() if v["tier"] == "lower")
        if args.fail_if_lower and lower:
            print(f"RESULT: LOWER {lower}")
            return 1
        print("RESULT: 記録のみ（この層はまだ破棄条件にしていない）")
        return 0

    rows = run_per_chapter(paths, args)
    if rows:
        scores = [r["score"] for r in rows]
        print(f"\n== {len(rows)}章 ==  平均 {sum(scores) / len(scores):.2f} / "
              f"最低 {min(scores)} / 最高 {max(scores)}")
        print("※ 実測: この絶対点は識別しない（つまらない稿も採用版も 8点）。参考値としてのみ使う")
        if args.out:
            with Path(args.out).open("w", encoding="utf-8") as f:
                f.write("file\tch\tscore\tquote_ok\twant_next\treason\tquote\n")
                for r in rows:
                    f.write(f"{r['file']}\t{r['ch']}\t{r['score']}\t"
                            f"{'ok' if r['quote_ok'] else 'NG'}\t{r['want_next']}\t"
                            f"{r['reason']}\t{r['quote']}\n")
            print(f"TSV: {args.out}")
        if args.json:
            Path(args.json).write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
            print(f"JSON: {args.json}")
        if args.fail_under is not None:
            bad = [r["ch"] for r in rows if r["score"] < args.fail_under]
            if bad:
                print(f"RESULT: BELOW THRESHOLD {args.fail_under} -> {bad}")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
