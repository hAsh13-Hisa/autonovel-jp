#!/usr/bin/env python3
"""1章を「1回の生成」で書く（分割しない版）。

使い方:
  uv run python draft_chapter_one.py 3

背景（2026-10-06 実測）:
  1,200字ずつ分割して書かせると
    (1) 各チャンクが同じ場面に戻る（第1章で同じやり取りが4回・同一の台詞が2回そのまま）
    (2) 1章あたり約40万トークン。書き手の呼び出しが18〜26回あり、毎回 world/characters/
        canon を送り直す（1回あたり約15,000トークン）ため。
  1回で書かせると、この2つが同時に直る見込み（入力1回＋出力6,000字）。
  章ゲートはLLMを使わないので、何回か試してから選抜できる（良いものを採る）。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
# .env はカレント（＝作り直し版のコピー先）を優先し、無ければリポジトリ側。
# なぜ: 作り直しの作業はコピー先で行うので、エンドポイントの設定も
# コピー先の .env を使うべき。リポジトリ側の .env は git 管理外で
# 存在しないことがある（実測 2026-10-14）。
_cwd_env = Path.cwd() / ".env"
load_dotenv(_cwd_env if _cwd_env.exists() else BASE_DIR / ".env")

from draft_chapter_split import (  # noqa: E402
    CHAPTERS_DIR, TARGET_CHAPTER, call_writer, character_names, dialogue_ratio,
    extract_beats, extract_chapter_outline, extract_plants, load_file,
    min_window_ratio, prune_sections, repeated_sentences, trim_to_sentence,
)

ATTEMPTS = int(os.environ.get("AUTONOVEL_DRAFT_ATTEMPTS", "3"))
MAX_TOKENS = int(os.environ.get("AUTONOVEL_DRAFT_MAX_TOKENS", "12000"))
MIN_CHARS = 3000


def gate_stats(path: Path) -> tuple:
    """章ゲートを回す（LLMを使わない）。(合格したか, [OK]の数, 出力) を返す。"""
    env = {**os.environ, "AUTONOVEL_GATE_WARN_ITEMS": "dialogue_ratio,min_window"}
    r = subprocess.run([sys.executable, str(BASE_DIR / "verify_chapter_gates.py"),
                        "--band", "3000 99999", str(path)],
                       capture_output=True, text=True, env=env)
    out = r.stdout or ""
    return r.returncode == 0, out.count("[OK]"), out


def build_prompt(n: int, ctx: dict) -> str:
    return f"""この小説の第{n}章を、最初から最後まで一度に書いてください。

【スタイル見本・必ずこの文体で書く】
{ctx['exemplar']}

VOICE（声の定義）：
{ctx['voice']}

WORLD（関連部分のみ）：
{ctx['w']}

CHARACTERS（関連部分のみ）：
{ctx['c']}

CANON（確定事実）：
{ctx['can']}

この章のアウトライン：
{ctx['co']}

この章のビート（必ず全部を物語に織り込む）：
{ctx['beat_list']}

伏線の植え込み：
{ctx['plants']}

{ctx['prev_block']}
執筆指示：
1. この章の本文を最初から最後まで書く。長さは約{TARGET_CHAPTER}字（{MIN_CHARS}字以上、1万字は超えない）。
2. 【最重要】ライトノベル。台詞（会話文）が本文の約70%、地の文が約30%。地の文は台詞の間に1〜2文で、各文は長め（30〜50字）に書く。
3. 【同じことを繰り返さない】同じやり取り・同じ台詞・同じ情景を二度書かないこと。話を前に進めること。章の後半では必ず状況が変わること（新しい人物・新しい場所・新しい事件）。
4. 動作・観察・心理も、地の文で説明せず、キャラクターの台詞・問いかけ・独白で語ること。
5. 章として起承転結を通す。中盤に山を作り、最後はこの章の結末を書く（次章への引きを残す）。
6. 台詞は characters の話し方に従い、問いかけ・相槌・反論・驚きを入れて弾ませる。
7. 禁止語・AI定型句は使わない。文の長さを変え、長文（25〜50字）を主体にする。
8. 「——」は章全体で1回まで。「……」は章全体で2回まで。間は句点・読点・短い動作や台詞で表すこと。
9. 比喩（まるで／〜ように・ような・ようだ／みたい／あたかも／ごとく）は使いすぎないこと。1000字あたり3.0以下に収め、「まるで」と「みたい」の同型の繰り返しは4回以下にすること。
10. 比喩や動作で「地の文が500字以上続く区間」を作らないこと（会話を絶やさない）。

今すぐこの章の本文を書いてください。本文は必ず日本語で書いてください。
"""


def main() -> int:
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
        for w in re.findall(r"[\u4e00-\u9fff]{2,}", b):
            if w not in kws:
                kws.append(w)
    kws = kws[:40]

    prev_block = ""
    prev_txt = ""
    if n > 1:
        p = CHAPTERS_DIR / f"ch_{n - 1:02d}.md"
        if p.exists():
            prev_txt = p.read_text(encoding="utf-8")
            prev_block = ("【前章の末尾（この続きから始める。前章と同じ台詞・同じやり取りを"
                          f"繰り返さない）】\n{prev_txt[-1500:]}\n")

    ctx = {
        "exemplar": exemplar, "voice": voice,
        "w": prune_sections(world, kws), "c": prune_sections(chars, kws),
        "can": prune_sections(canon, kws, 5000),
        "co": co, "beat_list": "\n".join(f"{i}. {b}" for i, b in enumerate(beats, 1)),
        "plants": plants, "prev_block": prev_block,
    }
    prompt = build_prompt(n, ctx)

    tmp = CHAPTERS_DIR / f"ch_{n:02d}_try.md"
    CHAPTERS_DIR.mkdir(exist_ok=True)
    best = None          # (key, text, attempt)
    for attempt in range(1, ATTEMPTS + 1):
        print(f"章の試行 {attempt}/{ATTEMPTS}（1回生成）...", file=sys.stderr)
        text = trim_to_sentence(call_writer(prompt, max_tokens=MAX_TOKENS))
        tmp.write_text(text, encoding="utf-8")
        gate_ok, passed, gate_out = gate_stats(tmp)
        reps = repeated_sentences(text, prev_txt) if prev_txt else []
        ratio, mw = dialogue_ratio(text), min_window_ratio(text)
        print(f"  試行{attempt}: {len(text)}字 セリフ{ratio * 100:.0f}% 最小窓{mw * 100:.0f}% "
              f"ゲート{'合格' if gate_ok else f'{passed}項目OK'} 前章と丸かぶり{len(reps)}件",
              file=sys.stderr)
        key = (gate_ok, passed, len(text) >= MIN_CHARS and not reps, round(mw, 3), round(ratio, 3))
        if best is None or key > best[0]:
            best = (key, text, attempt)
        if gate_ok and len(text) >= MIN_CHARS and not reps:
            break

    out = CHAPTERS_DIR / f"ch_{n:02d}.md"
    out.write_text(best[1], encoding="utf-8")
    if tmp.exists():
        tmp.unlink()
    print(f"Saved {out}（試行{best[2]}を採用・{len(best[1])}字）", file=sys.stderr)
    print(best[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
