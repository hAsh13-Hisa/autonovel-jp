#!/usr/bin/env python3
"""
Length-based split chapter draft (draft_chapter_split.py).

Generates a chapter in ~1200-char chunks. Each chunk is given the FULL
accumulated text as context (so voice/flow is preserved across the whole
chapter), and each chunk is gated on dialogue density (>=55%) with retry
before moving on. Finally the stitched chapter is checked with the same
min-500-char-window gate (>=55%) used by draft_chapter_single.py.

This addresses the single-call "tail degradation" (prose-heavy stretches)
by keeping every generation short enough that the model maintains dialogue.

Usage: python draft_chapter_split.py 1
"""
import sys, re, os, statistics
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

CHUNK_TARGET = int(os.environ.get("AUTONOVEL_CHUNK_TARGET", "1200"))  # target chars per chunk
CHUNK_MIN = 1000
CHUNK_MAX = 1500
DIALOGUE_GATE = 0.50     # チャンクごとの再試行ライン（会話の文字割合。ゲートの合格線と揃える）
CHAPTER_MW_OK = 0.30     # 章の早期打ち切りライン（最も薄い500字区画の会話割合）
#   2026-10-06 実測: 会話の文字割合は 全体 43.7〜52.3%／最も薄い500字区画 22〜35%。
#   以前は行ベースで測っていたため「55%」が達成不能〜別物の数字になっていた。
MAX_CHUNK_RETRY = int(os.environ.get("AUTONOVEL_CHUNK_RETRY", "3"))
MAX_CHAPTER_RETRY = int(os.environ.get("AUTONOVEL_CHAPTER_RETRY", "3"))
#   2026-10-06 実測: 1章あたりの書き手呼び出しは 3回試行×4〜5チャンク×2再試行＝最大26回で
#   約40〜53万トークン。24章だと週の枠を超える。試行を1回に絞ると 8〜12万トークン/章。
TARGET_CHAPTER = 6000    # target total chapter chars


def load_file(path):
    try:
        return Path(path).read_text()
    except FileNotFoundError:
        return ""


def call_writer(prompt, max_tokens=6000):
    return llm.call_writer(
        prompt,
        system="あなたはファンタジー小説の章を執筆する文芸小説家です。三人称限定・過去形で書き、一人のPOVキャラクターに固定します。声の定義に正確に従い、アウトラインのビートをすべて押さえます。禁止語は使わず、感情は「見せる」だけで「言いません」。散文は滑らかで読みやすく、1文に動作・状況・心理をまとめます。文の長さを変えます。読者を信じます。本文は必ず日本語で書いてください。",
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


def dialogue_ratio(text):
    """会話（「」で囲まれた部分）が本文の何割か（**文字ベース**）。

    2026-10-06 修正: 以前は「『」を含む行の割合」で見ていたため、実測で**同じ本文が
    行ベース 71.7%／文字ベース 50.2%** と食い違っていた。ドラフターは 55% を超えたと
    判断して早々に打ち切り、章の会話量が仕様（約70%）より低いまま止まっていた。
    ゲートの「セリフ比(文字ベース)」と同じものさしに揃える。
    """
    body = text.strip()
    if not body:
        return 0.0
    return len("".join(re.findall(r"「[^」]*」", body))) / len(body)


def min_window_ratio(text, win=500):
    """500字ごとの区画で、会話が最も薄い区画の割合（**文字ベース**）。

    2026-10-06 修正: 以前は (1) 行の割合で見ており、(2) 端の半端な区画も判定に入れていた。
    そのため末尾が地の文で終わる章は**必ず 0%** になり（実測: 会話の無い最長区間が122字の
    章でも 0%）、ドラフターの章の採否も誤っていた。
    """
    # 2026-10-17 修正: 改行・空白も1文字として数えていたため、段落の空行が多い章ほど
    # 窓が低く出ていた（本文が同じでも整形だけで数字が動く＝物差しの側の不具合）。
    # 空白を除いた本文で測り、整形に依存しないようにする。
    body = re.sub(r"[\r\n\t ]+", "",
                  "\n".join(l for l in text.split("\n") if not l.strip().startswith("#")))
    vals = []
    for i in range(0, len(body), win):
        seg = body[i:i + win]
        if len(seg) < win * 0.5:          # 端の半端な区画は判定に入れない
            continue
        vals.append(dialogue_ratio(seg))
    return min(vals) if vals else 0.0


def dialogue_lines(text, min_len=8):
    """本文中の台詞（「」の中身）で min_len 字以上のものを登場順に返す（同型は1つ）。"""
    out, seen = [], set()
    for m in re.finditer(r"「([^」]{%d,})」" % min_len, text):
        s = m.group(1).strip()
        if s[:14] in seen:
            continue
        seen.add(s[:14])
        out.append(s[:40])
    return out


def repeated_sentences(new_text, prev_text, min_len=8):
    """生成した本文の中に、それ以前と丸かぶりする文（min_len 字以上）があれば返す。

    2026-10-06 実測: 1,200字ずつの分割生成では、各チャンクが前の場面に戻ってしまう
    （第1章で同じやり取りが4回、同一の台詞が2回）。生成直後に検出して書き直させる。
    """
    prev = re.sub(r"\s+", "", prev_text)
    hits = []
    for s in re.split(r"(?<=[。！？」])", re.sub(r"\s+", "", new_text)):
        s = s.strip()
        if len(s) >= min_len and s in prev:
            hits.append(s[:40])
    return hits


def denial_lines(text):
    """否定・拒絶の型の台詞（「そんなわけない」「ありえない」「正気か」…）を返す。

    2026-10-06 実測: 第3章で11回。話は進むのに同じ反応で止まるため、章が反復に感じられた
    （実際に読んだ感想＝「芯は良いが、編集者が最初に削る部分が丸ごと残っている」）。
    """
    return [l.strip() for l in text.split("\n") if l.strip()
            and re.search(r"そんな(わけ|こと|もん)|ありえない|あり得ない|正気|嘘(つけ|だろ)|"
                          r"馬鹿な|やめろ|冗談|はずがな|わけがな", l)]


def trim_to_sentence(text):
    """Trim trailing partial sentence so a chunk ends at a natural break."""
    text = text.rstrip()
    for m in re.finditer(r"[。」」！？]", text):
        pass
    last = max(text.rfind("。"), text.rfind("」"), text.rfind("！"), text.rfind("？"))
    if last > len(text) * 0.5:
        return text[:last + 1]
    return text


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

    num_chunks = max(4, round(TARGET_CHAPTER / CHUNK_TARGET))

    def build_chunk_prompt(i, accumulated, is_last):
        ending = ("このチャンクで章を締めくくること。この章のビートをすべて回収し、章の結末を書くこと。"
                  if is_last else
                  "このチャンクは章の途中なので、結末を書かないこと。物語を前進させること。")
        used = dialogue_lines(accumulated)
        used_block = ("【すでに使った台詞（同じ台詞・同じやり取りを繰り返さないこと。話を前に進める）】\n"
                      + "\n".join(f"・{u}" for u in used[-12:]) + "\n") if used else ""
        # チャンクごとに「役割」を割り当てる。2026-10-06 実測: 全部のチャンクが
        # 「主人公が何かを見る→相棒が否定する」の同じ型に戻り、ある章では否定の台詞が
        # 11回・「主人公は」の地の文が18回あった（＝編集者が最初に削る部分）。
        # ※作品名・登場人物名は書かない（公開予定のリポジトリなので匿名表現にする）。
        if i == 1:
            role = ("【このチャンクの役割：導入】場所と状況を具体的に描き、人物を動かして始める。"
                    "読者が先を読みたくなる小さな異変を1つ入れる。")
        elif i == num_chunks:
            role = ("【このチャンクの役割：結末】クライマックスの結果と代償を書く。人物が何かを失うか、"
                    "何かが決定的に変わること。次章への引きを1文残して終える。")
        elif i == num_chunks - 1:
            role = "【このチャンクの役割：山】主人公が決断し、実行する。ここで対立が最大になる。"
        else:
            role = ("【このチャンクの役割：展開】新しい出来事・新しい人物・新しい場所のいずれかを"
                    "必ず1つ入れて話を前へ進める。前のチャンクと同じやり取りを繰り返さない。")
        return f"""この小説の第{n}章の、チャンク{i}/{num_chunks}を書いてください。

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

{role}

{prev_txt}

【ここまでに書いた本文（この直後から続ける。既に書いたことを繰り返さない）】
{accumulated}

執筆指示：
1. このチャンクの本文だけを書く。約1,200字（1,000〜1,500字）。
2. 【最重要】ライトノベル。台詞（会話文）がこのチャンクの約70%、地の文が約30%。地の文は少なめ（台詞の間に1〜2文）だが、各文は滑らかで長め（30〜50字）に書く。断片文（2〜5字）を連発しないこと。
3. 【会話を途切れさせない】このチャンクの冒頭から最後の一文まで、会話を続けること。地の文が3文以上続いたら、必ず台詞を挟むこと。地の文ばかりの長い区間（500字以上）を作らないこと。
4. 動作・観察・心理も、地の文で説明するのではなく、キャラクターの台詞・問いかけ・独白で語ること。
5. 既に書いた本文の直後から自然に続けること。既に起きたことを繰り返さない。前のチャンクと同じ台詞・動作・比喩を使わない。
6. このチャンクの末尾は、自然な文の切れ目（「。」や「」」）で終えること。途中で切らない。
7. 台詞は問いかけ・相槌・反論・驚きを入れて生き生きと弾ませる。
8. 台詞は characters の話し方に従う。
9. 禁止語・AI定型句は使わない。
10. 文の長さを変え、長文（25〜50字）を主体にする。
11. {ending}
12. 「——」はこの章全体で1回までにすること（台詞冒頭のダッシュも含めて数える）。多用しないこと。自然な言い回しで始めること。
13. 「……」はこの章全体で2回までにすること。間や沈黙を「……」で表す書き方を繰り返さないこと。間は句点・読点・短い動作や台詞で表すこと。
14. 比喩（まるで／〜ように・ような・ようだ／みたい／あたかも／ごとく）は使いすぎないこと。1000字あたり3.0以下に収め、「まるで」と「みたい」の同型の繰り返しは4回以下にすること。比喩を重ねず、動作・台詞・具体物で書くこと。
15. 【反応を繰り返さない】「そんなわけない」「ありえない」「正気か」と否定する台詞は、この章で2回まで。同じ反応で話を止めず、代わりに行動・提案・別の目的・突き放しで反応させること。
16. 【人物を動かす】この章の中盤で、対立する人物の立場が必ず変わること（最後まで否定し続ける人物を書かない）。
17. 【同じ動作を繰り返さない】「呆れる」「わざとらしく」「肩をすくめる」「首を振る」「耳を塞ぐ」を同じ章で2回以上使わないこと。

{used_block}今すぐこのチャンクの本文を書いてください。本文は必ず日本語で書いてください。
"""

    best_full = None
    best_mw = 0.0
    for chapter_attempt in range(1, MAX_CHAPTER_RETRY + 1):
        print(f"章の試行 {chapter_attempt}/{MAX_CHAPTER_RETRY}（{num_chunks}チャンク）...", file=sys.stderr)
        accumulated = ""
        chunk_results = []
        for i in range(1, num_chunks + 1):
            is_last = (i == num_chunks)
            prompt = build_chunk_prompt(i, accumulated, is_last)
            best_chunk = None
            best_ratio = 0.0
            for retry in range(1, MAX_CHUNK_RETRY + 1):
                print(f"  チャンク{i}/{num_chunks} 試行{retry}/{MAX_CHUNK_RETRY}...", file=sys.stderr)
                result = call_writer(prompt)
                result = trim_to_sentence(result)
                ratio = dialogue_ratio(result)
                reps = repeated_sentences(result, accumulated)
                dens = denial_lines(result)
                if best_chunk is None:
                    best_chunk, best_ratio = result, ratio
                elif len(result) < 0.7 * len(best_chunk):
                    pass          # 短すぎる案は採らない（章の文字数が痩せるため）
                elif (ratio, len(result)) > (best_ratio, len(best_chunk)):
                    # 採用は「セリフ比」で決める。2026-10-06 修正: 以前は最長のものを採っていた
                    # ため、セリフの薄い再試行が勝ち、章全体が 43.7% まで落ちた（実測）。
                    best_chunk, best_ratio = result, ratio
                if reps:
                    print(f"    前と丸かぶりの文 {len(reps)}件（例: {reps[0]}）→ 書き直し", file=sys.stderr)
                if len(dens) >= 3:
                    print(f"    否定の台詞が{len(dens)}回（上限2回）→ 書き直し", file=sys.stderr)
                print(f"    セリフ{ratio*100:.0f}% 長さ{len(result)}字 -> "
                      f"{'OK' if ratio >= DIALOGUE_GATE and not reps and len(dens) < 3 else '再試行'}",
                      file=sys.stderr)
                if ratio >= DIALOGUE_GATE and not reps and len(dens) < 3:
                    break
            chunk_results.append(best_chunk)
            accumulated = "\n".join(chunk_results)
            print(f"  チャンク{i}確定: セリフ{best_ratio*100:.0f}% 累計{len(accumulated)}字", file=sys.stderr)

        full = "\n".join(chunk_results)
        mw = min_window_ratio(full)
        print(f"章の試行{chapter_attempt} 完了: 最小窓{mw*100:.0f}% 総{len(full)}字 -> {'OK' if mw >= DIALOGUE_GATE else '再試行'}", file=sys.stderr)
        if best_full is None or mw > best_mw:
            best_full = full
            best_mw = mw
        if mw >= CHAPTER_MW_OK:
            break

    out = CHAPTERS_DIR / f"ch_{n:02d}.md"
    out.write_text(best_full)
    print(f"Saved {out}", file=sys.stderr)
    print(f"Chars: {len(best_full)}  MinWindow: {best_mw*100:.0f}%", file=sys.stderr)
    print(best_full)


if __name__ == "__main__":
    main()
