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
  uv run python repair_chapter.py chapters/ch_05.md --out /tmp/ch_05_fixed.md --max-rounds 4

会話を足す修復（insert）は 1ラウンドで1箇所しか直せない（最も薄い区画の1段落）。
セリフ比が大きく届いていない章は 4〜8ラウンド必要（実測 2026-10-13: 第18章は
セリフ比29.2%の状態から4ラウンドで31.9%まで回復。50%にはまだ届かない）。
  uv run python repair_chapter.py chapters/ch_05.md --dry-run     # 直せる箇所の列挙だけ

exit 0 = 修復後にゲート通過 / exit 1 = 通過しなかった（それでも修復版は書き出す）。
"""
import argparse
import math
import re
import sys
import traceback
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
# 最小窓（最も会話の薄い500字区画の会話の文字割合）の下限。
# verify_chapter_gates.py と同じ30%に揃える。旧55%は行ベースの数字で、
# 2026-10-06 実測（3章: 最も薄い区画でも 29.8〜35.4%）で到達不能だった。
# 修復が55%を追い続けると「直せない」を繰り返して元のまま戻るだけになる。
MIN_WINDOW_MIN = 0.30


def norm(s: str) -> str:
    return re.sub(r"[\s\u3000]", "", s)


def rc_dialogue_ratio(text: str) -> float:
    """会話（「」で囲まれた部分）の文字割合。**文字ベース**（ゲートと同じ物さし）。

    置き換えた箇所が本当に会話になったかを見るために使う。窓（500字区画）の
    割り当てに影響されないので、修復の採用判定に使える。
    """
    body = norm(text)
    if not body:
        return 0.0
    return len("".join(re.findall(r"「[^」]*」", body))) / len(body)


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
        "min_window_ok": mw >= MIN_WINDOW_MIN * 100,
        "dup": len(dups),
        "dup_ok": len(dups) == 0,
        "dup_list": dups,
        "dots": text.count("……"),
        "dash": text.count("——"),
        "symbols_ok": text.count("……") <= 2 and text.count("——") <= 1,
        "english": len(re.findall(r"[A-Za-z]{4,}", text)),
        "english_ok": len(re.findall(r"[A-Za-z]{4,}", text)) == 0,
        "brackets": {"「」": text.count("「") - text.count("」"),
                     "『』": text.count("『") - text.count("』"),
                     "（）": text.count("（") - text.count("）")},
        "brackets_ok": (text.count("「") == text.count("」")
                        and text.count("『") == text.count("』")
                        and text.count("（") == text.count("）")),
        "metaphor": mk,
        "metaphor_density": (mk_total / (chars / 1000)) if chars else 0.0,
        "metaphor_same": mk["まるで"] + mk["みたい"],
        "metaphor_ok": (mk_total / (chars / 1000) if chars else 0) <= METAPHOR_DENSITY_MAX
                       and (mk["まるで"] + mk["みたい"]) <= METAPHOR_SAME_MAX,
    }


def all_ok(m: dict) -> bool:
    return all(m[k] for k in ("chars_ok", "ratio_ok", "min_window_ok",
                              "dup_ok", "symbols_ok", "english_ok", "metaphor_ok",
                              "brackets_ok"))


def brackets_detail(m: dict) -> str:
    """括弧の過不足を1行で表す（show 用）。"""
    return " ".join(f"{k}{v:+d}" for k, v in m["brackets"].items() if v) or "対応OK"


def show(label: str, m: dict) -> None:
    print(f"  {label}: 文字数 {m['chars']}{'' if m['chars_ok'] else '✗'} / "
          f"セリフ {m['ratio']:.1f}%{'' if m['ratio_ok'] else '✗'} / "
          f"最小窓 {m['min_window']:.0f}%{'' if m['min_window_ok'] else '✗'} / "
          f"重複 {m['dup']}{'' if m['dup_ok'] else '✗'} / "
          f"記号 ……{m['dots']}——{m['dash']}{'' if m['symbols_ok'] else '✗'} / "
          f"英語 {m['english']}{'' if m['english_ok'] else '✗'} / "
          f"比喩 {m['metaphor_density']:.1f}{'' if m['metaphor_ok'] else '✗'}"
          f"（まるで+みたい={m['metaphor_same']}）/ "
          f"括弧 {brackets_detail(m)}{'' if m['brackets_ok'] else '✗'}")


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


def fix_brackets(text: str, log: list) -> str:
    """「」『』（）の過不足を直す（会話の閉じ忘れを1行ずつ直す）。

    修復パス（会話の置き換え）が「」を閉じ忘れる事故を実測で確認したため追加（2026-10-04）。
    実測: 生原稿は「」75/75 で対応していたのに、修復版は 70/61（9個の閉じ忘れ）になっていた。
    行ごとに開きが多い行は行末に閉じを足し、閉じが余る行は余分な閉じを外す。
    行内で解決できない不足が残った場合は、章末で帳尻を合わせず（誤修復を避けて）記録だけ残す。
    """
    pairs = (("「", "」"), ("『", "』"), ("（", "）"))
    lines = text.split("\n")
    fixed = 0
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        for open_c, close_c in pairs:
            diff = line.count(open_c) - line.count(close_c)
            if diff > 0:
                lines[idx] = line + close_c * diff
                line = lines[idx]
                fixed += diff
                log.append(f"{idx + 1}行目: {open_c}{close_c} の閉じ忘れ {diff}個 を補った")
            elif diff < 0:
                new = line
                cut = 0
                for _ in range(-diff):
                    pos = new.rfind(close_c)
                    if pos <= 0:
                        break
                    new = new[:pos] + new[pos + 1:]
                    cut += 1
                if cut:
                    lines[idx] = new
                    line = new
                    fixed += cut
                    log.append(f"{idx + 1}行目: 余分な {close_c} を {cut}個 外した")
    out = "\n".join(lines)
    left = {f"{a}{b}": out.count(a) - out.count(b) for a, b in pairs}
    if any(left.values()):
        log.append(f"括弧の対応が章全体で残った（機械では直さない）: {left}")
    if fixed:
        log.append(f"括弧の修正は合計 {fixed}箇所")
    return out


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
        # 直前・直後の文脈（プロンプトに添える）。話者が章の1文目にいる場合、text[:idx] に
        # 「。」が1つも無く `split("。")[-2]` が IndexError になる（2026-10-06 実測: これで
        # 修復が丸ごと落ち、機械的に直せていた記号の修正まで失われた）。
        prev_parts = text[:idx].split("。")
        prev_s = prev_parts[-2].strip() if len(prev_parts) >= 2 else ""
        next_s = text[idx + len(target):].split("。")[0].strip()
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


DIALOGUE_PROMPT = """次の地の文の段落は、会話が少なく読者が退屈しやすい箇所です。
この段落を、**同じ内容のまま会話（「」のやりとり）中心**に書き換えてください。

【この前後の流れ】
{scene}

【書き換える段落】
{target}

条件:
- 登場人物はこの抜粋に出ている人物だけ。新しい人物・新しい事実・新しい展開を足さない
- 会話は2〜3往復。**誰が話しているかを、最初のセリフより前に地の文（ト書き）で必ず書く**
  （例: 「〇〇が月を見たまま言った。」のように、話し手の名前か立場を出す）
- 二人目も応答の前に分かるようにする（名前、または「女房」「傍らの男」など抜粋での呼び方）。
  **誰が言ったか分からないセリフを1つも残さない**
- 話し手は交互にする（同じ人物の連続は1回まで）。独り言にしない
- 名前はこの抜粋に出てくる呼び方のまま使う（新しい名前・新しい呼び方は作らない）
- 地の文は最小限のト書きだけ（情景描写を増やさない）
- 文体・人称・語尾はこの抜粋に合わせる
- 長さは元の0.8〜1.2倍（長さを保つと、他の区画の区切りが崩れない）
- 書き換えた段落だけを返す（前後の地の文は書かない）

JSONのみ: {{"rewrite": "<書き換えた段落>"}}
"""


def _body(text: str) -> str:
    """見出し行を除いた本文（ゲートと同じ土俵）。"""
    return "\n".join(l for l in text.split("\n") if not l.strip().startswith("#"))


def block_ratios(body: str) -> list:
    """ゲートと同じ物さし: 本文を500字ずつに区切った各区画の**会話の文字割合**。

    行ベースの「会話を含む行の割合」ではなく文字ベースにした。理由:
    - ゲート（`verify_chapter_gates.py` の `min_window`）は**文字ベース**で判定する
    - 行ベースだと「短い台詞1行＋長い地の文1行」が 50% と出て、
      実際の会話の薄さを測れない（実測 2026-10-13: 第18章の修復が
      2ラウンド続けて『見送り』になり、会話が1文字も増えなかった）
    → 同じ物さしに揃えないと、修復は「改善したつもり」で何も直せない。
    """
    out = []
    for i in range(0, len(body), WINDOW):
        seg = body[i:i + WINDOW]
        if len(seg) < WINDOW * 0.5:      # 端の半端な区画は判定に入れない（ゲートと同じ規則）
            continue
        quotes = "".join(re.findall(r"「[^」]*」", seg))
        out.append(len(quotes) / len(seg) if seg else 0.0)
    return out


def weakest_block(text: str) -> tuple:
    """最も会話が薄い区画（割合, 索引, 区画の本文）。"""
    body = _body(text)
    rr = block_ratios(body)
    if not rr:
        return 0.0, 0, ""
    idx = min(range(len(rr)), key=lambda k: rr[k])
    return rr[idx], idx, body[idx * WINDOW:(idx + 1) * WINDOW]


def target_paragraph(block: str) -> str:
    """区画のうち、いちばん長い「会話を含まない段落」を選ぶ（そこを会話に置き換える）。"""
    paras = [p.strip() for p in block.split("\n") if p.strip()]
    cands = [p for p in paras if "「" not in p and "」" not in p and len(p) >= 60]
    if not cands:
        cands = [p for p in paras if "「" not in p and len(p) >= 30]
    return max(cands, key=len) if cands else ""


def insert_dialogue(text: str, log: list) -> str:
    """会話が薄い区画の地の文を、**同じ内容の会話に置き換える**。改善しなければ元に戻す。

    足すのではなく置き換えるのは、長さを保つと区画の区切りが崩れず、
    他の区画の判定を巻き込まないため（足す方式は下流の区画がずれて悪化しやすかった）。
    """
    r, idx, block = weakest_block(text)
    target = target_paragraph(block)
    if not target:
        log.append("会話に置き換えられる地の文が見つからなかったので見送り")
        return text
    at = text.find(target)
    if at < 0:
        log.append("置き換え対象の段落が見つからなかったので見送り")
        return text
    scene = text[max(0, at - 300):at + len(target) + 300]
    try:
        raw = llm.call("revision", DIALOGUE_PROMPT.format(scene=scene, target=target),
                       system=REWRITE_SYSTEM, max_tokens=900, temperature=0.5)
        new = str(llm_parse(raw).get("rewrite") or "").strip()
    except Exception as exc:                 # noqa: BLE001
        log.append(f"会話への置き換えに失敗（{exc}）")
        return text
    if not new or "「" not in new:
        log.append("会話の案が返らなかったので見送り")
        return text
    # 長さの許容幅。**地の文を会話に直すと自然に字数は減る**（地の文の描写を
    # 削って台詞にするので）。旧 0.7〜1.5倍 だと短い段落がほぼ全部弾かれ、
    # 実測（2026-10-13・第18章）では「75→130字」「75→155字」が両方見送りになり
    # 会話が1文字も増えなかった。→ 0.5〜2.5倍に広げる（大幅な変化だけ弾く）。
    if not (0.5 * len(target) <= len(new) <= 2.5 * len(target)):
        log.append(f"長さが変わりすぎたので見送り（{len(target)}→{len(new)}字）: {new[:60]}…")
        return text
    cand = text[:at] + new + text[at + len(target):]
    # 合格条件は**窓のズレに影響されない2つだけ**にする:
    #   (a) 置き換えた箇所自身が会話になった（dialogue_ratio が上がった）
    #   (b) 章全体の会話の文字数が増えた（= 地の文を会話にした分が効いている）
    #
    # なぜ古い条件をやめたか（実測 2026-10-13・第18章）:
    # 旧条件は「全体の最小窓が下がらない／薄い区画が増えない」を見ていたが、
    # 地の文を会話に直すと字数が減り **500字の窓の境界がずれる**。
    # その結果「対象の区画 17%→22%（改善）」なのに「全体の最小 17%→10%（＝別の窓が
    # 最小になっただけ）」で全部棄却され、3ラウンド続けて会話が1文字も増えなかった。
    # 窓の位置は直していないのに窓の割り当てで棄却するのは、直そうとしている
    # ものと別の物を見ていた。→ 窓ズレの影響を受けない2条件に変える。
    # 全体の最小窓（ゲートの判定そのもの）は多ラウンドで上昇していくので、
    # ここでは判定に使わず記録だけにまわす。
    b_dlg = len("".join(re.findall(r"「[^」]*」", text)))
    a_dlg = len("".join(re.findall(r"「[^」]*」", cand)))
    local_b, local_a = rc_dialogue_ratio(target), rc_dialogue_ratio(new)
    before_min, after_min = min_window_ratio(text), min_window_ratio(cand)
    if not (local_a > local_b and a_dlg > b_dlg):
        log.append(f"会話に置き換えても改善しないので見送り"
                   f"（置き換えた箇所 {local_b * 100:.0f}%→{local_a * 100:.0f}% / "
                   f"章の会話 {b_dlg:,}→{a_dlg:,}字 / "
                   f"全体の最小窓 {before_min * 100:.0f}%→{after_min * 100:.0f}%）: {new[:60]}…")
        return text
    log.append(f"地の文を会話に置き換え（箇所 {local_b * 100:.0f}%→{local_a * 100:.0f}%、"
               f"章の会話 {b_dlg:,}→{a_dlg:,}字、"
               f"全体の最小窓 {before_min * 100:.0f}%→{after_min * 100:.0f}%）: {new[:100]}…")
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

def clean_punct(text: str, log: list) -> str:
    """置換でできた記号の傷を消す（文頭の読点・連続した句読点）。

    2026-10-06 実測: 「……」を「、」に置換した結果、「、低いと思う」「。。。。」「？。。」の
    ような壊れた箇所が本文に5つ残った（未修復の原稿には0箇所）。置換の直後に必ず通す。
    """
    before = text
    text = re.sub(r"([「『（])\s*[、。]+", r"\1", text)                  # 文頭の読点・句点
    text = re.sub(r"[、。]{2,}", lambda m: "。" if "。" in m.group(0) else "、", text)
    if text != before:
        log.append("置換でできた記号の傷（文頭の読点・連続句読点）を整えた")
    return text


def safe_fix(name: str, fn, text: str, log: list) -> str:
    """1つの修復が例外で落ちても、他の修復と成果を失わない。

    2026-10-06 実測: 比喩の書き換えが IndexError で落ち、**機械的に直せていた記号の修正まで
    消えて**「修復0件」になり、試作1章が不合格になった（ゲートは検出するのに直す側が空）。
    """
    try:
        return fn(text, log)
    except Exception as exc:                     # noqa: BLE001
        traceback.print_exc()
        log.append(f"修復「{name}」で例外（{type(exc).__name__}: {exc}）— ここは飛ばして続けます")
        return text


def main() -> int:
    ap = argparse.ArgumentParser(description="章ゲートに落ちた章を直す（破棄しない）")
    ap.add_argument("path", help="章ファイル")
    ap.add_argument("--out", default="", help="出力先（既定 <名前>_repaired.md）")
    ap.add_argument("--band", default=BAND_DEFAULT, help='"lo hi" 形式の文字数帯')
    ap.add_argument("--max-rounds", type=int, default=4)
    ap.add_argument("--no-insert", action="store_true", help="会話の追加をしない")
    ap.add_argument("--dry-run", action="store_true", help="直せる箇所を数えるだけ")
    ap.add_argument("--only", default="symbol,dup,metaphor,insert",
                    help="実行する修復の種類（カンマ区切り: symbol,dup,metaphor,insert）")
    ap.add_argument("--force", action="store_true",
                    help="ゲートを通っていても修復を実行する（種類ごとの動作確認用）")
    args = ap.parse_args()

    kinds = {k.strip() for k in args.only.split(",") if k.strip()}
    lo, hi = (int(x) for x in args.band.split())
    src = Path(args.path)
    text = src.read_text(encoding="utf-8")
    m0 = measure(text, lo, hi)
    print(f"== 修復前 == {src.name}")
    show("before", m0)
    if all_ok(m0) and not args.force:
        print("（すでに全項目とおっているので何もしません）")
        return 0

    log = []
    if args.dry_run:
        print(f"\n重複文: {m0['dup']}件 {list(m0['dup_list'])[:5]}")
        print(f"記号: ……{m0['dots']} / ——{m0['dash']}  → 3個目以降を置換")
        print(f"比喩: 密度 {m0['metaphor_density']:.1f}（上限 {METAPHOR_DENSITY_MAX}）"
              f"・まるで+みたい {m0['metaphor_same']}（上限 {METAPHOR_SAME_MAX}）→ 超過分を言い換え")
        r, idx, _ = weakest_block(text)
        print(f"最小窓: {m0['min_window']:.0f}%（下限{MIN_WINDOW_MIN * 100:.0f}%）/ "
              f"最も薄い区画は索引{idx}で {r * 100:.0f}% → そこの地の文を会話に置き換え")
        return 0

    cur = text
    for rnd in range(1, max(1, args.max_rounds) + 1):
        print(f"\n-- 修復ラウンド {rnd}/{args.max_rounds} -- 種類: {', '.join(sorted(kinds))}")
        if "symbol" in kinds:
            cur = safe_fix("symbol",
                           lambda t, lg: clean_punct(fix_brackets(cut_symbols(t, lg), lg), lg),
                           cur, log)
        if "dup" in kinds:
            cur = safe_fix("dup", drop_dup_sentences, cur, log)
        if "metaphor" in kinds and not measure(cur, lo, hi)["metaphor_ok"]:
            cur = safe_fix("metaphor", rewrite_metaphor, cur, log)
        if "insert" in kinds and not args.no_insert and not measure(cur, lo, hi)["min_window_ok"]:
            cur = safe_fix("insert", insert_dialogue, cur, log)
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
