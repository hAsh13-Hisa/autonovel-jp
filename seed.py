#!/usr/bin/env python3
"""
seed.py -- Generate fantasy novel seed concepts.

Usage:
  uv run python seed.py              # Generate 10 concepts, pick one
  uv run python seed.py --count=5    # Generate 5 concepts
  uv run python seed.py --riff "magic costs memories"  # Riff on an idea
"""

import argparse
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

import llm




ANTHROPIC_BETA = "context-1m-2025-08-07"


def call_writer(prompt, max_tokens=4000):
    return llm.call_writer(
        prompt,
        system="あなたはファンタジー小説家です。このジャンルの最高傑作群——トールキン、ル＝グウィン、ロスフス、ウルフ、ジェミシン、ピーク、スザンナ・クラーク、アンドリュー・ピーターソン、ソフィア・サマター——を深く知っています。あなたはSPECIFIC（具体的）、SURPRISING（驚き）、STRUCTURALLY SOUND（構造的に堅牢）な小説コンセプトを生み出します。中世ヨーロッパ＋エルフのような凡庸な設定は決して出しません。出力は必ず日本語で書いてください。",
        max_tokens=max_tokens,
        temperature=1.0,
    )



GENERATE_PROMPT = """{count} 個のファンタジー小説の種コンセプトを生成してください。それぞれが、
そのまま小説一冊を組み立てられる完全な前提になっている必要があります。

各コンセプトで提供すること：

番号. タイトル（仮題。喚起的で、凡庸ではない）
HOOK: 本を手に取らせる一言。具体的で驚きがあり、「世界のどこかで…」のような文にしてはいけない。
WORLD: この世界の何が異なるのか？「魔法がある」だけではなく、この場所を定義する具体的で珍しい点は何か？具体化する——塩の平原、逆さに建つ塔、移動する都市、記憶する海など。SENSORY（感覚的）に。
MAGIC/COST: 中核となる思弁的要素と、それが代償（COST）するものは何か？サンダーソンの第二法則に従い、力よりも制限が重要。代償は興味深いジレンマを生むべき。
TENSION: 中心的な対立は何か？PERSONAL（一人のキャラクターの固有の問題）であると同時に COSMIC（世界に影響する）でなければならない。この二つが互いに緊張関係にあること。
THEME: この物語が探求する問いは何か。メッセージではなく、簡単に答えの出ない本物の問い。
WHY IT'S NOT GENERIC: 標準的なファンタジーの定番と何が違うかを一文で。

{count} 個のコンセプト全体で DIVERSITY（多様性）を狙う：
  - 少なくとも1つは非人間中心の世界であること
  - 少なくとも1つは叙事詩的というより文芸的／静かなものであること
  - 少なくとも1つは変わった叙述構造のアイデアを持つこと
  - 少なくとも1つは典型的なヨーロッパ風設定の外に置くこと
  - トーンを混ぜる：ダーク、暖かい、奇妙、メランコリック、浮遊感

生成しないこと：
  - 選ばれし者の預言（面白い形で覆さない限り）
  - 主な敵役としての魔王／究極の悪
  - 中世ヨーロッパ＋エルフ／ドワーフ／オーク
  - 「アカデミー」「魔法学校」の設定
  - 中心プロットとしての三角関係

出力はすべて日本語で書いてください。
"""

RIFF_PROMPT = """私にはファンタジー小説の種アイデアがある：

"{idea}"

このコンセプトを5パターン展開してください。核となる面白さを保ちつつ、別の方向へ押し出してください。各パターンに：

番号. TITLE
HOOK: 一言。
HOW IT DIFFERS: 元の種から何を変えたか、そしてなぜか。
WORLD: 具体的で感覚的な世界の詳細。
MAGIC/COST: 思弁的要素とその代償。
TENSION: 個人的＋宇宙的な対立。
THEME: 探求する問い。

各パターンを互いに本当に違うものにすること——表面上の細部をいじるだけにしない。主人公、舞台、トーン、構造、テーマの焦点を変えること。

出力する全文は日本語で書いてください。
"""


def main():
    parser = argparse.ArgumentParser(description="Generate novel seed concepts")
    parser.add_argument("--count", type=int, default=10,
                        help="Number of concepts to generate (default: 10)")
    parser.add_argument("--riff", type=str, default=None,
                        help="Riff on an existing idea")
    args = parser.parse_args()

    if args.riff:
        print(f"Riffing on: {args.riff}\n")
        prompt = RIFF_PROMPT.format(idea=args.riff)
    else:
        print(f"Generating {args.count} seed concepts...\n")
        prompt = GENERATE_PROMPT.format(count=args.count)

    result = call_writer(prompt, max_tokens=8000)
    print(result)
    print("\n" + "=" * 60)
    print("To pick a seed, copy the concept you like into seed.txt:")
    print("  nano seed.txt")
    print("Or remix several concepts into your own seed.")
    print("Then proceed to Step 2 in WORKFLOW.md.")


if __name__ == "__main__":
    main()
