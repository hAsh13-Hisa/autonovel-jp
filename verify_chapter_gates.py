# -*- coding: utf-8 -*-
"""verify_chapter_gates.py — 章ゲートの一括検証（リポジトリ同梱版）.

Hisa 品質ルール（2026-09-15改定。旧=2026-08-31運用ゲート）をまとめてチェックする:
  文字数 / 文字ベースセリフ比 >=50% / 最小窓(500字) >=55% /
  重複文(12字以上x2回) = 0 / ...... <=2 / —— <=1 / 英語指摘漏れ(4字以上英単語) = 0
  比喩の型の反復（2026-10-03追加）:
    比喩密度 (まるで+よう系+みたい+あたかも+ごとく)/1000字 <= 3.0
    同型の反復 まるで+みたい <= 4

全項目が合否対象（FAIL があれば exit 1）。run_pipeline.py の drafting が本スクリプトを
呼び、FAIL の章は破棄して書き直す（＝自動再生成）。

使い方（リポジトリの venv で実行 — draft_chapter_split の import に dotenv が必要）:
  uv run python verify_chapter_gates.py chapters/ch_05.md
  uv run python verify_chapter_gates.py --band 3000 99999 chapters/ch_05.md
  uv run python verify_chapter_gates.py ch_05 ch_06      # 複数章・ch_NN 相対指定も可

章パスは絶対パスでも ch_NN 相対でもよく、<repo>/chapters/ と <repo>/ を順に探索する。
文字数帯の既定は 6000 7500（初稿ゲート）。env `AUTONOVEL_CHAPTER_GATE_BAND="3000 99999"`
で既定を上書きできる（run_pipeline.py は下限のみ見る帯で呼ぶ）。

比喩の閾値の根拠: 過去作の採用版は 1.1〜2.9/1000字・まるで+みたい 0〜4、
最初期稿(v4-pro)は 4.4・20（実測 2026-10-03）。

exit 0 = 全項目PASS / exit 1 = FAILあり。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PATHS = [
    BASE_DIR / "chapters",
    BASE_DIR,
]


def find_file(arg: str) -> str:
    p = Path(arg)
    if p.is_absolute() and p.exists():
        return str(p)
    for base in DEFAULT_PATHS:
        for cand in (base / arg, base / f"{arg}.md"):
            if cand.exists():
                return str(cand)
    raise FileNotFoundError(f"章ファイルが見つかりません: {arg}")


def metaphor_counts(text: str) -> dict:
    """比喩マーカーを数える（2026-10-03 の実測と同じ定義に揃えている）。

    使う5種は prose_metrics.py / shiyo_stats.py と同じ:
    まるで / よう(に|な|だ) / みたい / あたかも / ごとく。
    過去作の採用版は 1.1〜2.9 / 1000字、最初期稿(v4-pro)は 4.4。
    """
    return {
        "まるで": len(re.findall(r"まるで", text)),
        "よう系": len(re.findall(r"よう(?:に|な|だ)", text)),
        "みたい": len(re.findall(r"みたい", text)),
        "あたかも": len(re.findall(r"あたかも", text)),
        "ごとく": len(re.findall(r"ごとく", text)),
    }


def count_english_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z]{4,}", text))


def main() -> int:
    argv = sys.argv[1:]
    env_raw = os.environ.get("AUTONOVEL_CHAPTER_GATE_BAND")
    env_band = (env_raw or "6000 7500").split()
    if len(env_band) != 2:
        print(f'ERROR: AUTONOVEL_CHAPTER_GATE_BAND は "lo hi" の形式で指定してください: {env_raw!r}')
        return 2
    lo, hi = int(env_band[0]), int(env_band[1])
    files = argv
    if argv and argv[0] == "--band":
        parts = argv[1].split()
        if len(parts) == 2:
            # --band "3000 99999" の1引数形（run_pipeline の env 経由でも使う）
            lo, hi = int(parts[0]), int(parts[1])
            files = argv[2:]
        else:
            # --band 3000 99999 の2引数形
            lo, hi = int(argv[1]), int(argv[2])
            files = argv[3:]
    if not files:
        print(__doc__)
        return 2

    sys.path.insert(0, str(BASE_DIR))
    from draft_chapter_split import min_window_ratio  # noqa: E402

    all_ok = True
    for arg in files:
        path = find_file(arg)
        text = Path(path).read_text(encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(BASE_DIR / "dialogue_ratio.py"), path],
            capture_output=True, text=True)
        out = r.stdout or ""
        print(f"== {Path(path).name} ==")
        m_chars = re.search(r"総文字数:\s*(\d+)", out)
        m_ratio = re.search(r"台詞\(括弧含む\):\s*\d+字\s*=\s*([\d.]+)%", out)
        m_dup = re.search(r"重複文\(12字以上,2回\):\s*(\d+)", out)
        if not (m_chars and m_ratio and m_dup):
            # 計測側が壊れた/出力形式が変わった場合は「検証不能」として FAIL 扱い（黙って通さない）
            print("  [FAIL] dialogue_ratio.py の出力が読めませんでした（判定不能）")
            print(f"    stdout={out[:200]!r} stderr={(r.stderr or '')[:200]!r}")
            all_ok = False
            continue
        chars = int(m_chars.group(1))
        ratio = float(m_ratio.group(1))
        dup = int(m_dup.group(1))
        mw = min_window_ratio(text) * 100
        dots, dash = text.count("……"), text.count("——")
        eng = count_english_words(text)
        for label, val, ok in [
            ("文字数", f"{chars} ({lo}-{hi})", lo <= chars <= hi),
            ("セリフ比(文字ベース)", f"{ratio}% (>=50)", ratio >= 50),
            ("最小窓500字", f"{mw:.0f}% (>=55)", mw >= 55),
            ("重複(12字以上x2)", f"{dup} (=0)", dup == 0),
            ("……/——/英語", f"{dots}/{dash}/{eng} (<=2/<=1/0)",
             dots <= 2 and dash <= 1 and eng == 0),
        ]:
            all_ok = all_ok and ok
            print(f"  [{'OK' if ok else 'FAIL'}] {label}: {val}")
        # --- 比喩の型の反復（合否ゲート）---
        mk = metaphor_counts(text)
        mk_total = sum(mk.values())
        mk_density = mk_total / (chars / 1000) if chars else 0.0
        mk_same = mk["まるで"] + mk["みたい"]
        detail = "/".join(f"{k}{v}" for k, v in mk.items() if v) or "なし"
        mk_ok = mk_density <= 3.0 and mk_same <= 4
        all_ok = all_ok and mk_ok
        print(f"  [{'OK' if mk_ok else 'FAIL'}] 比喩密度: {mk_density:.1f}/1000字 (<=3.0)"
              f"・まるで+みたい={mk_same} (<=4) — 内訳 {detail}")
    print()
    print("RESULT:", "ALL PASS" if all_ok else "FAILURES")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
