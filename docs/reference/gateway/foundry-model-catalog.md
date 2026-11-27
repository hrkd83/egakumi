# Foundryの初期配備モデル

初回配備の正本は `infra/config/test.json`・`infra/config/production.json` の `services.modelDeployments` です。配備先でモデルの版・リージョン・SKU・利用枠を確認してから構築します。 [モデルの変更・追加](../../operations/foundry.md)

## 初回配備する28モデル

テスト用・本番用の`config`とAPI例は、次の28モデルを共通の初回配備対象とする。
下記の67モデルの調査一覧は採用検討用のカタログであり、初回配備の正本は
`infra/config/test.json`・`infra/config/production.json`の`services.modelDeployments`である。

| 分類 | プロバイダー | 初回配備モデル | 件数 |
|---|---|---|---:|
| チャット・推論 | OpenAI | `gpt-6.1-sol`、`gpt-6-luna`、`gpt-6-sol`、`gpt-6-astra`、`gpt-5.6-luna`、`gpt-5.6-sol`、`gpt-5.6-terra` | 7 |
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
| **合計** | | | **28** |

Decisionは判断用のモデルである。
配備版は`1`、SKUは`DataZoneStandard`、利用枠名は
`AIServices.DataZoneStandard.Microsoft-Decision-1`を保持する。
ほかの27モデルは`GlobalStandard`で配備する。
初期capacityは`gpt-6.1-sol`が50、`gpt-5.6-luna`が5、ほかが1。
これらは初期割当値であり、配備前に対象サブスクリプションのSKU・利用枠・提供容量を再確認する。

### モデル配備後のAPI公開

`azd up`で上記28モデルと共通基盤を準備する。Entra登録後のGateway個別配備でAPIを公開し、続いてPortalを個別配備する。個別のAPI更新には同じ`deploy_gateway.py`を使う。
初期28モデルに対応するAPI・接続定義に加え、現在の標準APIは文書解析を含め19件ある。文書解析はこの28モデルへの追加配備ではなく、既存FoundryのDocument Intelligenceを使う。[標準API一覧](api-contracts.md)
次のモデルは、それぞれ異なる公開契約で扱う。実環境への公開時は、モデル別の要求・応答と上流到達を受入する。

| 対象 | API登録時に確認する内容 |
|---|---|
| `DeepSeek-V4-Flash`・`DeepSeek-V4-Pro`・`Mistral-Large-3`・`grok-4.7` | 対応するChat APIのパス・パラメーター・認証と、モデル別の出力制限 |
| `gpt-live-1` | Live用の接続URL・セッション・イベント形式 |
| `gpt-live-transcribe` | リアルタイム文字起こしの接続URL・イベント形式 |
| `FLUX.2-pro` | pro用のprovider routeと生成・編集の要求形式 |
| `model-router` | RouterのChat API設定とルーティング先の候補・選択方針 |

[API・Operation対応表](api-contracts.md#初期28モデルとapioperationの対応)と[組み込むポリシー](apim-policy-catalog.md)を参照する。

Routerの選択先はAzure側のRouter定義で管理される。上記28モデルの配備配列とは別に確認する。

### テキスト生成の出力トークン入力範囲

Gatewayの出力トークン項目は省略でき、指定時はモデル別の公表上限を検査する。Portalは公表値があるモデルの初期値にその最大値を使い、指定時の下限はChat Completionsが1、Responsesが16となる。API定義での独自の必須指定は撤廃している。推論に使用したトークンも出力枠に含まれるため、最小値では本文の生成前に`incomplete`となる場合がある。

テキスト・画像・PDFを含む通常AI要求の受付枠は、APIM Classicの技術上限500 MiB（524,288,000 bytes）です。JSONはBase64・prompt・構造、multipartは境界・付随項目も含みます。WAFは対象AIパスだけ先頭2000 KiBを検査し、超過部分は検査せず受け付けます。実際に利用できる容量・枚数・寸法・品質は提供元のモデル・API・料金プランにも従います。

画像・音声・文書の入力と出力は、各バックエンドの契約とGatewayの公開条件を確認する。 [APIMの制御](apim-policy-catalog.md) · [文書解析の非同期契約](document-analysis.md)
API例が扱うパス・要求形式は[初期モデルと公開API](api-contracts.md#初期28モデルとapioperationの対応)を参照する。

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

### Voice Liveの会話モデル

Voice Liveの通常WSSとWebRTC制御は、[公開bindings](../../../apps/api-gateway/public-bindings.json)で`gpt-6.1-sol`を選ぶ。Voice Liveが管理するモデルを使う。 [対応モデル表](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live)

通常WSSはAPI版`2026-04-10`、WebRTC制御の`/voice-live/realtime/calls`は`2026-01-01-preview`を使う。数値の`max_response_output_tokens`は1〜4,096、モデル最大を選ぶ場合は`"inf"`を送る。モデル仕様の出力最大128,000と、この数値指定の範囲を区別する。[通常WSSの仕様](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-api-reference-2026-04-10)、[WebRTC制御の仕様](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-webrtc)

Voice Liveの処理地域は、接続方式とモデルを選んだ上で確認する。 [公式の地域表](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/regions?tabs=voice-live) · [音声の受入試験](../../testing/integration-tests.md)

### OCRはAzure Document Intelligenceを使う

文書読取はAzure Document Intelligenceを使う。Readは印刷文字・手書き文字の抽出、
Layoutは文字に加えて表・文書構造の抽出を扱う。サービス内の事前構築済みモデルIDを指定して利用するため、
このカタログのMistral OCRモデルをFoundryへ配備する操作とは分けて管理する。

Coreの標準API定義には、既存Foundryを使う`document-analysis-v1`を含む。
`POST /tools/documents/v1/analyze?model=<PROVIDER_MODEL_ID>`へ原本または提供元のJSON入力を送り、202受付後に状態GETで文字・ページ・表などの解析結果を取得する。モデルの利用可否は提供元が判定する。Portalの試用ではReadとLayoutを選べる。 [入力上限・権限・配備と応答仕様](document-analysis.md)
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
