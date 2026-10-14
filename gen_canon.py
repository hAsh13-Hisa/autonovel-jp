#!/usr/bin/env python3
"""
Generate canon.md by extracting all hard facts from world.md + characters.md.
"""
import os
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
        system="あなたはファンタジー小説の企画文書から確定事実を抽出するコンティニュイティ編集者です。あなたは正確で網羅的であり、原文にない事実を決して捏造しません。出力は必ず日本語で書いてください。",
        max_tokens=max_tokens,
        temperature=0.2,
    )


world = (project_dir() / "world.md").read_text()
characters = (project_dir() / "characters.md").read_text()
seed = (project_dir() / "seed.txt").read_text()

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
canon_path = project_dir() / "canon.md"
canon_path.write_text(result.strip() + "\n")
print(result)
