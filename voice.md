# Voice Profile

This file has two parts:
1. **Guardrails** -- universal rules to avoid AI-generated slop. These
   apply to ALL voices and are non-negotiable.
2. **Voice Identity** -- the specific voice for THIS novel. Generated
   during the foundation phase. Could be anything: dense and mythic,
   spare and brutal, warm and whimsical. The voice emerges from the
   story's needs.

---

## Part 1: Guardrails (permanent, all novels)

These are the cliff edges. Stay away from them regardless of voice.

### Tier 1: Banned words -- kill on sight

These are statistically overrepresented in LLM output vs. human writing.
If one appears, rewrite the sentence. No exceptions.

| Kill this         | Use instead                                    |
|-------------------|------------------------------------------------|
| delve             | dig into, examine, look at                     |
| utilize           | use                                            |
| leverage (verb)   | use, take advantage of                         |
| facilitate        | help, enable, make possible                    |
| elucidate         | explain, clarify                               |
| embark            | start, begin                                   |
| endeavor          | effort, try                                    |
| encompass         | include, cover                                 |
| multifaceted      | complex, varied                                |
| tapestry          | (describe the actual thing)                    |
| testament to      | shows, proves, demonstrates                    |
| paradigm          | model, approach, framework                     |
| synergy           | (delete the sentence and start over)           |
| holistic          | whole, complete, full-picture                  |
| catalyze          | trigger, cause, spark                          |
| juxtapose         | compare, contrast, set against                 |
| nuanced (filler)  | (cut it -- if it's nuanced, show how)          |
| realm             | area, field, domain                            |
| landscape (metaphorical) | field, space, situation                 |
| myriad            | many, lots of                                  |
| plethora          | many, a lot                                    |

### Tier 2: Suspicious in clusters

Fine alone. Three in one paragraph = rewrite that paragraph.

robust, comprehensive, seamless, cutting-edge, innovative, streamline,
empower, foster, enhance, elevate, optimize, pivotal, intricate,
profound, resonate, underscore, harness, navigate (metaphorical),
cultivate, bolster, galvanize, cornerstone, game-changer, scalable

### Tier 3: Filler phrases -- delete on sight

These add zero information. The sentence is always better without them.

- "It's worth noting that..." -> just state it
- "It's important to note that..." -> just state it
- "Importantly, ..." / "Notably, ..." / "Interestingly, ..." -> just state it
- "Let's dive into..." / "Let's explore..." -> start with the content
- "As we can see..." -> they can see
- "Furthermore, ..." / "Moreover, ..." / "Additionally, ..." -> and, also, or just start
- "In today's [fast-paced/digital/modern] world..." -> delete the clause
- "At the end of the day..." -> delete
- "It goes without saying..." -> then don't say it
- "When it comes to..." -> just talk about the thing
- "One might argue that..." -> argue it or don't
- "Not just X, but Y" -> restructure (the #1 LLM rhetorical crutch)

### Structural slop patterns

These are the shapes that betray machine origin. Avoid them in any voice.

**Paragraph template machine**: Don't repeat the same paragraph
structure (topic sentence -> elaboration -> example -> wrap-up).
Vary it. Sometimes the point comes last. Sometimes a paragraph is
one sentence. Sometimes three long ones in a row.

**Sentence length uniformity**: If every sentence is 15-25 words,
it reads as synthetic. Mix in fragments. And long, winding,
clause-heavy sentences that carry the reader through a thought
the way a river carries a leaf. Then a short one.

**Transition word addiction**: If consecutive paragraphs start with
"However," "Furthermore," "Additionally," "Moreover," "Nevertheless"
-- rewrite. Start with the subject. Start with action. Start with
dialogue. Start with a sense detail.

**Symmetry addiction**: Don't balance everything. Three pros, three
cons, five steps -- that's a tell. Real writing is lumpy. Some
sections are long because they need to be. Some are two lines.

**Hedge parade**: "may," "might," "could potentially," "it's possible
that" -- pick one per page, max. State things or don't.

**Em dash overload**: One or two per page is fine. Five per paragraph
is a dead giveaway. Use commas, parentheses, or two sentences instead.

**List abuse**: Prose, not bullets. If the scene calls for a list
(a merchant's inventory, a spell's components), earn it. Don't
default to bullet points because it's easy.

### The smell test

After writing any passage, ask:
- Read it aloud. Does it sound like a person talking?
- Is there a single surprising sentence? Human writing surprises.
- Does it say something specific? Could you swap the topic and the
  words would still work? Specificity kills slop.
- Would a reader think "AI wrote this"? If yes, rewrite.

---

## Part 2: Voice Identity (generated per novel)

Everything below is discovered during the foundation phase.
The agent proposes a voice that serves THIS story, writes exemplar
passages, and calibrates against them throughout drafting.

### Tone
<!-- Generated during foundation. Examples:
     "Mythic and weighty, like stone tablets being read aloud."
     "Warm, slightly breathless, like a traveler telling stories by firelight."
     "Spare and cold. Sentences like knife cuts." -->

### Sentence Rhythm
<!-- Generated during foundation. Not rules -- tendencies.
     "Long sentences for worldbuilding, short for violence."
     "Dialogue is clipped. Narration flows." -->

### Vocabulary Register
<!-- Generated during foundation. The word-hoard for this world.
     What does this world SOUND like? Anglo-Saxon blunt? Latinate
     baroque? Colloquial modern? A mix? -->

### POV and Tense
<!-- Generated during foundation.
     Third limited? First? Rotating? Omniscient?
     Past tense? Present? Does it shift for effect? -->

### Dialogue Conventions
<!-- Generated during foundation.
     Tags: "said" only? Action beats? No tags at all?
     How do characters sound different from each other?
     Subtext rules: do characters say what they mean? -->

### Exemplar Passages
<!-- 3-5 paragraphs that ARE the voice. Written during foundation.
     The agent calibrates every chapter against these.
     These are the tuning fork. -->

### Anti-Exemplars
<!-- 3-5 paragraphs showing what this voice is NOT.
     Not the generic anti-slop stuff above -- specific to this novel.
     "This is too flowery for our tone." "This is too modern." -->

### 比喩の供給源（この作品の比喩はどこから取るか）

<!-- ここは foundation で自動生成されない。生成後（書き始める前）に埋める。
     作品固有の語彙から取るのが要点。例:
       「鏡・筆・字・光の語彙から取る。天候や季節からは取らない」
       「潮・灯・紙・匂いから取る。星や月からは取らない」 -->

- 供給源: <!-- 例: 鏡・筆・字・光 -->
- 取らない領域: <!-- 例: 天候・季節・星 -->

### 比喩の予算（必須・全作品共通）

- 比喩マーカー（まるで／みたい／〜よう／ごとく／あたかも）は **1,000字あたり3回まで**。
- **同型の反復を避ける**: 「まるで」＋「みたい」は **1章で合計4回まで**。
- 比喩を置いた直後の **2段落は比喩なし** にする（重ねない）。同じ供給源から続けて取らない。
- 比喩はゼロが目標ではない。**同じ型の繰り返し**が問題（読者が型に気づいた時点で、像ではなく癖として読まれる）。
