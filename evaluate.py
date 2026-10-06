#!/usr/bin/env python3
"""
evaluate.py -- Novel evaluation harness.

Usage:
  python evaluate.py --phase=foundation    # Score planning docs only
  python evaluate.py --chapter=5           # Score a single chapter
  python evaluate.py --full                # Score the entire novel

Output: structured scores to stdout + eval_logs/<timestamp>.json

This file is READ-ONLY during autonomous runs. The human edits it
to tune what "good" means. The agent treats it as a black box.
"""

import argparse
import hashlib
import json
import os
import sys
import glob
import re
from datetime import datetime
from pathlib import Path

# --- Configuration ---
BASE_DIR = Path(__file__).parent

# Load .env file if present
from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

import llm

# Judge/review models are configured per-role in .env (see llm.py).
# Use a different model than the writer to avoid self-congratulation.
CHAPTERS_DIR = BASE_DIR / "chapters"
EVAL_LOG_DIR = BASE_DIR / "eval_logs"
EVAL_LOG_DIR.mkdir(exist_ok=True)


# ---- Mechanical Slop Detection (no LLM needed) ----

TIER1_BANNED = [
    "delve", "utilize", "leverage", "facilitate", "elucidate",
    "embark", "endeavor", "encompass", "multifaceted", "tapestry",
    "paradigm", "synergy", "synergize", "holistic", "catalyze",
    "catalyst", "juxtapose", "myriad", "plethora",
]

# Japanese prose AI tells (over-polished / machine-familiar phrasing)
TIER1_JA_BANNED = [
    "多面的", "シナジー", "網羅", "深掘り", "テーゼ", "ケーススタディ",
    "肝要", "踏まえる", "俯瞰", "全体像を掴む", "幅広い視点", "多角的",
]

TIER2_SUSPICIOUS = [
    "robust", "comprehensive", "seamless", "seamlessly", "cutting-edge",
    "innovative", "streamline", "empower", "foster", "enhance", "elevate",
    "optimize", "pivotal", "intricate", "profound", "resonate",
    "underscore", "harness", "cultivate", "bolster", "galvanize",
    "cornerstone", "game-changer", "scalable",
]

# Japanese filler / essay-like connective phrasing that reads as AI-adjacent
# in prose (kept light; these penalize more when clustered than alone)
TIER2_JA_SUSPICIOUS = [
    "まず", "つまり", "しかしながら", "一方で", "例えば", "いわば",
    "というわけで", "そのため", "結果的に", "要するに", "端的に言えば",
    "言わば", "すなわち", "本質的には",
]

TIER3_FILLER = [
    r"it'?s worth noting that",
    r"it'?s important to note that",
    r"^importantly,?\s",
    r"^notably,?\s",
    r"^interestingly,?\s",
    r"let'?s dive into",
    r"let'?s explore",
    r"as we can see",
    r"^furthermore,?\s",
    r"^moreover,?\s",
    r"^additionally,?\s",
    r"in today'?s .*(fast-paced|digital|modern)",
    r"at the end of the day",
    r"it goes without saying",
    r"when it comes to",
    r"one might argue that",
    r"not just .+, but",
]

TRANSITION_OPENERS = [
    "however", "furthermore", "additionally", "moreover",
    "nevertheless", "consequently", "nonetheless", "similarly",
]

# Show-don't-tell detectors: emotion TELLING patterns
TELLING_PATTERNS = [
    r"\b(?:he|she|they|I|we|[A-Z]\w+) (?:felt|was|seemed|looked|appeared) (?:angry|sad|happy|scared|nervous|excited|jealous|guilty|anxious|lonely|desperate|furious|terrified|elated|miserable|hopeful|confused|relieved|horrified|disgusted|ashamed|proud|bitter|defeated|triumphant)\b",
    r"\b(?:angrily|sadly|happily|nervously|excitedly|desperately|furiously|anxiously|guiltily|bitterly|wearily|miserably)\b",
    # Japanese show-don't-tell: naming the emotion instead of showing it
    r"(?:彼|彼女|自分|私|僕|あなた|アイツ|老人|男|女|子供)[はが]\s*(?:怒っ|悲し|嬉し|怖が|緊張し|焦っ|興奮し|嫉妬し|罪悪感|不安|寂し|絶望|幸せ|混乱|安心|恐怖|嫌悪|恥ずかし|誇り|苦し|敗北|勝ち誇)",
    r"(?:怒り|悲しみ|恐怖|不安|緊張|焦り|嫉妬|幸福感|絶望感|疎外感|孤独感|喜び|苦しみ)が(?:\w{1,6}?)[。、\n]",
]

# Japanese fiction AI tells -- concrete, sensory clichés that recur in
# machine prose (over-used "reaction" beats and stock simile scaffolds).
FICTION_AI_TELLS_JA = [
    r"目を(?:見開|見張|開け)[いこだ]",
    r"目が(?:見開|開い)",
    r"息を(?:呑|飲み|の|呑み込み)",
    r"息が(?:詰ま|止ま|乱れ)",
    r"心臓が(?:高鳴|ドキドキ|バクバク)",
    r"心が(?:揺れ|締め付|張り裂け)",
    r"胸が(?:締め付|苦しく|切な|痛)",
    r"唇を(?:噛み|引き結び|震わせ)",
    r"拳を(?:握り締め|握りしめ)",
    r"視線を(?:そらし|逸らし|落とし)",
    r"ふと(?:思う|感じ|思い出す)",
    r"何かを(?:感じ|悟り|察し)",
    r"今までにない(?:感覚|感情)",
    r"言葉にできない(?:感覚|思い|感情)",
    r"心の奥(?:から|深く|で)",
    r"ざわめく(?:胸|心|何か)",
    r"全身に(?:走り|広が|響い)",
    r"背筋が(?:凍り|寒け|震え)",
    r"鳥肌が(?:立ち|立っ)",
    r"時が止まった(?:よう|みたい)",
    r"(?:氷|冷たい)ような(?:空気|視線|沈黙)",
    r"冷たい(?:風が|空気が|視線が)",
    r"何かが(?:間違い|おかしい|異変)",
    r"(?:目に見える|目に見えぬ)ほど",
    r"淡い(?:光|月明かり|色)",
    r"微かな(?:声|音|震え|香り)",
    r"静かな(?:夜|空間|時間|安らぎ)",
    r"不思議な(?:力|感覚|安心感|力)",
    r"信じられない(?:思い|思い込み)",
    r"鼓動が(?:早まり|速くなり|高鳴り)",
    r"涙が(?:溢れ|こぼれ|滲み)",
    r"(?:心|魂|記憶)が(?:揺さぶら|震え)",
    r"沈黙が(?:広が|流れ|支配)",
    r"耳に(?:こもる|入り込む|届く)",
    r"身体(?:が|の)(?:震え|こわば|硬直)",
    r"一瞬(?:のこと|目を)(?:だった|過ぎ)",
    r"それまで(?:と|も)",
]

# Structural AI tics -- rhetorical formulas that betray AI composition
STRUCTURAL_AI_TICS = [
    r"(?:I'm|I am) not (?:saying|asking|suggesting) .{3,40}(?:I'm|I am) (?:saying|asking|suggesting)",  # "I'm not saying X. I'm saying Y"
    r"(?:which|that) means either .{3,40} or ",  # "which means either X, or Y"
    r"[Tt]here'?s a (?:difference|distinction)\.",  # formula capper
    r"[Tt]hose are (?:different|not the same) things\.",  # formula capper
    r"[Nn]ot (?:just|merely|simply) .{3,40}, but ",  # "not just X, but Y"
    r"[Nn]ot (?:from|by|because of) .{3,40}, but (?:from|by|because)",  # "not from X, but from Y" in narration
    # Japanese rhetorical formulas
    r"(?:ではない|じゃない)[、。](?:むしろ|そうではなく)",
    r"(?:つまり|いわば|すなわち)、",
    r"(?:もちろん|勿論)、[^。]{3,40}[、。](?:だが|しかし)、",
    r"(?:それは|その)(?:違い|違いは|区別)",
    r"(?:「[^」]{1,12}」{0,1})と(?:は|いう).{0,10}(?:、?それは)",
]


def slop_score(text):
    """
    Mechanical slop detection. Returns a dict with:
      - tier1_hits: list of (word, count)
      - tier2_hits: list of (word, count)
      - tier3_hits: list of (pattern, count)
      - em_dash_density: em dashes per 1000 words
      - sentence_length_cv: coefficient of variation (higher = more human)
      - transition_opener_ratio: fraction of paragraphs starting with transitions
      - slop_penalty: 0-10 deduction (0 = clean, 10 = pure slop)
    """
    words = text.lower().split()
    word_count = len(words) or 1

    # Tier 1
    tier1_hits = []
    for w in TIER1_BANNED:
        c = sum(1 for token in words if token.strip(".,;:!?\"'()") == w)
        if c > 0:
            tier1_hits.append((w, c))
    # Tier 1 (Japanese -- substring match, no word boundaries in JP text)
    for w in TIER1_JA_BANNED:
        c = text.count(w)
        if c > 0:
            tier1_hits.append((w, c))

    # Tier 2 -- count per paragraph, flag clusters
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    tier2_hits = []
    tier2_cluster_count = 0
    for w in TIER2_SUSPICIOUS:
        c = sum(1 for token in words if token.strip(".,;:!?\"'()") == w)
        if c > 0:
            tier2_hits.append((w, c))
    for w in TIER2_JA_SUSPICIOUS:
        c = text.count(w)
        if c > 0:
            tier2_hits.append((w, c))
    for para in paragraphs:
        para_lower = para.lower()
        hits_in_para = sum(1 for w in TIER2_SUSPICIOUS if w in para_lower)
        hits_in_para += sum(1 for w in TIER2_JA_SUSPICIOUS if w in para)
        if hits_in_para >= 3:
            tier2_cluster_count += 1

    # Tier 3
    tier3_hits = []
    for pattern in TIER3_FILLER:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        if matches:
            tier3_hits.append((pattern, len(matches)))

    # Em dash density
    em_dashes = text.count("—") + text.count("--")
    em_dash_density = (em_dashes / word_count) * 1000

    # Sentence length variation (coefficient of variation)
    # Split on Western and Japanese sentence-ending punctuation.
    sentences = re.split(r'[.!?。！？…]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 4]
    if len(sentences) > 2:
        # Use character length (works for both English and Japanese, which
        # has no whitespace word boundaries).
        lengths = [len(s) for s in sentences]
        mean_len = sum(lengths) / len(lengths)
        variance = sum((l - mean_len) ** 2 for l in lengths) / len(lengths)
        std_len = variance ** 0.5
        sentence_length_cv = std_len / mean_len if mean_len > 0 else 0
    else:
        sentence_length_cv = 0.5  # not enough data, assume OK

    # Transition opener ratio
    transition_starts = 0
    for para in paragraphs:
        first_word = para.split()[0].lower().strip(".,;:!?\"'()") if para.split() else ""
        if first_word in TRANSITION_OPENERS:
            transition_starts += 1
    transition_ratio = transition_starts / len(paragraphs) if paragraphs else 0

    # Japanese monotone short-fragment chains.
    # An AI tell: many paragraphs where text is broken into MANY short
    # fragments by 読点 (、) and 句点 (。), producing a mechanical staccato
    # rhythm with no contrast. This differs from DELIBERATE short-sentence
    # emphasis (e.g. at an emotional peak per the 過去作 style guide),
    # which is localized to 1-2 paragraphs.
    # We detect: >= MONOTONE_THRESHOLD consecutive prose paragraphs that
    # each consist of >= MONOTONE_MIN_FRAG fragments where the MAJORITY
    # of fragments are short (<= MONOTONE_MAX_FRAG_LEN chars).
    MONOTONE_THRESHOLD = 3          # consecutive paragraphs to count as a chain
    MONOTONE_MAX_FRAG_LEN = 12      # a "short" fragment is <= 12 chars
    MONOTONE_MIN_FRAG = 5           # a paragraph needs >= 5 fragments to check
    MONOTONE_SHORT_RATIO = 0.85     # >=85% of fragments must be short

    def _is_monotone_para(para: str) -> bool:
        frags = [f.strip() for f in re.split(r'[。！？、]', para) if len(f.strip()) > 1]
        if len(frags) < MONOTONE_MIN_FRAG:
            return False
        short = sum(1 for f in frags if len(f) <= MONOTONE_MAX_FRAG_LEN)
        return (short / len(frags)) >= MONOTONE_SHORT_RATIO

    monotone_chain_len = 0
    monotone_chain_count = 0
    monotone_chains_hits = []       # store chain lengths
    for para in paragraphs:
        # Skip headings / list lines / fences / empty
        stripped = para.strip()
        if (not stripped or stripped.startswith(('#', '-', '*', '|', '```'))
                or re.match(r'^\d+[\.\)]', stripped)):
            monotone_chain_len = 0
            continue
        if _is_monotone_para(para):
            monotone_chain_len += 1
        else:
            if monotone_chain_len >= MONOTONE_THRESHOLD:
                monotone_chain_count += 1
                monotone_chains_hits.append(monotone_chain_len)
            monotone_chain_len = 0
    if monotone_chain_len >= MONOTONE_THRESHOLD:
        monotone_chain_count += 1
        monotone_chains_hits.append(monotone_chain_len)
    monotone_chain_total_paras = sum(monotone_chains_hits)

    # Fiction AI tells (English + Japanese)
    fiction_tells = []
    for pattern in FICTION_AI_TELLS_JA:
        matches = re.findall(pattern, text)
        if matches:
            fiction_tells.append((pattern[:40], len(matches)))
    fiction_tell_count = sum(c for _, c in fiction_tells)

    # Show-don't-tell violations
    telling_count = 0
    for pattern in TELLING_PATTERNS:
        telling_count += len(re.findall(pattern, text, re.IGNORECASE))

    # Structural AI tics (rhetorical formulas)
    structural_tics = []
    for pattern in STRUCTURAL_AI_TICS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            structural_tics.append((pattern[:40], len(matches)))
    structural_tic_count = sum(c for _, c in structural_tics)

    # Composite penalty (0 = clean, 10 = disaster)
    penalty = 0.0
    penalty += min(len(tier1_hits) * 1.5, 4.0)       # tier1: up to 4 pts
    penalty += min(tier2_cluster_count * 1.0, 2.0)    # tier2 clusters: up to 2 pts
    penalty += min(sum(c for _, c in tier3_hits) * 0.3, 2.0)  # tier3: up to 2 pts
    if em_dash_density > 15:
        penalty += min((em_dash_density - 15) * 0.3, 1.0)  # em dashes: up to 1 pt (threshold raised for voice)
    if sentence_length_cv < 0.3:
        penalty += 1.0  # uniform sentence length: 1 pt
    if transition_ratio > 0.3:
        penalty += min(transition_ratio * 2, 1.0)  # transition abuse: up to 1 pt
    # Monotone short-sentence chains: mechanical staccato rhythm.
    # Penalize chain COUNT and total chained paragraphs (up to 2.5 pts).
    if monotone_chain_count > 0:
        chain_penalty = min(monotone_chain_count * 1.0, 1.5) \
                        + min(monotone_chain_total_paras * 0.1, 1.0)
        penalty += chain_penalty
    penalty += min(fiction_tell_count * 0.3, 2.0)     # fiction AI tells: up to 2 pts
    penalty += min(telling_count * 0.2, 1.5)          # show-don't-tell: up to 1.5 pts
    penalty += min(structural_tic_count * 0.5, 2.0)   # structural AI tics: up to 2 pts

    penalty = min(penalty, 10.0)

    return {
        "tier1_hits": tier1_hits,
        "tier2_hits": tier2_hits,
        "tier2_clusters": tier2_cluster_count,
        "tier3_hits": tier3_hits,
        "fiction_ai_tells": fiction_tells,
        "structural_ai_tics": structural_tics,
        "telling_violations": telling_count,
        "monotone_chain_count": monotone_chain_count,
        "monotone_chained_paragraphs": monotone_chain_total_paras,
        "em_dash_density": round(em_dash_density, 2),
        "sentence_length_cv": round(sentence_length_cv, 3),
        "transition_opener_ratio": round(transition_ratio, 3),
        "slop_penalty": round(penalty, 2),
    }


def load_file(path):
    """Load a text file, return empty string if missing."""
    try:
        return Path(path).read_text()
    except FileNotFoundError:
        return ""


def load_layer_files(clip_chars: int | None = None):
    """Load all planning layer files.

    clip_chars: 各文書の上限文字数。None なら env の AUTONOVEL_JUDGE_DOC_CHARS。
    **設定（foundation）の判定は全文**、章の判定は切り詰める（章の判定は数が多く、
    費用が効くため。2026-10-05 実測: 判定1回 43,964トークン）。
    """
    limit = JUDGE_DOC_CHARS if clip_chars is None else clip_chars

    def clip(text: str) -> str:
        if limit and len(text) > limit:
            return text[:limit] + "\n\n【長いため以下略】\n"
        return text

    return {
        "voice": clip(load_file(BASE_DIR / "voice.md")),
        "world": clip(load_file(BASE_DIR / "world.md")),
        "characters": clip(load_file(BASE_DIR / "characters.md")),
        "outline": clip(load_file(BASE_DIR / "outline.md")),
        "canon": clip(load_file(BASE_DIR / "canon.md")),
    }


def load_chapter(n):
    """Load a single chapter file."""
    return load_file(CHAPTERS_DIR / f"ch_{n:02d}.md")


def load_all_chapters():
    """Load all chapter files in order."""
    chapters = {}
    for f in sorted(glob.glob(str(CHAPTERS_DIR / "ch_*.md"))):
        num = int(re.search(r'ch_(\d+)', f).group(1))
        chapters[num] = Path(f).read_text()
    return chapters


def call_judge(prompt, max_tokens=2000):
    return llm.call_judge(
        prompt,
        system='You are a literary critic and novel editor. You evaluate fiction with precision. Always respond with valid JSON. No markdown fences, no preamble -- just the JSON object.',
        max_tokens=max_tokens,
        temperature=0.3,
    )



def parse_json_response(text):
    """Extract JSON from a response that might have markdown fences or trailing text."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    # Find the outermost JSON object
    start = text.find('{')
    if start == -1:
        raise ValueError("No JSON object found in response")
    # Walk forward to find the matching closing brace
    depth = 0
    in_string = False
    escape = False
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
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i+1], strict=False)
    # Fallback: try loading as-is, with strict=False to handle control chars
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass
    # 途中で切れた（＝出力量の上限に張り付いた）応答を救う。
    # 実測（2026-10-05）: 設定評価が 16,000 トークン上限で切れて JSON が閉じず、
    # 解釈失敗 → スコア -1.0 扱い → 19周・約100万トークンを空回りした。
    salv = _salvage_truncated(text[start:])
    if isinstance(salv, dict) and any(
            k in salv for k in ("overall_score", "lore_score", "novel_score")):
        salv["_truncated_response_salvaged"] = True
        return salv
    scal = _extract_scalars(text[start:])
    if scal:
        scal["_truncated_response_salvaged"] = True
        return scal
    # Last resort: fix common issues (literal newlines in strings)
    fixed = re.sub(r'(?<!\\)\n', '\\n', text)
    try:
        return json.loads(fixed, strict=False)
    except json.JSONDecodeError:
        dump_parse_failure(text)
        raise


# --- 判定（judge）の応答が読めないときの保険 -------------------------------

JSON_COMPACT_RULES = (
    "\n\n=== OUTPUT RULES (must follow) ===\n"
    "1. 出力は **JSON オブジェクト1つだけ**。前後に説明・あいさつ・コードフェンスを書かない。\n"
    "2. `overall_score` と `lore_score`（全体評価なら `novel_score`）は **JSON の先頭**に書く。\n"
    "3. 各文字列は短く（おおむね40文字以内）。長文の講評・同じ内容の繰り返しは書かない。\n"
    "4. 各項目は **1文だけ**（60文字以内）。引用は短く切り、引用の説明・前置き・総評・"
    "「〜について述べます」のような導入を書かない。\n"
    "5. 出力全体で **4,000トークンを超えないこと**。長くなるほど役に立たない："
    "採点と、直すべき1点だけを書く。\n"
)

# 判定の出力上限。**実測（2026-10-06）: kimi-k3 の自然な出力量は約12,000トークン**。
# 6,000 にすると必ず途中で切れ、上限を上げて2回呼ぶことになる（＝1回の評価に2回分の
# 料金。実測 76,000トークン）。自然な大きさを確保して**1回で終わらせる**のが安い。
JUDGE_MAX_TOKENS = int(os.environ.get("AUTONOVEL_JUDGE_MAX_TOKENS", "16000"))
JUDGE_MAX_TOKENS_ESCALATED = int(
    os.environ.get("AUTONOVEL_JUDGE_MAX_TOKENS_ESCALATED", "32000"))
# 同じ入力の評価は再利用する（判定モデルを呼ばない）。実測: 1回 約44,000 トークン。
JUDGE_CACHE = os.environ.get("AUTONOVEL_JUDGE_CACHE", "1") != "0"
# 判定に送る各文書の上限文字数（0＝無制限）。章の判定は数が多いので既定で切る。
# 設定(foundation)の判定は全文（evaluate_foundation が clip_chars=0 を渡す）。
JUDGE_DOC_CHARS = int(os.environ.get("AUTONOVEL_JUDGE_DOC_CHARS", "6000"))


def _extract_scalars(text: str) -> dict:
    """壊れた／切れた JSON から、最低限の点数だけ拾う（最後の保険）。"""
    out = {}
    for key in ("overall_score", "lore_score", "novel_score"):
        m = re.search(rf'"{key}"\s*:\s*(-?\d+(?:\.\d+)?)', text)
        if m:
            out[key] = float(m.group(1))
    return out


def _salvage_truncated(body: str):
    """閉じ括弧が来ない（＝途中で切れた）JSON を、切れた所で閉じて読む。"""
    if not body.startswith("{"):
        return None
    cut = body.rfind(",")
    for cand in (body, body[:cut] if cut > 0 else body):
        fixed = cand.rstrip().rstrip(",")
        if fixed.count('"') % 2 == 1:          # 閉じていない文字列を閉じる
            fixed += '"'
        fixed += "}" * (fixed.count("{") - fixed.count("}"))   # 開いた括弧を閉じる
        try:
            return json.loads(fixed, strict=False)
        except json.JSONDecodeError:
            continue
    return None


def dump_parse_failure(text: str, label: str = "parse_fail") -> Path:
    """解釈できなかった生の応答を残す（次に調べるときに1秒で分かるように）。"""
    p = EVAL_LOG_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{label}.txt"
    p.write_text(text, encoding="utf-8")
    sys.stderr.write(f"[evaluate] 生の応答を保存しました: {p}\n")
    return p


def _cache_path(key: str) -> Path:
    d = EVAL_LOG_DIR / "cache"
    d.mkdir(exist_ok=True)
    return d / f"{key}.json"


def call_judge_json(prompt: str, max_tokens: int | None = None) -> dict:
    """judge を呼んで JSON を返す。同じ入力なら前の結果を再利用する（費用対策）."""
    prompt = prompt + JSON_COMPACT_RULES
    key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    cached = _cache_path(key)
    if JUDGE_CACHE and cached.exists():
        sys.stderr.write("[evaluate] 同じ入力の評価が既にあるので再利用します"
                         "（判定モデルを呼びません）\n")
        return json.loads(cached.read_text(encoding="utf-8"))
    result = _call_judge_json_uncached(prompt, max_tokens)
    if JUDGE_CACHE:
        cached.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _call_judge_json_uncached(prompt: str, max_tokens: int | None = None) -> dict:
    mt = max_tokens or JUDGE_MAX_TOKENS
    raw = call_judge(prompt, max_tokens=mt)
    try:
        return parse_json_response(raw)
    except Exception:
        if JUDGE_MAX_TOKENS_ESCALATED > mt:
            sys.stderr.write(
                f"[evaluate] 応答が解釈できません（{mt} トークン上限）→ "
                f"{JUDGE_MAX_TOKENS_ESCALATED} でやり直します\n")
            raw = call_judge(prompt, max_tokens=JUDGE_MAX_TOKENS_ESCALATED)
            return parse_json_response(raw)
        raise


# --- Foundation Evaluation ---

FOUNDATION_PROMPT = """Evaluate these fantasy novel planning documents.

SCORING CALIBRATION (read this before scoring anything):

  9-10: Could not improve this with a month of focused editorial work.
        Published-novel quality. You can name the specific published
        novel it competes with. Reserve 10 for work that SURPRISES you.
  7-8:  Strong. A skilled author could draft from this document with
        minimal invention. Gaps exist but are minor and enumerable.
  5-6:  Functional but thin. A writer would need to invent significant
        material on the fly. Major gaps or generic choices.
  3-4:  Sketchy. More questions than answers. Would require heavy
        supplementation before drafting.
  1-2:  Placeholder or stub. Not usable for drafting.
  0:    Empty or missing.

  A score of 8+ requires ZERO major gaps. A score of 9+ requires
  that you genuinely struggled to find flaws. Err toward lower scores.

MANDATORY: For EVERY dimension, before scoring, you must identify:
  (a) The single biggest GAP or WEAKNESS in that area
  (b) A specific, actionable improvement that would raise the score
  If you cannot find a gap, explain why you believe one doesn't exist.

VOICE DEFINITION:
{voice}

WORLD BIBLE:
{world}

CHARACTER REGISTRY:
{characters}

OUTLINE:
{outline}

CANON (established facts):
{canon}

CROSS-CHECKS (perform these before scoring):
1. Check all example dialogue lines against ANTI-SLOP patterns:
   - Look for structural formulas repeated across characters
     ("not X, but Y" / "either X, or Y" / "there's a difference")
   - Check for AI rhetorical tics disguised as character voice
   - Deduct from character_distinctiveness if multiple characters
     share the same sentence structures
2. Check for missing NEGATIVE SPACE -- what's absent?
   - Are there gaps in the magic system that would block a specific
     plot scene? (e.g., can Cass hear lies in written documents?
     What happens during the climax -- what rule resolves it?)
   - Are there characters needed for the plot who don't exist?
   - Are there scenes the outline demands that the world can't support?
3. Check for CONVENIENT GAPS vs DELIBERATE MYSTERY:
   - Convenient: "the details are unclear" where specifics are needed
   - Deliberate: withholding information from the READER while the
     AUTHOR knows the answer. If the planning docs dodge a question
     that a writer would need answered to draft a scene, that's a gap,
     not an iceberg.
4. Check the canon for INTERNAL CONTRADICTIONS:
   - Cross-reference dates, ages, and timelines
   - Check if character abilities match magic system rules
   - Look for factual conflicts between documents

Score these dimensions (gap + improvement required for each):

LORE & WORLDBUILDING:
- magic_system: Hard rules with COSTS and LIMITATIONS per Sanderson's
  Second Law. Could a writer resolve the CLIMACTIC CONFLICT using only
  rules already established? Are costs plot-driving, not decorative?
  Are there at least 3 societal implications explored with specificity?
  Is the system TESTABLE -- could you write a courtroom scene, a
  contract negotiation, and a magical confrontation without inventing
  new rules?
- world_history: Timeline of events creating PRESENT-DAY tensions.
  Each historical event should map to a current faction conflict or
  character motivation. Decorative history (cool but plot-irrelevant)
  counts against the score, not for it.
- geography_and_culture: Locations distinct with sensory signatures.
  Cultures with specific customs that GENERATE CONFLICT. Economy that
  creates class tension. Check: could two different scenes set in two
  different locations feel meaningfully different based on what's here?
- lore_interconnection: Does changing one element force changes in
  at least two others? Test by mentally removing the magic system --
  does the political structure collapse? Does the class system change?
  If elements are modular/detachable, score low.
- iceberg_depth: Implied depth vs stated depth. But CHECK: does the
  author actually know the answers to the mysteries, or are they
  handwaving? If a planning doc says "the answer will be revealed"
  without specifying WHAT the answer is, that's a gap wearing an
  iceberg costume.

CHARACTER:
- character_depth: Wound/want/need/lie chains that are CAUSALLY LINKED
  (not just thematically associated). The lie must logically follow
  from the wound. The want must be the wrong solution to the lie.
  The need must directly oppose the want. Check each chain for
  logical gaps. Also check: are ANY characters missing wound/want/need
  chains who probably need them?
- character_distinctiveness: Remove all dialogue tags from the example
  lines. Can you identify the speaker from sentence structure alone?
  Check for REPEATED STRUCTURAL FORMULAS across characters (e.g.,
  multiple characters using "X. Not Y." or balanced antithesis).
  Check that metaphor domains don't overlap. Check that speech
  patterns reflect character background (a 14-year-old should not
  sound like a 60-year-old merchant).
- character_secrets: Each major character's secret should be something
  that, if revealed, changes the plot's trajectory. Vague secrets
  ("he knows more than he says") score lower than specific ones
  ("he knows the harmonic means X, which would invalidate Y").

STRUCTURE:
- outline_completeness: Chapters with beats, POV, emotional arc,
  try-fail cycle type. Save the Cat beats at correct % marks.
  Score 0 if empty. Score 5+ only if act structure exists.
- foreshadowing_balance: Every planted thread has a planned payoff.
  Score 0 if ledger is empty regardless of implicit threads in
  other documents -- foreshadowing must be TRACKED to count.

CRAFT:
- internal_consistency: Actively hunt for contradictions. Cross-ref
  dates, ages, character counts, named locations. Flag any case
  where documents disagree. A single major contradiction caps this
  at 6. Three or more caps at 4.
- voice_clarity: Voice definition must be specific and ACTIONABLE.
  Exemplar passages must demonstrate the voice. Anti-exemplars must
  define boundaries. Check exemplar dialogue for AI slop patterns.
  A voice doc that is beautiful but contains slop in its own examples
  is undermined -- deduct.
- canon_coverage: Facts logged, sourced, and sufficient to catch
  contradictions. Check: if a writer introduced a NEW fact in
  chapter 5, could they verify it against the canon? Is the canon
  granular enough? Are there known facts from other docs that
  AREN'T in the canon?

Respond with JSON:
{{
  "magic_system": {{"score": N, "gap": "biggest weakness", "fix": "specific improvement", "note": "..."}},
  "world_history": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},
  "geography_and_culture": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},
  "lore_interconnection": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},
  "iceberg_depth": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},
  "character_depth": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},
  "character_distinctiveness": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},
  "character_secrets": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},
  "outline_completeness": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},
  "foreshadowing_balance": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},
  "internal_consistency": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},
  "voice_clarity": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},
  "canon_coverage": {{"score": N, "gap": "...", "fix": "...", "note": "..."}},
  "slop_in_planning_docs": {{"found": ["list any AI slop patterns found in exemplar dialogue, voice examples, or character descriptions"], "note": "..."}},
  "contradictions_found": ["list any factual contradictions between documents"],
  "overall_score": N,
  "lore_score": N,
  "weakest_dimension": "...",
  "top_3_improvements": ["ranked list of the 3 highest-leverage improvements"]
}}

WEIGHTING: lore/worldbuilding 40%, character 30%, structure 20%, craft 10%.
A novel with thin worldbuilding but a complete outline is WORSE than deep
worldbuilding with an incomplete outline.

FINAL CHECK: If your overall_score is above 7, re-read your gap lists.
If any gap describes a problem that would force a writer to stop and
invent something during drafting, your score is too high. Revise down.
"""


def evaluate_foundation():
    layers = load_layer_files(clip_chars=0)      # 設定の判定は全文で見る（数が少ないので費用は小さい）
    prompt = FOUNDATION_PROMPT.format(**layers)
    return call_judge_json(prompt)


# --- Chapter Evaluation ---

CHAPTER_PROMPT = """Evaluate this fantasy novel chapter against the planning docs.

SCORING CALIBRATION:
  9-10: Among the best chapters you've read in published fantasy. Name
        a specific published chapter it competes with, or don't give 9+.
  7-8:  Strong, publishable with editorial polish. Specific flaws exist
        but don't break the reading experience.
  5-6:  Functional but flat. A competent draft that needs substantial revision.
        Generic where it should be specific. Safe where it should risk.
  3-4:  Significant problems. Voice breaks, beats missed, prose generic.
  1-2:  Not usable. Rewrite from scratch.

  The MEDIAN score for a competent AI-generated chapter should be 6.
  A 7 means it does something a generic AI draft wouldn't.
  An 8 means a human editor would keep it with minor notes.
  Most dimensions should score 6-7. Reserve 8+ for genuine excellence.

MANDATORY: For each dimension, you must identify:
  (a) The single WEAKEST MOMENT -- quote the specific sentence or passage
  (b) What would make it better -- a concrete revision, not a vague note
  If every sentence is perfect, you're not reading carefully enough.

VOICE DEFINITION:
{voice}

WORLD BIBLE (summary):
{world}

CHARACTER REGISTRY:
{characters}

CANON (established hard facts -- violations are bugs):
{canon}

CHAPTER OUTLINE ENTRY:
{chapter_outline}

PREVIOUS CHAPTER (last 1500 words):
{prev_chapter_tail}

THE CHAPTER TO EVALUATE:
{chapter_text}

CROSS-CHECKS (perform before scoring):
1. QUOTE TEST: Find the 3 best sentences and 3 weakest sentences.
   If you can't find 3 weak ones, lower your standards -- every
   chapter has weak moments. Look for: generic phrasing where
   specificity was possible, rhythmic monotony in any paragraph,
   metaphors that don't come from the character's experience,
   emotional moments that tell instead of show, transitions that
   summarize instead of dramatize.
2. DIALOGUE REALISM: Read all dialogue aloud (mentally). Does it
   sound like speech or like written prose? Do characters say things
   a 14-year-old / 60-year-old / etc. would actually say?
3. SCENE VS SUMMARY: How much of the chapter is in-scene (moment
   by moment, with dialogue and action) vs summary (narrator
   compressing time)? Chapters heavy on summary score lower on
   engagement regardless of prose quality.
4. AI PATTERN CHECK: Look for these common AI writing patterns:
   - Every paragraph the same length
   - Observations always in threes (X, Y, and Z)
   - Emotional beats that arrive on schedule rather than surprising
   - Characters who never say the wrong thing or talk past each other
   - Description that catalogs instead of selecting (listing 5 sensory
     details when 2 specific ones would be sharper)
   - Internal monologue explaining what the scene already showed
5. EARNED VS GIVEN: Is tension earned through scene work or handed to
   the reader through the narrator's assertions? Is mystery maintained
   through genuine withholding or through the character conveniently
   not thinking about things they'd think about?

Score these dimensions:

- voice_adherence: Does the prose match voice.md Part 2? Check: sentence
  rhythm variation, vocabulary wells, body-before-emotion principle,
  the specific tone described. Quote the strongest voice moment AND
  the weakest. Does ANY passage sound like generic fantasy prose that
  could appear in any novel? If yes, score 7 max.

- beat_coverage: Did it hit every beat from the outline? Were beats
  dramatized or merely mentioned? A beat that's summarized in a sentence
  instead of lived in a scene counts as half-hit. Score reflects
  QUALITY of beat execution, not just presence.

- character_voice: Remove all dialogue tags mentally. Can you tell who's
  speaking? Do characters ever sound alike? Does dialogue read as speech
  or as written prose? Does Cass sound like a specific 14-year-old, or
  like "young protagonist"? Does anyone say something surprising -- not
  just the right thing, but a REAL thing? Characters who never stumble,
  hesitate, or say something slightly wrong are AI-pattern characters.

- plants_seeded: Were foreshadowing elements placed naturally? A plant
  that's obvious is worse than a plant that's invisible. Score based on
  HOW WELL they're integrated, not just whether they're present.

- prose_quality: Sentence variety (measure: do 3+ consecutive sentences
  start the same way?). Specificity (concrete nouns > abstract).
  Metaphors from Cass's experience, not from a thesaurus. Show-don't-tell
  at emotional peaks. QUOTE the weakest sentence and explain why. Also
  check for: repeated phrases, leaned-on constructions, paragraphs that
  could be cut without loss.

- continuity: Does it follow logically from the previous chapter? Emotional
  continuity as well as plot continuity. Does the character's state of
  mind track?

- canon_compliance: Check ALL facts against canon. List violations.
  One major violation caps score at 6. Check: character names, locations,
  magic system rules, timeline, established events, physical descriptions.

- lore_integration: Does the world do WORK in this chapter, or is it
  set dressing? A scene that could happen in any fantasy city with
  find-and-replace on proper nouns scores 5 max.

- engagement: Would a reader turn the page? Where does tension come from --
  plot, character, mystery, prose? Is there a moment that SURPRISES?
  Predictable excellence is still predictable. Score 8+ only if the
  chapter does something unexpected.

Respond with JSON:
{{
  "voice_adherence": {{"score": N, "weakest_moment": "quote the specific weak passage", "fix": "how to improve it", "note": "..."}},
  "beat_coverage": {{"score": N, "weakest_moment": "...", "fix": "...", "note": "..."}},
  "character_voice": {{"score": N, "weakest_moment": "...", "fix": "...", "note": "..."}},
  "plants_seeded": {{"score": N, "weakest_moment": "...", "fix": "...", "note": "..."}},
  "prose_quality": {{"score": N, "weakest_sentence": "quote it", "fix": "rewrite suggestion", "strongest_sentence": "quote it", "note": "..."}},
  "continuity": {{"score": N, "note": "..."}},
  "canon_compliance": {{"score": N, "violations": ["list any found"], "note": "..."}},
  "lore_integration": {{"score": N, "weakest_moment": "...", "fix": "...", "note": "..."}},
  "engagement": {{"score": N, "weakest_moment": "...", "fix": "...", "note": "..."}},
  "three_weakest_sentences": ["quote 1", "quote 2", "quote 3"],
  "three_strongest_sentences": ["quote 1", "quote 2", "quote 3"],
  "ai_patterns_detected": ["list any AI writing patterns found"],
  "overall_score": N,
  "weakest_dimension": "...",
  "top_3_revisions": ["specific, actionable revision 1", "revision 2", "revision 3"],
  "new_canon_entries": ["any new facts established in this chapter"]
}}

FINAL CHECK: If your overall_score is above 7, re-read your weakest_moment
quotes. If any of them describe a problem that an editor would flag, your
score is too high. The median AI chapter is a 6. An 8 is exceptional. A 9
is rare. A 10 does not exist for a first draft.
"""


def evaluate_chapter(chapter_num):
    layers = load_layer_files()
    chapter_text = load_chapter(chapter_num)
    if not chapter_text.strip():
        return {"error": f"Chapter {chapter_num} is empty or missing",
                "overall_score": 0.0}

    # Extract this chapter's outline entry (rough heuristic)
    outline = layers["outline"]
    ch_pattern = rf'###\s*Ch\s*{chapter_num}\b.*?(?=###\s*Ch\s*\d|## Act|## Foreshadowing|$)'
    ch_match = re.search(ch_pattern, outline, re.DOTALL)
    chapter_outline = ch_match.group(0) if ch_match else "(outline entry not found)"

    # Load previous chapter tail
    prev_text = load_chapter(chapter_num - 1) if chapter_num > 1 else "(first chapter)"
    prev_tail = prev_text[-1200:] if len(prev_text) > 1200 else prev_text

    prompt = CHAPTER_PROMPT.format(
        voice=layers["voice"],
        # 費用対策（2026-10-06 実測）: 章判定の入力は約21,000トークンあり、その大半が設定資料。
        # 文学的な判定に効くのは「声・登場人物・この章のアウトライン」で、世界観とカノンの
        # 違反検出は整合チェック（verify_consistency.py）が別に担当している（二重に払っていた）。
        world=layers["world"][:1500],
        characters=layers["characters"][:2800],
        canon=layers["canon"][:2200],
        chapter_outline=chapter_outline,
        prev_chapter_tail=prev_tail,
        chapter_text=chapter_text,
    )
    result = call_judge_json(prompt)

    # Mechanical slop check -- adjusts score independently of judge
    slop = slop_score(chapter_text)
    result["slop"] = slop
    if "overall_score" in result:
        adjusted = max(0, result["overall_score"] - slop["slop_penalty"])
        result["raw_judge_score"] = result["overall_score"]
        result["overall_score"] = round(adjusted, 2)

    return result


# --- Full Novel Evaluation ---

FULL_NOVEL_PROMPT = """Evaluate this complete fantasy novel holistically.
You have the planning docs and ALL chapter summaries with their individual scores.

VOICE DEFINITION:
{voice}

WORLD BIBLE:
{world_summary}

CHARACTER REGISTRY:
{characters}

OUTLINE + FORESHADOWING LEDGER:
{outline}

CHAPTER SUMMARIES AND SCORES:
{chapter_summaries}

Score these novel-level dimensions 0-10:
- arc_completion: Do character arcs resolve satisfyingly?
- pacing_curve: Does tension build properly across the book?
- theme_coherence: Are themes explored consistently?
- foreshadowing_resolution: Are all planted threads harvested?
- world_consistency: Any lore contradictions across chapters?
- voice_consistency: Is the voice steady throughout?
- overall_engagement: Is this a compelling read start to finish?

Respond with JSON:
{{
  "arc_completion": {{"score": N, "note": "..."}},
  "pacing_curve": {{"score": N, "note": "..."}},
  "theme_coherence": {{"score": N, "note": "..."}},
  "foreshadowing_resolution": {{"score": N, "note": "..."}},
  "world_consistency": {{"score": N, "note": "..."}},
  "voice_consistency": {{"score": N, "note": "..."}},
  "overall_engagement": {{"score": N, "note": "..."}},
  "novel_score": N,
  "weakest_dimension": "...",
  "weakest_chapter": N,
  "top_suggestion": "..."
}}
"""


def evaluate_full():
    layers = load_layer_files()
    chapters = load_all_chapters()

    if not chapters:
        return {"error": "No chapters found", "novel_score": 0.0}

    # Build chapter summaries (first/last 500 chars of each)
    summaries = []
    for num in sorted(chapters.keys()):
        text = chapters[num]
        word_count = len(text.split())
        head = text[:500]
        tail = text[-500:] if len(text) > 500 else ""
        summaries.append(
            f"Chapter {num} ({word_count} words):\n"
            f"  Opening: {head}...\n"
            f"  Closing: ...{tail}\n"
        )

    prompt = FULL_NOVEL_PROMPT.format(
        voice=layers["voice"],
        world_summary=layers["world"][:3000],
        characters=layers["characters"],
        outline=layers["outline"],
        chapter_summaries="\n".join(summaries),
    )
    return call_judge_json(prompt)


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Evaluate the novel")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--phase", choices=["foundation"],
                       help="Evaluate planning documents")
    group.add_argument("--chapter", type=int,
                       help="Evaluate a specific chapter number")
    group.add_argument("--full", action="store_true",
                       help="Evaluate the entire novel")
    args = parser.parse_args()

    if args.phase == "foundation":
        result = evaluate_foundation()
        score_key = "overall_score"
    elif args.chapter is not None:
        result = evaluate_chapter(args.chapter)
        score_key = "overall_score"
    elif args.full:
        result = evaluate_full()
        score_key = "novel_score"

    # Print structured output
    print("---")
    if score_key in result:
        print(f"{score_key}: {result[score_key]}")
    for key, val in result.items():
        if key == score_key:
            continue
        if isinstance(val, dict):
            print(f"{key}: {val.get('score', 'N/A')} -- {val.get('note', '')}")
        else:
            print(f"{key}: {val}")

    # Save full eval log
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = args.phase or (f"ch{args.chapter:02d}" if args.chapter else "full")
    log_path = EVAL_LOG_DIR / f"{timestamp}_{mode}.json"
    with open(log_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\neval_log: {log_path}")


if __name__ == "__main__":
    main()
