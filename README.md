# autonovel ― 日本語ライトノベル版 (JP Edition)

AIエージェントだけで小説を書き、推敲し、組版する自律パイプライン
**autonovel**（本家: [NousResearch/autonovel](https://github.com/NousResearch/autonovel)）の
**日本語ライトノベル向け派生版**です。

> ### 本家からの主な変更点
> - **OpenAI互換APIに全面対応**：本家はAnthropic API直叩きでしたが、LLM呼び出しを
>   `llm.py` に統一。ローカルOllama / ollama.com / DeepSeek / OpenRouter など
>   OpenAI互換エンドポイントであれば何でも動きます
> - **役割別モデル設定**：執筆(writer)／推敲(revision)／評価(judge)／査読(review)の
>   4役割ごとにモデルとエンドポイントを個別設定可能（例：執筆はクラウド、評価はローカル）
> - **日本語プロンプト＋ライトノベル品質ゲート**：
>   - `draft_chapter_split.py` — 長さ分割生成。500字窓ごとの最低セリフ比率チェックで
>     章終盤の「地の文だらけ化」を検出
>   - セリフ比率は**行数ではなく文字数ベース**で測定（セリフ7：地文3）
>   - 章の文字数ゲート、`——`/`……`の使用上限、読点スタッカート検出、定型AI表現の減点
>   - `style_exemplar.md`（文体アンカー）を毎章のプロンプトに注入して文体ドリフトを防止
> - **サンプル小説データはリセット済み**：`seed.txt` に自分の企画書を書いて開始します

---

## Quick Start

```bash
git clone <repo-url> && cd autonovel
cp .env.example .env    # エンドポイントとモデルを編集

uv sync

# seed.txt に企画書を書く（同梱の seed.txt は書式サンプル。自分のものに差し替えてください）

# パイプライン実行
uv run python run_pipeline.py --from-scratch
```

### .env 設定例

```bash
# ローカルOllama
AUTONOVEL_API_BASE_URL=http://host.docker.internal:11434/v1
AUTONOVEL_WRITER_MODEL=qwen3.6:latest

# クラウド（ollama.com / DeepSeek / OpenRouter 等、OpenAI互換なら何でも）
AUTONOVEL_API_BASE_URL=https://ollama.com/v1
AUTONOVEL_API_KEY=sk-...
AUTONOVEL_WRITER_MODEL=deepseek-v4-pro:0813

# 混在（執筆はクラウド、評価はローカル）
AUTONOVEL_JUDGE_BASE_URL=http://host.docker.internal:11434/v1
AUTONOVEL_JUDGE_MODEL=qwen3.8:latest
```

組み合わせの例は [.env.example](.env.example) にまとまっています。

---

## The Pipeline

### Phase 1: Foundation
seed.txt → 世界観・キャラクター・24章アウトライン・声・カノンを生成。
`foundation_score > 7.5` までループ。

### Phase 2: First Draft
章を順番に執筆し、各章を採点。`score > 6.0` で採用、未達ならリトライ。

### Phase 3a: Automated Revision
敵対的編集（削る500語分析）→ カット適用 → リーダーパネル → 改稿ブリーフ →
章の書き直し。スコアが停滞したらループ停止。

### Phase 3b: Dual-Persona Review Loop
全文をレビュー役モデル（本家ではClaude Opusを想定）に送り、文芸批評家＋
小説教授の二重ペルソナでレビュー。指摘を修正し、指摘が尽きるまで反復。

### Phase 4: Export
LaTeX組版、表紙アート、オーディオブック台本、ePub、ランディングページ生成。

詳細は [PIPELINE.md](PIPELINE.md)。

---

## Tools

### Foundation
| Tool | Purpose |
|------|---------|
| `seed.py` | Generate seed concepts |
| `gen_world.py` | Seed → world bible |
| `gen_characters.py` | Seed + world → character registry |
| `gen_outline.py` / `gen_outline_part2.py` | Outline with beats and foreshadowing (2-part split to avoid truncation) |
| `gen_canon.py` | Cross-reference hard facts |
| `voice_fingerprint.py` | Voice analysis and discovery |

### Drafting
| Tool | Purpose |
|------|---------|
| `draft_chapter_split.py` | **推奨** 長さ分割ドラフター＋会話ゲート |
| `draft_chapter_single.py` | 1回呼び出しドラフター（文体は滑らか、会話密度は下がる） |
| `draft_chapter_beat.py` | ビート分割ドラフター（短文になりがち） |
| `draft_chapter.py` | 本家由来の単発ドラフター |
| `run_drafts.py` | Batch sequential chapter drafter |

### Infrastructure
| Tool | Purpose |
|------|---------|
| `llm.py` | 統一OpenAI互換クライアント（writer/revision/judge/review の4役割） |

### Evaluation
| Tool | Purpose |
|------|---------|
| `evaluate.py` | Mechanical slop scorer + LLM judge |
| `adversarial_edit.py` | "Cut 500 words" analysis → classified cuts |
| `compare_chapters.py` | Head-to-head Elo tournament |
| `reader_panel.py` | 4-persona novel-level evaluation |
| `review.py` | Dual-persona review with stopping conditions |

### Revision
| Tool | Purpose |
|------|---------|
| `gen_brief.py` | Auto-generate revision briefs from feedback |
| `gen_revision.py` | Rewrite a chapter from a revision brief |
| `apply_cuts.py` | Batch adversarial cut applicator |

### Art & Cover / Audiobook / Orchestration
| Tool | Purpose |
|------|---------|
| `gen_art.py` ほか | Art pipeline, cover composite, print cover |
| `gen_audiobook_script.py` / `gen_audiobook.py` | Multi-voice audiobook (ElevenLabs) |
| `run_pipeline.py` | Full pipeline orchestrator (seed → finished novel) |

---

## File Structure

```
FRAMEWORK (reusable):
  llm.py                 — OpenAI-compatible LLM client (4 roles, per-role env vars)
  program.md             — Agent instructions per phase
  CRAFT.md               — Craft education (plot, character, world, prose)
  ANTI-SLOP.md           — Word-level AI tell detection
  ANTI-PATTERNS.md       — Structural AI pattern detection
  PIPELINE.md            — Full automation specification
  WORKFLOW.md            — Step-by-step human guide

PER-NOVEL (regenerated from seed.txt):
  seed.txt               — 企画書（唯一の入力。書式サンプル同梱）
  voice.md               — Part 1: guardrails. Part 2: discovered per novel
  world.md / characters.md / outline.md / canon.md / MYSTERY.md
  style_exemplar.md      — 文体アンカー（承認章の良い部分を貼り、全章に注入）
  state.json             — Pipeline state tracker
  chapters/ch_NN.md      — THE ACTUAL PROSE

TYPESETTING / ART / CONFIG:
  typeset/               — LaTeX + ePub scaffolding
  landing/               — Landing page template
  .env.example           — endpoint/model config (OpenAI-compatible)
  pyproject.toml         — Python dependencies
```

---

## API Keys

| Service | Key | Used for |
|---------|-----|----------|
| OpenAI-compatible LLM endpoint | `AUTONOVEL_API_KEY` | 執筆・推敲・評価・査読（ローカルOllamaなら不要） |
| fal.ai | `FAL_KEY` | Cover art (optional) |
| ElevenLabs | `ELEVENLABS_API_KEY` | Audiobook (optional) |

コアパイプラインに必須なのはLLMエンドポイントだけです。ローカルOllamaなら鍵不要で完結します。

---

## Upstream & License Notice

- 本リポジトリは [NousResearch/autonovel](https://github.com/NousResearch/autonovel) の派生版です。
  本家のコード・ドキュメントの権利は **© Nous Research** および各コントリビューターに帰属します。
- 本家には LICENSE ファイルが付与されていないため、コード部分は GitHub の利用規約に基づく
  フォーク/派生物として取り扱います。著作権表示を削除しないでください。
- 日本語化・品質ゲート等の**この版の追加実装**（`llm.py`、`draft_chapter_beat.py`、
  `draft_chapter_single.py`、`draft_chapter_split.py`、各品質ゲート、日本語プロンプト）は
  Hisa ([@hAsh13-Hisa](https://github.com/hAsh13-Hisa)) によるものです。

### Production History (upstream)

The first novel produced by the original pipeline:
*The Second Son of the House of Bells* — 79,456 words, 19 chapters.
See the upstream repository and its `autonovel/bells` branch.

---

## Inspiration

- [karpathy/autoresearch](https://github.com/karpathy/autoresearch) — the autonomous research loop
- Brandon Sanderson's writing lectures (Laws of Magic, character sliders)
- K.M. Weiland's *Creating Character Arcs*
- Blake Snyder's *Save the Cat*
- Ursula K. Le Guin's "From Elfland to Poughkeepsie"
- [slop-forensics](https://github.com/sam-paech/slop-forensics) and [EQ-Bench Slop Score](https://eqbench.com/slop-score.html)
- 本家 autonovel by Nous Research（Claude Opus / Sonnet による初の自動執筆小説）