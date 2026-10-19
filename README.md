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
   → クローン、`.venv` 作成（`uv venv && uv pip install -e .`）、`.env` 雛形作成までエージェントが実行します。
   **注意: `uv run` / `uv sync` は使いません。** 本家（Anthropic 直叩き版）の名残で、
   この日本語版では依存パッケージ（dotenv / httpx）が入らない別環境を作って
   `ModuleNotFoundError` で落ちます（実測 2026-10-13）。
   必ず **`.venv/bin/python` を直接**呼んでください（詳細は [Quick Start](#quick-start)）
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
>   - **`verify_chapter_gates.py`（章ゲート10項目）を `run_pipeline.py` の drafting が呼び、
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
>   - **本文を触ったら全ゲート**：改訂フェーズ（`adversarial_edit.py` / `apply_cuts.py` / `gen_revision.py`）が章を書き換えた
>     **直後**に、同じ章ゲート10項目を必ず通します。落ちた章は**元に戻し**（git checkout）、`gen_revision.py` の改訂は
>     **修復→それでも通らなければ元に戻す**ので、改訂で文体が崩れたまま残ることがありません。
>     env `AUTONOVEL_REVISION_GATES=0` で無効化（既定は有効）
>   - **全編の目標字数（`AUTONOVEL_TARGET_WORDS`）**：作り直しで全体が薄まりすぎていないかを見ます。
>     章ごとの下限（1章3,000字）は守られていても、総量はどこも見ていなかったための対策
>     （実測 2026-10-13: セリフ比の検査が効きすぎて 24章93,466字 → 17章60,520字＝35%減）。
>     **未設定なら章数 × `AUTONOVEL_DEFAULT_WORDS_PER_CHAPTER`（既定5,000字）を目標にする**
>     （17章なら85,000字。設定は不要）。明示するなら `AUTONOVEL_TARGET_WORDS=80000` か
>     `state.json` の `target_words`（env より優先）。0 で自動算出をやめる。
>     目標の `AUTONOVEL_TARGET_WORDS_MIN_RATIO`（既定0.9＝10%以上不足）を下回ると、
>     次の周のブリーフに「地の文を足して膨らませる」指示が出ます（会話を増やすのではなく
>     地の文が厚い章に情景・内面を足す方向）。
>   - **整合ゲート（`verify_consistency.py`）**：キャラ以外のブレを拾います。
>     機械項目は「**消えた人物**」（第1章に出ている定義キャラが最後の3章に出てこない＝退場の放置）だけ。
>     設定矛盾・声の逸脱・伏線の放置は**意味の判定**なので judge モデル（1章1回・引用付き）が担当し、
>     改訂ブリーフに自動で足されます（人間が読まなくても、次の改訂で直る）。
>     `--only unused_setting` で「設定に書いたのに本文で使われていない語」も出せます（全体を見る材料）。
>     env `AUTONOVEL_CONSISTENCY=0` で無効化
>   - **章ゲートは10項目**：文字数／セリフ比／最小窓／重複／記号・英語／文頭の読点・連続句読点／比喩密度／**括弧の対応（「」『』（）の過不足=0）**／**空の台詞「」**／**同じ行が2回以上（12字以上）**。
>     括弧は修復パスが「」を閉じ忘れる事故を実測で見つけたため追加しました（完成稿24章では ずれ0/24＝誤検出なし）。
>   - **一対比較（Elo）で章の採用を決める**：絶対点は実行ごとに最大1点動くので、「点が下がったから捨てる」と
>     良い改訂を捨てます。点が下がったときは旧版と新版を**両方の順序で**審査にかけ、両方で新版が勝ったときだけ採用します。
>     `compare_versions.py` が判定（adopt／keep／draw）と Elo 表（`edit_logs/elo.tsv`）を残します
>     （順序で答えが変わる場合は「引き分け＝現状維持」にしてノイズで入れ替えません）。
>     env `AUTONOVEL_ELO_DECIDE=0` で無効化
>   - **強みの移植（改訂の向きを逆にする）**：改訂が「弱い所を直す＝平均点を上げる」だけだと**天井が動かない**ので、
>     パネルが「最も良い場面」と答えた章から**効いている型を言語化**し（LLM 1回）、**弱い章の同じ種類の場面へ移植**する
>     指示書を作ります（1章1回）。指示書は改訂ループが拾って `gen_revision.py` に渡すので、章ゲート→修復→判定→
>     一対比較の安全網はそのまま効きます。env `AUTONOVEL_STRENGTH=0`／`AUTONOVEL_STRENGTH_MAX_BRIEFS`（既定2）
>   - **長丁場の運転を守る（再試行と消費量の記録）**：上限（429）・混雑（5xx）・通信断・
>     タイムアウトは**一時的な失敗**とみなし、指数バックオフで待ってやり直します（`Retry-After`
>     があればその秒数を優先）。数時間の放置運転が途中の1回の失敗で死ぬのを防ぎます。
>     あわせて**消費量を1呼び出し1行のTSV**（`edit_logs/llm_usage.tsv`）に記録するので、
>     通し試走の前に「24章でどれだけ使うか」を実測できます。
>     env `AUTONOVEL_LLM_RETRIES`（既定5）／`AUTONOVEL_LLM_BACKOFF`（既定15秒）／`AUTONOVEL_USAGE_LOG`
>   - **上限で「黙って周回し続ける」のを止める**：429（上限）が連続したら、`llm.py` が
>     `edit_logs/STOP_QUOTA.txt` を置き、`run_pipeline.py` はそれを見て**その場で停止**します
>     （8時間・19周を空回りした実測があるため）。再開は**そのファイルを消して同じコマンド**。
>     使用率を監視して自動で待機・再開させたい場合は同梱の `quota_guard.py` 経由で走らせます。
>     env `AUTONOVEL_QUOTA_STREAK`（既定20）
>   - **判定の応答が読めないときは「低い点」にしない**：judge の応答が上限で切れて解釈できない場合、
>     切れた所を補って読み直し（駄目なら点数だけ拾い）、それでも駄目なら**上限を上げて1回やり直し**ます。
>     それでも読めなければ**「評価の失敗」として停止**します（生成物は捨てません）。生の応答は
>     `eval_logs/*_parse_fail.txt` に残ります。env `AUTONOVEL_JUDGE_MAX_TOKENS` /
>     `AUTONOVEL_JUDGE_MAX_TOKENS_ESCALATED`
>   - **サンプル小説データはリセット済み**：`seed.txt` を自分の種コンセプトに差し替えて開始します。
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
| `gen_cover_composite.py`, `gen_cover_print.py`（表紙PIL処理） | **検証済み（2026-10-14）**。KDP 仕様（5.5×8.5インチ・背幅自動計算・300DPI）で表紙を作れることを確認。PIL 未インストール・出力フォルダ未作成・日本語が豆腐になる不具合3件を修正（`7b078fb`）。明朝体はこの環境に無いので日本語が描けるゴシック（IPAGothic）にフォールバックする |
| `typeset/` 以下（LaTeX・ePubテンプレ一式） | 本家まま・未使用（**`build_tex.py` は検証済み 2026-10-14**。作り直し版17章を正しく組めることを確認。それ以前は `REPO_DIR` 固定で作り直し版の章を読めず空の tex ができていた） |
| `landing/index.html` | 本家まま（ランディングページテンプレ） |
| `main.py` | 本家由来の stub（Hello World）のまま |
| `draft_chapter_beat.py`（本家の既定ドラフター） | 本家まま・**既定からは外れました**。`run_pipeline.py` の drafting は fork 推奨の `draft_chapter_split.py` を呼びます（戻すなら env `AUTONOVEL_DRAFTER=draft_chapter_beat.py`） |
| `draft_chapter.py` / `draft_chapter_beat.py` / `draft_chapter_one.py` / `draft_chapter_single.py` / `run_drafts.py` | **本家まま・使わないこと**。単発または一括のドラフター。実測 2026-10-14: `draft_chapter_single.py 1` は動くが、**作り直し版の設定を読まず**リポジトリ側の古い設定（別の章タイトル・世界観）で1章を書き直してしまった（`BASE_DIR` 固定）。`.env` だけは直したが、`outline.md` / `world.md` / `characters.md` の探索は未対応。**既定の `draft_chapter_split.py` を使う限り動かない**ので、使う場合は同じ `project_dir()` 対応が必要 |
| `voice_fingerprint.py` | 本家由来の**英語メトリクス**（語数・英文の文長）。日本語原稿では数字が意味を持たない（実測 2026-10-19: 約4,400字の章が「148語」）。章数固定（`range(1,25)`）と1文の章で落ちるバグは修正済み |
| `quota_guard.py` | **未使用**（429 連続で停止マーカーを置く機能は `llm.py` に内蔵済み。実測 2026-10-14: どのスクリプトも import していない） |
| `PIPELINE.md` | **英語のまま未翻訳**。自動化の全体仕様（[Phase 4 からリンク](#phase-4-export)） |
| `WORKFLOW.md` | **英語のまま未翻訳**。人間が手動で全工程を回す段取り（[人間が使う場合](#人間が使う場合)からリンク） |
| [`CRAFT.md`](CRAFT.md) | **英語のまま未翻訳**。プロット・キャラクター・文体の作り方（教育資料）。`AUTONOVEL_REPO` 配下を読みます |
| [`ANTI-SLOP.md`](ANTI-SLOP.md) | **英語のまま未翻訳**。AI 臭い語の検出（語単位）。採点・改訂が読みます |
| [`ANTI-PATTERNS.md`](ANTI-PATTERNS.md) | **英語のまま未翻訳**。AI 臭い構造の検出（話者帰属・説明台詞など）。採点・改訂が読みます |
| [`program.md`](program.md) | **英語のまま未翻訳**。フェーズごとのエージェント指示書 |
| `MYSTERY.md` | **英語のまま未翻訳**（ミステリ専用のガードレール。この日本語版では参照のみ） |
| [.gitignore](.gitignore), [`pyproject.toml`](pyproject.toml), `uv.lock`, `.python-version` | モデル非依存のため本家まま |

> **日本語で読めるのは [README.md](README.md)・[`voice.md`](voice.md)・[`style_exemplar.md`](style_exemplar.md) の3件だけです**（実測の日本語率: README 62% / voice 79% / style_exemplar 92%）。
> それ以外は**中身が英語**です（先頭の見出しだけ日本語のものもあるが、実質英語）。
> 動作には影響しません（採点・改訂はファイルの中身を読むので言語を問いません）が、
> 設計を知りたいときは日本語の README を見るか、翻訳が必要です。
>
> **英語の資料は2種類に分かれます:**
>
> **A. 読み物の教科書・仕様書（設計を知るため。7件・全て英語）**
> [`PIPELINE.md`](PIPELINE.md)（パイプライン全体の仕様書）／
> [`WORKFLOW.md`](WORKFLOW.md)（人間が手動で回す手順書）／
> [`CRAFT.md`](CRAFT.md)（小説の書き方の教科書＝Save the Cat ビート表など）／
> [`ANTI-SLOP.md`](ANTI-SLOP.md)（AI臭い**語**の図鑑）／
> [`ANTI-PATTERNS.md`](ANTI-PATTERNS.md)（AI臭い**構造**の図鑑）／
> [`program.md`](program.md)（エージェントへの指示書・5層の構造）／
> `MYSTERY.md`（ミステリ専用ガードレール・参照のみ）
>
> **B. 作品の設計図（データの入れ物。作品ごとに作り直される）**
> [`canon.md`](canon.md)（設定の正本）／ [`world.md`](world.md)（世界観）／
> [`characters.md`](characters.md)（キャラクター）／ [`outline.md`](outline.md)（あらすじの骨格）
>
> **日本語のガイド（こちらが文体の正本）:**
> [`voice.md`](voice.md)（文体のガードレール＝Part 1 と、作品ごとに発見した声＝Part 2）／
> [`style_exemplar.md`](style_exemplar.md)（文体アンカー。承認章の良い部分を貼って全章に注入）

> 動作実績があるのは **seed → foundation → drafting → evaluate/adversarial → revision → 読者パネル → 組版 → EPUB作成 → KDP入稿** までです（25章・約13万字の長編で実測）。
> 読者パネル（`panel_one_question.py`）は実測 2026-10-14 に作り直し版17章すべてを測定し、
> 平均 7.9 / 17章すべて「続きが読みたい」を確認（引用が本文と不一致は1件＝検査が効いている）。
> Art（fal.ai）・Audiobook（ElevenLabs）の各工程は、本家設計を引き継いでいるだけで
> このリポジトリでは検証していません（表紙2件と `build_tex.py` は検証済み）。

---

## Quick Start

> ⚠️ **`uv run` / `uv sync` は使わないでください。** 本家（Anthropic API 直叩き版）の名残で、
> この日本語版では依存パッケージ（dotenv / httpx）が入らない別環境を作って
> `ModuleNotFoundError: No module named 'dotenv'` で落ちます（実測 2026-10-13）。
> 必ず **`.venv/bin/python` を直接**呼んでください。

```bash
git clone <repo-url> && cd autonovel

# 1. 仮想環境を作る（初回だけ。.venv/bin/python ができる）
uv venv && uv pip install -e .

# 2. エンドポイントとモデルを設定
cp .env.example .env    # 編集する

# 3. 種コンセプトを作る（既定ルート。出力をそのまま seed.txt に貼る）
.venv/bin/python seed.py
# すでに着想がある場合は、それを展開させる
.venv/bin/python seed.py --riff "平安の才媛が鏡の世界へ行く話"

# 4. パイプライン実行
.venv/bin/python run_pipeline.py --from-scratch
```

### よく使う操作

```bash
# パイプラインを最初から（state.json をリセットして seed.txt から）
.venv/bin/python run_pipeline.py --from-scratch

# 指定フェーズだけ回す（foundation / drafting / revision / export）
.venv/bin/python run_pipeline.py --phase revision

# 改訂の周回数だけ変えて回す
.venv/bin/python run_pipeline.py --phase revision --max-cycles 8

# 原稿を Word にも出す（見出し・目次つき）
.venv/bin/python export_docx.py --with-toc
#   → 出版/<題名>.docx ができる。題名は seed.txt の「題名:」から取る
#   上書きせず別名で出すなら --out / 題名を明示するなら --title

# 縦書き・右開きの EPUB を作る（作品フォルダの 出版/epub.json を読む）
.venv/bin/python build_epub.py --font /path/to/NotoSerifJP-sub.ttf
#   → 出版/<題名>.epub。旧版は $AUTONOVEL_BACKUP_DIR（既定 ~/backups）へ退避される

# 章ゲートだけ単独で確認する
.venv/bin/python verify_chapter_gates.py
```

進み具合は `state.json` を見てください（フェーズ・章数・スコアが入っています）。

### 作り直し（リライト）の作業では

スクリプト一式を作業用ディレクトリへコピーして動かす運用です。
そのまま実行すると**リポジトリ側の古い設定**（別の作品の `outline.md` 等）を
読んでしまうので、環境変数で2つ渡します:

```bash
cp -r /path/to/autonovel /path/to/作業用コピー
cd /path/to/作業用コピー
# chapters/ と seed.txt を作業用コピー側に置いてから:

export AUTONOVEL_REPO=/path/to/autonovel                    # 共有ガイド（CRAFT.md 等）の場所
export AUTONOVEL_PYTHON=/path/to/autonovel/.venv/bin/python # 依存の入ったインタプリタ

.venv/bin/python run_pipeline.py --phase revision
```

`AUTONOVEL_REPO` を渡さないと共有ガイドが読めず、
`AUTONOVEL_PYTHON` を渡さないとシステム python3 に落ちて `ModuleNotFoundError` になります
（どちらも実測 2026-10-13 で発生）。

### 縦書きEPUBを作る

`build_epub.py` は**作品の情報をコードに持たない**。作品フォルダの `出版/` から読む:

| 置くもの | 役割 |
|---|---|
| `出版/epub.json` | 題名・著者名・行間・**ルビ表**・目次に何を出すか・表紙の有無・奥付を横組みにするか |
| `出版/あとがき.md` / `既刊案内.md` / `奥付.md` | あればページを作る（無ければ作らない）。既刊案内は `##` 見出し＋作品名の行 |
| `出版/表紙.jpg` | `epub.json` の `"cover": true` のときだけ使う（既定は入れない） |
| `本文/NN-第N章.md`（無ければ `chapters/ch_NN.md`） | 本文。1行目が章題、以降が段落 |

```json
{
  "title": "作品名", "author": "著者名", "date": "2026-10",
  "line_height": 2.0,
  "toc": {"chapters": true, "afterword": true, "books_list": false, "colophon": false},
  "title_ruby": {"真": "シン"},
  "ruby": [["瑞兆", "ずいちょう"], ["獣", "けもの"]]
}
```

```bash
# フォントを本文の文字に絞る（初回と、原稿を書き換えたときに作り直す）
/opt/data/.venv_epub/bin/python -m fontTools.subset NotoSerifJP-Regular.ttf \
  --text-file=chars.txt --output-file=NotoSerifJP-sub.ttf \
  --layout-features=vert,vrt2,kern,ccmp,locl,palt,halt,liga,calt \
  --no-hinting --desubroutinize --drop-tables+=DSIG --recalc-bounds --name-IDs=1,2,3,4,6

.venv/bin/python build_epub.py --project /path/to/作品 --font /path/to/NotoSerifJP-sub.ttf
```

**ルビの規則**

- ルビは**EPUB生成時に差し込む**。原稿のmdには書かない（docx側の組みが崩れる）
- **各章の初出1回だけ**（同一章の2回目以降は素の漢字、章が変われば再び振る）。章題に難読語があるときも振る
- 語幹に振り、送り仮名は外に出す（「煤けた」→ 煤(すす)けた／「煮え滾る」→ 煮え滾(にえたぎ)る）
- 複数の読みがある語（供物＝くもつ／そなえもの、藁沓＝わらじ／わらぐつ）は**本人に確認**する

**検証（必ずやる）**

- `epubcheck` を1回回す（`java -jar epubcheck.jar <file.epub>`）。CSSのフォントURLが `../fonts/…` だと `RSC-007` で落ちる
- **本文の欠落検査**：タグと `rt`/`rp` を除去したテキストが原稿と完全一致するか
- **グリフ網羅**：EPUB内の表示テキストの全文字がフォントの `cmap` にあるか（原稿を編集したのにフォントを作り直すのを忘れると、新しい文字が**無言で消える**）
- **見た目**：`style.css`・`fonts/`・対象の `.xhtml` を同じ階層に取り出してブラウザで開き、スクショを撮って位置を測る（縦組みは % 余白の基準が直感と違う）

**章数**は `AUTONOVEL_CHAPTERS`（既定24）。`gen_outline.py` / `gen_outline_part2.py` の前半・後半の分割もこの値から計算する。

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
章を結合して `manuscript.md`（原稿の正本）を作り、続けて:

- **`export_docx.py`** → `出版/<題名>.docx`（Word 用。`#` → 「見出し1」なので
  ナビゲーション窓と目次が使える。編集・校了はこちら）
- **`build_epub.py`** → `出版/<題名>.epub`（**縦書き・右開き**。Kindle に出す本はこれ）
- `build_tex.py` → `typeset/*.tex`、`tectonic` があれば `novel.pdf`（印刷用）

EPUB は作品の情報をコードに持たず、**作品フォルダの `出版/epub.json`** から読む
（著者名・行間・ルビ表・目次に何を出すか・表紙の有無）。`出版/あとがき.md`
`既刊案内.md` `奥付.md` があれば、その分のページを自動で作る（無ければ作らない）。
表紙アート（`gen_art.py` ほか）とオーディオブック（ElevenLabs）は
この日本語版では未検証です。

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
| `verify_chapter_gates.py` | **章ゲート10項目**（文字数／セリフ比／最小窓／重複／……・——・英語／読点・連続句読点／比喩密度／括弧／空の台詞／同じ行）。`--band 3000 99999` で完成稿向けの文字数帯を指定できる。`run_pipeline.py` の drafting が呼び、FAIL の章は破棄→書き直し |
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
| `build_epub.py` | **縦書き・右開きの EPUB**。作品情報は `出版/epub.json` から読む（コードに作品名を書かない）。ルビは各章初出に差し込む |
| `run_pipeline.py` | Full pipeline orchestrator (seed → finished novel) |

---

## File Structure

```
フレームワーク（使い回し。作品が変わっても同じ）:
  llm.py                 — OpenAI互換のLLMクライアント（writer/revision/judge/review の4役割・役割ごとにモデルを変えられる）
  export_docx.py         — 原稿を Word の .docx に出す（# → 「見出し1」。ナビゲーション・目次つき）
  build_tex.py           — LaTeX 組版（typeset/*.tex。tectonic があれば novel.pdf）
  build_epub.py          — 縦書き・右開きの EPUB（出版/epub.json を読む。ルビ・目次・奥付・任意で表紙）
  verify_chapter_gates.py — 章ゲート10項目（run_pipeline の drafting が呼ぶ。FAIL→破棄・書き直し）
  dialogue_ratio.py      — 章の計測（文字ベースのセリフ比・重複文・台詞長）
  program.md             — フェーズごとのエージェント指示書【英語・未翻訳】
  CRAFT.md               — プロット・キャラクター・文体の作り方（教育資料）【英語・未翻訳】
  ANTI-SLOP.md           — AI臭い語の検出（語単位）【英語・未翻訳】
  ANTI-PATTERNS.md       — AI臭い構造の検出（話者帰属・説明台詞など）【英語・未翻訳】
  PIPELINE.md            — 自動化の全体仕様【英語・未翻訳】
  WORKFLOW.md            — 人間が手動で全工程を回す段取り【英語・未翻訳】
  MYSTERY.md             — ミステリ専用のガードレール（この版では参照のみ）【英語・未翻訳】

  → 日本語で読めるのは README.md / voice.md / style_exemplar.md の3件。
    上記7ファイル（program.md・CRAFT.md・ANTI-SLOP.md・ANTI-PATTERNS.md・
    PIPELINE.md・WORKFLOW.md・MYSTERY.md）は本家（英語版）のまま。
    動作に影響なし（採点・改訂は中身を読むので言語を問わない）。

作品ごと（seed.txt から作り直される。テンプレートは全て英語）:
  seed.txt               — 種コンセプト（唯一の入力。書式の見本を兼ねる。seed.py の出力を貼る）
  voice.md               — Part 1: ガードレール（日本語）／ Part 2: 作品ごとに発見した声
  style_exemplar.md      — 文体アンカー（日本語。承認章の良い部分を貼り、全章に注入）
  canon.md               — 設定の正本（英語テンプレート）
  world.md               — 世界観の設計図（英語テンプレート）
  characters.md          — キャラクター設計（英語テンプレート）
  outline.md             — あらすじの骨格（英語テンプレート）
  state.json             — パイプラインの進み具合（フェーズ・章数・スコア）
  chapters/ch_NN.md      — 本文そのもの（第1章・第2章…）

組版・表紙・設定:
  typeset/               — LaTeX のテンプレと雛形（build_tex.py が使う）
  landing/               — ランディングページのテンプレ（本家のまま）
  .env.example           — エンドポイントとモデルの設定例（OpenAI互換なら何でも）
  pyproject.toml         — Python の依存関係
```

### 完成形（export が出すもの）

| ファイル | 用途 |
|---|---|
| `manuscript.md` | **原稿の正本**（章を全部つないだ Markdown） |
| `manuscript.docx`（`出版/<題名>.docx`） | Word 用。`#` → 「見出し1」なのでナビゲーション窓と目次（F9 で更新）が使える。編集・校了はこちら |
| `outline.md` / `arc_summary.md` | あらすじ（章から作り直し） |
| `typeset/*.tex` → `typeset/novel.pdf` | 印刷用の組版（`tectonic` が入っていれば） |
| `出版/<題名>.epub` | **Kindle 用の EPUB**（縦書き・右開き・ルビ・目次・奥付）。`build_epub.py` が作る |

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