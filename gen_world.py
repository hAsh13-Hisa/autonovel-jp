#!/usr/bin/env python3
"""
One-shot world.md generator for foundation phase.
Reads seed.txt + voice.md, calls the writer model, outputs world.md content.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

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
    コピー先（/opt/data/cache/scratch/final 等）で行う。BASE_DIR は
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
        system=(
            "あなたはサンダーソンの法則とル＝グウィンの散文哲学、TTRPG品質のロア設計に通じた"
            "ファンタジー世界構築者です。あなたは具体的で相互接続され、書かれた以上に奥行きを"
            "暗示する世界バイブルを書きます。AIスラング語（delve、tapestry、myriadなど）は決して"
            "使いません。明快で直接的な散文で書きます。すべてのルールには代償があり、すべての文化の"
            "細部は歴史を暗示し、すべての場所は感覚的な特徴を持ちます。出力は必ず日本語で書いてください。"
        ),
        max_tokens=max_tokens,
        temperature=0.7,
    )

seed = (project_dir() / "seed.txt").read_text()
voice = read_project_file("voice.md")
craft = read_project_file("CRAFT.md")

# Extract voice Part 2 only (the novel-specific voice)
voice_lines = voice.split('\n')
part2_start = next(i for i, l in enumerate(voice_lines) if 'Part 2' in l)
voice_part2 = '\n'.join(voice_lines[part2_start:])

prompt = f"""このファンタジー小説の完全な世界バイブルを作り上げてください。これは WORLD.MD ファイルです——この世界に存在するすべてのものの決定的な参照元。書き手はこの文書だけでどんな世界構築上の疑問も解決できるべきです。

SEED CONCEPT（種コンセプト）：
{seed}

VOICE IDENTITY（この小説の調子と言語）：
{voice_part2}

CRAFT REQUIREMENTS（CRAFT.md から。これに従うこと）：
- 魔法システムにはサンダーソンの第二法則に従った HARD RULES（厳格なルール）と COSTS（代償）、LIMITATIONS（制限）が必要
- 叙述上の重要性では制限 >= 力
- 魔法の含意を社会、経済、法、宗教にまで追跡すること
- 魔法の社会的含意を少なくとも2〜3つ深く探求すること
- 歴史はプロットを動かす PRESENT-DAY TENSIONS（現在の緊張）を生み出すこと（単なる背景ではない）
- 地理は具体的で感覚的であること（一般的なファンタジーではない）
- アイスバーグ原則：書かれた以上を暗示すること
- 相互接続：一つの糸を引けば、すべてが動くこと

このセクション構成で文書を構築すること：

## 宇宙論と歴史
主要な出来事のタイムライン。現在の緊張を生み出す出来事に焦点を当てる。建国神話、重要な転換点、プロットに関わる最近の出来事を含める。

## 魔法システム
### ハードルール（この世界の魔法体系）
具体的で検証可能なルール。どの間隔が何をするか。どの進行が何を束縛するか。ルールを破るとどうなるか。代償と制限を目立つように含める。

### ソフトマジック（主人公の特異な才能）
彼／彼女が何を知覚するか、どう機能するか、特に彼／彼女にとって何が代償になるか。神秘的に見えるが一貫した内部ロジックを持つべき。

### 社会的含意
魔法体系がどう形作るか：統治、商取引、教育、階級構造、犯罪、家族生活、幼少期、老化、障害。

## 地理
主要な舞台の物理的レイアウト、地区、特徴的な場所の音響・感覚特性。隣接する場所（少なくとも2〜3）。各場所の感覚的特徴。

## 派閥と政治
誰が権力を持ち、誰がそれを欲し、誰がそれに押しつぶされているか。少なくとも3〜4の対立する関心を持つ派閥。

## 動物相／植物相／自然世界
この世界の内外の自然世界の何が独特か？

## 文化的な細部
風習、禁忌、祭り、食べ物、服装、成人儀礼。日々の生活を具体的に感じさせるもの。

## 内的整合性ルール
書き手が破ってはいけないハードな制約。この世界の音の物理。可能なことと不可能なこと。

重要：
- 具体的であること。「都市に地区がある」ではなく、名前を付け、描写し、感覚的な特徴を与えること。
- すべてのルールに代償や制限を併記すること。
- 各セクションに、説明されていない事実を2〜3つ含め、より深いシステムをほのめかすこと（アイスバーグの深み）。
- 事実は相互接続するべき：魔法は政治を形作り、地理は文化を形作り、歴史は現在の派の対立を説明するべき。
- 明快で直接的な散文で書く。AIスラングなし。安っぽい「豊かなタペストリー」なし。「深掘り」なし。
- 世界は想像されたものではなく、接地し、生きていると感じられること。考えること：朝食はどんな匂いか？子供は何をして遊ぶか？老人はどうこぼすか？
- 目標は約3000〜4000語。詰め込みではなく密度。

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
# Write the world bible to world.md
world_path = project_dir() / "world.md"
world_path.write_text(result.strip() + "\n")
print(result)
