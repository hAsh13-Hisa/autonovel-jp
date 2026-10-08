#!/usr/bin/env python3
"""
Deep manuscript review via Opus.

Sends the full novel to Claude Opus for dual-persona review:
  1. Literary critic (newspaper book review style)
  2. Professor of fiction (specific, actionable craft suggestions)

Usage:
  python review.py                    # Review, save to edit_logs/
  python review.py --output reviews.md  # Also save human-readable copy
  python review.py --parse            # Parse last review into actionable items
"""
import os
import sys
import json
import re
import argparse
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env", override=True)

import llm

# Use Opus for reviews — it's the best at literary analysis


CHAPTERS_DIR = BASE_DIR / "chapters"
LOGS_DIR = BASE_DIR / "edit_logs"

REVIEW_PROMPT = """以下の小説「{title}」を読み、2つの立場で講評してください。

(1) 文芸評論家として — 新聞の書評のように、この作品の到達点と弱点を論じる。評点は★の5段階で示す。
(2) 小説作法の教授として — 直すべき欠陥を、**該当する章番号つきで**具体的に挙げる。欠陥が無いなら「無い」と書く。

{manuscript}

最後に、次の形の JSON を**1つだけ**出してください（JSONの前後に文章を書かないこと）。
章番号は必ず入れてください（作品全体に関わる指摘は "章": 0 にしてください）。

{{
  "評点": 4.0,
  "重大な指摘": [{{"章": 12, "提案": "どう直すか", "理由": "なぜそう言えるか"}}],
  "軽微な指摘": [{{"章": 5, "提案": "どう直すか", "理由": "なぜそう言えるか"}}]
}}
"""


def call_opus(prompt, max_tokens=8000):
    """Call the review model with the full manuscript."""
    print(f"Sending to review model ({len(prompt):,} chars)...", file=sys.stderr)
    return llm.call_review(
        prompt,
        max_tokens=max_tokens,
        temperature=0.3,
    )


def get_title():
    """Extract novel title from first chapter or outline."""
    outline = BASE_DIR / "outline.md"
    if outline.exists():
        first_line = outline.read_text().split("\n")[0]
        title = first_line.lstrip("# ").strip()
        if title:
            return title
    ch1 = CHAPTERS_DIR / "ch_01.md"
    if ch1.exists():
        first_line = ch1.read_text().split("\n")[0]
        return first_line.lstrip("# ").strip()
    return "Untitled Novel"


def build_manuscript():
    """Concatenate all chapters into a single text."""
    chapters = sorted(CHAPTERS_DIR.glob("ch_*.md"))
    if not chapters:
        print("ERROR: No chapters found.", file=sys.stderr)
        sys.exit(1)
    
    parts = []
    for ch in chapters:
        parts.append(ch.read_text())
    
    manuscript = "\n\n---\n\n".join(parts)
    wc = len(manuscript.split())
    print(f"Manuscript: {len(chapters)} chapters, {wc:,} words", file=sys.stderr)
    return manuscript


def _extract_json_block(text):
    """応答から最初の JSON オブジェクトを取り出す（```json フェンスも許す）。"""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _norm_item(raw, severity, number):
    """指摘1件を内部の形に揃える（辞書でもただの文字列でも受ける）。"""
    if isinstance(raw, dict):
        chapter = raw.get("章", raw.get("chapter", 0))
        proposal = raw.get("提案") or raw.get("suggestion") or raw.get("改善") or ""
        why = raw.get("理由") or raw.get("why") or raw.get("reason") or ""
        title = raw.get("題") or raw.get("title") or proposal
    else:
        chapter, proposal, why = 0, str(raw), ""
        title = str(raw)
    m = re.search(r"\d+", str(chapter))
    chapter = int(m.group(0)) if m else 0
    return {
        "number": number,
        "title": str(title).strip()[:120],
        "chapter": chapter,
        "severity": severity,
        "type": "revision",
        "qualified": False,
        "suggestion": str(proposal).strip()[:500],
        "why": str(why).strip()[:500],
        "full_text": (str(proposal) + (" ／ " + str(why) if why else ""))[:1000],
    }


def parse_review(review_text):
    """Parse a review into structured actionable items."""
    # 先に JSON 形式（いまのプロンプトが要求する形）を読む。日本語のキーでも
    # 「1件も取れない」ことが無いように、新旧のキー名を両方受ける。
    block = _extract_json_block(review_text)
    if isinstance(block, dict):
        items = []
        for key, sev in (("重大な指摘", "major"), ("軽微な指摘", "minor"),
                         ("top_3_improvements", "major"), ("改善提案", "moderate")):
            for raw in (block.get(key) or []):
                items.append(_norm_item(raw, sev, len(items) + 1))
        raw_stars = block.get("評点", block.get("stars"))
        stars = None
        if raw_stars is not None:
            m = re.search(r"\d+(?:\.\d+)?", str(raw_stars))
            stars = float(m.group(0)) if m else None
        summary = (block.get("note") or block.get("critic_summary")
                   or block.get("総評") or block.get("reasons") or "")
        if items or stars or summary:
            return {
                "stars": stars,
                "critic_summary": str(summary).strip()[:500],
                "professor_items": items,
                "total_items": len(items),
                "major_items": sum(1 for i in items if i["severity"] == "major"),
                "qualified_items": sum(1 for i in items if i["qualified"]),
                "raw_text": review_text,
            }

    items = []
    
    # Split into critic and professor sections
    sections = re.split(r'(?:Professor|PROFESSOR|professor).*?(?:Review|Assessment|Analysis|Craft)', 
                        review_text, maxsplit=1)
    
    critic_text = sections[0] if sections else review_text
    professor_text = sections[1] if len(sections) > 1 else ""
    
    # Extract star rating
    star_match = re.search(r'★+½?|(\d+\.?\d*)\s*/?\s*(?:out of\s*)?(?:five|5)', critic_text)
    stars = None
    if star_match:
        star_str = star_match.group(0)
        stars = star_str.count('★') + (0.5 if '½' in star_str else 0)
    
    # Extract professor's numbered items
    # Look for patterns like "1. Title" / "1. 見出し" / "1、見出し"
    prof_items = re.split(r'\n(?=\d+[.．、)]\s*\S)', professor_text)
    
    for section in prof_items:
        if not section.strip():
            continue
        
        # Extract the item title/number
        title_match = re.match(r'(\d+)[.．、)]\s*(.+?)(?:\n|$)', section)
        if not title_match:
            continue
        
        num = int(title_match.group(1))
        title = title_match.group(2).strip()
        
        # Classify severity based on language（日本語の言い回しも見る）
        text_lower = section.lower()
        if any(w in text_lower for w in ['major', 'significant', 'primary', 'most important',
                                         '重大', '致命的', '最重要', '根本的']):
            severity = "major"
        elif any(w in text_lower for w in ['minor', 'small', 'slight', 'cosmetic',
                                           '軽微', '些細', 'わずか', '小さな']):
            severity = "minor"
        else:
            severity = "moderate"
        
        # Classify type
        if any(w in text_lower for w in ['cut', 'compress', 'trim', 'reduce', 'consolidate']):
            fix_type = "compression"
        elif any(w in text_lower for w in ['add', 'expand', 'introduce', 'give', 'more']):
            fix_type = "addition"  
        elif any(w in text_lower for w in ['repetit', 'recurring', 'frequency', 'tic', 'gesture']):
            fix_type = "mechanical"
        elif any(w in text_lower for w in ['restructur', 'rearrang', 'move', 'reorganiz']):
            fix_type = "structural"
        else:
            fix_type = "revision"
        
        # Check if this is qualified/hedged (diminishing returns signal)
        qualified = any(phrase in text_lower for phrase in [
            'individually fine', 'largely successful', 'each instance works',
            'minor relative to', 'small complaint', 'costs of ambition',
            'not a flaw', 'deliberate choice', 'thematically coherent'
        ])
        
        # Extract specific suggestion
        suggestion = ""
        sugg_match = re.search(r'(?:Specific\s+)?[Ss]uggestion[s]?:?\s*\n?(.*?)(?=\n\d+\.|\n\n[A-Z]|\Z)', 
                               section, re.DOTALL)
        if sugg_match:
            suggestion = sugg_match.group(1).strip()[:500]
        
        items.append({
            "number": num,
            "title": title,
            "severity": severity,
            "type": fix_type,
            "qualified": qualified,
            "suggestion": suggestion,
            "full_text": section.strip()[:1000],
        })
    
    return {
        "stars": stars,
        "critic_summary": critic_text.strip()[:500],
        "professor_items": items,
        "total_items": len(items),
        "major_items": sum(1 for i in items if i["severity"] == "major"),
        "qualified_items": sum(1 for i in items if i["qualified"]),
        "raw_text": review_text,
    }


def should_stop(parsed_review):
    """Determine if the novel is done being revised.
    
    Stopping conditions:
    - Stars >= 4
    - No major unqualified items
    - More than half the items are qualified/hedged
    """
    stars = parsed_review.get("stars", 0) or 0
    total = parsed_review["total_items"]
    major = parsed_review["major_items"]
    qualified = parsed_review["qualified_items"]
    
    if stars >= 4.5 and major == 0:
        return True, "★★★★½ with no major items"
    if stars >= 4 and total > 0 and qualified / total > 0.5:
        return True, f"★{'★' * int(stars)} with {qualified}/{total} items qualified"
    if total <= 2:
        return True, f"Only {total} items found"
    
    return False, f"{major} major items, {total - qualified} unqualified"


def cmd_review(args):
    """Generate a review."""
    title = get_title()
    manuscript = build_manuscript()
    
    prompt = REVIEW_PROMPT.format(title=title, manuscript=manuscript)
    
    review_text = call_opus(prompt)
    
    # Save raw review
    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"{timestamp}_review.json"
    
    parsed = parse_review(review_text)
    parsed["timestamp"] = timestamp
    parsed["title"] = title
    parsed["word_count"] = len(manuscript.split())
    
    log_path.write_text(json.dumps(parsed, indent=2, default=str))
    print(f"\nReview saved to {log_path}", file=sys.stderr)
    
    # Save human-readable copy
    if args.output:
        Path(args.output).write_text(review_text)
        print(f"Human-readable copy: {args.output}", file=sys.stderr)
    
    # Print summary
    stop, reason = should_stop(parsed)
    print(f"\n{'='*50}")
    print(f"REVIEW SUMMARY")
    print(f"  Stars: {parsed['stars']}")
    print(f"  Items: {parsed['total_items']} ({parsed['major_items']} major)")
    print(f"  Qualified: {parsed['qualified_items']}/{parsed['total_items']}")
    print(f"  Stop revising? {'YES — ' + reason if stop else 'NO — ' + reason}")
    print(f"{'='*50}")
    
    return parsed


def cmd_parse(args):
    """Parse the most recent review into actionable items."""
    LOGS_DIR.mkdir(exist_ok=True)
    reviews = sorted(LOGS_DIR.glob("*_review.json"), reverse=True)
    if not reviews:
        print("No reviews found. Run: review.py first")
        sys.exit(1)
    
    latest = json.loads(reviews[0].read_text())
    
    print(f"Latest review: {latest.get('timestamp', 'unknown')}")
    print(f"Stars: {latest.get('stars', '?')}")
    print(f"\nACTIONABLE ITEMS ({latest['total_items']}):")
    
    for item in latest.get("professor_items", []):
        qual = " [QUALIFIED]" if item["qualified"] else ""
        print(f"\n  {item['number']}. [{item['severity'].upper()}] [{item['type']}]{qual}")
        print(f"     {item['title']}")
        if item["suggestion"]:
            print(f"     Suggestion: {item['suggestion'][:120]}...")
    
    stop, reason = should_stop(latest)
    print(f"\n{'='*50}")
    print(f"Stop revising? {'YES — ' + reason if stop else 'NO — ' + reason}")
    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description="Deep manuscript review via Opus")
    parser.add_argument("--output", "-o", default=None, help="Save human-readable review to file")
    parser.add_argument("--parse", action="store_true", help="Parse most recent review")
    
    args = parser.parse_args()
    
    if args.parse:
        cmd_parse(args)
    else:
        cmd_review(args)


if __name__ == "__main__":
    main()
