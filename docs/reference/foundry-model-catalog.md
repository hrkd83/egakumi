# Foundryモデルの分類・リリース日一覧

初回配備の正本は`config/test.json`・`config/production.json`の`services.modelDeployments`である。本書の初回配備一覧は現行configの32モデルを説明する。

採用検討用の67モデル・品質指数・速度・公開日の一覧は**2026年10月27日の調査記録**であり、現時点の提供可否や配備済み状態を保証しない。配備前には採用モデルの版・リージョン・SKU・利用枠を確認する。

## 調査範囲と採用候補の条件

- 対象：Foundryの一般提供モデル一覧から取得した67モデル。
- 性能値：同日に取得したFoundry Leaderboardの49モデルと照合し、モデルIDが一致した22モデルの値を採用。
- 提供状態：Microsoftの公式終了予定表と、East US 2のModels APIを照合。対象67モデルすべてにGA版があることを確認した。
- 除外条件：Deprecated／Retiredなど廃止段階に入ったモデル・バージョン。GA版の将来の終了日は、移行計画の情報として管理する。
- モデル名単位での除外は0件。採用時にはGA版の具体的なバージョンとデプロイSKUを選択する。

GAにも将来の終了日が設定される。提供状態・終了日は、モデル名に加えてバージョン・リージョン・SKUごとに確認する。

## 初回配備する32モデル

テスト用・本番用の`config`とAPI例は、次の32モデルを共通の初回配備対象とする。
下記の67モデルの調査一覧は採用検討用のカタログであり、初回配備の正本は
`config/test.json`・`config/production.json`の`services.modelDeployments`である。

| 分類 | プロバイダー | 初回配備モデル | 件数 |
|---|---|---|---:|
| チャット・推論 | OpenAI | `gpt-6.1-sol`、`gpt-6-luna`、`gpt-6-sol`、`gpt-6-astra`、`gpt-5.6-luna`、`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.5`、`gpt-5.4-mini`、`gpt-5.4-nano`、`gpt-5.4-pro` | 11 |
| チャット・推論 | DeepSeek | `DeepSeek-V4-Flash`、`DeepSeek-V4-Pro` | 2 |
| チャット・推論 | Mistral AI | `Mistral-Large-3` | 1 |
| チャット・推論 | xAI | `grok-4.7` | 1 |
| 判断・意思決定 | Microsoft | `Microsoft-Decision-1`（配備名：`decision-1`） | 1 |
| リアルタイム音声対話 | OpenAI | `gpt-live-1`、`gpt-realtime-2.1`、`gpt-realtime-2.1-mini` | 3 |
| 音声認識・文字起こし | OpenAI | `gpt-live-transcribe`、`gpt-transcribe` | 2 |
| 画像生成・編集 | OpenAI | `gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`、`gpt-image-2` | 3 |
| 画像生成・編集 | Black Forest Labs | `FLUX.2-flex`、`FLUX.2-pro` | 2 |
| 埋め込み | OpenAI | `text-embedding-3-large`、`text-embedding-3-small` | 2 |
| 埋め込み | Cohere | `embed-v-4-0` | 1 |
| 検索結果の再ランキング | Cohere | `Cohere-rerank-v4.0-fast`、`Cohere-rerank-v4.0-pro` | 2 |
| モデルルーティング | Microsoft | `model-router` | 1 |
| **合計** | | | **32** |

Decisionは判断用の追加採用モデルで、確認した版はPreviewである。
配備版は`1`、SKUは`DataZoneStandard`、利用枠名は
`AIServices.DataZoneStandard.Microsoft-Decision-1`を保持する。
ほかの31モデルは、確認したGA版を`GlobalStandard`で配備する。
初期capacityは`gpt-6.1-sol`が50、`gpt-5.6-luna`が5、ほかが1。
これらは初期割当値であり、配備前に対象サブスクリプションのSKU・利用枠・提供容量を再確認する。

再構築は動作テストと資材改修が完了した後の別試行で行う。既存環境の更新では、
現在のモデルと採用configの差分、既存利用、利用枠を確認して変更対象を決める。
通常のincremental配備では、配列から外した既存モデルの削除は別途実施する。

現行の32モデルを選定した際、候補から次の5モデルを外した。以下は2026-10-27の選定理由であり、現在の提供可否を示すものではない。

| モデル | 選定理由 |
|---|---|
| `qwen3.6-35b-a3b` | 2026-10-27取得のEast US 2カタログで、標準配備に使うSKUを確認できなかった |
| `Muse-Glimmer-30B` | 同カタログで、標準配備に使うSKUを確認できなかった |
| `qwen3-32b` | 同カタログの利用枠が`Qwen3-32B-finetune`であり、標準の未調整モデル配備との適合確認が必要 |
| `text-embedding-ada-002` | 埋め込みは第3世代のlarge・smallとCohereを採用する |
| `gpt-5.4` | 同用途の後継世代とDecisionを採用する |

### モデル配備後のAPI公開

`azd up`は上記32モデルと共通基盤を準備し、Gateway工程でAPIを公開してからPortalを配備する。個別のAPI更新には同じ`deploy_gateway.py`を使う。
初期32モデルに対応するAPI・接続定義に加え、現在の標準APIは文書解析を含め19件ある。文書解析はこの32モデルへの追加配備ではなく、既存FoundryのDocument Intelligenceを使う。[標準API一覧](api-design-proposal.md)
次のモデルは、それぞれ異なる公開契約で扱う。実環境への公開時は、モデル別の要求・応答と上流到達を受入する。

| 対象 | API登録時に確認する内容 |
|---|---|
| `DeepSeek-V4-Flash`・`DeepSeek-V4-Pro`・`Mistral-Large-3`・`grok-4.7` | 対応するChat APIのパス・パラメーター・認証と、モデル別の出力制限 |
| `gpt-live-1` | Live用の接続URL・セッション・イベント形式 |
| `gpt-live-transcribe` | リアルタイム文字起こしの接続URL・イベント形式 |
| `FLUX.2-pro` | pro用のprovider routeと生成・編集の要求形式 |
| `model-router` | RouterのChat API設定とルーティング先の候補・選択方針 |

[API・Operation対応表](api-design-proposal.md#初期32モデルとapioperationの対応)と[組み込むポリシー](apim-policy-catalog.md)を参照する。

Routerの選択先はAzure側のRouter定義で管理される。上記32モデルの配備配列とは別に確認する。

### テキスト生成の出力トークン入力範囲

Portalの出力トークン初期値は、選択したモデルの公開最大値を使用する。数値の下限はAPI方式ごとに異なり、Chat Completionsは1、Responsesは16となる。Responsesの`response`・`pdf`プロファイルには、この下限をAPI定義の`integerLimits.max_output_tokens`で設定し、APIM・Portalの入力欄・サーバー検証・リクエストスキーマへ同じ制約を適用する。推論に使用したトークンも出力枠に含まれるため、最小値では本文の生成前に`incomplete`となる場合がある。

画像・PDFを含むJSON本文（OpenAIのvision/pdfとFLUX.2の生成/編集）の公開上限は、UTF-8エンコード後の要求全体で2,000,000 bytesです。上限を1 byteでも超えるとAPIMが413を返します。WAFは本文検査・容量制限とも2000 KiB（2,048,000 bytes）を維持し、その境界に公開上限を重ねない余裕を取ります。Base64画像だけのサイズではなく、JSON構造・prompt等も含みます。OpenAI画像編集のmultipart上限とは別です。
API例が扱うパス・要求形式は[初期モデルと公開API](platform.md#初期モデルと公開api)を参照する。

## モデル配備とAzureの専用サービス

Foundryは、生成AIモデルとSpeech・Languageなどの専用サービスを利用する基盤となる。
この一覧のモデルを`services.modelDeployments`へ登録する操作と、専用サービスのAPIを使う操作を分けて考える。

| 目的 | 利用するモデル・サービス | 入力 → 出力 | 初回構成での扱い |
|---|---|---|---|
| チャット・推論 | FoundryのGPT、DeepSeek、Mistral、Grokなど | 質問・会話 → 回答 | 選択したモデルを配備する |
| 音声での対話 | Foundryの`gpt-live-1`・`gpt-realtime-2.1`系 | 音声 → 応答音声・テキスト | 選択したモデルを配備し、対応する音声APIを使う |
| OpenAIによる文字起こし | Foundryの`gpt-transcribe`・`gpt-live-transcribe` | 音声ファイル・音声ストリーム → テキスト | ファイル用とリアルタイム用のモデル・APIを使い分ける |
| 音声認識・文字起こし | Azure SpeechのSpeech to text | 音声 → テキスト | Speech APIを使う。上記のOpenAIモデルと用途が重なるため、言語・精度・遅延・料金・APIの要件で選ぶ |
| 文章の読み上げ・TTS | Azure SpeechのText to speech | テキスト・SSML → 音声 | 標準のニューラル音声を使う。音声名・言語・話速などをリクエストで指定する |
| 音声会話アプリの構成 | Azure SpeechのVoice Live | 音声 → 認識・会話処理・音声応答 | Speech側の会話サービスとして接続先・対応モデルを設定する |
| PDF・帳票・画像の読取 | Azure Document IntelligenceのRead・Layout | 文書・画像 → 文字・表・文書構造 | `prebuilt-read`・`prebuilt-layout`をサービスAPIから指定する |
| PII検出・文章分析 | Azure Language | テキスト → 個人情報・分類・感情などの分析結果 | CoreのPII APIはLanguageの機能を使う |

### TTSはAzure Speechを使う

今回の標準TTSはAzure Speechとする。標準のニューラル音声はサービスが提供しており、
`gpt-4o-mini-tts`のモデル配備を用意せずに利用できる。SSMLでは発音・話速・間などを調整できる。
独自の音声を作るCustom Voiceや、独自の音声認識モデルを学習するCustom Speechには、それぞれ追加の準備がある。
標準音声の利用とカスタムモデルの配備は区別する。

Coreの`services.speechEnabled`は、APIMのマネージドIDへSpeech呼出権限を付ける設定である。
SpeechのAPI例にはSSML読み上げとWAV認識がある。Foundryに配備する音声モデルの選択は
`services.modelDeployments`で管理する。

`gpt-live-1`というモデル名と、Azure SpeechのVoice Liveというサービス名は別の接続契約を表す。
モデル名が似ていても、接続URL・認証・イベント形式は利用するAPIの仕様を確認する。
`gpt-live-transcribe`のリアルタイム文字起こしも、音声ファイルを送るTranscriptions APIとは別に接続仕様を確認する。

### OCRはAzure Document Intelligenceを使う

文書読取はAzure Document Intelligenceを使う。Readは印刷文字・手書き文字の抽出、
Layoutは文字に加えて表・文書構造の抽出を扱う。サービス内の事前構築済みモデルIDを指定して利用するため、
このカタログのMistral OCRモデルをFoundryへ配備する操作とは分けて管理する。

Coreの標準API定義には、既存Foundryを使う`document-analysis-v1`を含む。
`POST /tools/documents/v1/analyze?model=prebuilt-read|prebuilt-layout`へPDF・画像のバイナリを送り、文字・ページ・表などの解析結果を受け取る。Gatewayが非同期処理の完了確認を担当し、Portalからは文書ファイルを選んで試せる。[入力上限・権限・配備と応答仕様](document-analysis.md)
Languageは、OCRで文字を抽出した後のテキスト分析にも利用できる。

### 配備数の上限

現在のCoreはFoundryリソースを1つ作り、その配下へモデルを配備する。
Coreの設定Schemaと入力検査は`services.modelDeployments`を最大32件に制限する。導入先のAzure上限・利用枠は配備前に別途確認する。標準Speech音声やDocument Intelligenceの
事前構築済みモデルIDをAPIで指定することは、この`services.modelDeployments`配列への追加とは別の操作となる。
32件を使い切った場合は、モデルを追加する前に配備の入れ替えかFoundryの追加を計画する。

公式資料：

- [Foundryの上限](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/quotas-limits)
- [Azure Speechの概要](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/overview)
- [Text to speech](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/text-to-speech)
- [Document Intelligenceの概要](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/overview)
- [Read OCRモデル](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/read)
- [Azure Languageの概要](https://learn.microsoft.com/en-us/azure/ai-services/language-service/overview)

## 並び順と公開日の基準

1. 用途で分類し、分類内をプロバイダーでまとめる。
2. 各プロバイダー内を、表の公開日が新しい順に並べる。同日はモデルID順とする。
3. 公開日は、提供元のAPI公開日または重み公開日を優先する。OpenAIのAPIモデルはAPIの公開履歴を用い、ChatGPT／Codex製品内での先行提供日と区別する。Whisperは重み公開日を用いる。
4. 提供元の公開日を確定できず、Azureの公開発表・Preview提供開始日を確認できた場合は、その日を用い、区分に「Azure公開」と明記する。Azureでの公開日と提供元の公開日はそれぞれの提供経路を示す。
5. 日付を確定できないモデルは「未確認」とし、そのプロバイダーの末尾にまとめる。追加調査時に日付と出典を補う。

**公開日を確認できた59モデル、日付未確認8モデル。** 日付はリンク先の公式公開履歴・発表・モデル資料に基づく。モデルのバージョン文字列、Azure Models APIの`systemData.createdAt`、資料の最終更新日は、それぞれ版・登録・文書更新の日時として区別する。

同じモデル名のスナップショット更新は、初回のモデル公開日とは別に扱う。たとえば`gpt-realtime-mini`と`gpt-audio-mini`は2025-10-06公開で、2025-12-15に更新版が公開された。`gpt-realtime-2.1`系は提供元APIの公開履歴が2026-07-06、Foundryのモデル版が2026-07-07のため、表には提供元APIの公開日を使う。

品質指数と速度は取得したベンチマークの参考値。推論設定・入力・出力・測定環境が変わると実際の品質や速度も変わる。コード系の品質指数は総合評価として扱う。

## 分類別一覧

| 分類 | モデル数 |
|---|---:|
| チャット・推論 | 30 |
| コード生成・開発支援 | 6 |
| リアルタイム音声対話 | 6 |
| 音声入出力・Chat Completions | 3 |
| 音声認識・文字起こし | 6 |
| 音声合成・TTS | 1 |
| 画像生成・編集 | 7 |
| 文書読取・OCR | 1 |
| 埋め込み | 4 |
| 検索結果の再ランキング | 2 |
| モデルルーティング | 1 |
| **合計** | **67** |

### チャット・推論（30モデル）

| プロバイダー | モデル | 公開日 ↓ | 公開日の区分・出典 | 品質指数（参考） | 速度［トークン/秒］（参考） |
|---|---|---|---|---:|---:|
| OpenAI | `gpt-6.1-sol` | [2026-09-29][openai-changelog] | 提供元API | 0.85 | 65 |
| OpenAI | `gpt-6-luna` | [2026-09-22][openai-changelog] | 提供元API | 0.79 | 198 |
| OpenAI | `gpt-6-sol` | [2026-09-22][openai-changelog] | 提供元API | 0.83 | 93 |
| OpenAI | `gpt-6-astra` | [2026-09-03][openai-changelog] | 提供元API | 0.86 | 54 |
| OpenAI | `gpt-5.6-luna` | [2026-07-09][openai-changelog] | 提供元API | 0.79 | 69 |
| OpenAI | `gpt-5.6-sol` | [2026-07-09][openai-changelog] | 提供元API | 0.82 | 20 |
| OpenAI | `gpt-5.6-terra` | [2026-07-09][openai-changelog] | 提供元API | 0.82 | 69 |
| OpenAI | `gpt-5.5` | [2026-04-24][openai-changelog] | 提供元API | 0.82 | 50 |
| OpenAI | `gpt-5.4-mini` | [2026-03-17][openai-changelog] | 提供元API | 0.67 | 142 |
| OpenAI | `gpt-5.4-nano` | [2026-03-17][openai-changelog] | 提供元API | 0.64 | 177 |
| OpenAI | `gpt-5.4` | [2026-03-05][openai-changelog] | 提供元API | 0.81 | 21 |
| OpenAI | `gpt-5.4-pro` | [2026-03-05][openai-changelog] | 提供元API | 未掲載 | — |
| OpenAI | `gpt-5.2` | [2025-12-11][openai-changelog] | 提供元API | 0.78 | 60 |
| OpenAI | `gpt-5.1` | [2025-11-13][openai-changelog] | 提供元API | 0.75 | 76 |
| OpenAI | `gpt-5-pro` | [2025-10-06][openai-changelog] | 提供元API | 未掲載 | — |
| OpenAI | `gpt-5` | [2025-08-07][openai-changelog] | 提供元API | 0.74 | 69 |
| OpenAI | `gpt-5-mini` | [2025-08-07][openai-changelog] | 提供元API | 未掲載 | — |
| OpenAI | `gpt-5-nano` | [2025-08-07][openai-changelog] | 提供元API | 0.53 | 224 |
| DeepSeek | `DeepSeek-V4-Flash` | [2026-04-30][ms-deepseek4] | Azure公開発表 | 0.72 | 91 |
| DeepSeek | `DeepSeek-V4-Pro` | [2026-04-30][ms-deepseek4] | Azure公開発表 | 0.78 | 84 |
| DeepSeek | `DeepSeek-V3.2` | [2025-12-15][ms-deepseek32] | Azure公開（Preview） | 未掲載 | — |
| DeepSeek | `DeepSeek-V3.2-Speciale` | [2025-12-15][ms-deepseek32] | Azure公開（Preview） | 未掲載 | — |
| Alibaba | `qwen3.6-35b-a3b` | [2026-04-15][qwen36] | 提供元の重み公開 | 未掲載 | — |
| Alibaba | `qwen3-32b` | [2025-04-29][qwen3] | 提供元の重み公開発表 | 未掲載 | — |
| Alibaba | `qwen3.8-27b` | 未確認 | 公式モデルカードを確認。取得した発表記事はQwen3.8-Maxが対象のため、27Bの公開日は追加確認 | 未掲載 | — |
| Meta | `Muse-Glimmer-30B` | 未確認 | 公式モデル情報の公開日を追加確認 | 未掲載 | — |
| Mistral AI | `Mistral-Large-3` | [2025-12-02][ms-mistral] | Azure公開（Preview） | 未掲載 | — |
| xAI | `grok-4-1-fast-non-reasoning` | 未確認 | 提供元の公開日を追加確認 | 未掲載 | — |
| xAI | `grok-4-1-fast-reasoning` | 未確認 | 提供元の公開日を追加確認 | 未掲載 | — |
| xAI | `grok-4.7` | 未確認 | 提供元の公開日を追加確認 | 未掲載 | — |

### コード生成・開発支援（6モデル）

| プロバイダー | モデル | 公開日 ↓ | 公開日の区分・出典 | 品質指数（参考） | 速度［トークン/秒］（参考） |
|---|---|---|---|---:|---:|
| OpenAI | `gpt-5.3-codex` | [2026-02-24][openai-changelog] | 提供元API | 0.68 | 23 |
| OpenAI | `gpt-5.2-codex` | [2026-01-14][openai-changelog] | 提供元API | 0.75 | 32 |
| OpenAI | `gpt-5.1-codex-max` | [2025-12-04][openai-changelog] | 提供元API | 0.64 | 86 |
| OpenAI | `gpt-5.1-codex` | [2025-11-13][openai-changelog] | 提供元API | 0.74 | 33 |
| OpenAI | `gpt-5.1-codex-mini` | [2025-11-13][openai-changelog] | 提供元API | 0.68 | 43 |
| OpenAI | `gpt-5-codex` | [2025-09-23][openai-changelog] | 提供元API | 未掲載 | — |

### リアルタイム音声対話（6モデル）

| プロバイダー | モデル | 公開日 ↓ | 公開日の区分・出典 | 評価・用途 |
|---|---|---|---|---|
| OpenAI | `gpt-live-1` | [2026-09-10][openai-changelog] | 提供元API（GA） | リアルタイム音声会話 |
| OpenAI | `gpt-realtime-2.1` | [2026-07-06][openai-changelog] | 提供元API | リアルタイム音声会話 |
| OpenAI | `gpt-realtime-2.1-mini` | [2026-07-06][openai-changelog] | 提供元API | リアルタイム音声会話 |
| OpenAI | `gpt-realtime-1.5` | [2026-02-23][openai-changelog] | 提供元API | リアルタイム音声会話 |
| OpenAI | `gpt-realtime-mini` | [2025-10-06][openai-changelog] | 提供元API | リアルタイム音声会話 |
| OpenAI | `gpt-realtime` | [2025-08-28][openai-realtime] | 提供元API | リアルタイム音声会話 |

### 音声入出力・Chat Completions（3モデル）

| プロバイダー | モデル | 公開日 ↓ | 公開日の区分・出典 | 評価・用途 |
|---|---|---|---|---|
| OpenAI | `gpt-audio-1.5` | [2026-02-23][openai-changelog] | 提供元API | 音声を含むチャット・音声生成 |
| OpenAI | `gpt-audio-mini` | [2025-10-06][openai-changelog] | 提供元API | 音声を含むチャット・音声生成 |
| OpenAI | `gpt-audio` | 未確認 | Azure版は2025-08-28。提供元の公開日は追加確認 | 音声を含むチャット・音声生成 |

### 音声認識・文字起こし（6モデル）

| プロバイダー | モデル | 公開日 ↓ | 公開日の区分・出典 | 評価・用途 |
|---|---|---|---|---|
| OpenAI | `gpt-live-transcribe` | [2026-07-28][openai-changelog] | 提供元API | リアルタイム文字起こし |
| OpenAI | `gpt-transcribe` | [2026-07-28][openai-changelog] | 提供元API | 文字起こし |
| OpenAI | `gpt-4o-mini-transcribe` | [2025-03-20][openai-audio] | 提供元API | 文字起こし |
| OpenAI | `gpt-4o-transcribe` | [2025-03-20][openai-audio] | 提供元API | 文字起こし |
| OpenAI | `whisper` | [2022-09-21][openai-whisper] | 提供元の重み公開 | 音声認識 |
| OpenAI | `gpt-4o-transcribe-diarize` | 未確認 | Azure版は2025-10-15。提供元の公開日は追加確認 | 話者分離付き文字起こし |

### 音声合成・TTS（1モデル）

| プロバイダー | モデル | 公開日 ↓ | 公開日の区分・出典 | 評価・用途 |
|---|---|---|---|---|
| OpenAI | `gpt-4o-mini-tts` | [2025-03-20][openai-audio] | 提供元API | テキスト読み上げ |

### 画像生成・編集（7モデル）

| プロバイダー | モデル | 公開日 ↓ | 公開日の区分・出典 | 評価・用途 |
|---|---|---|---|---|
| OpenAI | `gpt-image-2.5-flare` | [2026-09-08][openai-changelog] | 提供元API | 公式位置付け：速度と品質の両立 |
| OpenAI | `gpt-image-2.5-sunburst` | [2026-09-08][openai-changelog] | 提供元API | 公式位置付け：編集精度重視 |
| OpenAI | `gpt-image-2` | [2026-04-21][openai-changelog] | 提供元API | 画像生成・編集 |
| OpenAI | `gpt-image-1.5` | [2025-12-16][openai-changelog] | 提供元API | 画像生成・編集 |
| OpenAI | `gpt-image-1-mini` | [2025-10-06][openai-changelog] | 提供元API | 画像生成 |
| Black Forest Labs | `FLUX.2-flex` | [2025-11-25][bfl-release] | 提供元API | 画像生成・編集、生成設定の調整 |
| Black Forest Labs | `FLUX.2-pro` | [2025-11-25][bfl-release] | 提供元API | 画像生成・編集、複数画像の参照 |

Sunburst／Flareの位置付けは[OpenAI画像生成ガイド](https://developers.openai.com/api/docs/guides/image-generation)を参照。

### 文書読取・OCR（1モデル）

| プロバイダー | モデル | 公開日 ↓ | 公開日の区分・出典 | 評価・用途 |
|---|---|---|---|---|
| Mistral AI | `mistral-document-ai-2512` | 未確認 | このモデルIDに対応する公開日を追加確認 | 文書・画像からの文字や構造の抽出 |

### 埋め込み（4モデル）

| プロバイダー | モデル | 公開日 ↓ | 公開日の区分・出典 | 評価・用途 |
|---|---|---|---|---|
| OpenAI | `text-embedding-3-large` | [2024-01-25][openai-embedding3] | 提供元API | 公式MTEB：64.6% |
| OpenAI | `text-embedding-3-small` | [2024-01-25][openai-embedding3] | 提供元API | 公式MTEB：62.3% |
| OpenAI | `text-embedding-ada-002` | [2022-12-15][openai-ada] | 提供元API | 公式MTEB：61.0% |
| Cohere | `embed-v-4-0` | [2025-04-15][cohere-embed] | 提供元API | 検索用の埋め込み。今回の資料では同条件の比較値なし |

OpenAIのMTEB値は[公式埋め込みガイド](https://developers.openai.com/api/docs/guides/embeddings)を参照。Cohereとの順位比較は同条件の評価を追加して判断する。

### 検索結果の再ランキング（2モデル）

| プロバイダー | モデル | 公開日 ↓ | 公開日の区分・出典 | 評価・用途 |
|---|---|---|---|---|
| Cohere | `Cohere-rerank-v4.0-fast` | [2025-12-11][cohere-rerank] | 提供元API | 検索結果を関連度で並べ替える |
| Cohere | `Cohere-rerank-v4.0-pro` | [2025-12-11][cohere-rerank] | 提供元API | 検索結果を関連度で並べ替える |

両モデルは同じ2025-12-11のAPI公開発表に記載されている。

### モデルルーティング（1モデル）

| プロバイダー | モデル | 公開日 ↓ | 公開日の区分・出典 | 評価・用途 |
|---|---|---|---|---|
| Microsoft | `model-router` | [2025-05-19][ms-router] | Azure公開発表（初版） | リクエストに応じてモデルを選択。品質は選択先モデルに依存 |

## 日付を追加確認するモデル

- `qwen3.8-27b`：公式モデルカードと公式ブログを照合したが、リンク先の記事はQwen3.8-Maxを対象としていた。27Bの公開日を個別に確認する。
- `Muse-Glimmer-30B`：今回取得できた公式モデル資料では、初回公開日を確定できなかった。
- `grok-4-1-fast-non-reasoning`・`grok-4-1-fast-reasoning`・`grok-4.7`：提供元の公開履歴を確認する。今回の調査環境では提供元サイトへの接続に制限があった。
- `mistral-document-ai-2512`：このモデルIDに対応する提供元の公開日を確認する。今回の調査環境では提供元サイトへの接続に制限があった。
- `gpt-4o-transcribe-diarize`：公式モデルページとAzureのモデル版2025-10-15を確認した。提供元の初回公開日を確定する資料を追加確認する。
- `gpt-audio`：Azureのモデル版2025-08-28を確認した。同日のGPT-Realtime公開発表にはこのモデルの初回公開日の明記がないため、個別の公開履歴を追加確認する。

参照先：[Qwen3.8-27B公式モデルカード](https://huggingface.co/Qwen/Qwen3.8-27B)、[GPT-4o Transcribe Diarize公式モデルページ](https://developers.openai.com/api/docs/models/gpt-4o-transcribe-diarize)、[MicrosoftのGrokモデル資料](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-grok)、[Azureモデル一覧][azure-models]。

## 配置可否の追加確認

今回のEast US 2のModels APIでは、次の3モデルは配置用SKUが空だった。

- `Muse-Glimmer-30B`
- `qwen3.8-27b`
- `qwen3.6-35b-a3b`

これらはカタログ掲載とGA版の存在までを確認した候補として扱い、実際の配置方法・提供状況を追加確認する。採用モデルが決まったら、配置先リージョン・SKU・クォータ・利用条件を照合し、構築用configのモデル定義へ反映する。

## 更新方法

1. Foundryのモデル一覧とLeaderboardを同じ調査日で取得する。
2. 公式終了予定表を確認し、配置先リージョンのModels APIでモデルID・バージョン・ライフサイクル・終了日・SKUを取得する。
3. モデルIDで突き合わせ、GA版と旧版の情報を分けて分類・性能値を更新する。
4. 提供元の公開履歴・公式発表からAPI／重み公開日を確認し、確認できたAzure公開日と区分を分けて記載する。各プロバイダー内を新しい順に並べ、日付未確認は末尾へまとめる。
5. 採用するバージョンとSKUを決定し、クォータや提供条件を確認する。

~~~sh
az cognitiveservices model list \
  --subscription "<subscription-id>" \
  --location "<location>" \
  --output json
~~~

取得した生の応答は、導入先のアクセス管理された場所で保管する。公開文書にはモデル名・評価値・調査条件・公開情報を整理して掲載する。

## 出典

- Foundryモデルカタログ：調査時に取得した一般提供67モデルの一覧。
- Foundry Leaderboard：調査時に取得した49モデルのベンチマーク。品質指数・スループットを参照。
- [Microsoft FoundryモデルのライフサイクルとModels API](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)
- [Microsoft Foundryモデルの終了予定表](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/model-lifecycle-retirement)
- [Azureから直接提供されるモデルと機能](https://learn.microsoft.com/en-us/azure/ai-foundry/foundry-models/concepts/models-sold-directly-by-azure)
- [OpenAIモデル一覧](https://developers.openai.com/api/docs/models)
- [OpenAI画像生成ガイド](https://developers.openai.com/api/docs/guides/image-generation)
- [OpenAI埋め込みガイド](https://developers.openai.com/api/docs/guides/embeddings)

### 公開日の出典

- [OpenAI API公開履歴][openai-changelog]
- [GPT-Realtime／Realtime API公開発表][openai-realtime]
- [OpenAI音声モデルの公開発表][openai-audio]
- [Whisper重み公開発表][openai-whisper]
- [Embedding 3公開発表][openai-embedding3]
- [Ada 002公開発表][openai-ada]
- [Qwen3.6-35B-A3B公式公開発表][qwen36]
- [Qwen3公式公開発表][qwen3]
- [Black Forest Labs公開履歴][bfl-release]
- [Cohere Embed 4公開発表][cohere-embed]
- [Cohere Rerank 4公開履歴][cohere-rerank]
- [Microsoft Model Router初回公開発表][ms-router]
- [DeepSeek V4のAzure公開発表][ms-deepseek4]
- [DeepSeek V3.2のAzure公開発表][ms-deepseek32]
- [Mistral Large 3のAzure公開発表][ms-mistral]

[openai-changelog]: https://developers.openai.com/api/docs/changelog
[openai-realtime]: https://openai.com/index/introducing-gpt-realtime/
[openai-audio]: https://openai.com/index/introducing-our-next-generation-audio-models/
[openai-whisper]: https://openai.com/index/whisper/
[openai-embedding3]: https://openai.com/index/new-embedding-models-and-api-updates/
[openai-ada]: https://openai.com/index/new-and-improved-embedding-model/
[qwen36]: https://qwen.ai/blog?id=qwen3.6-35b-a3b
[qwen3]: https://qwenlm.github.io/blog/qwen3/
[bfl-release]: https://docs.bfl.ai/release-notes#november-25%2C-2025
[cohere-embed]: https://cohere.com/blog/embed-4
[cohere-rerank]: https://docs.cohere.com/v2/changelog/rerank-v4.0
[ms-router]: https://devblogs.microsoft.com/foundry/announcing-developer-essentials-for-agents-and-apps-in-azure-ai-foundry/
[ms-deepseek4]: https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/introducing-deepseek-v4-flash-and-v4-pro-in-microsoft-foundry/4515174
[ms-deepseek32]: https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/introducing-deepseek-v3-2-and-deepseek-v3-2-speciale-in-microsoft-foundry/4477549
[ms-mistral]: https://azure.microsoft.com/en-us/blog/introducing-mistral-large-3-in-microsoft-foundry-open-capable-and-ready-for-production-workloads/
[azure-models]: https://learn.microsoft.com/en-us/azure/ai-foundry/foundry-models/concepts/models-sold-directly-by-azure
