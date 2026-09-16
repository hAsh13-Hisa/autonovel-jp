# -*- coding: utf-8 -*-
"""verify_character_consistency.py — キャラ登録簿 ↔ 本文/アウトラインの双方向整合チェック.

foundation の各文書（world/characters/outline/canon）は独立に生成されるため、
モデルが characters.md のキャラを使わず独自に追加したり（未定義キャラの登場）、
characters.md に定義したキャラが本文で一度も使われなかったり（未登場キャラ）する
逸脱が、品質ゲート（文体・セリフ比率のみ）をすり抜ける。

本スクリプトは以下を検証する:

【ハードFAIL — パイプラインをブロックする信頼できる信号】
  1. characters.md に定義されたキャラが outline.md / chapters/ に登場するか（未登場キャラ）
  2. outline.md の POV に登場するキャラが characters.md に定義されているか（未定義キャラ）

【警告 — 要確認（日本語は大文字小文字がなく頻度ベースの固有名詞検出は不正確なため）】
  3. outline.md（または chapters/）に高頻度で登場する語が characters.md に定義されておらず、
     かつ seed.txt の登場人物にもない場合（未定義キャラの候補）

作品固有の語彙はこのファイルに埋め込まず、リポジトリ側の語彙ファイルで与える:

  <repo>/verify_terms.txt    1行1語。世界観用語・概念語など「キャラ名ではないが
                             本文/アウトラインに頻出する語」を列挙する（# でコメント）。
  <repo>/verify_aliases.txt  1行につき「別名=本体名」。短縮形・異表記の誤検出を防ぐ。

どちらも無くても動作する（その場合、キャラ名と汎用ストップワードのみで判定）。
敬称付きの形（〜様/〜さん/〜殿/〜ちゃん/〜先生）は、定義済みキャラ名・別名から
自動生成するので列挙不要。

使い方:
  python verify_character_consistency.py --dir <repo>            # 全チェック
  python verify_character_consistency.py --dir <repo> --outline  # outline のみ
  python verify_character_consistency.py --dir <repo> --chapters # chapters のみ
  python verify_character_consistency.py --dir <repo> --json     # 機械可読出力
  python verify_character_consistency.py --dir <repo> \\
      --terms my_terms.txt --aliases my_aliases.txt              # 語彙ファイルを明示指定

exit 0 = 整合（警告のみなら 0）/ exit 1 = ハードFAIL（未登場 or 未定義キャラ）
"""
import argparse
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 本文/outline で多用されるが「キャラ名」ではない語（誤検出を防ぐ）。
# ここには作品に依存しない語だけを置く。作品固有の語は verify_terms.txt へ。
STOPWORDS_CORE = {
    # 人称・指示語
    "二人", "自分", "彼女", "彼", "私", "わたし", "わたくし", "あなた", "俺", "僕", "君",
    # 構造語（このパイプラインの日本語ドキュメントで使う見出し・欄名）
    "ビート", "植え込み", "POV", "章", "幕", "前半", "後半", "総括", "伏線", "語数",
    # 汎用の抽象語
    "真実", "世界", "存在", "言葉", "文字", "意味", "場所", "正体", "理解", "目標",
    "位置", "感情", "回収", "代償", "記憶", "時間", "心", "声", "影",
}

# 敬称（定義済みキャラ名・別名から自動生成する）
HONORIFIC_SUFFIXES = ("様", "さん", "殿", "ちゃん", "先生")


def load_terms(path: "Path | None") -> set:
    """語彙ファイル（1行1語・# コメント）を読む。無ければ空集合。"""
    if not path or not Path(path).exists():
        return set()
    terms = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.add(line)
    return terms


def load_aliases(path: "Path | None") -> dict:
    """別名ファイル（別名=本体名・# コメント）を読む。無ければ空の辞書。"""
    if not path or not Path(path).exists():
        return {}
    aliases = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            alias, _, canon = line.partition("=")
            alias, canon = alias.strip(), canon.strip()
            if alias and canon:
                aliases[alias] = canon
    return aliases


def build_stopwords(defined: list, aliases: dict, extra_terms: set) -> set:
    """作品語彙・別名・敬称形を合成したストップワード集合。"""
    stopwords = set(STOPWORDS_CORE) | set(extra_terms)
    bases = {n.split("（")[0].strip() for n in defined}
    bases |= set(aliases.keys()) | set(aliases.values())
    for base in bases:
        if base:
            stopwords |= {base + suf for suf in HONORIFIC_SUFFIXES}
    return stopwords


def extract_defined_names(characters: Path) -> list:
    """characters.md の `## キャラ名（かな）` 見出しからキャラ名を抽出."""
    if not characters.exists():
        return []
    names = []
    for line in characters.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s+(.+?)(?:（.*?）)?\s*$", line.strip())
        if m:
            name = m.group(1).strip()
            if name and not name.startswith(("追加", "キャラクター間", "最終")):
                names.append(name)
    return names


def extract_seed_characters(seed: Path) -> list:
    """seed.txt の「登場人物:」欄からキャラ名を抽出."""
    if not seed.exists():
        return []
    names = []
    in_section = False
    for line in seed.read_text(encoding="utf-8").splitlines():
        if re.match(r"^登場人物", line):
            in_section = True
            continue
        if in_section:
            if line.strip() and not line.startswith(("#", "ストーリー", "魅力的")):
                m = re.match(r"^[-*]\s*(.+?)[:：]", line.strip())
                if m:
                    names.append(m.group(1).strip())
            elif line.startswith(("ストーリー", "魅力的")):
                break
    return names


def extract_outline_povs(outline: Path) -> list:
    """outline.md の `- **POV：** キャラ名` から POV キャラを抽出."""
    if not outline.exists():
        return []
    povs = []
    for line in outline.read_text(encoding="utf-8").splitlines():
        m = re.search(r"\*\*POV[：:]\*\*\s*(.+)", line)
        if m:
            povs.append(m.group(1).strip())
    return povs


def extract_text(path: Path) -> str:
    """ファイル（または chapters/ 配下の全章）を連結した文字列を返す."""
    if path.is_dir():
        text = ""
        for f in sorted(path.glob("ch_*.md")):
            text += f.read_text(encoding="utf-8") + "\n"
        return text
    return path.read_text(encoding="utf-8") if path.exists() else ""


def check_defined_but_unused(defined: list, outline_text: str,
                             chapter_text: str, stopwords: set) -> list:
    """characters.md に定義されたが outline/本文に一度も登場しないキャラ."""
    unused = []
    for name in defined:
        base = name.split("（")[0].strip()
        if base in stopwords:
            continue
        if base not in outline_text and base not in chapter_text:
            unused.append(name)
    return unused


def check_pov_undefined(defined: list, outline_povs: list, stopwords: set) -> list:
    """outline の POV に登場するが characters.md に定義されていないキャラ."""
    defined_bases = {n.split("（")[0].strip() for n in defined}
    undefined = []
    for pov in outline_povs:
        base = pov.split("（")[0].strip()
        if base and base not in defined_bases and base not in stopwords:
            undefined.append(f"outline POV: {pov}")
    return undefined


def check_frequent_undefined(defined: list, seed_names: list, text: str,
                             stopwords: set, aliases: dict) -> list:
    """テキストに高頻度で登場するが、characters.md にも seed にもない語（要確認）."""
    defined_bases = {n.split("（")[0].strip() for n in defined}
    # 別名マップで定義済みキャラに解決できる語は既知扱い
    known = defined_bases | set(seed_names) | stopwords | set(aliases.keys())
    freq = {}
    for m in re.finditer(r"[\u4e00-\u9fff]{2,4}", text):
        w = m.group(0)
        if w in known:
            continue
        freq[w] = freq.get(w, 0) + 1
    # 高頻度（>=10回）のみ候補として報告（警告扱い）
    return [f"{w}({c}回)" for w, c in sorted(freq.items(), key=lambda x: -x[1])
            if c >= 10]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(BASE_DIR),
                    help="対象リポジトリのディレクトリ（デフォルト: スクリプトの場所）")
    ap.add_argument("--outline", action="store_true", help="outline のみチェック")
    ap.add_argument("--chapters", action="store_true", help="chapters のみチェック")
    ap.add_argument("--json", action="store_true", help="機械可読出力")
    ap.add_argument("--terms", default=None,
                    help="作品固有の語彙ファイル（既定: <dir>/verify_terms.txt）")
    ap.add_argument("--aliases", default=None,
                    help="別名=本体名のファイル（既定: <dir>/verify_aliases.txt）")
    args = ap.parse_args()

    base = Path(args.dir)
    characters = base / "characters.md"
    outline = base / "outline.md"
    chapters = base / "chapters"
    seed = base / "seed.txt"

    defined = extract_defined_names(characters)
    seed_names = extract_seed_characters(seed)
    outline_povs = extract_outline_povs(outline)
    outline_text = extract_text(outline)
    chapter_text = extract_text(chapters) if not args.outline else ""

    terms_path = Path(args.terms) if args.terms else base / "verify_terms.txt"
    aliases_path = Path(args.aliases) if args.aliases else base / "verify_aliases.txt"
    extra_terms = load_terms(terms_path)
    aliases = load_aliases(aliases_path)
    stopwords = build_stopwords(defined, aliases, extra_terms)

    # ハードFAIL（信頼できる信号）
    unused = check_defined_but_unused(defined, outline_text, chapter_text, stopwords)
    undefined = check_pov_undefined(defined, outline_povs, stopwords)

    # 警告（要確認）
    warnings = []
    if not args.chapters:
        warnings = check_frequent_undefined(defined, seed_names, outline_text,
                                            stopwords, aliases)
    elif chapter_text:
        # chapters モード: 本文の高頻度語も未定義キャラ候補として警告
        warnings = check_frequent_undefined(defined, seed_names, chapter_text,
                                            stopwords, aliases)

    unused = sorted(set(unused))
    undefined = sorted(set(undefined))
    warnings = sorted(set(warnings))

    hard_fail = bool(unused or undefined)

    if args.json:
        import json
        print(json.dumps({
            "defined_characters": defined,
            "seed_characters": seed_names,
            "outline_povs": outline_povs,
            "defined_but_unused": unused,
            "used_but_undefined": undefined,
            "warnings": warnings,
            "consistent": not hard_fail,
        }, ensure_ascii=False, indent=2))
    else:
        print("== キャラ整合チェック ==")
        print(f"characters.md 定義キャラ: {len(defined)}")
        print(f"seed.txt 登場人物: {len(seed_names)}")
        print(f"outline POV キャラ: {len(outline_povs)}")
        print(f"語彙ファイル: 固有語 {len(extra_terms)}件"
              f" / 別名 {len(aliases)}件"
              f"（{'あり' if extra_terms or aliases else 'なし'}）")
        print()
        if unused:
            print("[FAIL] 定義されたが未登場のキャラ:")
            for n in unused:
                print(f"  - {n}")
        else:
            print("[OK] 定義されたキャラはすべて登場")
        print()
        if undefined:
            print("[FAIL] 登場するが未定義のキャラ:")
            for n in undefined:
                print(f"  - {n}")
        else:
            print("[OK] 登場キャラ（POV）はすべて定義済み")
        print()
        if warnings:
            print("[WARN] 要確認（未定義キャラの候補・頻度ベース）:")
            for w in warnings:
                print(f"  - {w}")
        else:
            print("[OK] 未定義キャラの候補なし")
        print()
        print("RESULT:", "CONSISTENT" if not hard_fail else "INCONSISTENT")
        return 0 if not hard_fail else 1

    return 0 if not hard_fail else 1


if __name__ == "__main__":
    sys.exit(main())
