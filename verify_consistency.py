#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_consistency.py — 整合ゲート（キャラ以外の「ブレ」を拾う）.

既存の verify_character_consistency.py は「キャラの有無」を見る。本スクリプトはそこを拡張し、
24章を通したときのブレを拾う。

  [機械・無料・毎章。辞書が要らず精度の出る2項目だけ]
    1. 使われない設定 : canon.md / world.md / characters.md の見出し語・太字・「」語が、
                        本文に一度も出てこない（設定したのに書かれていない）
    2. 消えた人物     : 第1章に出ている定義キャラが、最後の3章に出てこない（退場の放置）

  [審査・judge モデル・1章1回]
    3. 設定矛盾 / 声の逸脱 / 伏線の放置 : canon.md / world.md / voice.md と章立て（植え込み・回収）に
       照らして、おかしい記述を「本文から引用して」挙げさせる。無ければ空リスト。

【実測して見送った検査（2026-10-03・完成稿24章を材料に計測）】
  本スクリプトの初版は「本文から固有名詞を拾う」方式だったが、日本語では辞書なしに通用しない:
  - 未定義キャラ候補 = **97件**、上位が「足元」「輪郭」「呼吸」「心地」＝普通名詞の誤検出だらけ。
  - 伏線の未回収 = **223件**、植え込みの説明文から漢字語を抜く方式のため「両方」「登場」「質問」など。
  - 人称の混在 = 1件（第5章の地の文に わたくし4/わたし2）→ 三人称の地の文で人物ごとに一人称が違うのは
    正常なので誤検出。
  → 誤検出でパイプラインを止めると人間の時間が増える（Hisa 方針に反する）。
     よって「設定文書に書かれた語を基準にする」項目と、審査モデルの意味判定に絞った。
     伏線の回収漏れ・設定矛盾・声の逸脱は**意味の判定**なので審査モデルに任せる（機械では拾わない）。

使い方:
  python verify_consistency.py --dir <repo>                 # 機械2項目
  python verify_consistency.py --dir <repo> --llm           # ＋審査（1章1回）
  python verify_consistency.py --dir <repo> --llm --chapters 1,2
  python verify_consistency.py --dir <repo> --report <path> # 人間用レポート
  python verify_consistency.py --dir <repo> --brief <path>  # 改訂ブリーフ（gen_revision に渡せる）
  python verify_consistency.py --dir <repo> --only unused_setting
  python verify_consistency.py --dir <repo> --llm --json

exit 0 = FAIL なし（既定。指摘は警告として記録）／exit 1 = --strict で警告を FAIL 扱いしたとき
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 既定で回す機械項目。unused_setting は「全体を見る材料」なので既定では回さない
# （--only unused_setting で使う。実測: 完成稿では40件出るが、書式ラベルや作例の引用が混ざる）
MECH_ITEMS = ("late_vanish",)
ALL_MECH_ITEMS = ("late_vanish", "unused_setting")

HONORIFICS = ("様", "さん", "殿", "ちゃん", "先生", "君", "どの", "ども")

# 設定文書の「節の見出し」（地理・タイムライン等の書式ラベル）は設定語ではないので外す
TEMPLATE_STOP = {
    "地理", "タイムライン", "語数", "ビート", "POV", "魔法システム", "魔法システムのルール",
    "キャラクターの事実", "政治的／派閥的", "政治的/派閥的", "文化的", "物語内で確定済み",
    "不一致の記録", "登場人物", "世界観", "設定", "用語", "伏線", "概要", "目的", "構成",
}


def strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def read(path: Path) -> str:
    p = Path(path)
    return strip_html_comments(p.read_text(encoding="utf-8")) if p.exists() else ""


def load_chapters(repo: Path, chapters_dir: str = "") -> "list[tuple[int, str, str]]":
    """(章番号, ファイル名, 本文) を章番号順に返す。"""
    chdir = Path(chapters_dir) if chapters_dir else (
        repo / "chapters" if (repo / "chapters").is_dir() else repo)
    out = []
    for f in sorted(chdir.glob("ch_*.md")):
        m = re.search(r"(\d+)", f.stem)
        if m:
            out.append((int(m.group(1)), f.name, f.read_text(encoding="utf-8")))
    return sorted(out, key=lambda t: t[0])


# ---------------------------------------------------------------- 機械チェック

def extract_setting_terms(repo: Path) -> dict:
    """設定文書の「設定語」を {語: 出典} で返す（本文で使われたかを見る基準）。

    拾うもの: characters.md の見出し（＝人物名）／canon.md・world.md の太字と「」語。
    拾わないもの: canon/world の節見出し（書式ラベル）、英字だけの語、8文字以上の語、
                  書式ストップワード（TEMPLATE_STOP）。
    """
    terms = {}
    sources = {"characters": (r"^##\s+(.+)$",),   # レベル3以下は節ラベル（外見・関係性など）
               "canon": (r"\*\*(.+?)\*\*", r"[「『]([^」』]{2,20})[」』]"),
               # world.md は文体の作例（悪例の引用）を含むので「」語は拾わない
               "world": (r"\*\*(.+?)\*\*",)}
    for key, pats in sources.items():
        text = read(repo / f"{key}.md")
        for pat in pats:
            for m in re.finditer(pat, text, flags=re.M):
                w = m.group(1).strip()
                w = re.sub(r"（[^）]*）|\([^)]*\)", "", w).strip()
                w = re.split(r"[：:、,。\s]", w)[0].strip()
                if not (2 <= len(w) <= 7):
                    continue
                if w in TEMPLATE_STOP or w.startswith(("-", "*", "#")):
                    continue
                if re.match(r"^(第)?\d+", w) or w.isascii():
                    continue
                # 書式のラベル（〜の視点／〜場面／〜の会話 など）は設定語ではない
                if re.search(r"(視点|場面|会話|例|欄|記録|ラベル)$", w):
                    continue
                terms.setdefault(w, key)
    return terms


def check_unused_setting(terms: dict, chapters: list) -> list:
    """設定文書に書いてあるのに、本文に一度も出てこない語（設定の書きっぱなし）。"""
    text = "\n".join(t for _n, _f, t in chapters)
    out = []
    for w, src in terms.items():
        if w in text:
            continue
        if any(w + h in text for h in HONORIFICS):
            continue
        out.append({"word": w, "source": src})
    return out


def extract_defined_names(characters: Path) -> list:
    names = []
    for line in read(characters).splitlines():
        m = re.match(r"^##\s+(.+?)(?:（.*?）)?\s*$", line.strip())
        if m:
            name = m.group(1).strip()
            if name and not name.startswith(("追加", "キャラクター間", "最終", "用語", "世界")):
                names.append(name)
    return names


def name_variants(name: str) -> list:
    """定義名の照合に使う形（姓名の一部だけでも拾う）。

    実測（2026-10-03）:
      - characters.md の定義は「安倍晴明」、本文は「晴明」→ 定義名そのままだと誤って「消えた」と判定した。
      - 姓の側（「紫」など1〜2字）まで候補にすると、無関係な語に当たって**見逃す**（実測で検出漏れ）。
    よって「定義名」＋「下の名前の部分（末尾2字）」だけを見る。
    """
    base = name.split("（")[0].strip()
    out = [base]
    if len(base) >= 3:
        out.append(base[-2:])
    return [v for v in dict.fromkeys(out) if len(v) >= 2]


def check_late_vanish(defined: list, chapters: list, tail: int = 3) -> list:
    """第1章には出るのに、最後の tail 章に出てこない定義キャラ（退場の放置）。"""
    if len(chapters) < tail + 1:
        return []
    tail_nums = [n for n, _f, _t in chapters][-tail:]
    tail_text = "\n".join(t for n, _f, t in chapters if n in tail_nums)
    head = chapters[0][2]
    out = []
    for name in defined:
        variants = name_variants(name)
        if not variants or not any(v in head for v in variants):
            continue
        if any(v in tail_text or any(v + h in tail_text for h in HONORIFICS)
               for v in variants):
            continue
        out.append({"name": name, "missing_from": tail_nums})
    return out


# ---------------------------------------------------------------- 審査チェック

JUDGE_SYSTEM = (
    "あなたは日本語小説の整合性を検査する担当です。設定文書と本文を読み、"
    "設定に反する記述・人物の話し方の逸脱・放置された伏線だけを挙げます。"
    "感想や推測は書かず、必ず本文から短く引用します。回答は日本語で書きます。"
)


def outline_block(outline_text: str, ch: int) -> str:
    """章立てから、その章の記述（植え込み・回収を含む）だけを切り出す。"""
    marks = [(m.start(), int(m.group(1)))
             for m in re.finditer(r"第\s*(\d+)\s*章", outline_text)]
    start = None
    end = len(outline_text)
    for pos, num in marks:
        if num == ch and start is None:
            start = pos
        elif start is not None and num > ch:
            end = pos
            break
    return outline_text[start:end][:2500] if start is not None else ""


def llm_consistency(chapter_no: int, text: str, docs: dict, repo: Path) -> dict:
    """1章を設定文書と章立てに照らして審査し、指摘（引用付き）を返す。"""
    sys.path.insert(0, str(BASE_DIR))
    sys.path.insert(0, str(repo))   # llm.py はリポジトリ直下にある
    import llm  # noqa: E402  （リポジトリ同梱の LLM ラッパー）

    prompt = f"""次の章を、設定文書と章立てに照らして検査してください。

# 世界観（抜粋）
{docs.get('world', '')[:2000]}

# 設定・決まりごと（抜粋）
{docs.get('canon', '')[:2500]}

# 人物の話し方（抜粋）
{docs.get('voice', '')[:1500]}

# この章の章立て（植え込み・回収）
{outline_block(docs.get('outline', ''), chapter_no)}

# 検査する章（第{chapter_no}章）
{text[:6000]}

# 挙げてほしいこと（3種類だけ・合わせて最大3件）
- canon: 決まりごと（能力の条件・場所・時系列・持ち物など）に反している
- voice: その人物の話し方（一人称・語尾・敬語の段階）から外れている
- foreshadow: 植え込みとして置かれた要素が、この章以降どこにも触れられず放置されている

書き方（厳守）:
- JSON 以外は何も書かない。前置き・説明・感想は書かない。
- quote は本文から20字以内でそのまま抜く。why は40字以内。

出力例: {{"conflicts": [{{"kind": "canon", "quote": "短い引用", "why": "どこがどう食い違うか"}}]}}
指摘が無ければ {{"conflicts": []}}
"""
    return parse_conflicts(llm.call("judge", prompt, system=JUDGE_SYSTEM, max_tokens=16000))


def parse_conflicts(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", flags=re.S)
    if not m:
        return {"conflicts": [], "raw": (raw or "")[:300]}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"conflicts": [], "raw": (raw or "")[:300], "error": "JSONの解析に失敗"}
    clean = []
    for it in (data.get("conflicts") or []):
        if isinstance(it, dict):
            clean.append({"kind": str(it.get("kind", "?"))[:20],
                          "quote": str(it.get("quote", ""))[:200],
                          "why": str(it.get("why", ""))[:300]})
    return {"conflicts": clean}


# ---------------------------------------------------------------- 出力

def build_report(repo: Path, mech: dict, judged: dict) -> str:
    lines = [f"# 整合ゲート: {repo}", ""]
    lines.append("## 機械項目（辞書なしで精度の出る2項目）")
    uns = mech.get("unused_setting")
    if uns is not None:
        lines.append(f"- 使われない設定: {len(uns)}件")
        for it in uns[:40]:
            lines.append(f"  - 「{it['word']}」（{it['source']}.md に記載）が本文に一度も出てこない")
    lv = mech.get("late_vanish")
    if lv is not None:
        lines.append(f"- 消えた人物（最後の3章に出てこない）: {len(lv)}件")
        for it in lv:
            lines.append(f"  - {it['name']}（第1章には登場）")
    if judged:
        lines.append("")
        lines.append("## 審査項目（judge モデル・1章1回）")
        total = sum(len(v.get("conflicts", [])) for v in judged.values())
        lines.append(f"- 指摘: {total}件（{len(judged)}章を審査）")
        for num in sorted(judged):
            res = judged[num]
            if res.get("error"):
                lines.append(f"  - 第{num}章: 審査に失敗（{res['error']}）")
            for c in res.get("conflicts", []):
                lines.append(f"  - 第{num}章 [{c['kind']}] 「{c['quote']}」→ {c['why']}")
    return "\n".join(lines) + "\n"


def build_brief(repo: Path, mech: dict, judged: dict) -> str:
    """改訂ブリーフ形式（gen_revision.py に渡せる形）。"""
    out = ["# 整合の修正指示（自動生成）", "",
           "次の指摘を直してください。話の流れ・文体・既存の伏線は変えないこと。", ""]
    for num in sorted(judged):
        conf = judged[num].get("conflicts", [])
        if conf:
            out.append(f"## 第{num}章")
            for c in conf:
                out.append(f"- [{c['kind']}] 「{c['quote']}」→ {c['why']}")
            out.append("")
    lv = mech.get("late_vanish") or []
    if lv:
        out.append("## 消えた人物")
        for it in lv:
            out.append(f"- {it['name']} が最後の3章に出てこない（退場を書くか、登場させる）")
        out.append("")
    return "\n".join(out)


def warnings_count(mech: dict, judged: dict) -> int:
    n = 0
    for key in MECH_ITEMS:
        n += len(mech.get(key) or [])
    for v in (judged or {}).values():
        n += len(v.get("conflicts") or [])
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="整合ゲート（キャラ以外のブレを拾う）")
    ap.add_argument("--dir", default=str(BASE_DIR))
    ap.add_argument("--chapters-dir", default="")
    ap.add_argument("--only", default="", help="機械項目の選択: " + ",".join(MECH_ITEMS))
    ap.add_argument("--chapters", default="", help="審査する章番号（例 1,2。既定は全章）")
    ap.add_argument("--llm", action="store_true", help="審査（judge モデル）も回す")
    ap.add_argument("--report", default="", help="人間用レポートの書き出し先")
    ap.add_argument("--brief", default="", help="改訂ブリーフの書き出し先")
    ap.add_argument("--strict", action="store_true", help="警告があれば exit 1（既定は exit 0）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo = Path(args.dir).resolve()
    only = [x.strip() for x in args.only.split(",") if x.strip()] or list(MECH_ITEMS)
    docs = {
        "world": read(repo / "world.md"),
        "canon": read(repo / "canon.md"),
        "voice": read(repo / "voice.md"),
        "outline": read(repo / "outline.md"),
    }
    chapters = load_chapters(repo, args.chapters_dir)
    if not chapters:
        print(f"{repo} に章がありません（chapters/ch_*.md を置いてください）")
        return 0

    mech = {}
    if "unused_setting" in only:
        mech["unused_setting"] = check_unused_setting(extract_setting_terms(repo), chapters)
    if "late_vanish" in only:
        mech["late_vanish"] = check_late_vanish(
            extract_defined_names(repo / "characters.md"), chapters)

    judged = {}
    if args.llm:
        want = [int(x) for x in args.chapters.split(",") if x.strip().isdigit()]
        for num, _name, text in chapters:
            if want and num not in want:
                continue
            print(f"  審査中: 第{num}章 …", flush=True)
            try:
                judged[num] = llm_consistency(num, text, docs, repo)
            except Exception as e:  # noqa: BLE001
                judged[num] = {"conflicts": [], "error": str(e)[:200]}

    if args.report:
        Path(args.report).write_text(build_report(repo, mech, judged), encoding="utf-8")
        print(f"レポート: {args.report}")
    if args.brief:
        Path(args.brief).write_text(build_brief(repo, mech, judged), encoding="utf-8")
        print(f"改訂ブリーフ: {args.brief}")
    if args.json:
        print(json.dumps({"repo": str(repo), "chapters": len(chapters),
                          "mechanical": mech, "judged": judged},
                         ensure_ascii=False, indent=1))
    else:
        print(build_report(repo, mech, judged))

    n = warnings_count(mech, judged)
    print(f"（警告 {n}件／FAIL {'1' if args.strict and n else '0'}）")
    return 1 if (args.strict and n) else 0


if __name__ == "__main__":
    sys.exit(main())
