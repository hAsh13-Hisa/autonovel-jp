#!/usr/bin/env python3
"""Generate outline.md (part 1: chapters 1-12) from seed + world + characters + mystery + craft."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
# 章数（2026-10-19: プロンプトに 24 を直書きしていた。作品ごとに変えられるよう
# env `AUTONOVEL_CHAPTERS` から取る。既定は本家と同じ24章。前半/後半の分割も章数から計算する）
N_CHAPTERS = int(os.environ.get("AUTONOVEL_CHAPTERS", "24"))
HALF = N_CHAPTERS // 2
# .env はカレント（＝作り直し版のコピー先）を優先し、無ければリポジトリ側。
# なぜ: 作り直しの作業はコピー先で行うので、エンドポイントの設定も
# コピー先の .env を使うべき。リポジトリ側の .env は git 管理外で
# 存在しないことがある（実測 2026-10-14）。
_cwd_env = Path.cwd() / ".env"
load_dotenv(_cwd_env if _cwd_env.exists() else BASE_DIR / ".env")

def repo_dir() -> Path:
    """autonovel-jp のリポジトリ本体（共有ガイドの置き場所）。

    なぜ必要か（実測 2026-10-14）: コピー先で動かすと BASE_DIR は
    Path(__file__).parent＝コピー先自身を指す。すると「カレントに無いので
    リポジトリ側を探す」が同じ場所を探して終わり、MYSTERY.md 等の共有ガイドが
    見つからなかった。
    → AUTONOVEL_REPO で渡す（run_pipeline.py が設定する）。無い場合は
      自分の場所を使う（＝リポジトリから直接動かす従来の動作）。
    """
    env = os.environ.get("AUTONOVEL_REPO")
    if env and Path(env).is_dir():
        return Path(env)
    return BASE_DIR


def project_dir() -> Path:
    """作品ファイル（seed.txt / world.md / characters.md / outline.md 等）の場所。

    なぜ BASE_DIR をそのまま使わないか（実測 2026-10-14）: 作り直しの作業は
    コピー先（作業用のコピー先）で行う。BASE_DIR は
    Path(__file__).parent＝自分が置かれた場所で固定なので、作り直し版の設定を
    読み書きできず、正典 canon.md が 10/8 の古いまま残っていた
    （一人称「俺」の指定が正典に入らない）。
    → カレントを優先し、作品ファイルが無いときだけ自分の場所を使う。
      （他のスクリプトと同じ規則: pyexe() / chapters_dir() と同じ考え方）
    """
    here = Path.cwd()
    if (here / "characters.md").exists() or (here / "seed.txt").exists() \
            or (here / "outline.md").exists():
        return here
    return BASE_DIR


def read_project_file(name: str) -> str:
    """作品ファイルを読む。カレントに無ければリポジトリ側から読む。

    なぜ1ファイルずつフォールバックするか（実測 2026-10-14）: 作り直しの
    コピー先には seed.txt / world.md / characters.md はあっても MYSTERY.md や
    CRAFT.md が無いことがある。→ 作品に固有のファイルはカレント、
    共有のガイド（MYSTERY/CRAFT/voice）はリポジトリにも探しに行く。
    """
    here = Path.cwd() / name
    if here.exists():
        return here.read_text(encoding="utf-8")
    alt = repo_dir() / name
    if alt.exists():
        return alt.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"{name} が見つかりません（{Path.cwd()} にも {repo_dir()} にも無い）")
def read_project_file(name: str) -> str:
    """作品ファイルを読む。カレントに無ければリポジトリ側から読む。

    なぜ1ファイルずつフォールバックするか（実測 2026-10-14）: 作り直しの
    コピー先には seed.txt / world.md / characters.md はあっても MYSTERY.md や
    CRAFT.md が無いことがある。project_dir() をカレント一択にすると
    FileNotFoundError で起動すらできない。
    → 作品に固有のファイルはカレント、共有のガイド（MYSTERY/CRAFT/voice）は
      リポジトリにも探しに行く。
    """
    here = Path.cwd() / name
    if here.exists():
        return here.read_text(encoding="utf-8")
    alt = repo_dir() / name
    if alt.exists():
        return alt.read_text(encoding="utf-8")
    raise FileNotFoundError(f"{name} が見つかりません（{Path.cwd()} にも {repo_dir()} にも無い）")
import llm


def call_writer(prompt, max_tokens=16000):
    return llm.call_writer(
        prompt,
        system="あなたはSave the Catのビート、サンダーソンのプロット原則、ダン・ハーモンのストーリーサークル、MICE商に深い知識を持つ小説設計者です。あなたは作者がその場で構造を発明せずに草稿を書けるようなアウトラインを構築します。AIスラング語は決して使いません。出力は必ず日本語で書いてください。",
        max_tokens=max_tokens,
        temperature=0.5,
    )


seed = (project_dir() / "seed.txt").read_text()
world = (project_dir() / "world.md").read_text()
characters = (project_dir() / "characters.md").read_text()
mystery = read_project_file("MYSTERY.md")
craft = read_project_file("CRAFT.md")

# Voice Part 2 only（見出しは「Part 2」でも「第○部」でも拾う。無ければ全文を使う）
voice = read_project_file("voice.md")
voice_lines = voice.split('\n')
part2_start = next((i for i, l in enumerate(voice_lines)
                    if l.lstrip().startswith('#')
                    and ('Part 2' in l or '第2部' in l)), 0)
voice_part2 = '\n'.join(voice_lines[part2_start:])

prompt = f"""このファンタジー小説の章アウトラインの前半（第1章〜第{HALF}章）を構築してください。
全体は{N_CHAPTERS}章、合計約{N_CHAPTERS*3000:,}〜{N_CHAPTERS*4000:,}語（1章あたり約3,000〜4,000語）の予定です。今回は第1章〜第{HALF}章だけを書いてください。

SEED CONCEPT（種コンセプト）：
{seed}

THE CENTRAL MYSTERY（中心の謎。作者だけの目——読者は徐々に発見する）：
{mystery}

WORLD BIBLE（世界バイブル）：
{world}

CHARACTER REGISTRY（キャラクター登録簿）：
{characters}

VOICE（調子と言語）：
{voice_part2}

CRAFT REFERENCE（従うべき構造）：
{craft}

アウトラインを以下で構築すること：

## 幕構成
**作品全体（第1章〜第{N_CHAPTERS}章）の設計図として書くこと。** 第I幕（0〜23%）、第II幕前編（23〜50%）、
第II幕后編（50〜77%）、第III幕（77〜100%）の**4つすべて**を、主要な出来事のパーセンテージ位置つきで書く。
この段階で章のアウトラインを書くのは第{HALF}章までだが、**幕構成は第{N_CHAPTERS}章までの全体を書く**
（後半の章は続く工程が追記する。幕構成まで後半を空にしないこと）。

## 章ごとのアウトライン（第1章〜第{HALF}章）

各章で提供すること：
### 第N章：[タイトル]
- **POV（視点）：**（SEED の登場人物から選んだ主要キャラクター。列挙が無ければ世界と対立から導く。三人称限定）
- **場所：**どの場所か
- **Save the Catビート：**この章が果たすビート（オープニングイメージ、セットアップ、触媒など）
- **%位置：**小説のどこに当たるか
- **感情のアーク：** 始まりの感情 → 終わりの感情
- **トライ・フェイル・サイクル：** イエス・バット／ノー・アンド／ノー・バット／イエス・アンド
- **ビート：** 起こらなければならない具体的なシーンのビート3〜5
- **植え込み：** この章で植えられる伏線要素
- **回収：** ここで回収される伏線要素
- **キャラクターの動き：** 章末までに POV キャラクター（または他のキャラクター）に何が変わるか
- **嘘：** POV キャラクターの嘘（SEED のテーマから導く）がこの章で
  強化されるか挑戦されるか
- **約〜語数の目標：** リズムのため

主要プロット構造（SEED に「ストーリー構成」「各幕の事件」の記述があればそれに従うこと。無ければ以下に従うこと）：

第1幕（第1〜6章あたり）：主人公の世界、彼女／彼の痛み、才能、日常、家族を確立する。
謎を早めに植える（導き手の異様な力、禁じられた場所、隠された過去）。
触覚：何かが主人公に中心的な謎の調査を強いる。

第2幕前編（第7〜12章あたり）：調査。主人公は異世界の秘密を掘り下げ、
導き手と出会い、仲間と同盟し、自分の能力をより明確に理解し始める。
中間点：主人公が彼女／彼の手法を変える部分的な真実を学ぶ（偽の勝利か敗北）。

制約：
- 主人公の調査は、通過儀礼アークに重ねられたミステリープロットのように感じられるべき
- 安定の罠：悪いことは悪いままであるべき。すべてがきれいに解決されるべきではない
- 中心的な謎の存在（導き手）はどこかの時点で本人として登場すること（記憶や手紙だけではない）
- **「静かな章」（アクションが薄くキャラクター中心の章）を並べないこと**。各章にピンチ（危機・障害・損失の予感）と、その章なりの小さな克服を入れる。読者がワクワクして読み、読み終えてスッキリする構成にすること（ピンチ→克服の落差で感動を作る）
- トライ・フェイルの種類を変える：60%以上は「はい・しかし」または「いいえ・そして」
- 退屈の防止（必須）：(1) 全体の40〜60%地点に「取り返しのつかない損失」を1つ置く。以後の章で元に戻さない (2) 対立面（敵・障害）を第II幕の終わりまで持続させる。敵が不在の章を3章以上続けない (3) 各章の末尾に「次をめくる理由」（新しい疑問・差し迫る危険・約束）を1つ置く。静かな着地で終える章は全体で3章まで
- 上記の幕の割り当て（40〜60%地点＝第II幕前編の後半）と、「取り返しのつかない損失」の位置が一致していること

出力はすべて日本語で書いてください。第1章〜第12章まで、完全に書いてください。
"""

print("Calling writer model...", file=sys.stderr)
result = call_writer(prompt)
# Retry on empty/short output
attempt = 1
while (len(result.strip()) < 500) and attempt < 4:
    print(f"Empty/short output ({len(result.strip())} chars), retrying ({attempt}/3)...", file=sys.stderr)
    result = call_writer(prompt)
    attempt += 1
# Write part 1 to outline.md and hand off to /tmp/outline_output.md for part 2
outline_path = project_dir() / "outline.md"
outline_path.write_text(result.strip() + "\n")
tmp_path = Path("/tmp/outline_output.md")
tmp_path.write_text(result.strip() + "\n")
print(result)
