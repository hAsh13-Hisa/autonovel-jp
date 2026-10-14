#!/usr/bin/env python3
"""
Draft a single chapter using the writer model.
Usage: python draft_chapter.py 1
"""
import os
import re
import sys
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

def call_writer(prompt, max_tokens=16000):
    return llm.call_writer(
        prompt,
        system="あなたはファンタジー小説の章を執筆する文芸小説作家です。三人称限定・過去形で書き、一人のPOVキャラクターに固定します。声の定義に正確に従います。アウトラインのすべてのビートを押さえます。禁止リストの言葉は決して使いません。感情は「見せる」だけで「言いません」。散文は具体的で感覚的で接地しています。比喩はキャラクターの経験に由来します。文の長さを変化させます。読者を信じます。章の全文を書きます——切り詰めたり、要約したり、先へ飛ばしたりしません。章の本文は必ず日本語で書いてください。",
        max_tokens=max_tokens,
        temperature=0.8,
    )


def load_file(path):
    try:
        return Path(path).read_text()
    except FileNotFoundError:
        return ""

def extract_chapter_outline(outline_text, chapter_num):
    """Extract a specific chapter's outline entry (Japanese format: ### 第N章)."""
    pattern = rf'###\s*第{chapter_num}章.*?(?=###\s*第{chapter_num + 1}章|##\s*伏線台帳|##\s*Foreshadowing|$)'
    match = re.search(pattern, outline_text, re.DOTALL)
    return match.group(0).strip() if match else "(not found)"

def extract_next_chapter_outline(outline_text, chapter_num):
    """Extract the next chapter's outline (just first few lines for continuity)."""
    next_entry = extract_chapter_outline(outline_text, chapter_num + 1)
    if next_entry == "(not found)":
        return "(final chapter)"
    lines = next_entry.split('\n')[:10]
    return '\n'.join(lines)

def main():
    chapter_num = int(sys.argv[1])

    # Load all context
    voice = load_file(BASE_DIR / "voice.md")
    world = load_file(BASE_DIR / "world.md")
    characters = load_file(BASE_DIR / "characters.md")
    outline = load_file(BASE_DIR / "outline.md")
    canon = load_file(BASE_DIR / "canon.md")

    # Chapter-specific context
    chapter_outline = extract_chapter_outline(outline, chapter_num)
    next_chapter = extract_next_chapter_outline(outline, chapter_num)

    # Previous chapter (if exists)
    prev_path = CHAPTERS_DIR / f"ch_{chapter_num - 1:02d}.md"
    if prev_path.exists():
        prev_text = prev_path.read_text()
        prev_tail = prev_text[-2000:] if len(prev_text) > 2000 else prev_text
    else:
        prev_tail = "(first chapter -- no previous)"

    prompt = f"""この小説の第{chapter_num}章を書いてください。タイトルと設定はアウトラインに従うこと。

VOICE DEFINITION（声の定義。これに正確に従うこと）：
{voice}

THIS CHAPTER'S OUTLINE（この章のアウトライン。すべてのビートを押さえること）：
{chapter_outline}

NEXT CHAPTER'S OUTLINE（続きのため。この章を次に続くように終わらせること）：
{next_chapter}

PREVIOUS CHAPTER'S ENDING（ここから続けること）：
{prev_tail}

WORLD BIBLE（世界構築の詳細の参照）：
{world}

CHARACTER REGISTRY（話し方と行動の参照）：
{characters}

CANON（確定事実。これに矛盾しないこと）：
{canon}

執筆指示：
1. 章の全文を書くこと。目標は約3,200語。切り詰めたり要約したりしないこと。
2. 三人称限定・過去形、アウトラインで指定された POV キャラクターに固定。
3. アウトラインの番号付きビートをすべて順番に踏むこと。
4. 「植え込み」に列挙されたすべての伏線要素を植えること。
5. 感覚の細部を見せること：POV キャラクターが聞くもの、嗅ぐもの、身体で感じるもの。
6. この世界の魔法体系の代償は具体的な身体・感覚の結果を伴う（ぼんやりした不快ではなく）。
7. 台詞は characters.md で定義された話し方に従うこと。
8. voice.md 第1部のガードレールからの禁止語は使わないこと。
9. AI小説定型表現は使わないこと：「a sense of」「couldn't help but feel」「eyes widened」は禁止。
10. 文の長さを変えること。効果のために短い文、構築するために長い文。
11. 比喩は POV キャラクターの経験から：彼女／彼の世界、仕事、身体の感覚に由来させる。
12. 読者を信じること。シーンの意味を説明しないこと。伝わるままにさせること。
13. 章を説明ではなく場面で始めること。要約ではなく一瞬で終えること。

避けるべきパターン（前の章で指摘済み）：
14. 三つ組の感覚リスト禁止。決して「X。Y。Z。」や、続く三つの別々の項目としての「XとYとZ」を
    使わないこと。二つに統合するか、一つを切るか、再構成する。
15. 章ごとに「彼は[動詞]しなかった」は1回まで。否定を積極的な代替に変えるか、ただ切る。
16. 「彼は[X]について考えた」という構文禁止。代わりに：断片としての思考そのもの、
    身体の動作、または台詞。
17. 章ごとに比喩の接続詞として「[X]が[Y]するように」は2回まで。別の比喩構造を使うか、比較を切る。
18. 示した後に説明しすぎないこと。場面が何かを実演したなら、語り手が言い直さないこと。場面を信じる。
19. リズムの頼りとしての区切り（---）禁止。本物の時間・場所の跳躍にだけ使うこと。章あたり最大2回。
20. 段落の長さを意図的に変えること。似た長さの段落を3つ連続させないこと。1〜2文の段落を
    少なくとも1つ、6文以上の段落を少なくとも1つ含めること。
21. 前の章とは違う終わり方にすること。この章に固有の結末を見つけること。
22. 驚く瞬間を少なくとも1つ含めること——キャラクターが間違ったことを言う、感情のビートが
    早くまたは遅く来る、予想のパターンに合わない細部。予測可能な卓越はやはり予測可能だ。
23. 要約より場面を優先すること。章の少なくとも70%は場面（瞬間ごと、台詞と行動）であるべき。
    要約（語り手が時間を圧縮する）ではないこと。
24. 台詞は散文ではなく話し言葉に聞こえるべき。キャラクターは時々つまずき、遮り、言葉を
    途中で切り、わずかに間違ったことを言うべき。

今すぐ章を書いてください。完全な本文、始めから終わりまで。本文は必ず日本語で書いてください。
"""

    print(f"草稿を執筆中: 第{chapter_num}章...", file=sys.stderr)
    result = call_writer(prompt)

    # Save
    out_path = CHAPTERS_DIR / f"ch_{chapter_num:02d}.md"
    out_path.write_text(result)
    print(f"Saved to {out_path}", file=sys.stderr)
    print(f"Word count: {len(result.split())}", file=sys.stderr)
    print(result)

if __name__ == "__main__":
    main()
