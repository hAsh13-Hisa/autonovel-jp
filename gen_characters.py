#!/usr/bin/env python3
"""
One-shot characters.md generator for foundation phase.
Reads seed.txt + voice.md + world.md + CRAFT.md, calls writer model.
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
        system="あなたは文芸小説向けのキャラクターデザイナーです。wound/want/need/lie（傷／欲求／必要／嘘）フレームワーク、サンダーソンの三つのスライダー、台詞の個性に深い知識を持っています。あなたは矛盾、秘密、そして聞こえる話し方を持つ、現実の人間のように感じられるキャラクターを作ります。AIスラング語は決して使いません。出力は必ず日本語で書いてください。",
        max_tokens=max_tokens,
        temperature=0.7,
    )


seed = (project_dir() / "seed.txt").read_text()
world = (project_dir() / "world.md").read_text()

# Voice Part 2 only（見出しは「Part 2」でも「第○部」でも拾う。無ければ全文を使う）
voice = read_project_file("voice.md")
voice_lines = voice.split('\n')
part2_start = next((i for i, l in enumerate(voice_lines)
                    if l.lstrip().startswith('#')
                    and ('Part 2' in l or '第2部' in l)), 0)
voice_part2 = '\n'.join(voice_lines[part2_start:])

prompt = f"""このファンタジー小説の完全なキャラクター登録簿を作り上げてください。これは CHARACTERS.MD ——
この物語に誰が存在し、何が彼らを駆り立て、どう話し、どんな秘密を抱えるかの決定的な参照元です。

SEED CONCEPT（種コンセプト）：
{seed}

WORLD BIBLE（キャラクターが住む世界）：
{world}

VOICE IDENTITY（この小説の調子）：
{voice_part2}

CHARACTER CRAFT REQUIREMENTS（CRAFT.md から）：

### 三つのスライダー（サンダーソン）
各キャラクターは三つの独立したダイヤル（0-10）を持つ：
  プロアクティビティ（率先性）—— 彼らがプロットを動かすのか、それとも反応するのか？
  好感度（Likability）—— 読者は彼らに共感するか？
  能力（Competence）—— 彼らは自分のすることに秀でているか？
ルール：魅力的 = 少なくとも二つで HIGH、または一つで HIGH かつ明確な成長がある。

### Wound / Want / Need / Lie（傷／希望／必要／嘘）フレームワーク
因果連鎖：
  GHOST（背景の出来事）-> WOUND（持続する傷）-> LIE（対処のための偽の信念）
    -> WANT（嘘に駆られた外面的な目標）-> NEED（内的な真実、嘘に対抗）
ルール：Want と Need は互いに緊張関係にあること。Lie は一文で言えること。
  Truth はその正反対。

### 台詞の個別性（8つの次元）
1. 語彙レベル  2. 文の長さ  3. 縮約／丁寧さ
4. 口癖  5. 質問対叙述の比率  6. 遮り方のパターン
7. 比喩の領域  8. 直接性 対 間接性
テスト：台詞タグを外す。誰が話しているか分かるか？

少なくとも以下のキャラクターで登録簿を構築すること。SEED CONCEPT に「登場人物」の列挙があれば、その主要キャラクターを必ず含めること。列挙が無い場合は、SEED の WORLD・TENSION・THEME から主要人物を設計すること。いずれの場合も、以下をすべて満たすこと：

1. **主人公（POVキャラクター）** — SEED の登場人物。列挙が無ければ世界と対立から導く
   - 完全な wound/want/need/lie 連鎖
   - 正当性付きの三つのスライダー
   - アークタイプ（正／負／静的）
   - 詳細な話し方（8次元）
   - 身体的習慣と癖
   - 少なくとも2つの秘密
   - 主要な関係性をマッピング

2. **もう一人の主要キャラクター（SEED の登場人物。無ければ世界と対立から導く）**
   - 主人公と同じ深さ
   - 主人公との関係、彼女／彼が知っていること、隠していること

3. **導き手／謎の存在（SEED の登場人物。無ければ世界と対立から導く）**
   - 物語の中心的な謎を体現する
   - 彼／彼女の真の目的が徐々に明らかになる

4. **敵対者／対立する人物（SEED の登場人物。無ければ世界と対立から導く）**
   - 悪役ではなく——彼／彼女の利害が主人公と衝突する人物
   - 彼／彼女自身の傷／望み／必要／嘘（彼／彼女が理解できるものであるべき）

5. **恋愛対象／因縁のある人物（SEED の登場人物。無ければ世界と対立から導く）**
   - 禁断の恋や過去の因縁を担う
   - テーマ的に何を代表するか

6. **物語がさらに必要とする、少なくとも1〜2人の追加キャラクター**
   - 主人公の同輩／友人？
   - 敵対者の側で主人公を知っている誰か？
   - 割れた忠誠を持つ人物？

各キャラクターに含めること：
- 名前、年齢、役割
- 傷／望／必要／裏の連鎖（主要キャラクター用）
- 三つのスライダー（プロアクティビティ／好感度／能力）を数字と正当性付きで
- アークタイプとアーク軌道
- 話し方（全8次元、例文付き）
- 外見（具体的で、一般的でない）
- 身体的習慣と無意識の裏
- 秘密（読者がすぐに知らないもの）
- 主要な関係性（他のキャラクターにマッピング）
- テーマ的な役割（このキャラクターはどんな問いを体現するか？）

重要：
- キャラクターは相互接続すること。彼らの望は互いに衝突するべき。
- すべての秘密は、もし明かされたら物語を変えるものであるべき。
- 話し方は、タグなしテストに合格するほど個別でなければならない。
- 主人公には彼／彼女の特異な才能に由来する習慣を与えること。
- 敵対者は主人公と同じくらい完全に実現されるべき——手ごたえのある敵対者に。
- 目標は約3000〜4000語。肉付けではなく密度のあるキャラクター仕事。

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
# Write the character registry to characters.md
characters_path = project_dir() / "characters.md"
characters_path.write_text(result.strip() + "\n")
print(result)
