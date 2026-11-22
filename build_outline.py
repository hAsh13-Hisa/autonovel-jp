#!/usr/bin/env python3
"""Rebuild outline.md from the actual chapters.

Reads each chapter, calls the LLM for a structured summary,
and assembles into an outline that reflects the novel as-written.

2026-11-22 修正（2026-11-19 のコードレビュー指摘）:
- 章を 1〜19 の決め打ちで回していた → `chapters_dir()` の実在ファイルを列挙する
- 見出しを別作品名 `THE SECOND SON OF THE HOUSE OF BELLS` で固定していた
  → `seed.txt` の「タイトル：」から取り、無ければ本文の第1章見出しを使う
- 章数を `23` で固定していた → 実測（実在章数と字数）
- 出力の見出しが英語 `### Ch N` だった → 本文パーサ（`## 第N章` / `- **ビート：**`）が
  読める日本語形式にする。export のたびに outline.md が英語形式で上書きされ、
  以降の工程が章立てを見失う事故を防ぐ
- 読み込んでいた `characters` / `old_outline` は未使用だったので削除
"""
import json
import os
import re
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

import llm
import safeio

CHAPTERS_DIR = BASE_DIR / "chapters"


def repo_dir() -> Path:
    """autonovel-jp のリポジトリ本体（共有ガイドの置き場所）。

    なぜ必要か（実測 2026-10-14）: コピー先で動かすと BASE_DIR は
    Path(__file__).parent＝コピー先自身を指す。
    → AUTONOVEL_REPO で渡す（run_pipeline.py が設定する）。
    """
    env = os.environ.get("AUTONOVEL_REPO")
    if env and Path(env).is_dir():
        return Path(env)
    return BASE_DIR


def chapters_dir() -> Path:
    """章ファイルのある場所。カレント優先（見つからなければリポジトリ側）。

    なぜ: 作り直しの作業はコピー先で行う。BASE_DIR 固定だと作り直し版の
    章を読めず、outline.md を古い章から作り直してしまう（実測 2026-10-14）。
    """
    here = Path.cwd() / "chapters"
    if here.is_dir() and any(here.glob("ch_*.md")):
        return here
    return CHAPTERS_DIR


def project_dir() -> Path:
    """作品ファイル（characters.md / outline.md 等）の場所。カレント優先。"""
    here = Path.cwd()
    if (here / "characters.md").exists() or (here / "outline.md").exists() \
            or (here / "chapters").is_dir():
        return here
    return BASE_DIR


def seed_title() -> str:
    """題名は seed.txt の「タイトル：」から（無ければ空文字）。"""
    seed = project_dir() / "seed.txt"
    if seed.exists():
        for line in seed.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("タイトル"):
                return line.split("：", 1)[-1].split(":", 1)[-1].strip()
    return ""


def jp_char_count(text: str) -> int:
    """空白・改行を除いた文字数（章ゲートと同じ物差し）。"""
    return len(re.sub(r"[\r\n\t ]", "", text))


def call_model(prompt, max_tokens=1500):
    text = llm.call_judge(
        prompt,
        system="You produce structured outline entries for novel chapters. Be precise about what HAPPENS, what CHANGES, and what threads are planted/harvested. Output valid JSON only.",
        max_tokens=max_tokens,
        temperature=0.1,
    )
    # Extract JSON from response
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    return json.loads(text)


def main():
    cdir = chapters_dir()
    paths = sorted(cdir.glob("ch_*.md"))
    if not paths:
        print(f"ERROR: {cdir} に章ファイルがありません", file=sys.stderr)
        sys.exit(1)

    entries = []
    for path in paths:
        m = re.search(r"ch_(\d+)", path.name)
        if not m:
            print(f"  章番号が読めないので飛ばします: {path.name}", file=sys.stderr)
            continue
        ch = int(m.group(1))
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            print(f"  空なので飛ばします: {path.name}", file=sys.stderr)
            continue
        wc = jp_char_count(text)

        title_line = text.strip().split('\n')[0].lstrip('# ').strip()

        prompt = f"""Analyze this chapter and produce a structured outline entry.

CHAPTER {ch}: "{title_line}" ({wc} chars)

{text}

Return JSON with these fields:
- "title": the chapter title (string)
- "location": primary setting (string)
- "characters": list of characters who appear (list of strings)
- "summary": 2-3 sentence summary of what happens, in Japanese (string)
- "beats": list of 3-5 key story beats in order, in Japanese (list of strings)
- "try_fail": the try-fail cycle type: "yes-but", "no-and", "yes-and", or "no-but" (string)
- "plants": foreshadowing threads PLANTED in this chapter, in Japanese (list of strings)
- "harvests": foreshadowing threads PAID OFF in this chapter, in Japanese (list of strings)
- "emotional_arc": one sentence describing the emotional movement, in Japanese (string)
- "chapter_question": the question left open at chapter's end, in Japanese (string)

JSON only, no other text."""

        data = call_model(prompt)
        data["num"] = ch
        data["words"] = wc
        entries.append(data)
        print(f"  {ch:2d}. {title_line} ({wc}字)")

    title = seed_title() or entries[0]["title"]
    total_chars = sum(e["words"] for e in entries)

    # Build new outline（本文パーサが読める日本語形式）
    lines = []
    lines.append(f"# {title}")
    lines.append("## 章アウトライン（本文から再構成）")
    lines.append("")
    lines.append(f"**{len(entries)}章 / {total_chars:,}字**")
    lines.append("")
    lines.append("---")
    lines.append("")

    for e in entries:
        lines.append(f"## 第{e['num']}章：{e['title']}")
        lines.append("")
        lines.append(f"- **場所：**{e.get('location', 'N/A')}")
        lines.append(f"- **登場人物：**{', '.join(e.get('characters', []))}")
        lines.append(f"- **トライ・フェイル：**{e.get('try_fail', 'N/A')}")
        lines.append(f"- **感情のアーク：**{e.get('emotional_arc', 'N/A')}")
        lines.append("")
        lines.append("- **ビート：**")
        for i, b in enumerate(e.get("beats", []), 1):
            lines.append(f"  {i}. {b}")
        lines.append("")
        if e.get("plants"):
            lines.append("- **植え込み：**")
            for p in e["plants"]:
                lines.append(f"  - {p}")
            lines.append("")
        if e.get("harvests"):
            lines.append("- **回収：**")
            for h in e["harvests"]:
                lines.append(f"  - {h}")
            lines.append("")
        lines.append(f"- **要約：**{e.get('summary', 'N/A')}")
        lines.append("")
        lines.append(f"- **章末の問い：**{e.get('chapter_question', 'N/A')}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Foreshadowing ledger
    lines.append("## 伏線台帳")
    lines.append("")
    lines.append("| 糸 | 植え込み（章） | 回収（章） |")
    lines.append("|----|---------------|-----------|")

    all_plants = {}
    all_harvests = {}
    for e in entries:
        for p in e.get("plants", []):
            key = p[:60]
            all_plants.setdefault(key, []).append(e["num"])
        for h in e.get("harvests", []):
            key = h[:60]
            all_harvests.setdefault(key, []).append(e["num"])

    all_threads = set(list(all_plants.keys()) + list(all_harvests.keys()))
    for thread in sorted(all_threads):
        planted = ", ".join(f"第{n}章" for n in all_plants.get(thread, []))
        harvested = ", ".join(f"第{n}章" for n in all_harvests.get(thread, []))
        lines.append(f"| {thread} | {planted} | {harvested} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*本文から再構成した章立て。*")

    out = "\n".join(lines)
    safeio.safe_write(project_dir() / "outline.md", out, min_chars=200)
    print(f"\nSaved outline.md ({total_chars:,}字 / {len(entries)}章)")


def cli() -> None:
    """他のスクリプトと同じく `--help` を持たせる（引数は無い）。"""
    import argparse
    argparse.ArgumentParser(
        description="本文（chapters/ch_*.md）から outline.md を再構成する。").parse_args()


if __name__ == "__main__":
    cli()
    main()
