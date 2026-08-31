#!/usr/bin/env python3
"""
Adversarial editing pass: ask the judge to CUT 500 words from each chapter.
What gets cut reveals what's weakest. The cut list IS the revision plan.

Usage: python adversarial_edit.py 1        # single chapter
       python adversarial_edit.py all      # all chapters
"""
import os
import sys
import json
import re
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

import llm




CHAPTERS_DIR = BASE_DIR / "chapters"
EDIT_LOG_DIR = BASE_DIR / "edit_logs"
EDIT_LOG_DIR.mkdir(exist_ok=True)

def call_judge(prompt, max_tokens=8000):
    return llm.call_judge(
        prompt,
        system="あなたは冷酷な文芸編集者です。あなたは散文から無駄を削ります。良い十分な文に対して感傷はありません——文章が居場所を稼いでいなければ、削除します。あなたは本文から正確に引用します。決して捏造したり言い換えたりしません。必ず有効なJSONで応答してください。",
        max_tokens=max_tokens,
        temperature=0.3,
    )


def parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    start = text.find('{')
    if start == -1:
        start = text.find('[')
    if start == -1:
        raise ValueError("No JSON found")
    # Try direct parse first
    try:
        return json.loads(text[start:], strict=False)
    except json.JSONDecodeError:
        # Find matching brace
        depth = 0
        in_string = False
        escape = False
        open_char = text[start]
        close_char = '}' if open_char == '{' else ']'
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\' and in_string:
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == open_char:
                depth += 1
            elif c == close_char:
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i+1], strict=False)
        return json.loads(text[start:], strict=False)

EDIT_PROMPT = """あなたはファンタジー小説の章を編集する編集者です。あなたの仕事は、この章をより引き締め、鋭く、生き生きとさせるために、何を削り、何を書き直すべきかを正確に特定することです。

THE CHAPTER（{word_count}語）:
{chapter_text}

YOUR TASK（あなたの仕事）:
1. 削除または書き直すべき具体的な箇所を10〜20個見つけること。
   各箇所について、正確なテキストを引用し（曖昧にならないよう引用は10語以上）、
   なぜ弱いのかを説明し、分類すること。

2. 各削除を以下のいずれかに分類すること：
   - FAT（脂肪）: 何も加えず、削除しても損失がない
   - REDUNDANT（冗長）: 前の文/場面がすでに示したことを言い直す
   - OVER-EXPLAIN（説明過多）: 語り手が場面がすでに実演したことを説明する
   - GENERIC（凡庸）: どの小説にも現れ得る、この世界・キャラクターに固有でない
   - TELL（説明）: 感情や状態を「見せる」代わりに「名付ける」
   - STRUCTURAL（構造的）: ペーシングやリズムを乱す段落・セクション

3. 書き直し候補（削除ではない）については、具体的な修正案を提供すること。

4. 章が失うことなく削れる総語数を見積もること。

JSONで応答すること:
{{
  "cuts": [
    {{
      "quote": "章からの正確なテキスト（10語以上）",
      "type": "FAT|REDUNDANT|OVER-EXPLAIN|GENERIC|TELL|STRUCTURAL",
      "reason": "なぜこれを削るべきか",
      "action": "CUT or REWRITE",
      "rewrite": "actionがREWRITEの場合は置換テキスト、CUTならnull"
    }}
  ],
  "total_cuttable_words": N,
  "tightest_passage": "章の中で最も良い2〜3文を引用——絶対に触れないもの",
  "loosest_passage": "最も悪い2〜3文を引用——最も手を入れるべきもの",
  "overall_fat_percentage": N,
  "one_sentence_verdict": "この章がうまくできていることと、何が重くしているかを一文で"
}}
"""

def edit_chapter(ch_num):
    ch_path = CHAPTERS_DIR / f"ch_{ch_num:02d}.md"
    text = ch_path.read_text()
    word_count = len(text.split())
    
    prompt = EDIT_PROMPT.format(chapter_text=text, word_count=word_count)
    raw = call_judge(prompt)
    result = parse_json(raw)
    
    # Save log
    log_path = EDIT_LOG_DIR / f"ch{ch_num:02d}_cuts.json"
    with open(log_path, "w") as f:
        json.dump(result, f, indent=2)
    
    return result, word_count

def main():
    if len(sys.argv) < 2:
        print("Usage: python adversarial_edit.py <chapter_num|all>")
        sys.exit(1)
    
    if sys.argv[1] == "all":
        chapters = list(range(1, 25))
    else:
        chapters = [int(sys.argv[1])]
    
    for ch in chapters:
        print(f"\n{'='*50}")
        print(f"EDITING CH {ch}")
        print(f"{'='*50}")
        
        try:
            result, wc = edit_chapter(ch)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue
        
        cuts = result.get("cuts", [])
        cuttable = result.get("total_cuttable_words", 0)
        fat_pct = result.get("overall_fat_percentage", 0)
        verdict = result.get("one_sentence_verdict", "")
        
        # Count by type
        type_counts = {}
        for c in cuts:
            t = c.get("type", "?")
            type_counts[t] = type_counts.get(t, 0) + 1
        
        print(f"  Words: {wc}")
        print(f"  Cuts found: {len(cuts)}")
        print(f"  Cuttable words: ~{cuttable} ({fat_pct}% fat)")
        print(f"  By type: {type_counts}")
        print(f"  Verdict: {verdict}")
        print(f"  Tightest: {result.get('tightest_passage', '')[:100]}...")
        print(f"  Loosest:  {result.get('loosest_passage', '')[:100]}...")

if __name__ == "__main__":
    main()
