# -*- coding: utf-8 -*-
"""repair_chapter.py — 章ゲートに落ちた章を「捨てずに直す」修復パス.

今までの動作は「ゲートに落ちたら章を破棄して、もう一度ゼロから書かせる」（＝くじ引き）。
このスクリプトは逆で、**落ちた原因の箇所だけを直す**:

  (1) 機械で直せる欠陥はコードで直す（LLMを使わない）
      - 重複文/重複段落（分割ドラフターの再帰）→ 2回目以降を削除
      - …… が2個超 / —— が1個超 → 3個目以降を「、」「。」に置換
  (2) 意味が要る欠陥は、LLMに**その箇所だけ**書き直させる
      - 比喩（まるで/よう/みたい/あたかも/ごとく）の予算超過 → 超過している文を1文ずつ言い換え
      - 最小窓（会話が薄い500字の窓）→ その場面に会話を足す
  (3) 直したら章ゲートを回して前後を比較する。通れば採用、通らなければ元に戻す

元ファイルは**上書きしない**（`--out` に書き出す。既定は `<名前>_repaired.md`）。

使い方（リポジトリの venv で。.env を読む）:
  uv run python repair_chapter.py chapters/ch_05.md
  uv run python repair_chapter.py chapters/ch_05.md --out /tmp/ch_05_fixed.md --max-rounds 2
  uv run python repair_chapter.py chapters/ch_05.md --dry-run     # 直せる箇所の列挙だけ

exit 0 = 修復後にゲート通過 / exit 1 = 通過しなかった（それでも修復版は書き出す）。
"""
import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

import llm  # noqa: E402
from draft_chapter_split import min_window_ratio  # noqa: E402

BAND_DEFAULT = "3000 99999"
WINDOW = 500
WINDOW_STEP = 100
METAPHOR_DENSITY_MAX = 3.0
METAPHOR_SAME_MAX = 4
DUP_MIN_LEN = 12


def norm(s: str) -> str:
    return re.sub(r"[\s\u3000]", "", s)


def metaphor_counts(text: str) -> dict:
    return {
        "まるで": len(re.findall(r"まるで", text)),
        "よう系": len(re.findall(r"よう(?:に|な|だ)", text)),
        "みたい": len(re.findall(r"みたい", text)),
        "あたかも": len(re.findall(r"あたかも", text)),
        "ごとく": len(re.findall(r"ごとく", text)),
    }


def measure(text: str, lo: int, hi: int) -> dict:
    quotes = re.findall(r"「[^」]*」", text)
    dlg = sum(len(q) for q in quotes)
    chars = len(text)
    sents = [s.strip() for s in re.split(r"[。！？]+", text) if s.strip()]
    dups = {k: v for k, v in Counter(sents).items() if v >= 2 and len(k) >= DUP_MIN_LEN}
    mk = metaphor_counts(text)
    mk_total = sum(mk.values())
    mw = min_window_ratio(text) * 100
    return {
        "chars": chars,
        "chars_ok": lo <= chars <= hi,
        "ratio": (dlg / chars * 100) if chars else 0.0,
        "ratio_ok": (dlg / chars * 100 if chars else 0) >= 50,
        "min_window": mw,
        "min_window_ok": mw >= 55,
        "dup": len(dups),
        "dup_ok": len(dups) == 0,
        "dup_list": dups,
        "dots": text.count("……"),
        "dash": text.count("——"),
        "symbols_ok": text.count("……") <= 2 and text.count("——") <= 1,
        "english": len(re.findall(r"[A-Za-z]{4,}", text)),
        "english_ok": len(re.findall(r"[A-Za-z]{4,}", text)) == 0,
        "metaphor": mk,
        "metaphor_density": (mk_total / (chars / 1000)) if chars else 0.0,
        "metaphor_same": mk["まるで"] + mk["みたい"],
        "metaphor_ok": (mk_total / (chars / 1000) if chars else 0) <= METAPHOR_DENSITY_MAX
                       and (mk["まるで"] + mk["みたい"]) <= METAPHOR_SAME_MAX,
    }


def all_ok(m: dict) -> bool:
    return all(m[k] for k in ("chars_ok", "ratio_ok", "min_window_ok",
                              "dup_ok", "symbols_ok", "english_ok", "metaphor_ok"))


def show(label: str, m: dict) -> None:
    print(f"  {label}: 文字数 {m['chars']}{'' if m['chars_ok'] else '✗'} / "
          f"セリフ {m['ratio']:.1f}%{'' if m['ratio_ok'] else '✗'} / "
          f"最小窓 {m['min_window']:.0f}%{'' if m['min_window_ok'] else '✗'} / "
          f"重複 {m['dup']}{'' if m['dup_ok'] else '✗'} / "
          f"記号 ……{m['dots']}——{m['dash']}{'' if m['symbols_ok'] else '✗'} / "
          f"英語 {m['english']}{'' if m['english_ok'] else '✗'} / "
          f"比喩 {m['metaphor_density']:.1f}{'' if m['metaphor_ok'] else '✗'}"
          f"（まるで+みたい={m['metaphor_same']}）")


# --------------------------------------------------------------------- 機械修復

def drop_dup_sentences(text: str, log: list) -> str:
    """重複文（12字以上・2回以上）の2回目以降を削除する。最初の1回は残す。"""
    sents = [s.strip() for s in re.split(r"[。！？]+", text) if s.strip()]
    dups = [k for k, v in Counter(sents).items() if v >= 2 and len(k) >= DUP_MIN_LEN]
    for sent in sorted(dups, key=len, reverse=True):
        parts = text.split(sent)
        if len(parts) > 2:
            text = sent.join(parts[:2]) + "".join(parts[2:])
            log.append(f"重複文の2回目以降を削除（{len(parts) - 2}箇所）: {sent[:40]}…")
    return text


def cut_symbols(text: str, log: list) -> str:
    """…… は2個まで・—— は1個までにする（3個目以降を地味な記号に置換）。"""
    out, dots, dash = [], 0, 0
    i = 0
    while i < len(text):
        if text.startswith("……", i):
            dots += 1
            if dots > 2:
                nxt = text[i + 2:i + 3]
                repl = "。" if nxt in ("\n", "", "」") else "、"
                log.append(f"…… の3個目以降を「{repl}」に置換")
                out.append(repl)
            else:
                out.append("……")
            i += 2
            continue
        if text.startswith("——", i):
            dash += 1
            if dash > 1:
                log.append("—— の2個目以降を「、」に置換")
                out.append("、")
            else:
                out.append("——")
            i += 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


# --------------------------------------------------------------------- LLM修復

REWRITE_SYSTEM = (
    "あなたは日本語の小説の推敲をする編集者です。"
    "意味・人称・文体・長さを保ったまま、指定された表現だけを書き換えます。JSONのみで答えます。"
)

REWRITE_PROMPT = """次の一文が、この作品で繰り返し使われている比喩の型（まるで／〜ように・ような・ようだ／みたい／あたかも／ごとく）を使っています。
**その比喩を使わずに**、同じ意味・同じ人称・同じ文体で書き直してください。

【直前の文】
{prev}

【書き換える文】
{target}

【直後の文】
{next}

条件:
- 比喩の型（まるで／ように・ような・ようだ／みたい／あたかも／ごとく）を1つも使わない
- 意味を変えない。出来事・人物・時制を足さない
- 長さは元の0.6〜1.4倍に収める
- 書き換え後の一文だけを返す

JSONのみ: {{"rewrite": "<書き換え後の一文>"}}
"""

INSERT_SYSTEM = (
    "あなたは日本語の小説の推敲をする編集者です。"
    "指定された場面に、自然な会話を足します。地の文の文体・人称・語彙を崩しません。JSONのみで答えます。"
)

INSERT_PROMPT = """次の場面は会話が少なく、読者が退屈しやすい箇所です。ここに**自然な会話（「」のやりとり）を2〜3往復**足してください。

【場面（この前後を含む抜粋）】
{scene}

条件:
- 登場人物はこの抜粋に出ている人物だけ。新しい人物・新しい事実・新しい展開を足さない
- 地の文とト書きでつなぎ、会話は「」で書く
- 文体・人称・語尾はこの抜粋に合わせる
- 200〜400字程度。段落として差し込める形で返す（前後の地の文は書かない）

JSONのみ: {{"insert": "<差し込む本文>"}}
"""


def rewrite_metaphor(text: str, log: list, max_edits: int = 20) -> str:
    """比喩の予算を超えている文を1文ずつ言い換える（超えなくなるまで）。

    1つ失敗しても止めない（次の候補に進む）。同じ文は二度試さない（無限ループ防止）。
    """
    tried = set()
    edits = 0
    while edits < max_edits:
        m = measure(text, -10 ** 9, 10 ** 9)
        if m["metaphor_ok"]:
            break
        sents = [s for s in re.split(r"(?<=[。！？」])", text) if s.strip()]
        # まるで/みたい が上限を超えているなら、それを先に減らす。超えていなければ よう系 を減らす。
        if m["metaphor_same"] > METAPHOR_SAME_MAX:
            pattern = r"まるで|みたい"
        else:
            pattern = r"よう(?:に|な|だ)|あたかも|ごとく"
        cands = [s for s in sents if re.search(pattern, s) and s.strip() not in tried]
        if not cands:
            break
        target = cands[0].strip()
        tried.add(target)
        idx = text.find(target)
        if idx < 0:
            continue
        prev_s = text[:idx].split("。")[-2].strip() if idx >= 0 else ""
        next_s = text[idx + len(target):].split("。")[0].strip() if idx >= 0 else ""
        try:
            raw = llm.call("revision",
                           REWRITE_PROMPT.format(prev=prev_s[-120:], target=target[:200],
                                                 next=next_s[:120]),
                           system=REWRITE_SYSTEM, max_tokens=400, temperature=0.4)
            new = str(llm_parse(raw).get("rewrite") or "").strip()
        except Exception as exc:            # noqa: BLE001
            log.append(f"比喩の書き換えに失敗（{exc}）")
            continue
        if not new or new == target:
            continue
        if re.search(r"まるで|みたい|よう(?:に|な|だ)|あたかも|ごとく", new):
            log.append(f"書き換えに比喩が残ったので見送り: {new[:30]}…")
            continue
        if not (0.6 * len(target) <= len(new) <= 1.4 * len(target)):
            log.append(f"長さが変わりすぎたので見送り: {new[:30]}…")
            continue
        text = text.replace(target, new, 1)
        log.append(f"比喩を言い換え: 「{target[:28]}…」→「{new[:28]}…」")
        edits += 1
    return text


def weakest_window(text: str) -> tuple:
    """会話が最も薄い500字の窓（割合, 開始位置）を返す。"""
    best = (1e9, 0)
    limit = max(1, len(text) - WINDOW + 1)
    for i in range(0, limit, WINDOW_STEP):
        w = text[i:i + WINDOW]
        r = sum(len(q) for q in re.findall(r"「[^」]*」", w)) / WINDOW
        if r < best[0]:
            best = (r, i)
    return best


def insert_dialogue(text: str, log: list) -> str:
    """会話が薄い窓に会話を足す。改善しなければ元に戻す。"""
    r, pos = weakest_window(text)
    scene = text[max(0, pos - 200):pos + WINDOW + 200]
    try:
        raw = llm.call("revision", INSERT_PROMPT.format(scene=scene),
                       system=INSERT_SYSTEM, max_tokens=700, temperature=0.5)
        add = str(llm_parse(raw).get("insert") or "").strip()
    except Exception as exc:                 # noqa: BLE001
        log.append(f"会話の追加に失敗（{exc}）")
        return text
    if not add or "「" not in add:
        log.append("会話の追加案が返らなかったので見送り")
        return text
    before = min_window_ratio(text)
    # 窓の終わりの段落境界に差し込む
    at = text.find("\n", pos + WINDOW)
    at = len(text) if at < 0 else at
    cand = text[:at] + "\n\n" + add + "\n" + text[at:]
    after = min_window_ratio(cand)
    if after <= before:
        log.append(f"会話を足しても最小窓が改善しないので見送り（{before * 100:.0f}%→{after * 100:.0f}%）")
        return text
    log.append(f"会話を追加（最小窓 {before * 100:.0f}%→{after * 100:.0f}%）: {add[:40]}…")
    return cand


def llm_parse(raw: str) -> dict:
    import json
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end + 1], strict=False)
        except Exception:                     # noqa: BLE001
            return {}
    return {}


# --------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="章ゲートに落ちた章を直す（破棄しない）")
    ap.add_argument("path", help="章ファイル")
    ap.add_argument("--out", default="", help="出力先（既定 <名前>_repaired.md）")
    ap.add_argument("--band", default=BAND_DEFAULT, help='"lo hi" 形式の文字数帯')
    ap.add_argument("--max-rounds", type=int, default=2)
    ap.add_argument("--no-insert", action="store_true", help="会話の追加をしない")
    ap.add_argument("--dry-run", action="store_true", help="直せる箇所を数えるだけ")
    args = ap.parse_args()

    lo, hi = (int(x) for x in args.band.split())
    src = Path(args.path)
    text = src.read_text(encoding="utf-8")
    m0 = measure(text, lo, hi)
    print(f"== 修復前 == {src.name}")
    show("before", m0)
    if all_ok(m0):
        print("（すでに全項目とおっているので何もしません）")
        return 0

    log = []
    if args.dry_run:
        print(f"\n重複文: {m0['dup']}件 {list(m0['dup_list'])[:5]}")
        print(f"記号: ……{m0['dots']} / ——{m0['dash']}  → 3個目以降を置換")
        print(f"比喩: 密度 {m0['metaphor_density']:.1f}（上限 {METAPHOR_DENSITY_MAX}）"
              f"・まるで+みたい {m0['metaphor_same']}（上限 {METAPHOR_SAME_MAX}）→ 超過分を言い換え")
        r, pos = weakest_window(text)
        print(f"最小窓: {m0['min_window']:.0f}%（下限55%）/ 最も薄い窓 {r * 100:.0f}% は {pos}字目 → 会話を追加")
        return 0

    cur = text
    for rnd in range(1, max(1, args.max_rounds) + 1):
        print(f"\n-- 修復ラウンド {rnd}/{args.max_rounds} --")
        cur = cut_symbols(cur, log)
        cur = drop_dup_sentences(cur, log)
        if not measure(cur, lo, hi)["metaphor_ok"]:
            cur = rewrite_metaphor(cur, log)
        if not args.no_insert and not measure(cur, lo, hi)["min_window_ok"]:
            cur = insert_dialogue(cur, log)
        m = measure(cur, lo, hi)
        show(f"round{rnd}", m)
        if all_ok(m):
            break

    m1 = measure(cur, lo, hi)
    out = Path(args.out) if args.out else src.with_name(f"{src.stem}_repaired.md")
    out.write_text(cur, encoding="utf-8")

    print("\n== 変更点 ==")
    for line in log or ["（変更なし）"]:
        print(f"  - {line}")
    print(f"\n== 修復後 == {out}")
    show("after", m1)
    print(f"文字数の増減: {m0['chars']} → {m1['chars']}（{m1['chars'] - m0['chars']:+d}）")
    ok = all_ok(m1)
    print("RESULT:", "全項目PASS" if ok else "未合格（残りは下の✗）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
