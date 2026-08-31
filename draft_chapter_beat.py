#!/usr/bin/env python3
"""
Draft a single chapter by generating each outline BEAT separately.
This solves the "good prose but boring story" problem: each beat is
generated as its own short unit (no late-chapter degradation), but the
beats are chained so the story's causality is preserved.

Usage: python draft_chapter_beat.py 1
"""
import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

import llm

CHAPTERS_DIR = BASE_DIR / "chapters"


def call_writer(prompt, max_tokens=6000):
    return llm.call_writer(
        prompt,
        system="あなたはファンタジー小説の章を執筆する文芸小説作家です。三人称限定・過去形で書き、一人のPOVキャラクターに固定します。声の定義に正確に従います。アウトラインのビートを押さえます。禁止リストの言葉は決して使いません。感情は「見せる」だけで「言いません」。散文は具体的で感覚的で接地しています。比喩はキャラクターの経験に由来します。文の長さを変化させます。読者を信じます。このビートの本文を書きます——切り詰めたり、要約したり、先へ飛ばしたりしません。本文は必ず日本語で書いてください。",
        max_tokens=max_tokens,
        temperature=0.8,
    )


def load_file(path):
    try:
        return Path(path).read_text()
    except FileNotFoundError:
        return ""


def extract_chapter_outline(outline_text, chapter_num):
    """Extract a specific chapter's outline entry."""
    pattern = rf'## 第{chapter_num}章.*?(?=## 第{chapter_num + 1}章|## 前半|## 幕構成|## 伏線台帳|## Foreshadowing|$)'
    match = re.search(pattern, outline_text, re.DOTALL)
    return match.group(0).strip() if match else "(not found)"


def extract_beats(chapter_outline):
    """Extract the numbered beats from a chapter outline."""
    beats = []
    # Beats are the numbered list items under "- **ビート：**"
    beat_section = re.search(r'- \*\*ビート：\*\*(.*?)(?=-\s|\Z)', chapter_outline, re.DOTALL)
    if not beat_section:
        return beats
    for m in re.finditer(r'^\s*(\d+)\.\s+(.+)$', beat_section.group(1), re.MULTILINE):
        beats.append(m.group(2).strip())
    return beats


def extract_plants(chapter_outline):
    """Extract the plants (伏線) list from a chapter outline."""
    m = re.search(r'- \*\*植え込み：\*\*(.*?)(?=- \*\*回収|-\s*\*\*|## |\Z)', chapter_outline, re.DOTALL)
    return m.group(1).strip() if m else "(none)"


def build_beat_role(i, total, is_first, is_last):
    """Assign each beat a UNIQUE narrative function so the story advances.

    Every beat must introduce at least one NEW element (information,
    character, turn, escalation, or hook) rather than restating the same
    theme.
    """
    if is_first:
        return (
            "このビートは章の冒頭。読者を世界に引き込み、POVキャラクターの現状と特異な才能を確立する。"
            "ここで新しい要素：POVキャラクターの日常と、世界の異常・謎を読者に示す。"
            "ただし、このビートで結論を出し切らないこと。"
        )
    if is_last:
        return (
            "このビートは章の結末。章の緊張を収束させつつ、次章への引っ掛かり（フック）を残す。"
            "ここで新しい要素：章の最後に、読者が「それで？」ではなく「次はどうなる？」と思う"
            "小さな異常・謎・予感を1つ置く。既に述べた結論を繰り返さない。"
        )

    # Middle beats: each gets a distinct narrative function by position
    middle_roles = {
        2: (
            "このビートは章の中盤。新しい要素：POVキャラクターの身近な人物（家族・同輩）の「異常」を、"
            "単なる不安ではなく具体的なものとして描く。その人物の秘密の伏線を自然に1つ植える。"
            "POVキャラクターが気づくが、相手が隠す、という緊張を作る。"
        ),
        3: (
            "このビートは章の中盤。新しい要素：最初の異常が「広がっている」ことを示す。"
            "前のビートで見た異常が、別の場所でも起きていることを通して、問題の規模が拡大していることを"
            "読者に伝える。新しい情報を1つ加える。"
        ),
        4: (
            "このビートは章の中盤。新しい要素：新しいキャラクターを登場させ、物語に転換を生む。"
            "POVキャラクターが一人で抱えていた問題に、外部からの介入・関心が加わる。"
            "会話を通して、POVキャラクターの能力が「価値あるもの」として認識される瞬間を作る。"
        ),
    }
    return middle_roles.get(
        i,
        "このビートは章の中盤。前のビートから自然に続き、物語を前進させる。"
        "新しい情報・キャラ・転換のうち少なくとも1つを必ず加えること。",
    )


# --- Context pruning: only pass what the beat needs, not whole 50KB docs ---

def _split_sections(text, header_pattern):
    """Split a doc into sections by header lines. Returns [(header, body), ...]."""
    lines = text.split("\n")
    sections = []
    current_header = ""
    current_body = []
    for line in lines:
        if re.match(header_pattern, line):
            if current_header or current_body:
                sections.append((current_header, "\n".join(current_body)))
            current_header = line
            current_body = []
        else:
            current_body.append(line)
    if current_header or current_body:
        sections.append((current_header, "\n".join(current_body)))
    return sections


def extract_character_names(characters_text):
    """Extract character names from characters.md section headers (### 名前)."""
    names = []
    for header, _ in _split_sections(characters_text, r"^###\s"):
        # Take the first token before any parenthesis/space as the search key
        name = header.replace("###", "").strip()
        if name:
            # Use the first 2-3 chars as a search key (e.g. 主人公)
            names.append(name)
    return names


def extract_character_sections(text, keywords):
    """Extract only the character sections relevant to the beat's keywords."""
    sections = _split_sections(text, r"^###\s")
    wanted = []
    for header, body in sections:
        if any(kw in header for kw in keywords):
            wanted.append(header + "\n" + body)
    return "\n\n".join(wanted) if wanted else text[:4000]


def extract_doc_sections(text, keywords, max_chars=6000):
    """Extract sections relevant to keywords; fall back to a trimmed head."""
    sections = _split_sections(text, r"^##\s")
    wanted = []
    for header, body in sections:
        combined = header + "\n" + body
        if any(kw in combined for kw in keywords):
            wanted.append(combined)
    result = "\n\n".join(wanted)
    if result.strip():
        return result[:max_chars]
    return text[:max_chars]


def extract_keywords(beat, character_names):
    """Derive search keywords from the BEAT text only (not the whole outline)."""
    kws = []
    for name in character_names:
        # Match the first token of the character name (e.g. 主人公)
        first = name.split("（")[0].split("(")[0].strip()
        if first and first in beat:
            kws.append(first)
    return kws


def main():
    chapter_num = int(sys.argv[1])

    # Load all context
    voice = load_file(BASE_DIR / "voice.md")
    world = load_file(BASE_DIR / "world.md")
    characters = load_file(BASE_DIR / "characters.md")
    outline = load_file(BASE_DIR / "outline.md")
    canon = load_file(BASE_DIR / "canon.md")

    chapter_outline = extract_chapter_outline(outline, chapter_num)
    beats = extract_beats(chapter_outline)
    plants = extract_plants(chapter_outline)

    if not beats:
        print(f"ERROR: No beats found for chapter {chapter_num}", file=sys.stderr)
        sys.exit(1)

    print(f"第{chapter_num}章: {len(beats)}ビートを分割生成します", file=sys.stderr)

    character_names = extract_character_names(characters)

    # Previous chapter tail (for continuity)
    prev_path = CHAPTERS_DIR / f"ch_{chapter_num - 1:02d}.md"
    if prev_path.exists():
        prev_text = prev_path.read_text()
        prev_tail = prev_text[-1500:] if len(prev_text) > 1500 else prev_text
    else:
        prev_tail = "(first chapter -- no previous)"

    # Generate each beat, chaining the previous beat's ending
    full_chapter = []
    running_tail = prev_tail

    for i, beat in enumerate(beats, 1):
        is_first = (i == 1)
        is_last = (i == len(beats))

        role_hint = build_beat_role(i, len(beats), is_first, is_last)

        # Prune context to only what this beat needs
        keywords = extract_keywords(beat, character_names)
        pruned_characters = extract_character_sections(characters, keywords)
        pruned_canon = extract_doc_sections(canon, keywords)
        pruned_world = extract_doc_sections(world, keywords)
        # voice is always needed in full (the voice doesn't change per beat)

        prompt = f"""この小説の第{chapter_num}章の、ビート{i}/{len(beats)}を書いてください。

VOICE DEFINITION（声の定義。これに正確に従うこと）：
{voice}

WORLD BIBLE（世界構築の詳細の参照。このビートに関連する部分のみ）：
{pruned_world}

CHARACTER REGISTRY（話し方と行動の参照。このビートに関連する部分のみ）：
{pruned_characters}

CANON（確定済みの設定事実。このビートに関連する部分のみ。これに矛盾しないこと）：
{pruned_canon}

このビートの内容（アウトラインから）：
{beat}

このビートの物語上の役割：
{role_hint}

章全体の植え込み（伏線。このビートで該当するものを植えること）：
{plants}

直前の文脈（この直後から続けること。既に起きたことを繰り返さない）：
{running_tail}

執筆指示：
1. このビートの本文だけを書くこと。約1,000字（900〜1,100字）。切り詰めたり要約したりしないこと。
   前のビートより短くしないこと。各ビートは十分な長さを持ち、物語を丁寧に描くこと。
2. 三人称限定・過去形、アウトラインで指定された POV キャラクターに固定。
3. このビートが「物語で果たす役割」を最優先すること。良い文章より物語を進めること。
   このビートは、前のビートと同じ結論を繰り返さないこと。必ず新しい情報・
   キャラ・転換・異常のうち少なくとも1つを導入して、物語を前進させること。
4. 前の文脈から自然に続けること。既に起きたことを繰り返さない。
5. 【最重要】前のビートで描いた場面・台詞・動作・比喩を繰り返さないこと。特に：
   - 同じキャラクター同士の同じ会話を再演しないこと。前のビートで交わした台詞は、このビートでは
     使わないこと（新しい台詞・新しい状況を書くこと）。
   - 同じ身体動作（指で空中に文字を書く、袖を巻く、影が二つに分かれる）を連発しないこと。
     同じ動作は章全体で1〜2回まで。このビートで既に多用したら、別の動作・別の表現に変えること。
   - 同じ比喩（「まるで鏡のように」「水面に映った像のように」）を繰り返さないこと。
   - 前のビートの結末を、このビートの冒頭で言い直さないこと。前のビートの最後の一文から
     自然に続けること。
6. 感覚の細部を見せること：POVキャラクターが聞くもの、嗅ぐもの、身体で感じるもの。
7. 台詞は characters.md で定義された話し方に従うこと。
8. voice.md 第1部のガードレールからの禁止語は使わないこと。
9. 文の長さを変えること。ただし短文ばかりにしないこと。平均文長は30〜40字を目指すこと。
   地の文は滑らかで読みやすく、1文に複数の要素（動作＋状況＋心理）をまとめること。
   短い文（15字以下）は感情のピークや強調の場面だけに使い、通常の叙述では適度に長い文（25〜50字）を
   主体にすること。短い文が2つ以上続いたら、必ず長い文を挟んでリズムを作ること。
   体言止めの列挙（「〜。〜。〜。」）は1シーンに1〜2回だけにし、連発しないこと。
10. 比喩は POVキャラクターの経験から：彼女／彼の世界、仕事、身体の感覚に由来させる。
11. 読者を信じること。シーンの意味を説明しないこと。
12. 三つ組の感覚リスト禁止。決して「X。Y。Z。」と続けないこと。
13. 要約より場面を優先すること。このビートの少なくとも70%は場面（瞬間ごと、台詞と行動）であるべき。
14. 台詞は散文ではなく話し言葉に聞こえるべき。キャラクターは時々つまずき、遮り、言葉を途中で切るべき。
15. 【文体の注意】以下の機械的パターンを避けること：
   - 「——」（ダッシュ）はこのビートで2回まで。多用しないこと。会話の間や強調に使うのは最小限に。
   - 文頭を変えること。「彼女は」「キャラクター名は」など主語で始まる文を連続させないこと。
     動作・感覚・情景・台詞・時間で始める文を混ぜること。同じ文型が3つ続いたら変えること。
   - 「息を呑む」「息を飲む」「目を見開く」「心臓が高鳴る」などの定型・AI的表現を使わないこと。
   - 段落の長さを変えること。似た長さの段落を3つ連続させないこと。1〜2文の短い段落と、
     5文以上の長い段落を混ぜること。
   - 読点（、）を過剰に使わないこと。短い文では読点を減らし、切れ味を出すこと。

今すぐこのビートの本文を書いてください。完全な本文。本文は必ず日本語で書いてください。

【最重要・セリフ比率】これはライトノベル。このビートは会話中心で、台詞（会話文）が全体の約7割、地の文（叙述）が約3割になるように書くこと。
- 会話を主役に。地の文は「量が少なめ」であること（台詞の間に1〜2文程度）。
  ただし、地の文の各文は滑らかで長め（30〜50字）に書くこと。短い地の文（15字以下）を連発しないこと。
- 地の文は動作＋状況＋心理を1文にまとめ、簡潔だが豊かな情報量を持つこと。
- 【地の文の書き方の例】「彼女は柱の陰に身を寄せながら、耳にした噂の意味を頭の中で反芻していた」「女房たちは声を潜め、几帳の向こうに何かを隠すように視線を交わし合った」のように、1文に複数の要素を盛り込むこと。
  逆に「足が止まる」「振り返る」のような2〜5字の断片文を連発しないこと。断片は1ビートに1〜2回の強調に留める。
- 観察・心理・情景・情報は、長い地の文で説明せず、キャラクター同士の台詞の中で自然に滲ませること。
- 説明は「語り手が説明する」のではなく「キャラクターが会話で伝える」こと。
- 地の文が3文以上連続したら、必ず台詞を挟むこと。
- 台詞は互いのやりとりが続くように書き、問いかけ・相槌・反論・驚きを入れて、会話が生き生きと弾むようにすること。
"""
        print(f"  ビート{i}/{len(beats)}を生成中...", file=sys.stderr)
        result = ""
        for call_attempt in range(1, 4):
            try:
                result = call_writer(prompt)
            except Exception as e:
                print(f"  WARNING: ビート{i}のAPI呼び出し失敗 ({e}), リトライ ({call_attempt}/3)", file=sys.stderr)
                result = ""
                continue
            if result.strip():
                break
            print(f"  WARNING: ビート{i}が空でした。リトライ ({call_attempt}/3)", file=sys.stderr)
            result = ""

        if not result.strip():
            print(f"  ERROR: ビート{i}が3回試行しても空でした。このビートはスキップします", file=sys.stderr)
            continue

        full_chapter.append(result.strip())
        # Chain: the running tail becomes the end of this beat (short, to avoid repetition)
        running_tail = result[-600:] if len(result) > 600 else result

    # Assemble the full chapter
    chapter_text = "\n\n".join(full_chapter)

    # Save
    out_path = CHAPTERS_DIR / f"ch_{chapter_num:02d}.md"
    out_path.write_text(chapter_text)
    print(f"Saved to {out_path}", file=sys.stderr)
    print(f"Total chars: {len(chapter_text)}", file=sys.stderr)
    print(chapter_text)


if __name__ == "__main__":
    main()
