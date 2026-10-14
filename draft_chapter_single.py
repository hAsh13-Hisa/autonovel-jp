#!/usr/bin/env python3
"""
Single-call chapter draft (whole chapter in one LLM call, pruned context).
Usage: python draft_chapter_single.py 1
"""
import sys, re, statistics
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
# .env はカレント（＝作り直し版のコピー先）を優先し、無ければリポジトリ側。
# なぜ: 作り直しの作業はコピー先で行うので、エンドポイントの設定も
# コピー先の .env を使うべき。リポジトリ側の .env は git 管理外で
# 存在しないことがある（実測 2026-10-14）。
_cwd_env = Path.cwd() / ".env"
load_dotenv(_cwd_env if _cwd_env.exists() else BASE_DIR / ".env")
import llm

CHAPTERS_DIR = BASE_DIR / "chapters"


def load_file(path):
    try:
        return Path(path).read_text()
    except FileNotFoundError:
        return ""


def call_writer(prompt, max_tokens=16000):
    return llm.call_writer(
        prompt,
        system="あなたはファンタジー小説の章を執筆する文芸小説家です。三人称限定・過去形で書き、一人のPOVキャラクターに固定します。声の定義に正確に従い、アウトラインのビートをすべて押さえます。禁止語は使わず、感情は「見せる」だけで「言いません」。散文は滑らかで読みやすく、1文に動作・状況・心理をまとめます。文の長さを変えます。読者を信じます。章の全文を書きます——切り詰めたり要約したりしません。本文は必ず日本語で書いてください。",
        max_tokens=max_tokens,
        temperature=0.8,
    )


def _split_sections(text, pattern):
    lines = text.split("\n")
    out = []
    cur_h, cur_b = "", []
    for line in lines:
        if re.match(pattern, line):
            if cur_h or cur_b:
                out.append((cur_h, "\n".join(cur_b)))
            cur_h, cur_b = line, []
        else:
            cur_b.append(line)
    if cur_h or cur_b:
        out.append((cur_h, "\n".join(cur_b)))
    return out


def extract_chapter_outline(text, n):
    m = re.search(rf'## 第{n}章.*?(?=## 第{n + 1}章|## 前半|## 幕構成|## 伏線台帳|$)', text, re.DOTALL)
    return m.group(0).strip() if m else "(not found)"


def extract_beats(co):
    b = re.search(r'- \*\*ビート：\*\*(.*?)(?=- |## |\Z)', co, re.DOTALL)
    if not b:
        return []
    return [m.group(1).strip() for m in re.finditer(r'^\s*(\d+)\.\s+(.+)$', b.group(1), re.MULTILINE)]


def extract_plants(co):
    m = re.search(r'- \*\*植え込み：\*\*(.*?)(?=- \*\*回収|- \*\*|## |\Z)', co, re.DOTALL)
    return m.group(1).strip() if m else "(none)"


def prune_sections(text, keywords, max_chars=9000):
    secs = _split_sections(text, r"^#{2,3}\s")
    wanted = [h + "\n" + b for h, b in secs if any(k in (h + b) for k in keywords)]
    res = "\n\n".join(wanted)
    if res.strip():
        return res[:max_chars]
    return text[:max_chars]


def character_names(char_text):
    return [
        h.replace("##", "").replace("###", "").strip().split("（")[0].split("(")[0]
        for h, _ in _split_sections(char_text, r"^##\s")
        if h.strip()
    ]


def main():
    n = int(sys.argv[1])
    outline = load_file(BASE_DIR / "outline.md")
    world = load_file(BASE_DIR / "world.md")
    chars = load_file(BASE_DIR / "characters.md")
    canon = load_file(BASE_DIR / "canon.md")
    voice = load_file(BASE_DIR / "voice.md")
    exemplar = load_file(BASE_DIR / "style_exemplar.md")

    co = extract_chapter_outline(outline, n)
    beats = extract_beats(co)
    plants = extract_plants(co)

    kws = []
    for nm in character_names(chars):
        if nm and nm in co:
            kws.append(nm)
    for b in beats:
        for w in re.findall(r'[\u4e00-\u9fff]{2,}', b):
            if w not in kws:
                kws.append(w)
    kws = kws[:40]

    prev_txt = "(第1章・前章なし)"
    if n > 1:
        p = CHAPTERS_DIR / f"ch_{n - 1:02d}.md"
        if p.exists():
            t = p.read_text()
            prev_txt = f"(前章の末尾) {t[-2000:]}"

    w = prune_sections(world, kws)
    c = prune_sections(chars, kws)
    can = prune_sections(canon, kws, 5000)
    beat_list = "\n".join(f"{i}. {b}" for i, b in enumerate(beats, 1))

    def build_prompt():
        return f"""この小説の第{n}章の全文を、丸ごと一回で書いてください。

【スタイル見本・必ずこの文体で書く】
{exemplar}

VOICE（声の定義）：
{voice}

WORLD（関連部分のみ）：
{w}

CHARACTERS（関連部分のみ）：
{c}

CANON（確定事実）：
{can}

この章のアウトライン：
{co}

この章のビート（必ず全部を物語に織り込む）：
{beat_list}

植え込み（伏線）：
{plants}

{prev_txt}

執筆指示：
1. 章の全文を最後まで書く。5,000〜7,000字程度。
2. 【最重要】ライトノベル。台詞（会話文）が全体の約70%、地の文が約30%。地の文は少なめ（台詞の間に1〜2文）だが、各文は滑らかで長め（30〜50字）に書く。断片文（2〜5字）を連発しないこと。
3. 【会話を途切れさせない】章の冒頭から最後の一文まで、会話を続けること。地の文が3文以上続いたら、必ず台詞を挟むこと。どの場面でも、地の文ばかりの長い区間（500字以上）を作らないこと。500字ごとに必ず台詞が入るようにすること。
4. 【終盤ほど台詞を増やす】章の後半・終盤（最後の3分の1）は、特に台詞を意識的に増やすこと。終盤で地の文が長く続いたら、必ずキャラクターの台詞・問いかけ・独白を挟んで、会話を最後まで維持すること。
5. 地の文は動作＋状況＋心理を1文にまとめ、滑らかに読ませる。
6. 説明は語り手がするのではなく、キャラクターの会話で伝えること。動作・観察・心理も、地の文で説明するのではなく、キャラクターの台詞・問いかけ・独白で語ること。
7. 台詞は問いかけ・相槌・反論・驚きを入れて生き生きと弾ませる。
8. 台詞は characters の話し方に従う。
9. 禁止語・AI定型句は使わない。
10. ビートをすべて押さえつつ、文が滑らかにつながるように書く。
11. 文の長さを変え、長文（25〜50字）を主体にする。

今すぐこの章の全文を書いてください。本文は必ず日本語で書いてください。
"""

    def evaluate(text):
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if not lines:
            return {"dialogue": 0.0, "prose_avg": 0.0, "frag": 1.0, "min_window": 0.0, "ok": False}
        dia = sum(1 for l in lines if "「" in l or "」" in l) / len(lines)
        prose = []
        for l in lines:
            if "「" in l or "」" in l:
                continue
            prose.extend([s for s in re.split(r"[。！？]", l) if s.strip()])
        avg = statistics.mean(len(s) for s in prose) if prose else 0.0
        frag = sum(1 for s in prose if len(s) <= 15) / len(prose) if prose else 1.0
        # 章内の「後半崩れ」検出：500字ごとの窓でセリフ比率を監視
        body = "\n".join(l for l in text.split("\n") if not l.strip().startswith("#"))
        win = 500
        win_ratios = []
        for i in range(0, len(body), win):
            seg = body[i:i + win]
            sl = [l.strip() for l in seg.split("\n") if l.strip()]
            if not sl:
                continue
            win_ratios.append(sum(1 for l in sl if "「" in l or "」" in l) / len(sl))
        min_window = min(win_ratios) if win_ratios else 0.0
        ok = dia >= 0.55 and avg >= 16 and frag < 0.5 and min_window >= 0.55
        return {"dialogue": dia, "prose_avg": avg, "frag": frag, "min_window": min_window, "ok": ok}

    best = None
    for attempt in range(1, 4):
        print(f"執筆中: 第{n}章（一括生成・試行{attempt}/3）...", file=sys.stderr)
        result = call_writer(build_prompt())
        q = evaluate(result)
        print(f"  品質: セリフ{q['dialogue']*100:.0f}% 地文{q['prose_avg']:.0f}字 断片{q['frag']*100:.0f}% 最小窓{q['min_window']*100:.0f}% -> {'OK' if q['ok'] else '再試行'}", file=sys.stderr)
        if best is None or len(result) > len(best):
            best = result
        if q["ok"]:
            break

    out = CHAPTERS_DIR / f"ch_{n:02d}.md"
    out.write_text(best)
    print(f"Saved {out}", file=sys.stderr)
    print(f"Chars: {len(best)}", file=sys.stderr)
    print(best)


if __name__ == "__main__":
    main()
