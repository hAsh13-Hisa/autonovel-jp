# autonovel ― 日本語ライトノベル版 (JP Edition)

AIエージェントだけで小説を書き、推敲し、組版する自律パイプライン
**autonovel**（本家: [NousResearch/autonovel](https://github.com/NousResearch/autonovel)）の
**日本語ライトノベル向け派生版**です。
設計の前提は **AIエージェントがツール群を操作すること** です（`program.md` は各フェーズの
エージェント指示書。人間が手動で全部動かす手順は [WORKFLOW.md](WORKFLOW.md)）。

## Hermes Agent で使い始める

このリポジトリURLを [Hermes Agent](https://github.com/NousResearch/Hermes) に渡せば、
セットアップから執筆ループまで任せられます:

1. **「このリポジトリをcloneして、autonovelパイプラインをセットアップして」**
   → クローン、`uv sync`、`.env` 雛形作成までエージェントが実行します
2. **「種コンセプトを一緒に考えたい」**
   → `seed.py`（ゼロから10案）または `seed.py --riff "<雑な着想>"`（着想を5案に展開）をエージェントが実行し、
   選んだ案を `seed.txt` に書き込みます（書式の正本は `seed.txt`）
3. **「第一章から執筆を始めて」**
   → 以降の執筆→品質ゲート→リジェクト時の再構成までエージェントが回します

人間が決めるのは **企画の核**（何を書くか・どのLLMエンドポイント/モデルを使うか・APIキーの用意）
と、生成された原稿の **読了・採用判断**。反復作業はエージェント側に寄せられます。

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
>   - 比喩の型の反復を測る（比喩密度 ≤3.0/1000字・まるで+みたい ≤4。閾値は採用版の実測から逆算）
>   - **`verify_chapter_gates.py`（章ゲート6項目）を `run_pipeline.py` の drafting が呼び、
>     FAIL の章は自動で破棄して書き直す**。判定は「破棄条件」と「記録のみ」に分かれます:
>     破棄＝**文字数・重複・記号・比喩の型**／記録のみ＝**セリフ比・最小窓**（完成稿の物差しなので
>     生原稿は落ちるのが普通）→ env `AUTONOVEL_GATE_WARN_ITEMS` で切替（手動の完成稿検査は未指定＝全項目合否）
>   - ドラフトは fork 推奨の分割ドラフター（`draft_chapter_split.py`）が既定。env `AUTONOVEL_DRAFTER` で
>     本家の `draft_chapter_beat.py` に戻せる（分割ドラフターは長時間なので `AUTONOVEL_DRAFT_TIMEOUT` も調整可）
>   - **試作1章テスト（設定の合格直後）**：24章を書き始める前に**1章だけ試作**して判定します
>     （章ゲート → 落ちたら修復 → もう一度ゲート → 判定スコア）。通ればその章を**第1章として採用**して2章以降へ、
>     通らなければ `briefs/trial_chapter_brief.md` に数行のブリーフ（落ちた項目・修復で直した箇所・
>     次に直すファイル）を残して**止まります**。人間が見るのはこのときだけ。
>     env `AUTONOVEL_TRIAL_TEST=0` で無効化、`AUTONOVEL_TRIAL_STOP=0` で止めずに記録だけして続行、
>     `AUTONOVEL_TRIAL_ONLY=1` で試作テストだけ実行（`run_pipeline.py --phase drafting` と併用。設定を直したあとの再試行用）
>   - **評価役（judge）は writer と別系統**：同じモデルに書かせて同じモデルに採点させると自分のクセを見逃します。
>     また**評価側のプロンプトにも回答言語の指定が必要**です（生成側だけが日本語指定になっていました。いまは
>     `llm.py` が全 judge/review 呼び出しに「回答は日本語で」を付けます。JSONのキーだけ英語のまま）。
>     2章（生原稿 < 人手で仕上げた章）×5モデルを2回ずつ採点した実測では、`kimi-k3` が2回とも順位一致＋
>     日本語で本文を引用した具体的な指摘、`gemma4:31b-cloud` は最速(7秒)だが writer 本人なので自己評価になる、
>     `glm-5.3` は出力が壊れる（2回連続）。**同じ章でも実行ごとに点が最大1点動く**ので1回の比較で決めない。
>     env `AUTONOVEL_JUDGE_MODEL`（推奨 `kimi-k3`）／`AUTONOVEL_REVIEW_MODEL`
> - **サンプル小説データはリセット済み**：`seed.txt` を自分の種コンセプトに差し替えて開始します。
>   種は `seed.py`（ゼロから生成）または `seed.py --riff "雑な着想"`（着想を展開）で作り、
>   出力をそのまま `seed.txt` に貼ります（`seed.txt` は書式の見本も兼ねます）
> - **seed.txt の書式を拡張**：`登場人物` `ストーリー構成` に加え、
>   `各幕の事件`（幕ごとの具体的な事件。第2幕後に「取り返しのつかない損失」を1つ）と
>   `章末フックの型`（3種）を追加。`seed.py` の出力もこの書式です
> - **アウトライン生成に退屈防止の必須事項**：`gen_outline.py` / `gen_outline_part2.py` に
>   「40〜60%地点の取り返しのつかない損失」「対立面を第II幕末まで持続」「各章末に次をめくる理由」を追加

### 本家のまま・未使用の部分（注記）

| 対象 | 状態 |
|------|------|
| `gen_audiobook.py`, `audiobook_voices.json`（ElevenLabs） | **本家まま・未使用**。オーディオブック工程はこの日本語版では未検証 |
| `gen_cover_composite.py`, `gen_cover_print.py`（表紙PIL処理） | 本家まま・未使用 |
| `typeset/` 以下（LaTeX・ePubテンプレ一式） | 本家まま・未使用（`build_tex.py` のみパス修正＋存在章だけ組むよう改修、空chaptersで動作確認済み） |
| `landing/index.html` | 本家まま（ランディングページテンプレ） |
| `main.py` | 本家由来の stub（Hello World）のまま |
| `draft_chapter_beat.py`（本家の既定ドラフター） | 本家まま・**既定からは外れました**。`run_pipeline.py` の drafting は fork 推奨の `draft_chapter_split.py` を呼びます（戻すなら env `AUTONOVEL_DRAFTER=draft_chapter_beat.py`） |
| `PIPELINE.md`, `WORKFLOW.md`, `CRAFT.md`, `ANTI-SLOP.md`, `ANTI-PATTERNS.md`, `program.md`, 本家README | **英語のまま未翻訳**（本家の設計参照ドキュメント） |
| `.gitignore`, `pyproject.toml`, `uv.lock`, `.python-version` | モデル非依存のため本家まま |

> 動作実績があるのは **seed → foundation → drafting → evaluate/adversarial → revision** までです。
> Art（fal.ai）・Audiobook（ElevenLabs）・Typeset の各工程は、本家設計を引き継いでいるだけで
> このリポジトリでは検証していません（`build_tex.py` のみ、上記の通り動作確認済み）。

---

## Quick Start

```bash
git clone <repo-url> && cd autonovel
cp .env.example .env    # エンドポイントとモデルを編集

uv sync

# 種コンセプトを作る（既定ルート。出力をそのまま seed.txt に貼る）
uv run python seed.py
# すでに着想がある場合は、それを展開させる
uv run python seed.py --riff "平安の才媛が鏡の世界へ行く話"

# パイプライン実行
uv run python run_pipeline.py --from-scratch
```

### .env 設定例

```bash
# ローカルOllama
AUTONOVEL_API_BASE_URL=http://localhost:11434/v1
AUTONOVEL_WRITER_MODEL=qwen3.6:latest

# クラウド（ollama.com / DeepSeek / OpenRouter 等、OpenAI互換なら何でも）
AUTONOVEL_API_BASE_URL=https://ollama.com/v1
AUTONOVEL_API_KEY=sk-...
AUTONOVEL_WRITER_MODEL=deepseek-v4.1-flash

# 混在（執筆はクラウド、評価はローカル）
AUTONOVEL_JUDGE_BASE_URL=http://localhost:11434/v1
AUTONOVEL_JUDGE_MODEL=qwen3.8-fast:latest   # 旧 qwen3.8:latest は 2026-09-30 に削除
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
| `draft_chapter_split.py` | **自律実行の既定**（`run_pipeline.py` が呼ぶ）長さ分割ドラフター＋会話ゲート。1章＝約1,200字×5チャンクで、各チャンクのセリフ比率を検査して再試行 |
| `draft_chapter_single.py` | 1回呼び出しドラフター（文体は滑らか、会話密度は下がる） |
| `draft_chapter_beat.py` | ビート分割ドラフター（短文になりがち） |
| `draft_chapter.py` | 本家由来の単発ドラフター |
| `run_drafts.py` | Batch sequential chapter drafter |

### Infrastructure
| Tool | Purpose |
|------|---------|
| `llm.py` | 統一OpenAI互換クライアント（writer/revision/judge/review の4役割）。`AUTONOVEL_DISABLE_THINKING=1` で思考型モデルの推論バジェット消費による空応答を防止。使用トークンを標準エラー出力へ記録 |
| `quota_guard.py` | `ollama.com` のクラウド使用率をポーリングし、上限到達時に子プロセスを自動停止(SIGSTOP)・リセット後に自動再開(SIGCONT)する汎用ラッパー。長時間バッチ実行用 |

### Evaluation
| Tool | Purpose |
|------|---------|
| `evaluate.py` | Mechanical slop scorer + LLM judge |
| `adversarial_edit.py` | "Cut 500 words" analysis → classified cuts |
| `compare_chapters.py` | Head-to-head Elo tournament |
| `reader_panel.py` | 4-persona novel-level evaluation |
| `panel_one_question.py` | **面白さの実測器（質問は1つ）**。全章の抜粋を1回の呼び出しで渡し「続きを読みたいか」で必ず3層に分けさせる（相対評価）。絶対点0-10・一対比較は識別しないと実測済み（下記）。`--md` で人間用ブリーフ（下層＝読むべき章）、`--repeat` で多数決＋不安定章の検出、引用は本文と機械照合 |
| `review.py` | Dual-persona review with stopping conditions |
| `verify_chapter_gates.py` | **章ゲート6項目**（文字数／セリフ比／最小窓／重複／……・——・英語／比喩の型）。`run_pipeline.py` の drafting が呼び、FAIL の章は破棄→書き直し |
| `dialogue_ratio.py` | 章の計測（文字ベースのセリフ比・重複文・台詞の長さ）。`verify_chapter_gates.py` が読み取る |
| `repair_chapter.py` | **落ちた章を直す修復パス（破棄しない）**。直し方は4つ＝(1) 重複文の削除【コード】(2) 記号の整理【コード】(3) 比喩の型の言い換え【LLM】(4) 会話が薄い区画の地の文を会話に置き換え【LLM】。それぞれ良くならなければ元に戻す。元ファイルは上書きしない。`--only <種類>` で種類を選び、`--force` でゲートを通っていても実行（種類ごとの動作確認用） |

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
  verify_chapter_gates.py — 章ゲート6項目（run_pipeline の drafting が呼ぶ。FAIL→破棄・書き直し）
  dialogue_ratio.py      — 章の計測（文字ベースのセリフ比・重複文・台詞長）

PER-NOVEL (regenerated from seed.txt):
  seed.txt               — 種コンセプト（唯一の入力。書式の見本を兼ねる。seed.py の出力を貼る）
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