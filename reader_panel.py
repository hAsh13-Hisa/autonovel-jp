#!/usr/bin/env python3
"""
4-reader panel for full-arc novel evaluation.
Each reader has a distinct persona and evaluates the NOVEL, not chapters.
The disagreements between readers are where editorial decisions live.

Usage: python reader_panel.py
"""
import os
import sys
import json
import re
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

import llm

# 出力量の上限。実測（2026-10-08）: 4,000 では**必ず切れて、4人格すべてが例外**になった
# （kimi-k3 の自然な出力量は10項目の長文で4,000を超える）。切れても救済で読むが、
# そもそも切らせない。thinking 系の判定モデルは出力が長い前提で取る。
PANEL_MAX_TOKENS = int(os.environ.get("AUTONOVEL_PANEL_MAX_TOKENS", "8000"))

READERS = {
    "editor": {
        "name": "編集者",
        "system": (
            "あなたは大手出版社のベテラン小説編集者です。200冊以上の小説を担当してきました。"
            "見るのは文章の質感、行間、一文レベルの技術、そして語り口が一貫していて"
            "「そう書かれるに値する」ものになっているかです。地の文が説明過多になっていないか、"
            "セリフが話し言葉ではなく「書かれた言葉」になっていないか、比喩が借り物か"
            "自分のものかを見抜きます。残酷ではありませんが正確です。"
            "「上手い文章」と「生きている文章」の違いを知っています。"
            "返すのは有効な JSON オブジェクト1つだけです。回答はすべて日本語で書いてください。"
        ),
    },
    "genre_reader": {
        "name": "ジャンルの読者",
        "system": (
            "あなたは年に50冊以上のファンタジーを読む読者です。気にするのはテンポ、謎、"
            "世界観が回収される気持ちよさ、そして「ページをめくり続けたいか」です。"
            "どこにも向かわない美文には退屈します。捜査や探索が停滞する、緊張が平らになる、"
            "作者が物語よりも自分の世界に入れ込んでいる、といった兆候に気づきます。"
            "好きなものには惜しみなく、退屈なものには率直です。"
            "返すのは有効な JSON オブジェクト1つだけです。回答はすべて日本語で書いてください。"
        ),
    },
    "writer": {
        "name": "書き手",
        "system": (
            "あなたは5冊の長編と主要な新人賞の候補になったことのあるファンタジー作家です。"
            "職人として読みます。構成を見ます — 山場がどこに落ちているか、伏線が回収されているか、"
            "人物の変化が完結しているか。技術が前に出ているか、物語に溶けているかにも気づきます。"
            "最高の褒め言葉は「読んでいることを忘れた」、最悪の言葉は「設計図が見える」。"
            "作品が狙ったものと、実際に届いたものの差を気にします。"
            "返すのは有効な JSON オブジェクト1つだけです。回答はすべて日本語で書いてください。"
        ),
    },
    "first_reader": {
        "name": "普通の読者",
        "system": (
            "あなたは思慮深い一般読者です。書き手でも編集者でも、ジャンルの専門家でもありません。"
            "体験として読みます。感じたことは分かるが、理由は分からないこともあります。"
            "心が動いたとき、退屈したとき、混乱したとき、誰かに話したくなったときに気づきます。"
            "技術用語は使いません。「この部分はどうでもよかった」「この場面のあとは一度本を置いて"
            "気持ちを落ち着けた」のような言い方をします。分析ではなく、感情に正直な感想を返します。"
            "返すのは有効な JSON オブジェクト1つだけです。回答はすべて日本語で書いてください。"
        ),
    },
}

# 章番号の拾い方: 日本語（第5章）と英語（Ch 5 / Chapter 5）の両方。
# 実測（2026-10-08）: 英語前提の `Ch(\\d+)` だけだったため、日本語で返ってくる回答からは
# 章番号が1つも拾えず、**読者どうしの食い違いの検出が永久に空**になっていた。
CHAPTER_RE = re.compile(r"第\s*(\d+)\s*章|Ch(?:apter)?\s*(\d+)", re.IGNORECASE)


def chapters_mentioned(answer: str) -> set:
    return {int(a or b) for a, b in CHAPTER_RE.findall(answer or "")}

READER_PROMPT = """あなたは、要約の形で小説を1冊読み終えたところです。
要約には章ごとの出来事、各章の書き出しと結び、主要なセリフが含まれます。

{arc_summary}

ここからは「作品全体」について答えてください。具体的に書いてください。
本文を引用できるところは引用し、章番号を必ず添えてください。

出力の決まり（守らないと答えが途中で切れます）:
- **各項目は2文以内・120字以内**で書く。長い講評を書かない。
- 前置き・お礼・全体の総評・「以下が回答です」のような文は**書かない**。
- 出すのは **JSON だけ**。JSON の外に文字を書かない。

返すのは次の形の JSON だけです（キーは英語のまま、**値はすべて日本語**）:
{{
  "momentum_loss": "物語はどこで勢いを失いますか。該当する章と、何が原因で停滞するのかを具体的に。最後まで失わないなら、そう書いて理由を説明してください。",

  "earned_ending": "結末は、それまでの積み上げに見合っていましたか。主人公の選択は納得できますか。最後の場面は最初の場面と噛み合っていますか。見合っていないと感じた点はどこですか。",

  "cut_candidate": "もし1割短くするとしたら、最初にどの章・どの部分を削りますか。理由と、削ると何が失われるかを書いてください。",

  "missing_scene": "この作品に欠けている場面はありますか。起きるべきなのに起きていない会話、書かれるに値するのに書かれていない瞬間、もっと出番が必要な人物。入れるならどこに入れるかを具体的に。",

  "thinnest_character": "最後まで薄いままだった人物は誰ですか。もっと知りたいと思った人物は誰ですか。削っても作品が損なわれない人物はいますか。",

  "best_scene": "作品で最も良い場面はどこですか。感情が動いた箇所を引用してください。なぜ効いているのですか。",

  "worst_scene": "最も弱い場面はどこですか。何が起きているのですか。どう直しますか。",

  "would_recommend": "この作品を誰かに勧めますか。誰にですか。一言で紹介するとしたら何と言いますか。",

  "haunts_you": "読み終えたあとに残っている一行や場面はありますか。引用してください。",

  "next_book": "この書き手の次の作品を読みますか。理由も書いてください。"
}}
"""

def call_reader(reader_key, arc_summary):
    reader = READERS[reader_key]
    raw = llm.call_judge(
        READER_PROMPT.format(arc_summary=arc_summary),
        system=reader["system"],
        max_tokens=PANEL_MAX_TOKENS,
        temperature=0.7,
    )

    # Parse JSON
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r'^```\w*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
    start = raw.find('{')
    if start >= 0:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(raw)):
            c = raw[i]
            if escape: escape = False; continue
            if c == '\\' and in_string: escape = True; continue
            if c == '"' and not escape: in_string = not in_string; continue
            if in_string: continue
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start:i+1], strict=False)
                    except json.JSONDecodeError:
                        break
    # --- 救済: 閉じていない／切れた応答から、書けている項目だけ拾う ---
    # 実測（2026-10-08）: 出力量の上限に張り付いて JSON が閉じず、4人格すべてが
    # 例外になって**パネルが黙って空の結果を返していた**（気づけたのは単体で走らせたから）。
    salvaged = salvage_pairs(raw)
    if salvaged:
        print(f"  [注意] JSONが閉じていませんでした。書けていた {len(salvaged)}項目だけ拾います"
              f"（上限 {PANEL_MAX_TOKENS} トークンに張り付き）")
        return salvaged
    if not raw:
        raise ValueError("応答が空です（思考に出力枠を食われた可能性。ローカル判定なら AUTONOVEL_OLLAMA_THINK を外す）")
    raise ValueError(f"JSONを読めませんでした（応答の先頭: {raw[:200]!r}）")


def salvage_pairs(raw: str) -> dict:
    """切れた JSON から `"キー": "値"` の組だけを拾う（読める分だけで進む）。"""
    out = {}
    for m in re.finditer(r'"([a-z_]+)"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.S):
        key, val = m.group(1), m.group(2)
        try:
            out[key] = json.loads(f'"{val}"', strict=False)
        except json.JSONDecodeError:
            out[key] = val
    return out

def find_disagreements(results):
    """Find where readers disagree -- that's where the editorial decisions live."""
    disagreements = []
    
    for question in ["momentum_loss", "cut_candidate", "thinnest_character", "worst_scene"]:
        answers = {k: v.get(question, "") for k, v in results.items()}
        # 章番号を拾う（日本語の「第5章」と英語の「Ch 5」の両方）。
        # 変数名を関数名と分ける（同名にすると自分自身を呼べなくなる）。
        mentioned = {}
        for reader, answer in answers.items():
            mentioned[reader] = chapters_mentioned(answer)

        # Find chapters where only some readers flagged an issue
        all_chs = set()
        for chs in mentioned.values():
            all_chs.update(chs)

        for ch in all_chs:
            flagged_by = [r for r, chs in mentioned.items() if ch in chs]
            not_flagged = [r for r, chs in mentioned.items() if ch not in chs]
            if flagged_by and not_flagged:
                disagreements.append({
                    "question": question,
                    "chapter": int(ch),
                    "flagged_by": flagged_by,
                    "not_flagged": not_flagged,
                    "details": {r: answers[r][:200] for r in flagged_by}
                })
    
    return disagreements

def main():
    arc_summary = (BASE_DIR / "arc_summary.md").read_text()
    
    results = {}
    failed = []
    for reader_key, reader_info in READERS.items():
        print(f"\n{'='*50}")
        print(f"READING: {reader_info['name']}")
        print(f"{'='*50}")
        
        try:
            result = call_reader(reader_key, arc_summary)
            results[reader_key] = result
            
            # Print highlights
            print(f"  Momentum loss: {result.get('momentum_loss', '')[:150]}...")
            print(f"  Best scene: {result.get('best_scene', '')[:150]}...")
            print(f"  Would recommend: {result.get('would_recommend', '')[:150]}...")
        except Exception as e:
            failed.append(reader_key)
            print(f"  ERROR: {e}")

    # 全員が失敗したら黙って空の結果を返さない（実測: これで気づかず走り続けた）
    if len(failed) == len(READERS):
        (BASE_DIR / "edit_logs").mkdir(exist_ok=True)
        # 前の周の結果を「今の結果」として読ませない（古い指摘で改訂するのを防ぐ）
        (BASE_DIR / "edit_logs" / "reader_panel.json").write_text(
            json.dumps({"readers": {}, "disagreements": [], "failed": True,
                        "failed_readers": failed,
                        "timestamp": datetime.now().isoformat()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("\n全員の読み取りに失敗しました。空の結果を「指摘なし」として扱わないため、ここで止めます。")
        print("（対処: 出力上限 AUTONOVEL_PANEL_MAX_TOKENS を上げる／判定モデルを替える）")
        return 1
    if failed:
        print(f"\n[注意] {len(failed)}人ぶんが読めませんでした: {', '.join(failed)}")
    
    # Find disagreements
    disagreements = find_disagreements(results)
    
    # Print consensus and disagreement
    print(f"\n{'='*60}")
    print("READER PANEL RESULTS")
    print(f"{'='*60}")
    
    for question in ["momentum_loss", "earned_ending", "cut_candidate", "missing_scene", 
                      "thinnest_character", "best_scene", "worst_scene", "would_recommend",
                      "haunts_you", "next_book"]:
        print(f"\n--- {question.upper()} ---")
        for reader_key in READERS:
            if reader_key in results:
                answer = results[reader_key].get(question, "N/A")
                print(f"  [{READERS[reader_key]['name']}]: {answer[:300]}")
    
    if disagreements:
        print(f"\n{'='*60}")
        print("DISAGREEMENTS (editorial decisions needed)")
        print(f"{'='*60}")
        for d in disagreements:
            print(f"\n  {d['question']} -- Ch {d['chapter']}")
            print(f"    Flagged by: {', '.join(d['flagged_by'])}")
            print(f"    Not flagged: {', '.join(d['not_flagged'])}")
    
    # Save full results
    output = {
        "readers": results,
        "disagreements": disagreements,
        "timestamp": datetime.now().isoformat()
    }
    out_path = BASE_DIR / "edit_logs" / "reader_panel.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
