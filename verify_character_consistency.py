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
  3. outline.md に高頻度で登場する語が characters.md に定義されておらず、
     かつ seed.txt の登場人物にもない場合（未定義キャラの候補）

使い方:
  python verify_character_consistency.py --dir <repo>            # 全チェック
  python verify_character_consistency.py --dir <repo> --outline  # outline のみ
  python verify_character_consistency.py --dir <repo> --chapters # chapters のみ
  python verify_character_consistency.py --dir <repo> --json     # 機械可読出力

exit 0 = 整合（警告のみなら 0）/ exit 1 = ハードFAIL（未登場 or 未定義キャラ）
"""
import argparse
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 本文/outline で多用されるが「キャラ名」ではない語（誤検出を防ぐ）
STOPWORDS = {
    # 世界・概念
    "帝", "大天鏡", "天鏡", "鏡都", "曇り", "映り", "素見", "筆真", "現世",
    "平安京", "宮中", "女房", "陰陽師", "鏡士", "大鏡士", "無映者", "鏡銘",
    "予言", "導き手", "記録者", "観察者", "源氏物語", "中宮", "鏡術", "生贄",
    "代償", "真実", "世界", "存在", "言葉", "文字", "意味", "場所", "正体",
    "理解", "消耗", "定着", "目標", "語数", "位置", "感情", "回収", "伏線",
    "二人", "自分", "彼女", "彼", "私", "わたし", "わたくし", "あなた",
    # 敬称付き（本体名は別途照合）
    "清少納言様", "紫式部様", "晴明様", "源氏様", "鏡守様", "帝様",
    # 構造語
    "ビート", "植え込み", "POV", "章", "幕", "前半", "後半", "総括",
}

# 定義済みキャラの短縮形・別名 → 本体名（警告の誤検出を防ぐ）
ALIASES = {
    "晴明": "安倍晴明",
    "式部": "紫式部",
    "少納言": "清少納言",
    "清少納言": "清少納言",
    "紫式部": "紫式部",
    "安倍晴明": "安倍晴明",
    "源氏": "源氏",
    "帝": "天鏡の帝",
    "黒鏡": "影の首領・黒鏡",
    "白菊": "白菊",
    "無名": "無名",
    "鏡守": "鏡守",
}


def extract_defined_names(characters: Path) -> list[str]:
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


def extract_seed_characters(seed: Path) -> list[str]:
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


def extract_outline_povs(outline: Path) -> list[str]:
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


def check_defined_but_unused(defined: list[str], outline_text: str,
                             chapter_text: str) -> list[str]:
    """characters.md に定義されたが outline/本文に一度も登場しないキャラ."""
    unused = []
    for name in defined:
        base = name.split("（")[0].strip()
        if base in STOPWORDS:
            continue
        if base not in outline_text and base not in chapter_text:
            unused.append(name)
    return unused


def check_pov_undefined(defined: list[str], outline_povs: list[str]) -> list[str]:
    """outline の POV に登場するが characters.md に定義されていないキャラ."""
    defined_bases = {n.split("（")[0].strip() for n in defined}
    undefined = []
    for pov in outline_povs:
        base = pov.split("（")[0].strip()
        if base and base not in defined_bases and base not in STOPWORDS:
            undefined.append(f"outline POV: {pov}")
    return undefined


def check_frequent_undefined(defined: list[str], seed_names: list[str],
                             outline_text: str) -> list[str]:
    """outline に高頻度で登場するが、characters.md にも seed にもない語（要確認）."""
    defined_bases = {n.split("（")[0].strip() for n in defined}
    # 別名マップで定義済みキャラに解決できる語は既知扱い
    known = defined_bases | set(seed_names) | STOPWORDS | set(ALIASES.keys())
    freq = {}
    for m in re.finditer(r"[\u4e00-\u9fff]{2,4}", outline_text):
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

    # ハードFAIL（信頼できる信号）
    unused = check_defined_but_unused(defined, outline_text, chapter_text)
    undefined = check_pov_undefined(defined, outline_povs)

    # 警告（要確認）
    warnings = []
    if not args.chapters:
        warnings = check_frequent_undefined(defined, seed_names, outline_text)
    elif chapter_text:
        # chapters モード: 本文の高頻度語も未定義キャラ候補として警告
        warnings = check_frequent_undefined(defined, seed_names, chapter_text)

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
