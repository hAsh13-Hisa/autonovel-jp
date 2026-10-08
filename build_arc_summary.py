#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""読者パネル用の「全編あらすじ」を作る（改訂フェーズの入力）。

各章について、書き出し・結び・主要なセリフ・3文の要約を並べ、
本編を全部読ませずに物語の弧を評価できる形にする。

2026-10-08 に日本語版として書き直した。上流の雛形のままでは動かなかった点:
  - 章を `range(1, 20)` で数えていた → **第20章以降が抜け、24章の小説が19章として要約される**
  - 「all 23 chapters」「Total novel: ... words」を**決め打ちで書いていた**
  - 作品名と前提（PREMISE）に**別作品の設定が焼き込まれていた**
  - 書き出し・結びを英単語の空白区切りで切っていた（日本語では1段落まるごとになる）
  - system プロンプトが英語
→ 章は**実在するファイルから数え**、作品名と前提は **seed.txt（人間が書いた唯一の入力）**から取り、
  書き出し・結び・セリフは**字数**で切るようにした。
"""
import re
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

import llm

CHAPTERS_DIR = BASE_DIR / "chapters"
# 書き出し・結びとして見せる長さ（字数）。英単語150語ぶんに相当する日本語の目安。
PASSAGE_CHARS = 300


def call_writer(prompt, max_tokens=4000):
    return llm.call_writer(
        prompt,
        system=(
            "あなたは小説の各章を正確に要約する読み手です。"
            "何が起きたか、何が変わったか、何が未解決のまま残ったかを書いてください。"
            "評価や褒め言葉は書かないこと。事実と変化だけを書いてください。"
            "回答はすべて日本語で書いてください。"
        ),
        max_tokens=max_tokens,
        temperature=0.1,
    )


def list_chapters():
    """実在する章ファイルを章番号の順に返す（章数はここで決まる）。"""
    out = []
    for p in sorted(CHAPTERS_DIR.glob("ch_*.md")):
        m = re.match(r"ch_(\d+)\.md$", p.name)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


def jp_char_count(text: str) -> int:
    """字数（章ゲートの「文字数」と同じ数え方＝改行込みの生の文字数）。"""
    return len(text)


def extract_key_passages(text):
    """書き出し・結び・主要なセリフを取る（日本語の本文向け）。"""
    body = text.strip()
    opening = body[:PASSAGE_CHARS]
    closing = body[-PASSAGE_CHARS:]

    # セリフは「」で囲まれた20字以上のものを、長い順に3つ
    dialogue = re.findall(r"「([^「」]{20,})」", body)
    dialogue.sort(key=len, reverse=True)
    return opening, closing, dialogue[:3]


def novel_header() -> str:
    """作品名と前提を seed.txt から取る。

    人間が書いた唯一の入力なので、ここから取るのが最も確か。
    無ければ作品名は「（無題）」、前提は省く（別作品の設定を埋めない）。
    """
    seed = BASE_DIR / "seed.txt"
    title, hook, world = "", "", ""
    if seed.exists():
        for ln in seed.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            low = s.lower()
            if low.startswith("タイトル"):
                title = re.split(r"[:：]", s, 1)[-1].strip()
            elif low.startswith("hook"):
                hook = re.split(r"[:：]", s, 1)[-1].strip()
            elif low.startswith("world"):
                world = re.split(r"[:：]", s, 1)[-1].strip()
    head = f"# {title or '（無題）'}\n## 読者パネル用の全編あらすじ\n\n"
    premise = " ".join(x for x in (hook, world) if x)
    if premise:
        head += f"前提: {premise}\n\n"
    return head


def main():
    chapters = list_chapters()
    if not chapters:
        print("章ファイルがありません（chapters/ch_*.md）")
        return

    summaries = []
    total_chars = 0
    for ch, path in chapters:
        text = path.read_text(encoding="utf-8")
        chars = jp_char_count(text)
        total_chars += chars
        opening, closing, dialogue = extract_key_passages(text)

        summary = call_writer(
            f"次の章を3文で要約してください。何が起き、何が変わり、"
            f"何が未解決のまま残ったかを書いてください。\n\n第{ch}章:\n{text}",
            max_tokens=600,
        )

        entry = f"### 第{ch}章（{chars:,}字）\n**要約:** {summary.strip()}\n\n"
        entry += f"**書き出し:** {opening}…\n\n"
        entry += f"**結び:** …{closing}\n\n"
        entry += "**主要なセリフ:**\n"
        for d in dialogue:
            entry += f"> 「{d}」\n\n"
        summaries.append(entry)
        print(f"第{ch}章: 要約済み（{chars:,}字）")

    full = novel_header()
    full += (f"全{len(chapters)}章・総字数 {total_chars:,}字。\n"
             f"以下は各章の要約・書き出し・結び・主要なセリフ。\n\n---\n\n")
    full += "\n---\n\n".join(summaries)

    out_path = BASE_DIR / "arc_summary.md"
    out_path.write_text(full, encoding="utf-8")
    print(f"\n保存: {out_path}（{jp_char_count(full):,}字 / {len(chapters)}章）")


if __name__ == "__main__":
    main()
