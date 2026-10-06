#!/usr/bin/env python3
"""Generate outline.md (part 2: chapters 13-24 + foreshadowing ledger).

Reads part 1 (chapters 1-12) from /tmp/outline_output.md, generates the
remaining chapters and the foreshadowing ledger, and appends to outline.md.
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
        system="あなたはSave the Catのビート、サンダーソンのプロット原則、ダン・ハーモンのストーリーサークル、MICE商に深い知識を持つ小説設計者です。あなたは作者がその場で構造を発明せずに草稿を書けるようなアウトラインを構築します。AIスラング語は決して使いません。出力は必ず日本語で書いてください。",
        max_tokens=max_tokens,
        temperature=0.5,
    )


seed = (BASE_DIR / "seed.txt").read_text()
world = (BASE_DIR / "world.md").read_text()
characters = (BASE_DIR / "characters.md").read_text()
mystery = (BASE_DIR / "MYSTERY.md").read_text()

# Part 1 (chapters 1-12) from the handoff file
part1_path = Path("/tmp/outline_output.md")
if not part1_path.exists():
    print("ERROR: /tmp/outline_output.md not found (run gen_outline.py first)", file=sys.stderr)
    sys.exit(1)
part1 = part1_path.read_text()

prompt = f"""このファンタジー小説の章アウトラインの後半（第13章〜第24章）と伏線台帳を構築してください。
前半（第1章〜第12章）はすでにあります。同じ形式で続けてください。

SEED CONCEPT（種コンセプト）：
{seed}

THE CENTRAL MYSTERY（中心の謎。作者だけの目——読者は徐々に発見する）：
{mystery}

WORLD BIBLE（世界バイブル）：
{world}

CHARACTER REGISTRY（キャラクター登録簿）：
{characters}

これまでのアウトライン（第1章〜第12章）：
{part1}

必要な残りの構造：

## 章ごとのアウトライン（第13章〜第24章）

各章で提供すること（前半と同じ形式）：
### 第N章：[タイトル]
- **POV（視点）：**（SEED の登場人物から選んだ主要キャラクター。列挙が無ければ世界と対立から導く。三人称限定）
- **場所：**どの場所か
- **Save the Catビート：**この章が果たすビート
- **%位置：**小説のどこに当たるか
- **感情のアーク：** 始まりの感情 → 終わりの感情
- **トライ・フェイル・サイクル：** イエス・バット／ノー・アンド／ノー・バット／イエス・アンド
- **ビート：** 起こらなければならない具体的なシーンのビート3〜5
- **植え込み：** この章で植えられる伏線要素
- **回収：** ここで回収される伏線要素
- **キャラクターの動き：** 章末までに POV キャラクター（または他のキャラクター）に何が変わるか
- **嘘：** POV キャラクターの嘘がこの章で強化されるか挑戦されるか
- **約〜語数の目標：** リズムのため

主要プロット構造（SEED に「ストーリー構成」「各幕の事件」の記述があればそれに従うこと。無ければ以下に従うこと）：

第2幕后編（第13〜18章あたり）：圧力が高まる。敵対勢力が主人公に対抗する。
導き手の秘密が表面化し始める。主人公の嘘はますます維持できなくなる。
全てを失う：主人公は真実と対峙し、完全な真実を学ぶ。

第3幕（第19〜24章あたり）：主人公は問いを理解する。どう答えるかを選ばなければならない。
クライマックスは確立された魔法体系のルールを使って展開する。
結末は彼女／彼の選択の結果を示す。

制約：
- クライマックスは確立された魔法体系のルールで機械的に解決可能でなければならない
- 安定の罠：悪いことは悪いままであるべき。すべてがきれいに解決されるべきではない
- 中心的な謎の存在（導き手）はどこかの時点で本人として登場すること（記憶や手紙だけではない）
- **「静かな章」（アクションが薄くキャラクター中心の章）を並べないこと**。各章にピンチ（危機・障害・損失の予感）と、その章なりの小さな克服を入れる。読者がワクワクして読み、読み終えてスッキリする構成にすること（ピンチ→克服の落差で感動を作る）
- トライ・フェイルの種類を変える：60%以上は「はい・しかし」または「いいえ・そして」
- 退屈の防止（必須）：(1) 全体の40〜60%地点に「取り返しのつかない損失」を1つ置く。以後の章で元に戻さない (2) 対立面（敵・障害）を第II幕の終わりまで持続させる。敵が不在の章を3章以上続けない (3) 各章の末尾に「次をめくる理由」（新しい疑問・差し迫る危険・約束）を1つ置く。静かな着地で終える章は全体で1章まで

それから書くこと：

## 伏線台帳

植えられたすべての糸を追跡する表：
| 糸 | 植え込み（章） | 強化（章） | 回収（章） | 種類 |

少なくとも15本の糸を含める。種類：物体、台詞、行動、象徴的、構造的。
植え込みから回収までの距離は少なくとも3章あること。

出力はすべて日本語で書いてください。第13章〜第24章と伏線台帳まで、完全に書いてください。
"""

print("Calling writer model...", file=sys.stderr)
result = call_writer(prompt)
# Retry on empty/short output
attempt = 1
while (len(result.strip()) < 500) and attempt < 4:
    print(f"Empty/short output ({len(result.strip())} chars), retrying ({attempt}/3)...", file=sys.stderr)
    result = call_writer(prompt)
    attempt += 1
# Append part 2 to outline.md
outline_path = BASE_DIR / "outline.md"
outline_path.write_text(part1.strip() + "\n\n" + result.strip() + "\n")
print(result)
