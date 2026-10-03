#!/usr/bin/env python3
"""Measure a chapter against Hisa's documented gates.

Usage: python dialogue_ratio.py chapters/ch_02.md

Reports the metrics Hisa enforces (all validated 2026-08-28):
  - Character-based dialogue ratio (chars inside 「」 / total) — Hisa's 7:3 target
    is ~70% BY CHARACTERS. Line-based ratio overstates badly (a line with one
    short quote + a long prose paragraph counts as a "dialogue line").
  - Total length (gate: >= 5,500-6,000 chars).
  - Sentence-level avg length measured on 。split (NOT 、split — 、-joined clauses
    are meaningful flow, not staccato fragments).
  - Duplicate-sentence scan (recursion check: >=10-char sentence appearing >=2x
    flags the split-drafter's document-wide recursion on quiet chapters).
  - Natural dialogue length (Hisa 2026-08-28): a single 「」quote must not be a
    wall of text. Target max ~120字, avg ~60字. Flag quotes >100字. Long
    monologues must be broken into natural-length quotes with prose beats between.

Exit code 0 always; read the numbers. This is a diagnostic, not a pass/fail gate.
verify_chapter_gates.py parses this stdout, so keep the labels stable.
"""
import re
import statistics
import sys
from collections import Counter


def main(path: str) -> None:
    text = open(path, encoding="utf-8").read()
    total = len(text)
    quotes = re.findall(r"「[^」]*」", text)
    dlg = sum(len(q) for q in quotes)
    dlg_nb = sum(len(q) - 2 for q in quotes)

    print(f"総文字数: {total}字  (gate: >=5500-6000)")
    print(f"台詞(括弧含む): {dlg}字 = {dlg / total * 100:.1f}%  (Hisa 7:3 = ~70%)")
    print(f"台詞(括弧除く): {dlg_nb}字 = {dlg_nb / total * 100:.1f}%")
    print(f"地文: {total - dlg}字 = {(total - dlg) / total * 100:.1f}%")

    if quotes:
        qlens = [len(q) for q in quotes]
        over100 = sum(1 for l in qlens if l > 100)
        print(
            f"台詞数: {len(quotes)}個  最長{max(qlens)}字 平均{statistics.mean(qlens):.0f}字 "
            f"中央値{statistics.median(qlens)}字  100字超:{over100}個  "
            f"(自然な長さ: 最長~120字・平均~60字)"
        )

    sents = [s.strip() for s in re.split(r"[。！？]+", text) if s.strip()]
    lens = [len(s) for s in sents]
    if lens:
        print(
            f"。区切り文: 平均{statistics.mean(lens):.1f} 中央値{statistics.median(lens)} "
            f"長文(>=20){sum(1 for l in lens if l >= 20) / len(lens) * 100:.0f}%"
        )

    c = Counter(sents)
    dups = {k: v for k, v in c.items() if v >= 2 and len(k) >= 12}
    print(f"重複文(12字以上,2回): {len(dups)}件  (0 = recursion-free)")
    for k, v in sorted(dups.items(), key=lambda x: -x[1])[:6]:
        print(f"  {v}回: {k}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
