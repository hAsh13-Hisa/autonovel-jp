#!/usr/bin/env python3
"""
Generate canon.md by extracting all hard facts from world.md + characters.md.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

import llm





def call_writer(prompt, max_tokens=16000):
    return llm.call_writer(
        prompt,
        system="あなたはファンタジー小説の企画文書から確定事実を抽出するコンティニュイティ編集者です。あなたは正確で網羅的であり、原文にない事実を決して捏造しません。出力は必ず日本語で書いてください。",
        max_tokens=max_tokens,
        temperature=0.2,
    )


world = (BASE_DIR / "world.md").read_text()
characters = (BASE_DIR / "characters.md").read_text()
seed = (BASE_DIR / "seed.txt").read_text()

prompt = f"""これらの計画文書から、EVERY hard fact（すべての確定事実）を構造化された正典データベースに抽出してください。
「hard fact（確定事実）」とは、書き手が矛盾させてはならないもの：名前、年齢、日付、身体的特徴、
魔法システムのルール、地理、関係性、確定した出来事。

SOURCE DOCUMENTS（出典文書）：

=== SEED.TXT ===
{seed}

=== WORLD.MD ===
{world}

=== CHARACTERS.MD ===
{characters}

出力を CANON.MD として、以下のカテゴリで整形すること：

## 地理
- 場所、距離、物理的特性についての具体的な事実

## タイムライン
- 日付付きの出来事、年齢、期間

## 魔法システムのルール
- この世界の魔法体系のハードルール（間隔、代償、制限）
- 主人公の特異な才能の詳細

## キャラクターの事実
- 年齢、身体的特徴、癖、人間関係
- 事実ごとに1エントリ（段落にしない）

## 政治的／派閥的
- 誰が何を支配しているか、同盟、衝突、契約

## 文化的
- 風習、禁忌、法律、祭り、食べ物、服装

## 物語内で確定済み
- 物語の過去にすでに起こった出来事
- 主要な契約、戦争、転換点など

RULES（ルール）：
- 各箇条に事実を1つだけ。短く。具体的に。検証可能に。
- 各事実の後に出典（world.md か characters.md）を括弧書きで含める。
- 最低80〜120エントリを目指す。徹底的に。
- 二つの文書がわずかに異なる詳細を与えている場合、その不一致を記すこと。
- 事実を捏造しないこと。明示的に書かれているものだけを記録すること。

出力はすべて日本語で書いてください。
"""

print("Calling writer model...", file=sys.stderr)
result = call_writer(prompt)
# Retry on empty/short output (intermittent empty responses from some models)
attempt = 1
while (len(result.strip()) < 500) and attempt < 4:
    print(f"Empty/short output ({len(result.strip())} chars), retrying ({attempt}/3)...", file=sys.stderr)
    result = call_writer(prompt)
    attempt += 1
# Write the canon database to canon.md
canon_path = BASE_DIR / "canon.md"
canon_path.write_text(result.strip() + "\n")
print(result)
