#!/usr/bin/env python3
"""manuscript.md（または chapters/）を Word の .docx にする。

なぜあるか:
    小説の完成形は md だが、**編集・校了・入稿の現場は Word** が多い。
    md の `#` 見出しを Word の「見出し1」スタイルにするので、
    Word のナビゲーション窓（見出しの一覧）と目次がそのまま使える。
    これで「出版物に近い形」で人に渡せる。

使い方:
    python export_docx.py                      # chapters/ → 出版/<題名>.docx
    python export_docx.py manuscript.md        # 1本のmdをdocxに
    python export_docx.py --title "題"          # ファイル名を指定
    python export_docx.py --with-toc           # 冒頭に目次も入れる

設計（Word で自然に見えるように）:
    - `#` → 見出し1（章）、`##` → 見出し2、`###` → 見出し3
    - 章の始まりは**次の章から改ページ**（章頭がページ先頭＝本の常識）
    - 段落は字下げ1字（日本語の小説の書き形）。Chapter の md に
      `---`（水平線）が残っていても無視する（章頭の改ページで足りる）
    - フォントは日本語の明朝系を指定（端末に無ければ Word 側で解決）
    - 横書き（縦書きは Word 側のページ設定で変える。docx の縦書きは
      Word 依存が強く、Python から確実に効かせられないため）
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml.ns import qn
from docx.shared import Pt

# ---------------------------------------------------------------------------
# 場所の決め方（他の export スクリプトと同じ規則）
# ---------------------------------------------------------------------------
# 作り直しの作業はコピー先で行うので、**カレント優先**で見る。
# BASE_DIR 固定だと作り直し版の章を読めず、別の章（または0章）を
# docx にしてしまう。実測 2026-10-15: run_pipeline.py から回した時に
# 「章が見つかりません」になった（BASE_DIR＝リポジトリ側で chapters/ が無い）。
BASE_DIR = Path(__file__).resolve().parent
_CHAPTERS_FALLBACK = BASE_DIR / "chapters"


def project_dir() -> Path:
    """作品ファイルの場所。カレント優先。"""
    here = Path.cwd()
    if (here / "chapters").is_dir() or (here / "characters.md").exists() \
            or (here / "outline.md").exists() or (here / "manuscript.md").exists():
        return here
    return BASE_DIR


def chapters_dir() -> Path:
    """章ファイルのある場所。カレント優先（見つからなければリポジトリ側）。"""
    here = Path.cwd() / "chapters"
    if here.is_dir() and any(here.glob("ch_*.md")):
        return here
    return _CHAPTERS_FALLBACK

# 章題から作品題名を作る時の既定（題名が取れない場合）
DEFAULT_TITLE = "novel"

# 日本語の本文書体（Word 側に無ければ Word が代替する）
JP_BODY = "游明朝"        # Windows の定番。Mac なら "ヒラギノ明朝 ProN"
JP_HEAD = "游明朝"

HEADING_STYLE = {1: "Heading 1", 2: "Heading 2", 3: "Heading 3"}


def set_jp_font(style, name: str, size_pt: float | None = None,
                bold: bool | None = None) -> None:
    """フォント名を日本語向けに2箇所（ascii と eastAsia）に設定する。

    なぜ2箇所: Word は latin（英数）と eastAsia（日本語）で別のフォントを
    持つ。ascii だけ変えると英数字だけが変わって日本語が元のままになる。
    """
    f = style.font
    f.name = name
    if size_pt is not None:
        f.size = Pt(size_pt)
    if bold is not None:
        f.bold = bold
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)


def chapter_files() -> list[Path]:
    cd = chapters_dir()
    if not cd.is_dir():
        return []
    return sorted(cd.glob("ch_*.md"))


def strip_ruby(text: str) -> str:
    """青空文庫のルビ記法（｜親《読み》）が残っていても読みだけ除く。"""
    return re.sub(r"｜([^《》｜]*)《([^》]*)》", r"\1", text)


def load_paragraphs(path: Path) -> list[tuple[int | None, str]]:
    """md を (見出しレベル or None, 本文) の列にする。

    見出しレベル: 1〜3（# の数）。None は通常段落。
    `---` の水平線、空でない行だけを拾う（空行は段落の区切り）。
    """
    out: list[tuple[int | None, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", s):
            continue          # 章の区切り線。改ページで足りるので捨てる
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            out.append((len(m.group(1)), strip_ruby(m.group(2)).strip()))
            continue
        out.append((None, strip_ruby(s)))
    return out


def guess_title(paths: list[Path]) -> str:
    """作品題名を推定する。優先順位:

    1. `seed.txt` の「題名:」行（パイプライン唯一の入力）
    2. 渡された md が1冊まとめ（manuscript.md 等）なら先頭の `#`
    3. `state.json` の title（あれば）
    4. 最初の章の `#` から「第1章：題」の題（= 作品名とは限らない）

    なぜ章題を既定にしないか: 章題と作品名は別物。
    「鉄の肺と錆びた聖歌」という作品の第1章は「錆びた澱の静寂」で、
    章題を題名にするとファイル名が別の本のようになってしまう
    （2026-10-15 実測で発生）。ここに来るのは最後の手段。
    """
    # 1. seed.txt（正本）
    seed = project_dir() / "seed.txt"
    if seed.exists():
        m = re.search(r"^\s*(?:#{1,6}\s*)?(?:題名|タイトル|Title)\s*[:：]\s*(.+)$",
                      seed.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1).strip().strip("*").strip()

    # 2. 1冊まとめの md が渡されたなら、その `#`
    if len(paths) == 1:
        for lvl, text in load_paragraphs(paths[0]):
            if lvl == 1:
                t = re.sub(r"^第\s*\d+\s*章\s*[:：]?\s*", "", text).strip()
                if t and not re.fullmatch(r"第\s*\d+\s*章", t):
                    return t
                break

    # 3. state.json
    st = project_dir() / "state.json"
    if st.exists():
        try:
            import json
            t = str(json.loads(st.read_text(encoding="utf-8")).get("title", "") or "").strip()
            if t:
                return t
        except Exception:
            pass

    # 4. 最初の章の章題（最後の手段）
    for p in paths:
        for lvl, text in load_paragraphs(p):
            if lvl == 1:
                t = re.sub(r"^第\s*\d+\s*章\s*[:：]?\s*", "", text).strip()
                if t and not re.fullmatch(r"第\s*\d+\s*章", t):
                    return t
                break
        break
    return DEFAULT_TITLE


def build_docx(paths: list[Path], title: str, with_toc: bool) -> tuple[Document, int]:
    doc = Document()

    # --- スタイル（日本語の小説の書き形）---
    normal = doc.styles["Normal"]
    set_jp_font(normal, JP_BODY, 11.0)
    pf = normal.paragraph_format
    pf.line_spacing = 1.5
    pf.space_after = Pt(0)
    # 字下げ1字（全角1文字分）。first_line_indent は em ではなく
    # 文字数で指定するので、本文の1文字の幅を 11pt と見なす。
    pf.first_line_indent = Pt(11.0)

    for lvl in (1, 2, 3):
        st = doc.styles[HEADING_STYLE[lvl]]
        set_jp_font(st, JP_HEAD, {1: 18.0, 2: 14.0, 3: 12.0}[lvl],
                    bold=(lvl == 1))
        st.paragraph_format.space_before = Pt(6)
        st.paragraph_format.space_after = Pt(12)
        st.paragraph_format.first_line_indent = Pt(0)

    if with_toc:
        doc.add_heading("目次", level=1)
        # Word の目次フィールド（開いたときに F9 で更新される）
        p = doc.add_paragraph()
        run = p.add_run()
        fld = run._r.makeelement(qn("w:fldSimple"), {
            qn("w:instr"): r'TOC \o "1-3" \h \z \u',
        })
        p._p.append(fld)

    n_para = 0
    for i, path in enumerate(paths):
        if i > 0:
            # 章頭は次の章から改ページ
            br = doc.add_paragraph()
            br.add_run().add_break(WD_BREAK.PAGE)
        for lvl, text in load_paragraphs(path):
            if lvl:
                doc.add_heading(text, level=lvl)
            else:
                para = doc.add_paragraph(text)
                # 会話文（「」始まり）は字下げしない（小説の慣習）
                if text.startswith("「"):
                    para.paragraph_format.first_line_indent = Pt(0)
                n_para += 1
    return doc, n_para


def main() -> int:
    ap = argparse.ArgumentParser(description="md の原稿を Word の docx にする")
    ap.add_argument("source", nargs="?",
                    help="md ファイル（省略時は chapters/ 全体）")
    ap.add_argument("--title", default="", help="出力ファイル名の題名")
    ap.add_argument("--with-toc", action="store_true",
                    help="冒頭に目次も入れる（Word で F9 で更新）")
    ap.add_argument("--out", default="", help="出力先のファイルパス")
    args = ap.parse_args()

    if args.source:
        paths = [Path(args.source)]
        if not paths[0].exists():
            print(f"見つかりません: {args.source}", file=sys.stderr)
            return 1
    else:
        paths = chapter_files()
        if not paths:
            print("章が見つかりません（chapters/ch_*.md）", file=sys.stderr)
            return 1

    title = args.title or guess_title(paths)
    doc, n_para = build_docx(paths, title, args.with_toc)

    out = Path(args.out) if args.out else project_dir() / "出版" / f"{title}.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))

    print(f"書いた: {out}")
    print(f"  題名: {title}")
    print(f"  入力: {len(paths)}ファイル")
    print(f"  段落: {n_para}本文 / 見出し {sum(1 for p in paths for l, _ in load_paragraphs(p) if l)}件")
    print(f"  目次: {'あり（Word で F9 で更新）' if args.with_toc else 'なし'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
