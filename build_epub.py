#!/usr/bin/env python3
"""原稿から縦書き・右開きの EPUB を組み立てる（作品非依存）。

作品の情報は「作品フォルダ」側にだけ置き、このコードには書かない:
  出版/epub.json     … 著者名・行間・ルビ表・目次に何を出すか・表紙の有無
  出版/あとがき.md   … あとがき（無ければページを作らない）
  出版/既刊案内.md   … 著書一覧（無ければページを作らない）
  出版/奥付.md       … 奥付（無ければページを作らない）
  出版/表紙.jpg      … epub.json で "cover": true のときだけ使う
  本文/NN-第N章.md   … 本文（無ければ chapters/ch_NN.md を使う）
  題名は seed.txt の「題名:」から（epub.json の "title" が優先）

使い方:
    .venv/bin/python build_epub.py --project /path/to/作品 --font /path/to/NotoSerifJP-sub.ttf
"""
from __future__ import annotations

import argparse
import html
import os
import json
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CONFIG = {
    "title": None,          # None なら seed.txt の「題名:」
    "author": None,         # None なら epub.json 必須
    "line_height": 2.0,
    "font_size": None,      # 例 "1em"
    "cover": False,
    "colophon_horizontal": True,
    "toc": {"enabled": True, "chapters": True, "afterword": True,
            "books_list": False, "colophon": False},
    "ruby": [],             # [["語幹","よみ"], ...]
    "description": None,    # OPF の dc:description（無ければ題名だけ）
}


def zen(n: int) -> str:
    return "".join(chr(ord("０") + int(c)) if c.isdigit() else c for c in str(n))


def esc(t: str) -> str:
    return html.escape(t, quote=False)


def load_config(project: Path) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg["toc"] = dict(DEFAULT_CONFIG["toc"])
    path = project / "出版" / "epub.json"
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(user.get("toc"), dict):
            cfg["toc"].update(user.pop("toc"))
        cfg.update(user)
    if not cfg.get("title"):
        seed = project / "seed.txt"
        if seed.exists():
            m = re.search(r"^題名[:：]\s*(.+)$", seed.read_text(encoding="utf-8"), re.M)
            if m:
                cfg["title"] = m.group(1).strip()
    if not cfg.get("title"):
        raise SystemExit("題名が決まりません: seed.txt の「題名:」か epub.json の title を設定してください")
    if not cfg.get("author"):
        raise SystemExit("著者名がありません: epub.json の author を設定してください")
    return cfg


def chapter_sources(project: Path) -> list[tuple[int, str, list[str]]]:
    """(章番号, 章題, 段落リスト) を返す。本文/NN-第N章.md を優先。"""
    out = []
    body_dir = project / "本文"
    if body_dir.is_dir():
        cand = [x for x in body_dir.glob("*.md") if re.search(r"\d+", x.name)]
        files = sorted(cand, key=lambda p: int(re.search(r"(\d+)", p.name).group(1)))
        for p in files:
            n = int(re.search(r"(\d+)", p.name).group(1))
            lines = p.read_text(encoding="utf-8").split("\n")
            title = lines[0].lstrip("# ").strip()
            out.append((n, title, [l.strip() for l in lines[1:] if l.strip() and not l.strip().startswith("#")]))
        return out
    ch_dir = project / "chapters"
    for p in sorted(ch_dir.glob("ch_*.md")):
        n = int(re.search(r"ch_(\d+)", p.name).group(1))
        lines = p.read_text(encoding="utf-8").split("\n")
        title = lines[0].lstrip("# ").strip()
        out.append((n, title, [l.strip() for l in lines[1:] if l.strip() and not l.strip().startswith("#")]))
    return out


def apparatus(project: Path) -> dict[str, list[str] | None]:
    """あとがき・既刊案内・奥付を読む（無ければ None）。"""
    def lines(name: str, keep_headings: bool = False) -> list[str] | None:
        p = project / "出版" / name
        if not p.exists():
            return None
        skip = ("<!--", "---", "title:", "#") if not keep_headings else ("<!--", "---", "title:")
        return [l.strip() for l in p.read_text(encoding="utf-8").split("\n")
                if l.strip() and not l.strip().startswith(skip)]
    return {"afterword": lines("あとがき.md"), "books": lines("既刊案内.md", keep_headings=True),
            "colophon": lines("奥付.md")}


def page(title: str, body: str, css: str = "../style.css") -> str:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ja" lang="ja">
<head>
<meta charset="utf-8"/>
<title>{esc(title)}</title>
<link rel="stylesheet" type="text/css" href="{css}"/>
</head>
<body>
{body}
</body>
</html>
'''


def build(project: Path, out: Path, font: Path, cfg: dict) -> dict:
    OEBPS = "OEBPS"
    files: list[tuple[str, bytes]] = []
    title, author = cfg["title"], cfg["author"]
    ruby_table = sorted((tuple(x) for x in cfg.get("ruby") or []), key=lambda x: -len(x[0]))
    ruby_count = 0

    def apply_ruby(text: str, done: set) -> str:
        nonlocal ruby_count
        for stem, reading in ruby_table:
            if stem in done:
                continue
            if stem in text:
                text = text.replace(stem, f"<ruby>{stem}<rp>（</rp><rt>{reading}</rt><rp>）</rp></ruby>", 1)
                done.add(stem)
                ruby_count += 1
        return text

    def para(t: str, cls_extra: str = "") -> str:
        cls = ' class="serifu"' if t.startswith("「") else (f' class="{cls_extra}"' if cls_extra else "")
        return f"<p{cls}>{esc(t)}</p>"

    # 本文
    pages: list[tuple[str, str]] = []
    chapters = chapter_sources(project)
    if not chapters:
        raise SystemExit("本文が見つかりません（本文/*.md か chapters/ch_*.md）")
    ch_pages: list[tuple[str, str]] = []
    for n, ch_title, paras in chapters:
        heading = f"第{zen(n)}章　{ch_title}"
        done: set = set()
        body = [f'<h1>{apply_ruby(esc(heading), done)}</h1>']
        for t in paras:
            cls = ' class="serifu"' if t.startswith("「") else ""
            body.append(f'<p{cls}>{apply_ruby(esc(t), done)}</p>')
        fn = f"{n + 2:03d}_ch{n:02d}.xhtml"
        files.append((f"{OEBPS}/text/{fn}", page(f"{title} / {heading}", "\n".join(body)).encode("utf-8")))
        ch_pages.append((fn, heading))
    pages.extend(ch_pages)

    # あとがき
    app = apparatus(project)
    if app["afterword"]:
        body = ["<h1>あとがき</h1>"] + [para(t) for t in app["afterword"]]
        files.append((f"{OEBPS}/text/{len(ch_pages)+3:03d}_afterword.xhtml",
                      page(f"{title} / あとがき", "\n".join(body)).encode("utf-8")))
        pages.append((f"{len(ch_pages)+3:03d}_afterword.xhtml", "あとがき"))
    # 著書一覧
    if app["books"]:
        body, first = [], True
        for line in app["books"]:
            if line.startswith("## "):
                body.append(f"<h2>{esc(line[3:].strip())}</h2>"); first = True
            elif line.startswith("# "):
                body.append(f"<h1>{esc(line[2:].strip())}</h1>"); first = True
            elif line.startswith("---"):
                body.append("<hr/>"); first = True
            else:
                body.append(f'<p class="list">{esc(line)}</p>'); first = False
        fn = f"{len(ch_pages)+4:03d}_books_list.xhtml"
        files.append((f"{OEBPS}/text/{fn}", page(f"{title} / 著書一覧", "\n".join(body)).encode("utf-8")))
        books_page = fn
    else:
        books_page = None
    # 奥付
    if app["colophon"]:
        fn = f"{len(ch_pages)+5:03d}_colophon.xhtml"
        body = "\n".join(f"<p>{esc(t)}</p>" for t in app["colophon"])
        p = page(title, body).replace("<body>", '<body class="colophon">')
        files.append((f"{OEBPS}/text/{fn}", p.encode("utf-8")))
        colophon_page = fn
    else:
        colophon_page = None

    # 題名ページ（書名だけ・縦組みで中央）
    tp_title = esc(title)
    for ch, reading in (cfg.get("title_ruby") or {}).items():
        tp_title = tp_title.replace(esc(ch), f"<ruby>{esc(ch)}<rp>（</rp><rt>{esc(reading)}</rt><rp>）</rp></ruby>", 1)
    tp = f'<h1 class="title">{tp_title}</h1>'
    files.append((f"{OEBPS}/text/001_titlepage.xhtml",
                  page(title, tp).replace("<body>", '<body class="titlepage">').encode("utf-8")))

    # 目次ページ
    toc_cfg = cfg["toc"]
    toc_enabled = bool(toc_cfg.get("enabled", True))
    if toc_enabled:
        toc_items = []
        if toc_cfg.get("chapters", True):
            toc_items += ch_pages
        if toc_cfg.get("afterword", True) and app["afterword"]:
            toc_items += [p for p in pages if p[1] == "あとがき"]
        if toc_cfg.get("books_list") and books_page:
            toc_items += [(books_page, "著書一覧")]
        if toc_cfg.get("colophon") and colophon_page:
            toc_items += [(colophon_page, "奥付")]
        body = "<h1>目次</h1>\n" + "\n".join(
            f'<p class="toc-item"><a href="{fn}">{esc(t)}</a></p>' for fn, t in toc_items)
        files.append((f"{OEBPS}/text/002_toc.xhtml",
                      page(f"{title} / 目次", body).replace("<body>", '<body class="toc">').encode("utf-8")))
        nav_pages = toc_items
    else:
        nav_pages = ch_pages

    # 表紙（epub.json で cover:true のときだけ）
    cover_item = ""
    if cfg.get("cover"):
        for name in ("表紙.jpg", "cover.jpg"):
            cp = project / "出版" / name
            if cp.exists():
                files.append((f"{OEBPS}/images/cover.jpg", cp.read_bytes()))
                files.append((f"{OEBPS}/text/000_cover.xhtml",
                              page(title, '<div class="coverpage"><img class="cover" src="../images/cover.jpg"'
                                          f' alt="{esc(title)}"/></div>').replace("<body>", '<body class="coverpage-body">').encode("utf-8")))
                cover_item = '    <item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>\n'
                break

    # CSS
    lh = cfg.get("line_height", 2.0)
    fs = f'  font-size: {cfg["font_size"]};\n' if cfg.get("font_size") else ""
    css = f'''@font-face {{
  font-family: "Noto Serif JP";
  src: url("fonts/NotoSerifJP-Regular-subset.ttf");
  font-weight: normal;
  font-style: normal;
}}
html, body {{
  writing-mode: vertical-rl;
  -webkit-writing-mode: vertical-rl;
  -epub-writing-mode: vertical-rl;
  text-orientation: mixed;
}}
body {{
  font-family: "Noto Serif JP", "Yu Mincho", "MS Mincho", serif;
{fs}  line-height: {lh};
  margin: 1em;
  padding: 0;
}}
h1 {{ font-size: 1.6em; text-align: center; margin-top: 2em; margin-bottom: 1.5em; font-weight: bold; }}
h2 {{ font-size: 1.3em; margin-top: 1.5em; margin-bottom: 1em; font-weight: bold; }}
p {{ text-indent: 1em; margin: 0; }}
p.serifu {{ text-indent: 0; }}
h1 + p, h2 + p, hr + p {{ text-indent: 0; }}
hr {{ border: none; text-align: center; margin: 1.5em 0; }}
ruby {{ ruby-align: center; }}
rt {{ font-size: 0.5em; letter-spacing: 0; line-height: 1; }}
img.cover {{ max-width: 100%; max-height: 100%; }}
.coverpage {{ margin: 0; padding: 0; text-align: center; }}
.coverpage-body {{ margin: 0; padding: 0; }}
body.titlepage {{ margin: 0; padding: 0; }}
body.titlepage h1.title {{
  margin: 0 30% 0 0;
  padding: 0;
  font-size: 2.4em;
  line-height: 1.6;
  letter-spacing: 0.1em;
  text-align: center;
  font-weight: bold;
}}
body.toc h1 {{ text-align: start; margin: 0 0 1.5em 0; }}
body.toc p.toc-item {{ text-indent: 0; margin: 0; line-height: {lh}; }}
body.toc p.toc-item a {{ color: #000; text-decoration: none; }}
p.list {{ text-indent: 0; }}
'''
    if cfg.get("colophon_horizontal", True):
        css += '''body.colophon {
  writing-mode: horizontal-tb;
  -webkit-writing-mode: horizontal-tb;
  -epub-writing-mode: horizontal-tb;
  text-align: left;
  margin: 30% 16% 0 16%;
}
body.colophon p { text-indent: 0; margin: 0 0 0.4em 0; }
'''
    files.append((f"{OEBPS}/style.css", css.encode("utf-8")))
    files.append((f"{OEBPS}/fonts/NotoSerifJP-Regular-subset.ttf", font.read_bytes()))

    # nav / ncx
    nav_items = "\n".join(f'      <li><a href="text/{fn}">{esc(t)}</a></li>' for fn, t in nav_pages)
    files.append((f"{OEBPS}/nav.xhtml", f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ja" lang="ja">
<head><meta charset="utf-8"/><title>目次</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>目次</h1>
    <ol>
{nav_items}
    </ol>
  </nav>
</body>
</html>
'''.encode("utf-8")))
    bookid = f"urn:uuid:{uuid.uuid4()}"
    np = "\n".join(
        f'    <navPoint id="np{i}" playOrder="{i+1}"><navLabel><text>{esc(t)}</text></navLabel>'
        f'<content src="text/{fn}"/></navPoint>' for i, (fn, t) in enumerate(nav_pages))
    files.append((f"{OEBPS}/toc.ncx", f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="ja">
  <head>
    <meta name="dtb:uid" content="{bookid}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{esc(title)}</text></docTitle>
  <navMap>
{np}
  </navMap>
</ncx>
'''.encode("utf-8")))

    # OPF
    desc = cfg.get("description") or f"{title}（{author}）"
    manifest = ['    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '    <item id="css" href="style.css" media-type="text/css"/>',
                '    <item id="font" href="fonts/NotoSerifJP-Regular-subset.ttf" media-type="font/ttf"/>']
    if cover_item:
        manifest.append(cover_item.rstrip("\n"))
    spine_order = ([( "000_cover.xhtml", "")] if cover_item else []) + [("001_titlepage.xhtml", "")]
    if toc_enabled:
        spine_order.append(("002_toc.xhtml", ""))
    spine_order += ch_pages
    if app["afterword"]:
        spine_order.append((f"{len(ch_pages)+3:03d}_afterword.xhtml", "あとがき"))
    if books_page:
        spine_order.append((books_page, ""))
    if colophon_page:
        spine_order.append((colophon_page, ""))
    spine = []
    for i, (fn, _) in enumerate(spine_order):
        pid = f"p{i:03d}"
        manifest.append(f'    <item id="{pid}" href="text/{fn}" media-type="application/xhtml+xml"/>')
        spine.append(f'    <itemref idref="{pid}"/>')
    meta_cover = '    <meta name="cover" content="cover-image"/>\n' if cover_item else ""
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="ja">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{bookid}</dc:identifier>
    <dc:title>{esc(title)}</dc:title>
    <dc:creator id="creator">{esc(author)}</dc:creator>
    <meta refines="#creator" property="role" scheme="marc:relators">aut</meta>
    <dc:language>ja</dc:language>
    <dc:publisher>{esc(author)}</dc:publisher>
    <dc:date>{cfg.get("date", datetime.now().strftime("%Y-%m"))}</dc:date>
    <dc:rights>{esc(cfg.get("rights") or f"© {datetime.now().year} {author}")}</dc:rights>
    <dc:description>{esc(desc)}</dc:description>
    <meta property="dcterms:modified">{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}</meta>
    <meta name="primary-writing-mode" content="vertical-rl"/>
{meta_cover}  </metadata>
  <manifest>
{chr(10).join(manifest)}
  </manifest>
  <spine page-progression-direction="rtl" toc="ncx">
{chr(10).join(spine)}
  </spine>
</package>
'''
    files.append((f"{OEBPS}/content.opf", opf.encode("utf-8")))
    files.append(("META-INF/container.xml", b'''<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
'''))

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        backup_dir = Path(os.environ.get("AUTONOVEL_BACKUP_DIR", str(Path.home() / "backups")))
        backup = backup_dir / f"{title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.epub"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(out), str(backup))
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in files:
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return {"path": str(out), "ruby": ruby_count, "files": len(files), "spine": len(spine),
            "chapters": len(ch_pages), "bookid": bookid}


def main() -> int:
    ap = argparse.ArgumentParser(description="原稿から縦書き・右開きの EPUB を作る")
    ap.add_argument("--project", default=".", help="作品フォルダ（既定: カレント）")
    ap.add_argument("--font", required=True, help="埋め込むサブセットフォント（.ttf）")
    ap.add_argument("--out", default=None, help="出力先（既定: <project>/出版/<題名>.epub）")
    a = ap.parse_args()
    project = Path(a.project).resolve()
    cfg = load_config(project)
    out = Path(a.out) if a.out else project / "出版" / f"{cfg['title']}.epub"
    info = build(project, out, Path(a.font), cfg)
    print(f"書いた: {info['path']} ({Path(info['path']).stat().st_size/1024/1024:.2f} MB)")
    print(f"章: {info['chapters']} / spine: {info['spine']}ページ / ルビ: {info['ruby']}箇所 / ファイル: {info['files']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
