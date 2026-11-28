# Core API・Operation定義

初期配備28モデルとPII・Graph・文書解析を提供する**標準19 APIの登録定義**を説明する。正本は [API別JSON](../../../apps/api-gateway/apis/)と [モデル設定](../../../infra/config/test.json)。初回は`azd up`で共通基盤を構築し、Entra登録後にGatewayとPortalを個別に配備する。登録後の実通信・受入判定は2つのテスト仕様書で別途記録する。ポリシーの適用順は[APIMポリシー一覧](apim-policy-catalog.md)を参照する。

入口認可と本人接続の分担は[Gatewayの認証と購読キー](../identity/gateway-authentication.md)を参照する。現行の標準19 APIでは、共通入口の購読キー検証と、個人向けAPIの本人OAuth接続を分けて設計する。

## 現在の公開定義

初期28モデルとPII・Graph・Document Analysisを19 APIで提供する。Graphの本人情報は1つのAPIで取得する。BearerはOBO、購読キーはCredential Manager本人接続を使う。文書解析はモデル配備とは別のFoundryサービスを使い、独立したAPIとして登録する。

| 数える対象 | 定義数 |
|---|---:|
| HTTP API | 13 |
| HTTP Operation（method＋route） | 20 |
| WebSocket API | 6 |
| API登録定義の合計 | **19** |
| 操作定義で扱う初期配備モデル | **28** |

上表はリポジトリの標準API定義の件数である。Azureに登録済みの件数とは区別する。WebSocketの`invoke`／`onHandshake`は接続時の代表操作として扱い、HTTP Operationの件数には含めない。PII生成MCPと、採用時のCM補助API2個は別計上する。

Live会話・リアルタイム文字起こしの3 APIは、Microsoft公式のAzure用接続先に対応する。公開前に、実環境の上流URL・MI権限・ネットワーク到達と音声イベントの受入を確認する。

## APIとOperationの分類思想

APIは引き続き、**公開契約 × 公開major version × 通信方式 × 権限の意味**で分割する。

| 階層 | 何を区別するか | 例 |
|---|---|---|
| API | 要求・応答・認証境界・通信方式としてまとまった公開契約 | OpenAI互換推論、Cohere、FLUX、Realtime WebSocket |
| Operation | HTTPメソッドとルートで表す操作 | Chat Completions、Responses、画像生成、画像編集 |
| 操作設定 | Operationの基本設定とモデル名別の差分。クライアントはモデルを指定する | 出力token項目、モデル別の固定上流URI |
| モデル配備 | 実行する配備名・固定版・SKU・容量 | `gpt-6.1-sol`、`DeepSeek-V4-Pro`、`FLUX.2-pro` |
| Product | APIを提供・購読するまとまり | `released-apis`、`model-users`、`pii-users`、`connection-users` |

OpenAI互換のChat要求は、既存の`openai-inference-v1`へまとめる。このAPI名のOpenAIは公開プロトコルを表す。DeepSeek・Mistral・Grok・Routerの追加は、そのChat Operationの許可モデルとモデル別設定の追加として扱う。[AzureのOpenAI互換v1 API](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle)

同じ要求・応答形式のモデル差は、Operation内のモデル選択・モデル別設定・固定ルーティング表へ寄せる。独立した会話セッション契約を持つLiveとRealtimeは、APIを分ける。WebSocket内のイベントは、その接続契約のメッセージとして管理する。

```mermaid
flowchart TD
    API[OpenAI互換推論 API] --> Chat[Chat Completions Operation]
    API --> Responses[Responses Operation]
    Chat --> Text[操作の基本設定とモデル別差分]
    Responses --> Input[操作の基本設定とモデル別差分]
    Text --> Models[許可した配備名を選択]
    Input --> Models
```

## 標準API一覧

完成URLは`scheme://gateway.example.com`＋URLプレフィックス＋Operationのサフィックス。HTTPは`https`、WebSocketは`wss`を使う。表のprefixは先頭`/`付きで示すが、API定義JSONの `path`には先頭`/`を付けない。`v1`はGatewayの公開契約版であり、上流API版・モデル版は接続定義で管理する。

### HTTP API：13件・20 Operation

| # | API識別子 | URLプレフィックス | Operation：method＋サフィックス | 主な契約 |
|---|---|---|---|---|
| 1 | `openai-inference-v1` | `/models/openai/v1` | `chat-completions`：`POST /chat/completions`<br>`responses-create`：`POST /responses` | ChatはDeepSeek・Mistral・Grok・Routerにも対応。操作とモデルで上流を選択 |
| 2 | `openai-embeddings-v1` | `/models/openai-embeddings/v1` | `embeddings`：`POST /embeddings` | OpenAI埋め込み2モデルを維持 |
| 3 | `cohere-inference-v1` | `/models/cohere/v1` | `cohere-embeddings`：`POST /embed`<br>`rerank`：`POST /rerank` | Embed v4とRerank fast／proを扱う |
| 4 | `openai-images-v1` | `/models/openai-images/v1` | `image-generate`：`POST /generations`<br>`image-edit`：`POST /deployments/{deployment}/edits` | GPT Imageの3モデルを共通操作で扱う |
| 5 | `blackforest-images-v1` | `/models/blackforestlabs/v1` | `flux2-generate`：`POST /flux-2/generations`<br>`flux2-edit`：`POST /flux-2/edits` | flex／proを同じ操作にまとめ、モデル別に上流を選択 |
| 6 | `azure-speech-v1` | `/models/azure-speech/v1` | `tts`：`POST /synthesize`<br>`stt`：`POST /recognize` | SpeechのSSML読み上げ・WAV認識を維持 |
| 7 | `openai-audio-v1` | `/models/openai-audio/v1` | `audio-transcriptions`：`POST /deployments/{deployment}/transcriptions` | ファイル文字起こしに`gpt-transcribe`を使用 |
| 8 | `microsoft-decision-v1` | `/models/microsoft-decision/v1` | `systemone`：`POST /systemone` | `decision-1`の判断APIを維持 |
| 9 | `openai-realtime-control-v1` | `/models/openai-realtime/v1` | `realtime-create`：`POST /calls`<br>`realtime-hangup`：`POST /calls/{callId}/hangup` | RealtimeのWebRTC接続準備・終了と所有者照合を維持 |
| 10 | **`openai-live-control-v1`** | **`/models/openai-live/v1`** | **`live-create`：`POST /sessions`** | **GPT-LiveのWebRTCセッション作成** |
| 11 | `pii-v1` | `/tools/pii/v1` | `pii_redact_text`：`POST /redact` | Azure LanguageのPII処理を維持 |
| 12 | `graph-profile-v1` | `/tools/graph/v1` | `graph_profile_self`：`GET /me` | 本人のGraph属性を取得。BearerはOBO、購読キーはCM |
| 13 | `document-analysis-v1` | `/tools/documents/v1` | `analyze_document`：`POST /analyze?model=...`<br>`get_document_operation`：`GET /operations/{operationId}` | PDF・画像から文字、レイアウト、表を抽出。受付後に状態と結果を取得 |

`{deployment}`・`{callId}`はURL変数。HTTPの通常応答とSSEは同じOperationの応答方式として扱う。

### WebSocket API：6件

| # | API識別子 | 接続先プレフィックス | セッション・モデル | 主な契約 |
|---|---|---|---|---|
| 1 | `openai-realtime-ws-v1` | `/models/openai-realtime/v1/ws` | Realtime通常／mini | 現行のmodelクエリー選択を維持 |
| 2 | `openai-realtime-observer-v1` | `/models/openai-realtime/v1/observer` | 所有するRealtime callのsideband | 所有者ticketとcall IDの照合を維持 |
| 3 | `azure-voice-live-ws-v1` | `/models/voice-live/v1/ws` | Azure Speech Voice Live | サービス固有の接続定義を維持 |
| 4 | `azure-voice-live-webrtc-v1` | `/models/voice-live/v1/webrtc` | Voice LiveのWebRTC制御 | 通常WSSと異なる制御用接続を維持 |
| 5 | **`openai-live-ws-v1`** | **`/models/openai-live/v1/ws`** | **`gpt-live-1`のLiveセッション** | **Live固有の開始・終了・音声イベントを扱う** |
| 6 | **`openai-transcription-ws-v1`** | **`/models/openai-transcription/v1/ws`** | **`gpt-live-transcribe`の文字起こしセッション** | **認識途中・確定テキストを返す** |

接続先はprefix自体である。APIMの`onHandshake`はこの接続時に実行され、音声・認識・終了イベントは接続後のメッセージとなる。

### Live・文字起こしAPIの完成URL

| API | method／方式 | 完成URL |
|---|---|---|
| `openai-live-control-v1` | POST | `https://gateway.example.com/models/openai-live/v1/sessions` |
| `openai-live-ws-v1` | WebSocket | `wss://gateway.example.com/models/openai-live/v1/ws` |
| `openai-transcription-ws-v1` | WebSocket | `wss://gateway.example.com/models/openai-transcription/v1/ws` |

## 初期28モデルとAPI・Operationの対応

表のモデル名は、この環境で使う配備名を表す。固定版・SKU・容量の正本は`services.modelDeployments`、モデルの選定理由は[モデル一覧](foundry-model-catalog.md#初回配備する28モデル)を参照する。

| # | 配備名 | API識別子 | 操作・選択方法 |
|---|---|---|---|
| 1 | `gpt-6.1-sol` | `openai-inference-v1` | Chat／Responses。既存のvision／pdfも使用 |
| 2 | `gpt-6-luna` | `openai-inference-v1` | Chat／Responses |
| 3 | `gpt-6-sol` | `openai-inference-v1` | Chat／Responses |
| 4 | `gpt-6-astra` | `openai-inference-v1` | Chat／Responses |
| 5 | `gpt-5.6-luna` | `openai-inference-v1` | Chat／Responses |
| 6 | `gpt-5.6-sol` | `openai-inference-v1` | Chat／Responses |
| 7 | `gpt-5.6-terra` | `openai-inference-v1` | Chat／Responses |
| 8 | `DeepSeek-V4-Flash` | `openai-inference-v1` | Chatのモデル別出力項目`max_tokens` |
| 9 | `DeepSeek-V4-Pro` | `openai-inference-v1` | Chatのモデル別出力項目`max_tokens` |
| 10 | `Mistral-Large-3` | `openai-inference-v1` | Chatのモデル別出力項目`max_tokens` |
| 11 | `grok-4.7` | `openai-inference-v1` | Chatのモデル別出力項目`max_tokens` |
| 12 | `gpt-live-1` | `openai-live-control-v1`・`openai-live-ws-v1` | Liveセッション作成／接続 |
| 13 | `gpt-realtime-2.1` | `openai-realtime-control-v1`・`openai-realtime-ws-v1`・`openai-realtime-observer-v1` | WebRTC制御／WSS／sideband |
| 14 | `gpt-realtime-2.1-mini` | `openai-realtime-ws-v1` | WSSのmodelクエリー選択 |
| 15 | `gpt-live-transcribe` | `openai-transcription-ws-v1` | 認識途中／確定テキスト |
| 16 | `gpt-transcribe` | `openai-audio-v1` | ファイル文字起こし |
| 17 | `gpt-image-2.5-flare` | `openai-images-v1` | 画像生成／編集 |
| 18 | `gpt-image-2.5-sunburst` | `openai-images-v1` | 画像生成／編集 |
| 19 | `gpt-image-2` | `openai-images-v1` | 画像生成／編集 |
| 20 | `FLUX.2-flex` | `blackforest-images-v1` | FLUX 2生成／編集、モデル別のflex上流URI |
| 21 | `FLUX.2-pro` | `blackforest-images-v1` | FLUX 2生成／編集、モデル別のpro上流URI |
| 22 | `text-embedding-3-large` | `openai-embeddings-v1` | embeddings |
| 23 | `text-embedding-3-small` | `openai-embeddings-v1` | embeddings |
| 24 | `embed-v-4-0` | `cohere-inference-v1` | cohere-embeddings |
| 25 | `Cohere-rerank-v4.0-fast` | `cohere-inference-v1` | rerankのモデル選択 |
| 26 | `Cohere-rerank-v4.0-pro` | `cohere-inference-v1` | rerankのモデル選択 |
| 27 | `model-router` | `openai-inference-v1` | Chat、出力上限は提供元が決定 |
| 28 | `decision-1` | `microsoft-decision-v1` | systemone。提供モデル名は`Microsoft-Decision-1` |

モデル対応はOperationの許可モデル・モデル別設定・固定上流、および音声セッションの接続契約で定義する。

## モデル別の制約と上流の選択

### Chat・Responsesとモデル別設定

ChatとResponsesの2つのOperationを使い、それぞれに基本設定を持たせる。モデル固有の違いだけを`modelSettings`へ記述し、基本設定へ項目単位で上書きする。クライアントはAPI・操作と本文の `model` で選択する。

`settingId` はAPI設定を監査で識別するラベルとして使う。

| Operation | 監査用settingId | 対象 | 出力token項目・モデル別上限 |
|---|---|---|---|
| chat-completions | chat | OpenAIモデル | max_completion_tokens、128,000 |
| chat-completions | compatible | DeepSeek・Mistral・Grok | max_tokens、DeepSeek 384,000／Grok 500,000。Mistralは未確認 |
| chat-completions | router | Model Router | 振り分け先で変わるため自動値 |
| responses-create | response | OpenAIモデル | max_output_tokens、128,000 |

上限と確認元は [model-capabilities.json](../../../apps/api-gateway/model-capabilities.json) で一元管理し、API配備とPortalカタログの双方へ展開する。要求の初期値とモデル上限は別の値であり、初期値を最大値まで増やすものではない。実際に出力できる量は入力とコンテキスト残量によっても変わる。

モデルごとに受け付ける推論設定・出力token項目・ストリーミング形式を確認して登録する。compatible内に追加の差がある場合も、同じOperation内のmodelSettings差分へ分ける。現行のAzureカタログでChat対応を確認したモデルをChatに、Responses対応を確認したモデルをResponsesに対応付ける。

Routerは`model-router`配備をChatから呼び出す。候補モデルとルーティング方針はFoundryのRouter設定で管理する。APIMは要求した配備名を監査し、実際の選択先はFoundry側の記録や応答で確認する。[Azure Model Router](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router)

### FLUX 2の生成・編集

公開Operationは`flux2-generate`・`flux2-edit`の2件を共用する。クライアントが指定した許可配備名から内部のflex／pro設定を自動選択し、そのmodelSettingsにあるbackendUrlTemplateから固定上流を選択する。

| 監査用settingId | 許可配備名 | Azure provider route |
|---|---|---|
| `flex` | `FLUX.2-flex` | `/providers/blackforestlabs/v1/flux-2-flex?api-version=preview` |
| `pro` | `FLUX.2-pro` | `/providers/blackforestlabs/v1/flux-2-pro?api-version=preview` |

両モデルのprovider routeと受け付ける生成パラメーターには差がある。モデル別に上限・追加パラメーターを定義し、公開側の既存の要求上限を引き継ぐ。解決済みモデル名から対応する固定URIへ転送する。[AzureのFLUX仕様](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-flux)

### Embedding・Rerank・画像・Decision

- OpenAI Embeddingは`input`、Cohere Embedは`texts`・`input_type`を使う契約を維持する。次元・モデル・版と検索インデックスの対応を管理する。
- Cohere Rerankのfast／proは共通の`rerank` Operationで選ぶ。
- GPT Image 3モデルは生成・編集の2 Operationで選ぶ。生成は本文の`model`、編集はURLの`{deployment}`へモデル選択を拘束する。Azureの対応API版・画像形式・品質指定を照合する。
- Decisionは`decision-1`を固定配備として既存の`systemone` Operationから呼び、`state`・型付き質問と結果の契約を維持する。

## 音声APIを分ける根拠と公開条件

| 契約 | モデル・サービス | 要求とイベントの特徴 | APIMでの分類 |
|---|---|---|---|
| ファイル文字起こし | `gpt-transcribe` | 音声ファイルをTranscriptions APIへ送る | 既存HTTP Operation |
| Realtime会話 | `gpt-realtime-2.1`系 | 会話セッション、WebRTCのcall制御、Realtimeイベント | 既存HTTP制御／WSS／Observer |
| Live会話 | `gpt-live-1` | Liveセッション、`session.start`・`session.close`などLive固有のイベント | HTTP制御＋WSS |
| リアルタイム文字起こし | `gpt-live-transcribe` | transcriptionセッションで認識途中と確定テキストを返す | WSS |
| Speech TTS／STT | Azure Speech | SSMLの読み上げ／WAVの認識 | 既存Speech APIの2 Operation |
| Voice Live | Azure Speech Voice Live | サービス固有の音声会話・WebRTC制御 | 既存の2接続契約 |

OpenAI公式では、LiveのWebRTC作成は`POST /v1/live/sessions`、WebSocketは同パスへ接続後に`session.start`でモデル等を指定する。音声ストリームの終了は`session.close`イベントとして扱う。これに合わせ、Live制御APIのHTTP Operationはセッション作成の1件とする。[LiveのWebRTC](https://developers.openai.com/api/docs/guides/voice-webrtc?api=live)、[LiveのWebSocket](https://developers.openai.com/api/docs/guides/voice-websockets?api=live)

OpenAI公式のリアルタイム文字起こしは、transcription用の接続意図を指定して接続し、`session.update`で文字起こしモデルを設定する。会話用WSSと、開始設定・返却イベントの契約を分ける。[Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription)、[ファイル文字起こし](https://developers.openai.com/api/docs/guides/speech-to-text)

### Azure接続定義と公開前の受入

| API | Azure側の接続先 | モデルの設定箇所 |
|---|---|---|
| Live WebRTC制御 | `POST https://<resource>.openai.azure.com/openai/v1/live/sessions` | Operationの固定配備名から`session.model`を生成 |
| Live WSS | `wss://<resource>.openai.azure.com/openai/v1/live/sessions` | クライアントの`session.start.session.model` |
| 文字起こしWSS | `wss://<resource>.openai.azure.com/openai/v1/realtime?intent=transcription` | `session.update.session.audio.input.transcription.model` |

Live WSSのURLは公開bindingの`LIVE_WS_BASE_URL`、文字起こしWSSは`TRANSCRIPTION_WS_BASE_URL`へ指定する。後者の`intent`は`websocket.queryParameters`からポリシーが固定設定するため、bindingのURLにはqueryを付けない。これらのv1接続先はAPI版queryを付けない。[Azure Live WSS](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live)、[Azure Live WebRTC](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live-webrtc)、[Azure文字起こしWSS](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/realtime-audio-websockets#transcribe-audio-in-real-time)

Live WebRTCの公開入力は`{sdp,instructions?,voice?}`。ポリシーが固定モデル・`delegation.type=client`・`transport.type=webrtc`を組み立て、上流の応答を`{sessionId,sdp}`へ整える。初期voiceは`marin`。本文はAPIM Classicの500 MiB枠、SDP・instructionsは型と提供元の契約を検証し、独自の小さい文字数上限は設けない。

WSSの`websocket.session.models`は、クライアントとカタログが参照するモデル契約である。APIMはhandshakeの購読キー・接続先・固定query・回数上限を検査する。接続後のmodel指定・音声frame・セッション設定は、クライアント実装と上流サービスで検査する。WSS接続時の監査は接続と主体を記録し、frame内の実モデルや音声利用量はサービス側の記録と照合する。[APIMのWebSocketポリシー境界](https://learn.microsoft.com/en-us/azure/api-management/websocket-api)

実環境ではMIのRBAC・audience、Private Endpointへの到達、Liveの`session.started`→音声／transcript→`session.closed`、文字起こしの途中／確定イベントを受入する。WebRTCメディアは端末とサービスのICE経路を確認し、WSS接続確認と別に試験する。

TTSはAzure Speech、PII分析はAzure Languageを使う。文書OCRは既存FoundryのDocument Intelligenceを使う独立した`document-analysis-v1`として標準APIへ追加している。開始POSTは202を返し、同じ認証主体と署名チケットによる状態GETで処理状況・完了結果を取得する。Portalはこの照会と再開を扱う。[文書解析の契約](document-analysis.md)・[モデルと専用サービスの区分](foundry-model-catalog.md#モデル配備とazureの専用サービス)

## 認証・Product・テナントの扱い

| 場所 | 担当する設定・検証 |
|---|---|
| 初期構築 | 購読キーヘッダー、入口経路、Realtime署名鍵等の名前付きの値とCM providerを設定 |
| API登録時の共通ポリシー | APIM Subscription key、購読ID、信頼した入口経路、要求ID |
| API・Operation・モデル設定 | 共通のキー／Bearer認証、操作・モデル・本文形式・サイズ・出力上限。CMの接続セッション検査は必要なAPIで設定 |
| セッション制御 | 検証済みの呼出主体とsession／callの所有関係、ticketの署名・期限 |
| バックエンド | サービスごとのマネージドID・audience・接続先、CMの委任設定 |

API登録処理が全体ポリシーのXMLとETagを確認し、API登録の成功後に共通キー認可を適用する。通常のキーはリリース済みAPIをまとめた`released-apis`へ所属する。検証用製品はモデル・音声の`model-users`、PII・文書解析の`pii-users`、本人接続の`connection-users`を維持する。本人接続APIは、購読とCMセッションの所有者・接続・期限を照合する。

購読キーでGraphなどのCMを使うAPIを呼ぶ場合は、セッション未指定・期限切れ・CM再認証時にHTTP 409 `ConnectionRequired`と認証URL・完了確認用の情報を返す。基盤がMicrosoft認証・CM・SQLを担当し、呼び出しアプリはURL表示・完了確認・利用者ごとのセッション保存・元要求の1回再実行を行う。BearerによるGraph APIはOBOを使う。[本人接続の利用手順](../identity/credential-manager-login.md)

`azureTenantId`はAzure配置・Key Vault・RBAC・構築用認証、`entraTenantId`はGatewayとCMの本人認証テナントとして扱う。後者の省略時は前者を使う。CMは購読ID・provider・確認済み本人・期限に結び付けた接続セッションを参照する。BFFはログイン利用者と保存セッションを対応付ける。[Entraの準備](../../setup/07-params.md#azureとentraのテナント指定)

すべてのXMLポリシーに、各処理の目的・入力・適用条件・失敗時の応答が分かる日本語コメントを付ける。

## MCPの呼び出しと本人接続

PIIから生成するMCPは、PIIのREST APIと同じProductの購読キーで利用します。クライアントはサーバー側のMCP SDKでキーをHTTPヘッダーへ付け、`initialize → tools/list → tools/call`を実行します。

PIIの`tools/call`は、型・必須項目・対応言語・追加項目・要求サイズを入口で検証します。不正な引数は実行せず、HTTP 400とJSON-RPCの`error.code=-32602`で返します。引数自体や本文の個人情報はエラーへ転記しません。

PIIの`maxRequestBytes=1000000`は、`text`・`language`をコンパクトJSONへ再直列化したUTF-8 byte数の上限です。JSONエスケープ等を考慮した解析前の受信枠はその6倍で、上流Language用の包みを付けた最終本文にも提供元の1,000,000 bytes上限を検査します。容量超過はHTTP 413・`PiiInputTooLarge`・`requestId`と`Cache-Control: no-store`、入力不正はHTTP 400・`PiiInvalidInput`です。MCPも`params.arguments`に同じ予算を適用します。文字列を正規化・切り詰めせず、Languageの5,120 Unicodeテキスト要素などのモデル制約は提供元で検証します。他の通常AI APIの本文上限設定はAPIM Classicのバッファー上限500 MiBに揃えます。音声認識・音声ファイルの文字起こし・画像編集は申告サイズだけを確認し、サイズ検査だけの全量読取りを省きます。音声合成も共有部品では申告サイズだけを確認し、専用の`core-speech-ssml-guard`が本文を一度読み、実サイズとSSML宣言を検査します。Document・JSONなど本文を読む操作では実サイズを検査します。[本文サイズ確認の方式](../gateway/apim-policy-catalog.md)を参照してください。

上記のPII入力エラー形式はAPIMまで到達した要求に適用します。壊れたJSONはApplication GatewayのWAFで先にHTTP 403となる場合があり、この場合はAPIMの`requestId`がないため、WAFの診断記録で拒否を確認します。

APIMのnative REST-backed MCPは、REST側の上流エラーを`content[].text`へ包み、HTTP 200・`isError:false`とする場合があります。クライアントはHTTP状態、JSON-RPCの`error`、`isError`だけで成功を確定せず、`structuredContent`または各`content[].text`のJSONが[PII成功スキーマ](../../../apps/api-gateway/schemas/pii.json)に一致することも検証してください。複数の結果が食い違う場合も失敗にします。Portalはこのスキーマ検証を行い、上流エラーをマスキング成功として表示しません。生成されたMCP応答ストリームを書き換える構成は採用していません。

外部MCP proxyで個人の下流認証が必要なら、`credentialConnection.mode=personal`を指定し、CM共通窓口で取得した接続セッションを追加します。MCPのtransport・セッションID・再接続規則と、Gatewayの本人接続セッションは区別します。[CM共通接続手順](../identity/credential-manager-login.md)

## API変更時の確認順

| 順序 | 対象 | 内容 |
|---|---|---|
| 1 | Azure／提供元の接続仕様 | 固定上流と音声契約を公式資料に照合。実環境のURL・権限・到達を確認 |
| 2 | `apps/api-gateway/apis/<API名>.json` | 対象APIの許可モデル・モデル別設定・固定上流を変更する。新規APIは1ファイルを追加する |
| 3 | `infra/config/configuration.schema.json`・`scripts/deployment/helpers/configuration.py` | モデル別ルーティングとLive系入力・WSS設定の検査を追加 |
| 4 | `scripts/deployment/deploy_gateway.py` | 新しい接続定義の合成・検査・登録計画を生成 |
| 5 | `apps/api-gateway/api-publication.bicep`・`apps/api-gateway/policies/` | FLUXモデル別の固定上流選択、Liveセッション制御、文字起こしWSSの登録・ポリシー生成を実装 |
| 6 | 接続クライアントの確認 | profileヘッダーなしのモデル選択と新しい音声イベントを扱うクライアントから受入 |
| 7 | ローカル試験・Azureでの受入 | 件数・モデル対応・権限・通常／SSE／音声の正常終端を確認し、APIMへ公開 |

`azd up`で共通基盤とFoundryモデルを準備する。Entra登録後のGateway個別配備でDB・Brokerを準備し、API・Operation・バックエンド・Product・ポリシーを登録する。個別更新も同じ`deploy_gateway.py`を使う。

## 受入条件

動作の対象・手順・受入条件・結果は [動作テスト仕様書](../../testing/integration-tests.md)、資材・構築・再公開・撤去は [構築テスト仕様書](../../testing/construction-tests.md)で管理する。API・Operation・モデルの登録件数や公開成功だけを、業務APIの実通信の合格へ読み替えない。

## 補助APIの数え方

PII MCPは`pii-v1`の`pii_redact_text` から生成し、同じREST操作へ接続する。本人接続にはサインイン開始・状態照会・切断用のAPIと、ブラウザー用の起動・戻り先APIを追加する。これらの補助APIは19登録定義とは別に数える。

## 現行資材の参照先

- 初期配備28モデル：`infra/config/test.json`・`infra/config/production.json`と[モデル一覧](foundry-model-catalog.md)。
- 標準19 APIの配備定義：[apps/api-gateway/apis/](../../../apps/api-gateway/apis/)。1 API 1ファイルで、APIの名前とファイル名を一致させる。
- 現行の登録は `scripts/deployment/deploy_gateway.py`、設定検査は `scripts/deployment/helpers/configuration.py` を使用する。
- 公開の実装：`apps/api-gateway/api-publication.bicep`・`apps/api-gateway/policies/`。
- 本文の19 API一覧と28モデル対応表：登録内容の確認と受入試験に使用する。
- ポリシーの適用順・検査内容：[APIMポリシー一覧](apim-policy-catalog.md)。
