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
MECH_ITEMS = ("late_vanish", "death_then_alive", "exposition_dialogue",
              "pronoun_shift", "term_shift", "speaker_gap",
              "voice_pronoun_conflict")
ALL_MECH_ITEMS = ("late_vanish", "unused_setting", "death_then_alive",
                  "exposition_dialogue", "pronoun_shift", "term_shift",
                  "speaker_gap", "voice_pronoun_conflict")

HONORIFICS = ("様", "さん", "殿", "ちゃん", "先生", "君", "どの", "ども")

# 章の冒頭は前の文脈が無いので「話者が不明」は当然（誤検出を避けるため判定対象外）
HEAD_SKIP = 20

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
    """定義キャラの「呼び名」を取り出す。

    実測（2026-10-08）: 上流のまま `## 見出し` をそのまま名前にすると
    「2. 聖歌隊長ルナ (Luna)」「キャラクター相関マップ」のような**見出し文字列**が
    名前として扱われ、本文の「ルナ」と一致しない（＝検査が素通りする）。
    しかも節見出しが「消えた人物」として報告されていた（誤検出）。
    よって番号・肩書き・ローマ字を落とし、末尾のカタカナ（無ければ漢字かな）の
    並びを名前として取る。
    """
    names = []
    for line in read(characters).splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line.strip())
        if not m:
            continue
        raw = m.group(1).strip()
        if raw.startswith(("キャラクター相関", "テーマ的", "用語", "世界", "相関")):
            continue
        raw = re.sub(r"^\d+[.．、]\s*", "", raw)          # 先頭の番号
        raw = re.sub(r"[（(].*?[)）]", "", raw).strip()    # ローマ字表記
        raw = re.split(r"[：:]", raw)[-1].strip()          # 「追加キャラクター：リナ」
        tail = re.search(r"[ァ-ヶー]{2,6}$", raw) or re.search(r"[ぁ-ん一-龥]{2,6}$", raw)
        name = (tail.group(0) if tail else raw).strip()
        if name:
            names.append(name)
    return names


def name_variants(name: str) -> list:
    """定義名の照合に使う形（姓名の一部だけでも拾う）。

    実測（2026-10-03）:
      - 定義は「姓＋名」（4字）、本文は下の名前（2字）だけ、という食い違いが普通に起きる。
        定義名そのままで照合すると、本文に出ている人物を「消えた」と誤判定した（実測で1件の誤検出）。
      - 姓の側（1〜2字）まで候補にすると、無関係な語に当たって**見逃す**（実測で検出漏れ）。
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


# 死の描写と、生きている描写（台詞・動作）の手がかり
# 「死ん」「殺され」は「生きてるのか死んでるのか」「死ぬほど」のような比喩・疑問で誤検出する
# （実測 2026-10-08: 第15章の一節でガラムとゼノスが誤検出された）ため、断定の強い語だけを使う。
DEATH_MARKS = re.compile(r"(息を引き取|息絶え|絶命|亡くな|遺体|死体|最期|命を落と|永眠)")
DEATH_EXCLUDE = ("死んでるのか", "死んだよう", "死ぬほど", "死んでも", "死ぬ気", "死んだも同然")
ALIVE_MARKS = re.compile(
    r"(座り|座る|笑い|笑う|揺らし|預け|指し示|問いか|立ち上が|歩い|隣に|隣り|"
    r"呟い|言った|答えた|叫んだ|囁い|微笑ん|見つめ|手を振|駆け|走っ|頷い|振り返)")


# 回想・遺品の言及に付く語。これが近くにある登場は「生きている」と数えない。
MEMORY_MARKS = ("遺し", "遺品", "形見", "かつて", "の声", "の言葉", "の設計図", "のメモ", "の面影",
                "生前", "もういない", "亡くな", "死ん", "錆びた破片", "言っていた", "最期",
                "思い出", "記憶", "教え", "受け継", "残して")


def _alive_evidence(name: str, text: str, radius: int = 60) -> int:
    """その章で「生きている」と読める登場の数。

    実測（2026-10-08）の作り直し:
      - 「名前＋動作語」を動作語の一覧で拾うやり方は、動作語を書き漏らすと**生きている人物を
        見落とす**（第19章の「ガラムがゴーグルのダイヤルを調整し」「絶叫し」を全部外した）。
      - そこで **「回想・遺品の語が近くに無い登場」を数える**向きに変える。
        死んだ人物の言及は「遺した」「かつて」「の設計図」「もういない」のどれかを伴うため、
        これで分離できる（第18章の遺品の言及＝0、第19章の生きた言及＝5、と実測で分かれた）。
    """
    n = 0
    for v in name_variants(name):
        for m in re.finditer(re.escape(v), text):
            seg = text[max(0, m.start() - radius): m.end() + radius]
            if any(k in seg for k in MEMORY_MARKS):
                continue
            n += 1
    return n


def check_death_then_alive(defined: list, chapters: list,
                            min_gap: int = 2, min_alive: int = 5) -> list:
    """「死んだ（消えた）あとに生きて再登場している」人物を挙げる。

    2026-10-08 実測: 第17章で死んだヒロインが、第22〜24章で普通に会話していた。
    **章ごとの判定では見えない**（どの章も単体では破綻していない）ので、全編を並べて見る。

    作り方の理由（誤検出を避けるため）:
      - 単に「死」の語の近くに名前があるかで判定すると、主人公が必ず引っかかる
        （死の場面に立ち会っているため）。実測で4件中3件が誤検出だった。
      - そこで **「生きている手がかり（台詞・動作）が途切れ、そのあと再び現れる」**という
        形を主の手がかりにし、「消える前後に死の描写がある」ことを条件に足す。
    """
    out = []
    nums = [n for n, _f, _t in chapters]
    for name in defined:
        variants = name_variants(name)
        if not variants:
            continue
        # 1) 名前に直接つく「もう戻らない」表現（最も確実）。こちらは章の間隔を問わない。
        #    実測（2026-10-08）: 「ガラムだったものの残骸が…鉄の砂となって広がっている」（第13章）
        #    のあと、第15章から操舵輪を回して生きていた。間隔はわずか1章なので、
        #    「途切れて再登場」を待つやり方では捕まらない。
        gone = None
        for n, _f, t in chapters:
            for v in variants:
                m = re.search(re.escape(v) + r"(?:だったものの)?(?:残骸|遺体|死体)|"
                              + re.escape(v) + r"(?:は|が|も)?(?:もういない|もう戻らない|"
                                              r"戻ってこない|静寂に溶けた|帰らない)", t)
                if m:
                    gone = (n, m.group(0)[:30])
                    break
            if gone:
                break
        if gone:
            later = [{"chapter": n, "alive": _alive_evidence(name, t)}
                     for n, _f, t in chapters
                     if n > gone[0] and _alive_evidence(name, t) >= min_alive]
            if later:
                out.append({"name": name, "died_in": gone[0], "mark": gone[1],
                            "gap": later[0]["chapter"] - gone[0] - 1,
                            "returns_in": later[0]["chapter"],
                            "alive_later": [x["alive"] for x in later],
                            "samples": [f"第{later[0]['chapter']}章で生きている（台詞・動作）"]})
            continue
        present = [n for n, _f, t in chapters if _alive_evidence(name, t) >= min_alive]
        if len(present) < 3:
            continue
        for i in range(len(present) - 1):
            gap = present[i + 1] - present[i] - 1
            if gap < min_gap:
                continue
            last_seen = present[i]
            deaths = []
            for n, _f, t in chapters:
                if not (last_seen <= n <= last_seen + max(gap, 2)):
                    continue
                for m in DEATH_MARKS.finditer(t):
                    seg = t[max(0, m.start() - 90):m.end() + 90]
                    if any(x in seg for x in DEATH_EXCLUDE):
                        continue
                    if any(v in seg for v in variants):
                        deaths.append({"chapter": n, "mark": m.group(0)})
            if deaths:
                out.append({
                    "name": name,
                    "died_in": deaths[0]["chapter"],
                    "mark": deaths[0]["mark"],
                    "gap": gap,
                    "returns_in": present[i + 1],
                    "alive_later": [_alive_evidence(name, t)
                                    for n, _f, t in chapters if n > last_seen + gap],
                    "samples": [f"第{present[i+1]}章で再登場（{gap}章ぶり）"],
                })
                break   # 同じ人物は1件でよい
    return out


# 説明の言い回し（設定・仕組みを説明する台詞に付く語）
EXPL_PHRASES = ("という", "つまり", "だから", "わけ", "のよ", "なの", "仕組み", "理由", "構造",
                "はず", "ことになる", "からこそ", "によって", "ために", "というのは",
                "覚えてお", "知っての", "原理", "理論")


def check_exposition_dialogue(chapters: list, min_chars: int = 60, min_lines: int = 4,
                              warn_ratio: float = 0.5, max_quotes: int = 4,
                              quote_chars: int = 90) -> list:
    """設定や仕組みを説明する長い台詞の比率が高い章を挙げる。

    講評の重大な指摘「世界観の説明を台詞ではなく、視覚描写に分散させよ」に対応する見張り。
    実測（2026-10-08・第1〜24章）: 60字以上の台詞が4本以上ある章は7つ。そのうち
    説明の言い回しを含む割合は 第5章0.71・第6章0.55・第9章0.80 と突出し、
    第2章0.43・第23章0.25 が続いた（＝序盤の世界観説明の章が濃い）。
    しきい値はその実測に合わせて 0.5（4本以上ある章だけを対象）。

    `quotes` は改訂ブリーフに載せる**実際の台詞**。指摘に台詞が無いと
    「同じ調子で書き直される」だけで比率が動かない（実測 2026-10-08:
    検出した3章を書き直しても 0.55→0.55 と変化しなかった）。
    """
    out = []
    for num, _f, text in chapters:
        lines = re.findall(r"「([^「」]{1,300})」", text)
        long_lines = [ln for ln in lines if len(ln) >= min_chars]
        if len(long_lines) < min_lines:
            continue
        exp = [ln for ln in long_lines if any(k in ln for k in EXPL_PHRASES)]
        ratio = len(exp) / len(long_lines)
        if ratio >= warn_ratio:
            # ブリーフに載せる台詞は**重複を除いて順序を保つ**。同じ台詞が章の中に
            # 何度も出ることがあり（同じ説明を別の場面で繰り返す）、そのまま出すと
            # ブリーフが同じ行で埋まって読めない。
            uniq = list(dict.fromkeys(exp))
            out.append({"chapter": num, "ratio": round(ratio, 2),
                        "expository": len(exp), "long_lines": len(long_lines),
                        "samples": [ln[:34] for ln in uniq[:2]],
                        "quotes": [ln[:quote_chars] for ln in uniq[:max_quotes]]})
    return out


# ---------------------------------------------------------------- 審査チェック
JUDGE_SYSTEM = (
    "あなたは日本語小説の整合性を検査する担当です。設定文書と本文を読み、"
    "設定に反する記述・人物の話し方の逸脱・放置された伏線だけを挙げます。"
    "感想や推測は書かず、必ず本文から短く引用します。回答は日本語で書きます。"
)


def check_pronoun_shift(chapters: list, span: int = 2, min_count: int = 3) -> list:
    """主人公の一人称が章をまたいで不自然に入れ替わっていないか（口調のブレ）。

    なぜ必要か（実測 2026-10-13・『鉄の肺と錆びた聖歌』17章版）:
    講評が ★★★☆☆ の理由の一つに「主人公シオンの呼称が『俺』から『僕』へ
    不自然に変化している」を挙げていた。実測してみると**指摘は正しく**、
      第1〜3章 俺のみ → 第4〜6章 僕のみ → 第7〜9章 俺のみ → 第10〜12章 混在
    と**4回も切り替わって**いた。LLM は章ごとに独立に書くので、章をまたいで
    一人称をそろえる仕組みが無い。characters.md の例文も一人称を書いていない
    （「……青い。君の歌は…」）ため、どちらを使うかの指定が存在しなかった。

    判定: 直近 span 章ぶんの一人称の構成が前後で違ったら警告する。
    「混在」のまま続くのは一貫しているので警告しない（意図的な揺らぎと区別できないため）。
    """
    out = []
    prev = None
    for num, _name, text in chapters:
        dlg = re.findall(r"「([^」]*)」", text)
        ore = sum(d.count("俺") for d in dlg)
        boku = sum(d.count("僕") for d in dlg)
        watashi = sum(d.count("私") + d.count("わたし") for d in dlg)
        if ore + boku + watashi < min_count:
            prev = None
            continue
        # 台詞の一人称が1種類だけなら「俺」「僕」「私」、複数なら「混在」
        used = [n for n, c in (("俺", ore), ("僕", boku), ("私", watashi)) if c]
        cur = used[0] if len(used) == 1 else "混在"
        if prev is not None and prev != cur:
            out.append({
                "chapter": num,
                "from": prev,
                "to": cur,
                "detail": f"一人称が「{prev}」から「{cur}」に切り替わった（この章: 俺={ore} 僕={boku} 私={watashi}）",
            })
        prev = cur
    return out


def check_term_shift(chapters: list, characters: Path, span: int = 2) -> list:
    """定義キャラを、名前ではなく「女」「男」などの種類語で呼んでいないか。

    なぜ必要か（実測 2026-10-13・同作）: 講評の指摘に「ルナの呼称が『女』から
    『君』へ変化している」があった。実測では全17章に「女」が残っていた
    （第1章だけで9回）。設定には名前があるので、本文も名前で呼ぶのが原則。
    地の文での「女」は描写として成り立つ場合もあるので、**警告**にとどめる。
    """
    try:
        defined = extract_defined_names(characters)
    except Exception:
        return []
    if not defined:
        return []
    generic = ("女", "男", "少女", "少年", "老人", "相手", "相手方")
    out = []
    for num, _name, text in chapters:
        # その章に定義キャラの名前が出ているときだけ見る（出ていなければ対象外）
        present = [n for n in defined if n in text]
        if not present:
            continue
        hits = {g: len(re.findall(rf"{g}[、。はがもに]?", text)) for g in generic}
        hits = {g: c for g, c in hits.items() if c >= 2}
        if hits:
            out.append({
                "chapter": num,
                "names": present,
                "generic": hits,
                "detail": f"名前のある人物（{'・'.join(present)}）を種類語で呼んでいる: "
                          + " ".join(f"{g}={c}" for g, c in sorted(hits.items(), key=lambda x: -x[1])),
            })
    return out


def _voice_identifies(line: str, characters: Path) -> bool:
    """口調だけで話者が一意に決まる台詞か（＝話者の地の文が無くても許容してよいか）。

    characters.md の「口癖」を読み、その台詞に**ちょうど1人分の口癖だけ**が
    含まれていれば、話者は口調で自明とみなす。
    実測: シオンの口癖「……」が入った短文は他に候補が無いので許容してよい。
    逆に「私」はルナとゼノスの両方に当たるので、ここでは判定に使わない。
    """
    try:
        text = read(characters)
    except Exception:
        return False
    # 「口癖」行から「」で囲まれた口癖を集める（例: 口癖: 「……（沈黙）」「（視える）」）
    habits = {}
    for m in re.finditer(r"口癖[^:：]*[:：]\s*(.+)", text):
        for q in re.findall(r"「([^」]+)」", m.group(1)):
            core = re.sub(r"[（(][^)）]*[)）]", "", q).strip()   # 「……（沈黙）」→「……」
            if core:
                habits[core] = True
    if not habits:
        return False
    hit = [q for q in habits if q and q in line]
    return len(hit) == 1


def check_speaker_gap(chapters: list, characters: Path) -> list:
    """台詞の直前に**話者を示す地の文が無い**箇所（話者の帰属が壊れている）。

    なぜ必要か（実測 2026-10-13・『鉄の肺と錆びた聖歌』第6章）:
    講評が「キャラクターの口調や役割が途中で揺らいでいる」と指摘していた。
    実測してみると原因は口調ではなく**話者の帰属そのものが抜けていた**:

      ゼノス「さて、まずはあなたのその空っぽな肺の構造を…」
      シオン「いきなり解剖するつもりかよ…」
      ？？？「解剖などという野蛮なことはしませんよ…」   ← ルナの口調（私・〜ですよ）
      ゼノスの言葉に合わせて、天井から…                ← 直後に「ゼノスの言葉」と帰属

    「〜しませんよ」はルナの口調なのに、次の地の文は「ゼノスの言葉」。
    間に「ルナが言った」の一行が無いので、**読者にも講評モデルにも誰の台詞か不明**。
    結果として「口調が揺らいでいる」という評になる。

    判定: 台詞があり、その直前に（a）誰かの名前（b）話者を示す語（言った・呟いた・叫んだ 等）
    のどちらも無い行を拾う。前の行が台詞だけの場合、話者が切り替わった可能性が高い。
    """
    try:
        defined = extract_defined_names(characters)
    except Exception:
        return []
    speaker_cues = ("言っ", "呟", "叫", "囁", "答", "尋ね", "問", "声", "続け", "付け加",
                    "付け足", "遮", "反論", "確認", "宣言", "唱")
    out = []
    for num, _name, text in chapters:
        lines = [l for l in text.split("\n")]
        for i, l in enumerate(lines):
            if i < HEAD_SKIP:
                continue          # 章の冒頭は前の文脈が無いので話者が不明で当然（判定対象外）
            if "「" not in l:
                continue
            prev = next((lines[j] for j in range(i - 1, -1, -1) if lines[j].strip()), "")
            if not prev:
                continue
            if "「" in prev:
                continue          # 直前も台詞＝同じ話者の連続発話として許容
            if any(n in prev for n in defined):
                continue          # 名前で話者が示されている
            if any(c in prev for c in speaker_cues):
                continue          # 「〜と答えた」などで話者が示されている
            # 口調で話者が一意に決まる台詞は許容する（独立行の台詞は小説の書式として普通）。
            # 実測: 「……静かすぎるな」はシオンの沈黙＋短文なので他に候補が無い。
            if _voice_identifies(l, characters):
                continue
            out.append({
                "chapter": num,
                "line_no": i + 1,
                "prev": prev.strip()[:60],
                "dialogue": l.strip()[:60],
                "detail": f"{i + 1}行目の台詞の直前に話者を示す地の文がありません"
                          f"（直前の行: 「{prev.strip()[:50]}」）。"
                          f"誰の台詞か判別できないので、話者の一行（例:「ルナが言った。」）を足してください",
            })
    return out


def check_voice_pronoun_conflict(chapters: list, characters: Path) -> list:
    """台詞の**口調と一人称が食い違って**いないか（設定と照合）。

    なぜ必要か（実測 2026-10-13・『鉄の肺と錆びた聖歌』第13章）:
    一人称を「俺」に統一する作業で見つけた。本文にこうあった:

      ルナが振り返った。銀色の瞳に強い決意が宿っている。
      「試してみる価値はあるわね。…鍵が、僕たちの欠陥にあるのだとしたら」

    「〜わね」はルナの口調（設定: 一人称「私」）なのに「僕たち」になっている。
    LLM が**口調と一人称を別々に生成した**ため、帰属は合っていても中身が矛盾する。
    これは口調のブレとして読者に明確に判るので、機械で拾う。

    判定: 台詞の中に「一人称X＋語尾」の組み合わせで、設定の人物と食い違うものを拾う。
    語尾は話者の帰属が取れないので、**設定の口調の語尾（例: ルナ「わね」「でしょう」）と
    一人称（僕たち）が同じ台詞に同居**しているときだけ警告する（安全側）。
    """
    try:
        text = read(characters)
    except Exception:
        return []
    # 設定の人物ごとの一人称と口調の語尾を抜く
    # 形式: 「0. **一人称...**: **「俺」**」と「口癖: 「調律しましょう」」
    blocks = re.split(r"^##\s+", text, flags=re.M)
    persons = {}
    for b in blocks[1:]:
        head = b.splitlines()[0] if b.splitlines() else ""
        # 見出しは「1. シオン (Shion)」「5. 追加キャラクター：リナ (Lina)」の形。
        # ローマ字を落とし、番号・肩書き・ローマ字を除いた**日本語の名前**を取る。
        raw = re.sub(r"[（(][^)）]*[)）]", "", head).strip()   # ローマ字を落とす
        raw = re.sub(r"^\d+[.．、]?\s*", "", raw)             # 先頭の番号
        raw = re.split(r"[：:]", raw)[-1].strip()             # 「追加キャラクター：リナ」
        nm = re.search(r"[ァ-ヶー]{2,6}$", raw) or re.search(r"[ぁ-ん一-龥]{2,6}$", raw)
        name = nm.group(0) if nm else None
        if not name:
            continue
        pm = re.search(r"一人称[^:：]*[:：][^「]*「([^」]+)」", b)
        pron = pm.group(1) if pm else None
        # 口調の語尾＝口癖の**語尾部分**。口癖行は「調律しましょう」「不協和ですね」なので、
        # 括弧を除いた本体の**末尾2〜4文字**を語尾として取る。
        # （「わね」「でしょう」という語尾ワードの照合では、口癖が固有名詞だと拾えなかった。
        #   実測 2026-10-13: ルナの口癖から語尾が取れず、第13章の「僕たち」を見逃した）
        tail_words = []
        for t in re.findall(r"口癖[^:：]*[:：](.+)", b):
            for q in re.findall(r"「([^」]+)」", t):
                core = re.sub(r"[（(][^)）]*[)）]", "", q).strip()
                if len(core) >= 3:
                    tail_words.append(core[-3:])       # 口癖の語尾そのもの
        # 口癖に加えて、その人物の話し方を示す**語尾ワード**も見る。
        # 口癖は「調律しましょう」なので「わね」は出てこない。第13章の実測では
        # ルナの台詞が「試してみる価値はあるわね。…僕たちの欠陥が…」だったので、
        # 「わね」を語尾として持たせないと見逃す。
        voice_tail = re.search(r"文の長さ|丁寧|婉曲|流麗", b)
        if voice_tail and ("丁寧" in b or "婉曲" in b or "流麗" in b):
            tail_words += ["わね", "でしょう", "かしら", "ますわ"]
        if pron or tail_words:
            persons[name] = {"pron": pron, "tails": tail_words}
    if not persons:
        return []

    conflicts = []
    for num, _name, ch_text in chapters:
        lines = ch_text.split("\n")
        cur = None
        for l in lines:
            if "「" not in l:
                # 地の文。直前の名前で話者を持つが、**口調が別人のものなら口調を優先**する
                # （実測: 「シオンは不信感を露わにし」の直後の「あら、私の調和が…」は
                #  ルナの台詞。地の文にシオンが出ただけでシオンにすると誤検出13件になった）
                found = None
                for n in persons:
                    if n in l:
                        found = n
                        break
                if found:
                    cur = found
                continue
            # 口調から話者を推定（一人称＋語尾の両方が設定と食い違う人物を探す）
            speaker = _guess_speaker_by_voice(l, persons) or cur
            if speaker not in persons:
                continue
            p = persons[speaker]
            if p["tails"] and p["pron"]:
                wrong = [x for x in ("僕", "俺", "私") if x in l and x != p["pron"]]
                hit_tail = [t for t in p["tails"] if t in l]
                if wrong and hit_tail:
                    conflicts.append({
                        "chapter": num,
                        "name": speaker,
                        "dialogue": l.strip()[:70],
                        "detail": f"{speaker}の台詞に口調（{'・'.join(hit_tail)}）と"
                                  f"設定と違う一人称（{'・'.join(wrong)}）が同居しています。"
                                  f"設定の一人称は「{p['pron']}」です",
                    })
    return conflicts


def _guess_speaker_by_voice(line: str, persons: dict) -> "str | None":
    """台詞の口調（語尾）から話者を推定する。

    複数の人物が同じ語尾を使う場合は None（推定できない）。
    「わね」「ましょう」はルナ、「だぜ」はリナ、「クソったれ」はガラム、
    「……」（沈黙）はシオン、といった具合に一意に決まるものだけ返す。
    """
    hit = [n for n, p in persons.items() if p["tails"] and any(t in line for t in p["tails"])]
    # 一人称も材料に入れる（「私」ならルナかゼノス）
    hit2 = [n for n in hit if persons[n]["pron"] in line] if hit else []
    if len(hit2) == 1:
        return hit2[0]
    if len(hit) == 1:
        return hit[0]
    return None


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
    lines.append("## 機械項目（辞書なしで精度の出る3項目）")
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
    dta = mech.get("death_then_alive")
    if dta is not None:
        lines.append(f"- 死んだのに後で生きている人物: {len(dta)}件")
        for it in dta:
            lines.append(f"  - {it['name']}（第{it['died_in']}章で「{it['mark']}」）→ "
                         f"{it['gap']}章の空白を挟んで第{it['returns_in']}章から台詞・動作あり"
                         f"（以後の手がかり {sum(it['alive_later'])}箇所）")
    expo = mech.get("exposition_dialogue")
    if expo is not None:
        lines.append(f"- 説明台詞の多い章: {len(expo)}件")
        for it in expo:
            lines.append(f"  - 第{it['chapter']}章: 長い台詞 {it['long_lines']}本のうち "
                         f"{it['expository']}本が説明（{it['ratio']}）"
                         "＝設定を台詞で説明している。地の文・視覚描写へ移す候補")
    ps = mech.get("pronoun_shift")
    if ps is not None:
        lines.append(f"- 一人称の切り替わり（口調のブレ）: {len(ps)}件")
        for it in ps:
            lines.append(f"  - 第{it['chapter']}章: {it['detail']}"
                         "＝ characters.md の一人称にそろえる")
    ts = mech.get("term_shift")
    if ts is not None:
        lines.append(f"- 名前のある人物を種類語で呼んでいる章: {len(ts)}件")
        for it in ts:
            lines.append(f"  - 第{it['chapter']}章: {it['detail']}"
                         "＝ 設定の名前で呼ぶ（地の文の描写として成り立つ場合は許容）")
    sg = mech.get("speaker_gap")
    if sg is not None:
        lines.append(f"- 話者の示されていない台詞: {len(sg)}件")
        for it in sg[:12]:
            lines.append(f"  - 第{it['chapter']}章 {it['line_no']}行: "
                         f"台詞「{it['dialogue'][:40]}」の前に話者の地の文が無い")
        if len(sg) > 12:
            lines.append(f"  - …ほか {len(sg) - 12}件")
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
    dta = mech.get("death_then_alive") or []
    if dta:
        out.append("## 死んだのに後で生きている人物（最優先で直す）")
        for it in dta:
            out.append(f"- **{it['name']} は第{it['died_in']}章で死んでいる（「{it['mark']}」）のに、"
                       f"{it['gap']}章の空白を挟んで第{it['returns_in']}章から生きて台詞・動作がある。**"
                       "生きている人物として書かないこと。触れてよいのは、記憶・遺した歌や品・"
                       "残響としての言及（過去形）だけ。その場面の役割は、居合わせる人物と主人公の内面で引き受ける。")
        out.append("")
    expo = mech.get("exposition_dialogue") or []
    if expo:
        out.append("## 説明台詞の多い章（設定説明を台詞から降ろす）")
        for it in expo:
            out.append(f"- 第{it['chapter']}章: 長い台詞 {it['long_lines']}本のうち "
                       f"{it['expository']}本が設定説明（{it['ratio']}）。")
            # 実際の台詞を出す。**これが無いと「同じ調子で書き直される」だけで比率が動かない**
            # （実測 2026-10-08: 検出した3章を書き直しても説明台詞の比率が 0.55→0.55 と
            #  変化しなかった。指摘に台詞が無く、直す対象が特定できていなかったため）。
            out.append("  次の台詞が設定の説明になっています。1本ずつ、**視覚や体の感覚の"
                       "描写に置き換える**か、短いやり取りに分割してください。"
                       "登場人物に世界の仕組みを語らせないこと。")
            for q in (it.get("quotes") or []):
                out.append(f"  - 「{q}」")
        out.append("")
    ps = mech.get("pronoun_shift") or []
    if ps:
        out.append("## 一人称の切り替わり（口調のブレ）")
        out.append("主人公の一人称が章をまたいで変わっています。**characters.md に書いてある"
                   "一人称にそろえてください。** 成長や心境の変化で変える場合は、その章の"
                   "中で読者に分かる形（場面の転換・時間経過）を示してから変えること。"
                   "唐突な切り替えはキャラクターの同一性が損なわれます。")
        for it in ps:
            out.append(f"- 第{it['chapter']}章: {it['detail']}")
        out.append("")
    ts = mech.get("term_shift") or []
    if ts:
        out.append("## 名前のある人物を種類語で呼んでいる章")
        out.append("設定に名前のある人物を「女」「男」などの種類語で呼んでいます。"
                   "**設定の名前で呼んでください。** ただし、視点人物が相手を識別できない"
                   "状況（暗がり・初対面・記憶の断片）での「女」は描写として成り立つので許容します。")
        for it in ts:
            out.append(f"- 第{it['chapter']}章: {it['detail']}")
        out.append("")
    sg = mech.get("speaker_gap") or []
    if sg:
        out.append("## 話者の示されていない台詞（口調のブレの原因）")
        out.append("**この章の台詞に、誰が喋っているか判別できない箇所があります。**")
        out.append("直前の行が地の文なのに、話者の名前も「〜と答えた」も無い状態です。")
        out.append("読者にも審査にも誰の台詞か不明なので、「キャラクターの口調が揺らいでいる」")
        out.append("という評になります。**話者を一行で示してください**（例:「ルナが言った。」）。")
        out.append("同じ人物が続けて喋る場合は、直前の行が台詞なので問題ありません。")
        for it in sg:
            out.append(f"- 第{it['chapter']}章 {it['line_no']}行: {it['detail']}")
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
    if "death_then_alive" in only:
        mech["death_then_alive"] = check_death_then_alive(
            extract_defined_names(repo / "characters.md"), chapters)
    if "exposition_dialogue" in only:
        mech["exposition_dialogue"] = check_exposition_dialogue(chapters)
    if "pronoun_shift" in only:
        mech["pronoun_shift"] = check_pronoun_shift(chapters)
    if "term_shift" in only:
        mech["term_shift"] = check_term_shift(chapters, repo / "characters.md")
    if "speaker_gap" in only:
        mech["speaker_gap"] = check_speaker_gap(chapters, repo / "characters.md")
    if "voice_pronoun_conflict" in only:
        mech["voice_pronoun_conflict"] = check_voice_pronoun_conflict(
            chapters, repo / "characters.md")

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
