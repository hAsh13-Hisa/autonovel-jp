#!/usr/bin/env python3
"""
Revision chapter generator. Rewrites a chapter from a specific revision brief.
Usage: python gen_revision.py <chapter_num> <brief_file>
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

import llm





def call_revision(prompt, max_tokens=16000):
    return llm.call_revision(
        prompt,
        system='あなたは特定の改稿ブリーフに基づいてファンタジー小説の章を書き直す作家です。ブリーフに正確に従います。声、世界、キャラクターを保ちながら、指定された構造変更を行います。章の全文を書きます。本文は必ず日本語で書いてください。重要な指示：思考プロセス、メタコメント、計画、英語の注記、推敲メモは一切出力しないでください。出力は改稿後の章の本文のみにしてください。',
        max_tokens=max_tokens,
        temperature=0.8,
    )


def novel_title():
    """題名は seed.txt の「タイトル：」から取る（outline.md には見出し行が無い。
    上流雛形の別作品名を焼き込まない）。"""
    seed = BASE_DIR / "seed.txt"
    if seed.exists():
        for line in seed.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("タイトル"):
                return line.split("：", 1)[-1].split(":", 1)[-1].strip()
    return ""


def main():
    ch_num = int(sys.argv[1])
    brief_file = sys.argv[2]
    
    title = novel_title()
    heading = f"「{title}」第{ch_num}章" if title else f"第{ch_num}章"
    voice = (BASE_DIR / "voice.md").read_text()
    characters = (BASE_DIR / "characters.md").read_text()
    world = (BASE_DIR / "world.md").read_text()
    brief = Path(brief_file).read_text()
    
    # Prune context to only what this revision needs (prevents 40KB+ prompt
    # that triggers empty responses on long-prose models). Reuse the beat
    # script's pruning helpers.
    import draft_chapter_beat as dcb
    keywords = dcb.extract_keywords(brief, brief)
    pruned_characters = dcb.extract_character_sections(characters, keywords)
    pruned_world = dcb.extract_doc_sections(world, keywords)
    pruned_canon = dcb.extract_doc_sections((BASE_DIR / "canon.md").read_text(), keywords)
    
    # Load adjacent chapters for continuity
    prev_path = BASE_DIR / "chapters" / f"ch_{ch_num - 1:02d}.md"
    next_path = BASE_DIR / "chapters" / f"ch_{ch_num + 1:02d}.md"
    prev_tail = prev_path.read_text()[-2000:] if prev_path.exists() else "(first chapter)"
    next_head = next_path.read_text()[:1500] if next_path.exists() else "(last chapter)"
    
    # Load old version if exists
    old_path = BASE_DIR / "chapters" / f"ch_{ch_num:02d}.md"
    old_text = old_path.read_text() if old_path.exists() and old_path.stat().st_size > 0 else "(no existing draft)"
    
    prompt = f"""{heading}を書き直してください。

REVISION BRIEF（改稿ブリーフ。これに正確に従うこと）：
{brief}

VOICE DEFINITION（声の定義）：
{voice}

CHARACTER REGISTRY（キャラクター登録簿。この改稿に関連する部分のみ）：
{pruned_characters}

WORLD BIBLE（世界バイブル。この改稿に関連する部分のみ）：
{pruned_world}

CANON（確定済みの設定事実。この改稿に関連する部分のみ）：
{pruned_canon}

PREVIOUS CHAPTER ENDING（連続性を保つこと）：
{prev_tail}

NEXT CHAPTER OPENING（これに流れ込むように終わらせること）：
{next_head}

THE EXISTING DRAFT（既存の草稿。生の材料として使うこと——うまくいっているものを残し、うまくいかないものを切る）：
{old_text}

ANTI-PATTERN RULES（反パターン規則）：
- 三つ組の感覚リスト禁止（X。Y。Z。）
- 「彼は[動詞]しなかった」は1回まで
- 「彼は[X]について考えた」構文禁止
- 比喩の接続詞として「[X]が[Y]するように」は2回まで
- 叙述で「Xではなく、Y」の公式禁止
- 示した後に説明しすぎるのを禁止
- 区切りは最大2回
- 本当に驚く瞬間を少なくとも1つ
- 70%以上を場面（台詞と行動。要約ではない）
- 台詞は話し言葉に聞こえるべき。散文ではないこと

改稿後の章の全文を今すぐ書いてください。本文は必ず日本語で書いてください。"""

    print(f"Rewriting Chapter {ch_num}...", file=sys.stderr)
    result = call_revision(prompt)
    
    out_path = BASE_DIR / "chapters" / f"ch_{ch_num:02d}.md"
    out_path.write_text(result)
    print(f"Saved to {out_path}", file=sys.stderr)
    print(f"Word count: {len(result.split())}", file=sys.stderr)

if __name__ == "__main__":
    main()
