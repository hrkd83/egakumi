# API・認証・モデル・証明書の仕様

[README](../../README.md)から導入の第1〜6章と、任意の第7章（ハブ＆スポーク対応）へ進みます。本書はAPI契約・運用機能の参照先です。環境固有の値は`<...>`で示し、導入先の値へ置き換えます。

### コアのEntraアプリ登録

Gatewayの共通入口はAPIM購読キーで認可します。Microsoft認証が必要な下流APIにはCredential Manager（CM）を使用します。

| 用途 | 事前準備 |
| --- | --- |
| モデル・PII・一般のAPI | 利用するProduct/APIに対応するAPIM購読と、サーバー側のキー保管先 |
| CMの本人接続 | Entraアプリ、下流の委任権限・同意、client secret、CMのWeb redirect |
| Portalへのログイン | CoreのPortal用Entra設定。Gatewayの購読と本人接続の期限はそれぞれ管理 |

CMには既存のCore Entraアプリを再利用できます。Graphの場合は`User.Read`等の採用権限と必要な管理者同意、`offline_access`を準備し、Web redirectへ`https://authorization-manager.consent.azure-apim.net/redirect/apim/<APIM名>`を登録します。秘密値は保護ファイルへ保存し、paramsには参照先だけを記載します。

### MCPの購読キーと本人接続

PIIから生成するMCPも、RESTと同じProductの購読キーで利用します。サーバー側のクライアントが設定したキーをMCP SDKのHTTPヘッダーへ付け、`initialize → tools/list → tools/call`を実行します。

PIIの`tools/call`は、型・必須項目・対応言語・追加項目・要求サイズを入口で検証します。不正な引数は実行せず、HTTP 400とJSON-RPCの`error.code=-32602`で返します。引数自体や本文の個人情報はエラーへ転記しません。

PIIに限り、`maxRequestBytes=32768`は、厳密な`text`・`language`の入力を解析後にコンパクトJSONへ再直列化したUTF-8 byte数の上限です。RESTの受信本文そのものを32,768 bytesで拒否する方式から変更し、JSONエスケープや構文上の空白による表現の差を許容します。解析前の受信本文にはその6倍、196,608 bytesの独立した上限を適用します。どちらのサイズ超過もHTTP 413・`error.code=PiiInputTooLarge`・`requestId`の固定形式と`Cache-Control: no-store`で返します。受信上限内のJSON・必須値・言語等の入力不正はHTTP 400・`PiiInvalidInput`です。MCPもJSON-RPC全体ではなく`params.arguments`の同じコンパクトJSON予算で判定し、超過時はREST実行前に上記の400／`-32602`で拒否します。文字列値をNFC/NFKC等で正規化したり、前後の空白を削除したり、切り詰めたりしません。Languageの5,120 Unicodeテキスト要素とは別の上限であり、他のAPIの`maxRequestBytes`は引き続き受信本文のbyte数です。

上記のPII入力エラー形式はAPIMまで到達した要求に適用します。壊れたJSONはApplication GatewayのWAFで先にHTTP 403となる場合があり、この場合はAPIMの`requestId`がないため、WAFの診断記録で拒否を確認します。

APIMのnative REST-backed MCPは、REST側の上流エラーを`content[].text`へ包み、HTTP 200・`isError:false`とする場合があります。クライアントはHTTP状態、JSON-RPCの`error`、`isError`だけで成功を確定せず、`structuredContent`または各`content[].text`のJSONが[PII成功スキーマ](../../apps/gateway/apim/schemas/pii.json)に一致することも検証してください。複数の結果が食い違う場合も失敗にします。Portalはこのスキーマ検証を行い、上流エラーをマスキング成功として表示しません。生成されたMCP応答ストリームを書き換える構成は採用していません。

外部MCP proxyで個人の下流認証が必要なら、`credentialConnection.mode=personal`を指定し、CM共通窓口で取得した接続セッションを追加します。MCPのtransport・セッションID・再接続規則と、Gatewayの本人接続セッションは区別します。[CM共通接続手順](../setup/credential-manager-login.md)

## APIと認証の仕様

Azure資源・APIM MI・Key Vault・RBACは`azureTenantId`、OAuth接続の既定認証先は`entraTenantId`を使用します。同一・別テナントの両方に対応し、providerの明示的な`oauthTenantId`を保持します。

API／Operationは通信契約、Productは利用範囲のまとまりです。標準は[正式19 API](api-design-proposal.md)（HTTP 13 API・19 Operation、WebSocket 6 API）で、Graph APIはCMを使います。PII生成MCPと、採用時のCM補助API2個は別計上します。

| 通信 | 適用する認可・認証 |
| --- | --- |
| クライアントのサーバー → APIM | `api-key`（設定した購読キーヘッダー）で購読状態・Product/API範囲を検証 |
| APIM → Foundry／Key Vault | APIMのマネージドIDとAzure RBAC |
| APIM → 本人の下流API | CMが保管・更新するtokenと、購読へ結び付けた本人接続セッション |
| 固定サービス用CM接続 | 管理者が設定した`service`接続。個人接続と区別 |

APIの`authorization.mode`は`subscriptionKey`です。`operationConstraints`で許可モデル・profile・入力・出力・公開操作を検査します。本人接続が必要なAPIは、CMセッションの購読・所有者・接続・期限を照合します。

キーはバックエンドの秘密保管先へ置きます。Next.jsのサーバー処理、FastAPI、PHP、SaaS等は同じHTTP契約で呼びます。ブラウザー画面からはBFF等を経由し、キーをJavaScriptや公開設定へ含めません。APIキーだけを設定できる製品では、その製品の送信ヘッダー・パス・`api-version`・stream形式とGatewayの契約が一致することを実機で確認します。

既定のキー受信口は`api-key`です。Azure OpenAI形式の`api-key`と、`Authorization: Bearer <APIキー>`形式の接続設定は区別します。後者だけを送る製品では、サーバー側のヘッダー設定または連携処理をGatewayの契約へ合わせます。URLだけで互換と判断せず、deploymentパス型と`/v1`型、query、SSEの終端まで確認します。

### 汎用protocol中継

Coreの汎用中継は`tests/fixtures/external-api-definitions.json`で回帰検証します。外部APIの接続を追加するときは、固定の公開prefix・下流URL・TLS・追加認証・FW許可・利用停止の条件を合わせます。

## 要求・利用量の監査

現行資材のCore管理ログは、Application GatewayのアクセスURIからWorkspace取り込み変換でqueryを除去し、変換後のLAWと監査Storageへ保存する構成です。既存環境は配備記録・変換後ログ・exportと診断経路を照合して反映状況を確認します。WAFのlogScrubbingは別に維持します。企業Policy等が別Workspaceへ送るコピーは別の管理境界として記録し、Coreから設定変更・収集除外を行いません。[設定と既存環境の段階更新](../testing/log-privacy.md)を参照してください。

APIMの`core-gateway`は検証済み購読ID、要求ID、API／操作、結果code／status、許可したmodelを記録します。下流の応答IDは固定3ヘッダーの検査済み値だけを退避し、SSE本文を読み取ったり全量bufferしたりしません。親要求IDは相関の候補であり、要求者の本人・利用許可・購読の課金帰属を上書きしません。相関処理の正本は [監査context](../../apps/gateway/apim/policies/audit-context.xml)と [Gateway転送](../../apps/gateway/apim/policies/gateway.xml)です。

数値usageの初期対象は、`foundryOpenAi`のHTTP Chat Completions操作だけです。既存Developer/classicを含む[AI gatewayの適用tier](https://learn.microsoft.com/en-us/azure/api-management/genai-gateway-capabilities)と、[managed Chat Completionsの標準ログ手順](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-llm-logs)を採用します。APIM診断を`2025-09-01-preview`へ揃えて`largeLanguageModel.logs`を有効化しますが、任意の`requests/responses`メッセージ設定は指定せず、既存のfrontend/backend本文取得は0 byteを維持します。[診断の公開定義](https://learn.microsoft.com/en-us/azure/templates/microsoft.apimanagement/2025-09-01-preview/service/apis/diagnostics)

既存LAWへ`GatewayLlmLogs`を送り、[標準`ApiManagementGatewayLlmLog`の列](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apimanagementgatewayllmlog)を固定契約にします。`CorrelationId`はGateway要求との一致、`RequestId`は言語モデル側の要求ID、`DeploymentName`は許可済み配備名との厳密一致に使い、`PromptTokens/CompletionTokens/TotalTokens`を検査します。要求／応答行の同じ数値は重複計上せず、異なる数値や複数モデル要求IDは`ambiguous-usage`として残します。Responses、Anthropic、音声、PII、MCP等はこの初期の数値対象外で、未提供・途中切断・不正値を含め`none`とし、0や推計値を補いません。Chat SSEでもproviderがusageを返す必要があり、`stream_options.include_usage`等の対応条件と実ログ到達を試験で確認します。これはtoken制限や課金台帳の追加ではありません。

[監査query](../../scripts/audit-usage.kql)は許可済み実行の集計用です。自前JSONのschema／eventとGatewayの上位`CorrelationId`を照合し、`TraceRecords`内の`message/Message`を明示対応します。内部`source`の存在には依存しません。帰属の矛盾は`ambiguous-request`の1行で残し、generated MCP表口とobserverを実処理件数へ重複計上しません。`accepted`のみなら最終結果は不明とします。拒否・policyエラー等を含む全体監査は`ApiManagementGatewayLogs`の`ResponseCode/BackendResponseCode`、全phaseのtraceと別途照合します。新しいログcategoryの導入は通常の`azd up`で行い、その後のAPI差分公開も同じAPI診断と設定を適用します。導入前の配備をAPI差分公開だけで更新してcategoryまで有効化したとは扱いません。

## 本人・共有キー・サービスの回数制限

回数制限は明示的に有効化した場合だけAPIM購読IDごとに適用します。キーを更新しても同じ購読枠です。config正本の既定は無効（無制限）です。

```json
"rateLimits": {
  "user": { "enabled": false, "calls": 60, "renewalPeriodSeconds": 60 },
  "subscriptionKey": { "enabled": false, "calls": 60, "renewalPeriodSeconds": 60 },
  "application": { "enabled": false, "calls": 60, "renewalPeriodSeconds": 60 }
}
```

`enabled: false`の間は`calls`や`renewalPeriodSeconds`の数値を適用せず、60回／分の制限も発生しません。共通制限が必要な場合だけ`subscriptionKey.enabled`を`true`にします。`user`／`application`の有効化は設定検査で拒否します。本人接続の有無やCMのtoken更新で、同じ購読の計数枠は変わりません。有効化時はparamsの`gateway.rateLimits.subscriptionKey.overrides`で`{"callerId":"subscription:sales-models","calls":120,"renewalPeriodSeconds":60}`のように上限を指定できます。利用範囲はProduct/APIの購読設定で管理します。

認可・操作制約の後、CM/MI・下流転送の前に即時計数します。超過は本文なし429・数値`Retry-After`・要求IDを返し、下流を呼びません。通常操作・一括処理・並列実行で採用する上限を確認してください。APIMの近似的な要求回数制限であり、料金上限やWAFの代替ではありません。

HTTP/SSEは要求、gRPCはRPC開始、WebSocketは初回握手を数えます。生成MCPは参照先RESTの実処理で1回計数します。登録済みの取消・終了操作の免除と、偽装した要求を免除しないことも確認します。[APIMの回数制限](https://learn.microsoft.com/en-us/azure/api-management/rate-limit-by-key-policy)

## CMによるGraphの本人接続

`graph-profile-v1`の`/tools/graph/v1/me`は、購読キーとCMの本人接続セッションでGraphを呼びます。クライアントのサーバーがログイン取引を開始し、利用者がMicrosoftサインインを完了すると、APIMがGraphの`/me`で本人を確認します。接続セッションには購読・provider・本人・接続・期限を結び付けます。

APIの呼出時は、`X-Core-Connection-Session`を購読キーと合わせて送ります。APIMがセッションを照合し、CMの`get-authorization-context`で下流用tokenを取得します。本人接続のtoken取得・更新はCMが管理します。[CM共通接続手順](../setup/credential-manager-login.md)

## 初期モデルと公開API


`services.modelDeployments` がモデルの正式名・版・SKU・配備容量を定義し、
`apiDefinitions[].authorization.operationConstraints` が公開する配備名と要求上限を定義します。
両者を揃えて変更してください。API の `model` はモデルカタログ名ではなく、
この環境の配備名です。`Microsoft-Decision-1` は安全な配備名 `decision-1` を使います。

初期構成は一つのFoundryに[選定した28モデル](foundry-model-catalog.md#初回配備する28モデル)を配備します。
標準API定義には、初期28モデルの操作・接続に加え、PII・Graph /me・文書解析を含む19 APIがあります。Document Intelligenceはモデル配備とは別のFoundryサービスとして扱います。[文書解析の契約](document-analysis.md)
DeepSeek・Mistral・Grok・Routerは共通Chat Operation、FLUX flex/proは生成・編集Operationを共用します。
Live会話とリアルタイム文字起こしは、それぞれの音声契約として公開します。
[モデル対応表](api-design-proposal.md#初期28モデルとapioperationの対応)と[ポリシー一覧](apim-policy-catalog.md)で詳細を確認してください。
モデルの販売地域・利用枠・提供状態は構築前に確認してください。
モデルが配備できることと、すべての API が使えることは同じではありません。

GPT-5.6 Sol／Terra／Lunaは版`2026-07-09`・`GlobalStandard`で配備し、共通のChat Completions／Responses APIへ公開します。初期capacityはSol／Terraが1、Lunaはテスト・本番とも5です。Lunaは複数のAPIやAgentから連続利用する代表モデルなので、短い要求でもTPM／RPM枠に達しにくい初期割当にしています。利用枠名は各`OpenAI.GlobalStandard.gpt-5.6-<sol|terra|luna>`です。capacity `1`は初期の小さい割当で、提供者が認定した最小値という意味ではありません。利用枠・提供容量は構築前にも再確認します。[Microsoftのモデル案内](https://azure.microsoft.com/en-us/blog/gpt-5-6-now-available-in-microsoft-foundry/)

| 用途 | 初期モデル | 公開パス | 要求の形式 |
| --- | --- | --- | --- |
| 会話・Responses | `gpt-6.1-sol`、`gpt-6-luna`、`gpt-6-sol`、`gpt-6-astra`、`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna` | `/models/openai/v1/chat/completions`、`/models/openai/v1/responses` | JSON。SSE は `stream:true` |
| OpenAI Embedding | `text-embedding-3-small`、`text-embedding-3-large` | `/models/openai-embeddings/v1/embeddings` | `model`、`input`、`encoding_format` の JSON |
| Cohere Embedding | `embed-v-4-0` | `/models/cohere/v1/embed` | `model`、`texts`、`input_type`、`embedding_types` の JSON |
| GPT 画像 | `gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`、`gpt-image-2` | `/models/openai-images/v1/generations`、`/models/openai-images/v1/deployments/{deployment}/edits` | 生成は JSON、編集は multipart の画像 |
| 互換Chat | `DeepSeek-V4-Flash`、`DeepSeek-V4-Pro`、`Mistral-Large-3`、`grok-4.7` | `/models/openai/v1/chat/completions` | `compatible` profile、JSONの`max_tokens` |
| Router | `model-router` | `/models/openai/v1/chat/completions` | `router` profile、JSONの`max_completion_tokens` |
| FLUX 画像生成・編集 | `FLUX.2-flex`、`FLUX.2-pro` | `/models/blackforestlabs/v1/flux-2/generations`、`/models/blackforestlabs/v1/flux-2/edits` | BFL providerのJSON。flex／pro profileで固定上流を選択 |
| Live会話 | `gpt-live-1` | `/models/openai-live/v1/sessions`、`/models/openai-live/v1/ws` | WebRTC作成のJSON、またはLive WSSイベント |
| リアルタイム文字起こし | `gpt-live-transcribe` | `/models/openai-transcription/v1/ws` | WSS、transcriptionセッション |
| 再ランキング | `Cohere-rerank-v4.0-fast`、`Cohere-rerank-v4.0-pro` | `/models/cohere/v1/rerank` | `query`、`documents`、`top_n` の JSON |
| 判断 | `decision-1` | `/models/microsoft-decision/v1/systemone` | `state`、`questions` の JSON |
| OpenAI ファイル文字起こし | `gpt-transcribe` | `/models/openai-audio/v1/deployments/{deployment}/transcriptions` | multipart の `file`。配備名は URL で固定 |
| Realtime 会話 | `gpt-realtime-2.1` | `/models/openai-realtime/v1/ws`、`/models/openai-realtime/v1/calls` | WebSocket または WebRTC の接続準備 |
| Realtime Mini 会話 | `gpt-realtime-2.1-mini` | `/models/openai-realtime/v1/ws?model=gpt-realtime-2.1-mini` | 共通WebSocketのmodel選択。操作制約のallowedModelsで制限。Mini 用 WebRTC は公開しません |

画像入力は共通の`/models/openai/v1/chat/completions`へ`X-Egakumi-Profile: vision`、PDF入力は`/models/openai/v1/responses`へ`X-Egakumi-Profile: pdf`を付けます。通常入力はprofileを省略できます。許可と上限は入力profileごとに保持します。[全APIと操作・profileの定義](api-design-proposal.md)を参照してください。
Speech サービスの SSML 読み上げ `/models/azure-speech/v1/synthesize`、
WAV 文字起こし `/models/azure-speech/v1/recognize`、Voice Live も独立した操作として使用します。
これらは OpenAI Audio API と入力形式・認証・応答イベントが異なります。
Voice Liveは通常WSS・WebRTCとも`gpt-6.1-sol`を選びます。これはVoice Liveが管理する会話モデルで、初期28配備とは別の設定です。[対応モデル・API版・処理scope](foundry-model-catalog.md#voice-liveの会話モデル)を確認してください。

### モデル選択と添付形式

モデル選択欄は APIM の許可一覧から作ります。選択すると本文と URL パラメーターを
そのモデル用の初期例へ置き換えます。変更済みの本文や添付は引き継ぎません。
初期例を選んだ後も JSON を編集できますが、最終的な許可モデルと上限は APIM が検査します。

API定義内の通常の会話例は出力512 tokenです。Portalの出力トークン初期値は、選択したモデルの公開最大値を使用します。
公開するモデル別上限は [model-capabilities.json](../../apps/gateway/apim/model-capabilities.json) で管理し、APIとPortalへ同じ値を展開します。例の初期値をモデル上限として扱いません。推論に使う token も上限に含まれるため、
`status:incomplete` の場合は結果を成功した回答と扱わず、返却された終了理由を確認します。
推論設定はモデル別の短い疎通用初期値です。GPT-5.6 Sol/Terra/Lunaには`none`、
GPT-6.1 SolとGPT-6 Astraには`low`を設定します。Chatでは`reasoning_effort`、
Responsesでは`reasoning.effort`を使い、他のモデルへ同じ値を推測で追加しません。
初期値のまま正常な本文が返ることは実機で確認し、空応答を成功としません。

画像や音声ファイルは Browser → Console では multipart で送り、Console が
登録されたGateway APIの形式へ変換します。FLUXの編集画像は`input_image`の生Base64、
ファイル文字起こしはmultipartとして上流へ送ります。
変換後の本文も API と WAF の小さい方の上限で検査します。
JSON へ変換する添付を、multipart 用の大きな WAF 上限で通せるとは扱いません。

WebSocket と WebRTC の音声は、共通の通信処理を利用します。
WebRTC の接続準備・終了制御は Gateway 経由、音声本体は端末と Azure 間です。
モデルごとに任意の上流 URL や未登録モデルへ自動転送する機能はありません。

### Providerごとの契約

FLUX は Azure Foundry の `/providers/blackforestlabs/v1/` を呼び、
APIM のマネージド ID を使用します。モデルごとに上流のパスを固定します。
Azureの同期provider応答は `data[].b64_json` と `data[].url` の両方を扱います。Base64はConsoleで復号し、登録済みFLUX操作の構造化応答URLはConsole BFFが画像artifactとして取得して表示します。利用者入力や自由文のURLは取得しません。取得先はHTTPSの公開IPに限定し、DNSの全回答を検査して検査済IPへ接続を固定します。資格情報を転送せず、redirect・内部IP・時間/サイズ超過・画像以外を拒否し、署名URLは画面や証跡へ渡しません。生成APIはGateway経由、artifact取得はConsole側の通信です。
[Microsoft公式のFLUX応答例](https://github.com/microsoft-foundry/foundry-samples/blob/main/samples/python/black-forest-labs/flux/README.md)
BFL 直販の `x-key`、`polling_url`、外部配信ホストを Azure の契約へ混在させません。
[Microsoft の FLUX API](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-flux)

Cohere の Embedding は `/providers/cohere/v2/embed` を使います。
OpenAI Embedding と同じ `input` へ黙って読み替えず、Cohere の `texts` と `input_type` を指定します。
APIM は Foundry Models 用のマネージド ID と、Foundry に限定した組み込みの
`Cognitive Services User` 権限を使用します。
[Cohere 公式 Azure 例](https://github.com/cohere-ai/cohere-azure-workshops)、
[Foundry の Entra 認証](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/configure-entra-id)

標準TTSはAzure SpeechのSSML API、OpenAIのファイルSTTはmultipartのAPIを使います。
STT は配備名を含む Azure の URL へ束縛し、multipart 内の `model` を切替口にしません。
[OpenAI Audio の Azure REST 仕様](https://learn.microsoft.com/en-us/azure/foundry/openai/reference-preview-latest)、
[Azure の配備名付き STT](https://learn.microsoft.com/en-us/azure/foundry/openai/whisper-quickstart)

Realtime はモデルを接続先に固定します。WebRTC は一つの公開 create/hangup 契約と
所有者チケットを使用します。Mini は WebSocket のみとし、別 WebRTC 入口へ
同じチケットを使う構成を増やしません。
[Azure Realtime の接続方法とセッション](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/realtime-audio)

Microsoft Decisionは`Microsoft-Decision-1` v1／DataZoneStandard／初期割当1、配備名`decision-1`です。`state`と`noul`・`choice`・`score`を受け、型付き判断を返します。上流は`/providers/microsoft/v1/systemone`、公開上限8,192byteです。初期28モデルのうちDecisionはPreview、ほかは選定時GAです。版・SKU・quota名の正本はconfigで、`gpt-6.1-sol`の初期capacityは50、`gpt-5.6-luna`は5、ほかは1です。1は初期割当値です。[Decision仕様](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision)

Cohereは共有Foundryのprovider routeとMIを使い、利用者のtokenを転用しません。採用版の実route・MI対応・PE到達は受入で確認します。`disableLocalAuth:true`を緩めたりAPIキーへ自動fallbackしたりせず、401／403等は相関と権限を調べます。[CohereのAzure例](https://github.com/cohere-ai/cohere-azure-workshops)、[Foundry認証](https://learn.microsoft.com/en-us/azure/foundry/concepts/authentication-authorization-foundry#feature-support-matrix)

### 音声サービスの選び方

| 用途 | 提供API・方式 |
| --- | --- |
| 完成文章の読み上げ | Speech SSML REST `/models/azure-speech/v1/synthesize`。音声を再生・保存 |
| 録音済みファイルの認識 | Speech WAV REST `/models/azure-speech/v1/recognize`、OpenAI multipart transcription。認識テキストを表示 |
| 双方向会話・入力音声認識 | Realtime／Voice Live WebSocket。Voice Liveの`azure-speech`入力認識も画面で照合 |
| ブラウザーの低遅延会話 | Realtime／Voice Live WebRTC。制御はGateway、mediaは端末とサービス間 |

Speech TTSの正式コンソールは、`input.body`に`text`・`voice`・`language`だけを持つJSONを送ります。BFFが文章をXMLエスケープし、公開APIへは従来どおり`application/ssml+xml`を送ります。文章は空でない10,000文字以下、voiceはSpeechのlocale付き音声名、languageは35文字以下のBCP 47タグを検査し、変換後のUTF-8 byte上限も適用します。構造化入力で任意のXML属性は追加できず、生SSMLでもDTD・実体定義は拒否します。既存の生SSML文字列は上級者向けに残しますが、その文字列がWAFを通ることは保証しません。

| 例 | `input.body` |
| --- | --- |
| 日本語 | `{"text":"接続を確認します。","voice":"ja-JP-NanamiNeural","language":"ja-JP"}` |
| 英語 | `{"text":"Connection check.","voice":"en-US-AvaMultilingualNeural","language":"en-US"}` |

SpeechとOpenAI Audioは認証・入力・イベントが異なります。Voice Liveの入力認識を単体Speech STT WebSocket APIと同じ契約にはしません。WebSocketのUpgrade後のframeはAppGW WAFの検査対象外で、APIMの認可は主にhandshakeです。接続後の設定変更・取消・失効と利用量は実サービスで照合します。

STTの公開`audio/wav`はAPIMがSpeech向けの固定codec・16kHzヘッダーへ変換します。WAFはSSML/WAVの登録済み完全パス、画像/PDFの登録済みJSON欄、従来MCPの正規messages経路にだけ必要な例外を適用し、他の検査・本文上限を維持します。企業管理WAFは自動変更せず、担当者が同じ範囲を確認します。

## 管理ポータルの使い方

[Core内の管理ポータル](../setup/portal.md)は`apps/portal`で管理します。画面ログインとGateway利用を分け、サーバー側で購読キーを付けて公開APIを呼びます。本人接続が必要なAPIでは、認証URLの表示・認証完了の確認・元操作の再実行を確認します。

## 本人OAuth接続

CMはMicrosoftのaccess/refresh tokenを保管・更新し、BrokerとSQLが確認済み本人、プール割当、認証取引、本人接続セッションv2を管理します。モデル・PIIの通常利用はAPIM MIで下流へ接続します。Graph等で本人接続が必要な場合にだけ、購読キーと`X-Core-Connection-Session`を合わせて使います。

### 構築で用意するもの

共通基盤はSQLサーバー・非公開経路・ACA環境・ACRを準備します。Gateway工程はCM provider、credentials DB、Broker・初期化／定期照合ジョブ、本人接続の共通APIを配備します。Portal工程は認証DB・アプリ・購読管理と、利用者ごとの接続状態を扱う機能を配備します。

本人のCM接続は実際のログイン時に作成または再利用します。任意のUPNや接続名を本人証明にしません。設定とOAuthの前提は [CM接続手順](../setup/credential-manager-login.md)、配備単位は [Gatewayの配備](../setup/credential-sql.md)を参照してください。

### 認証と業務再開

1. Portalのサーバー処理が、有効な購読キーでGraphを呼びます。未接続なら409 `ConnectionRequired`と認証案内を受け取ります。
2. 仮セッション・認証取引を本人に結び付けてサーバーへ保存し、`loginUrl`だけを利用者へ案内します。
3. Microsoftの本人確認とCM接続のGraph本人照合後、仮セッションが有効になります。statusは状態・本人・期限だけを返し、セッション平文を再発行しません。
4. Portalログイン本人との一致を確認し、保存済みセッションで元の読み取り要求を一度再開します。書込み要求の自動再送は、その操作の冪等性と利用者操作の契約に従います。

ブラウザーへキー・pollSecret・接続セッション・CM tokenを渡しません。Portalの保存はSQLの用途別暗号化状態とし、複数サーバーの更新はリースと版数で排他します。[クライアント実装](connection-session-clients.md)

### 期限・更新・失効

既定は無操作7日、認証完了から最長30日、値の更新間隔24時間です。業務利用前の本人・接続照合を経て無操作期限を更新し、最長期限は延長しません。更新候補は保存してからcommitし、旧値の猶予は確定から固定5分です。

現在セッションのlogout、本人の全セッション失効、CM接続解除はそれぞれ別操作です。SQLの期限・失効検査は次の要求から適用しますが、既に始まった上流処理の取消や、Entraのgrant取消が独自セッションへ即時伝播することは保証しません。接続解除はCMの削除確認後に容量を解放します。

短命取引・候補の回収、CMの定期照合、SQL障害・更新不能時の扱いは [プール・セッション仕様](credential-pool-design.md)を参照してください。実機の受入条件・判定は [動作テスト](../testing/integration-tests.md)に記録します。

### 更新と撤去

同じparams・今回の配備記録でGatewayを更新し、SQL初期化・Broker起動・API公開の順序と、既存接続・セッションの保持を確認します。通常のCM照合で、作成からの経過だけを理由に永続接続を削除しません。Core全体のdownでは所有APIM内のprovider・access policy・接続を照合して撤去します。[運用手順](../operations.md)

## 証明書の取得・更新

| `certificate.mode` | 動作と担当 |
| --- | --- |
| `dnsPlugin` | 管理VMのPowerShell 7でLet's Encrypt DNS-01を実行しKey Vaultへ格納。systemd timerが既定12時間ごとに確認し期限前更新 |
| `prepared` | 保護PFXとパスワードを格納。更新は外部の証明書管理者が担当 |

以下の`/var/lib/egakumi-certificates/`と`egakumi-certificate-`はworker実装の固定パス／prefixです。導入先の製品名や、管理端末のclone先を指定する値ではありません。

管理VMは署名済みMicrosoft APTからPowerShell 7.6.7、Posh-ACME 4.34.0を導入します。状態は`/var/lib/egakumi-certificates/<KV>-<cert>`（ディレクトリ700・ファイル600）で保護し、VMのMIでVaultの最新版DNS資格情報を取得します。ACMEアカウント・証明書再利用状態もVaultへ保存し、VM再作成時に復元します。Vaultの完全削除後は再利用できません。

Azure DNSではDNS資格情報secretを使いません。`azd up`でDNS側Subscription IDと事前作成したユーザー割り当てMIのリソースIDを対話入力し、paramsへ保存・再利用します。IaCは証明書取得前にそのIDを管理VMへ追加し、workerは毎回IMDSの`mi_res_id`でIDを指定してARMトークンを取得します。Key Vaultには従来のシステム割り当てMIを使います。DNS側の権限付与・ID作成は事前準備です。

```sh
CERTIFICATE_VAULT_NAME='<対象Key Vaultの名前>'
CERTIFICATE_NAME='<対象証明書の名前>'
CERTIFICATE_UNIT="egakumi-certificate-${CERTIFICATE_VAULT_NAME}-${CERTIFICATE_NAME}"
CERTIFICATE_WORKER="/var/lib/egakumi-certificates/${CERTIFICATE_VAULT_NAME}-${CERTIFICATE_NAME}"
systemctl status "${CERTIFICATE_UNIT}.timer"
journalctl -u "${CERTIFICATE_UNIT}.service"
```

必要なDNS検証では一時TXTを作り、検証後に**その検証値だけ**を回収します。CA側の有効な認証を再利用した更新では、新しいTXTを作らない場合もあります。恒久的な公開A・Entra所有確認TXT・無関係なTXTは削除しません。運用中はDNS権限・トークン期限を維持し、専用資格情報が不要になった時点で担当者が失効させます。

期限前に運用上の理由で証明書を交換するときは、Key Vaultの現在の証明書バージョンのSHA-1 thumbprint（40桁の公開識別値）を確認し、管理VMで次を一度実行します。`dnsPlugin`専用で、正常な既存証明書・対応するPFX・同じSANのACME注文とアカウントが必要です。通常の`azd up`・定期更新の期限判定は変わりません。

```sh
sudo /usr/bin/pwsh -NoLogo -NoProfile -NonInteractive \
  -File "${CERTIFICATE_WORKER}/certificate.ps1" \
  -Action Renew -ConfigFile "${CERTIFICATE_WORKER}/config.json" \
  -RotateFromThumbprint '<現在の公開証明書の40桁thumbprint>'
```

指定した旧証明書と一致するときだけ既存注文を更新します。発行後のVault登録で失敗した場合は、同じ指定で再実行すると検査済みの新しいキャッシュ証明書を取り込み、重複発行を避けます。既に別の正常な証明書へ変わっていれば追加発行せず、`current-already-different`と表示します。これは今回の実行で交換したという意味ではありません。要求はtimerや設定に残さず、既存の証明書版・ACME状態を保持します。CAの回数制限で失敗した場合は連続実行せず原因を確認してください。完了後も通常の自動更新を続け、AppGWとAPIMが実際に返す公開証明書をそれぞれ照合します。

AppGWはバージョンなしKey Vault secret URIを参照し、通常約4時間ごとに新証明書を確認します。更新タスクへAppGW管理権限は与えません。gRPCのAPIM証明書も取得・更新・再適用を確認し、Developerでは自動同期を保証せず必要時に再同期します。Linux AMA/DCRがSyslog・性能・証明書状態を監視します。

### API Centerのカタログ管理

API Center本体とAPIM同期はCoreで管理します。運用担当者はAzure Portalで同期元・API説明・仕様・認証条件・公開URLを確認し、`authorization.catalogReaders`で指定した閲覧者のAzure RBACを照合します。API Center Portalを独自に公開する場合は、そのクライアント登録と閲覧権限を別途設計します。

FreeはAPIM一つ・同期200APIまでで、基本のPortal閲覧を使えます。semantic searchはStandardです。APIMの対象SKUに追加無償特典があっても自動昇格を前提にせず、**Overview → Manage plan**の実プランと`services.apiCenterSkuName`を合わせます。[Portal設定](https://learn.microsoft.com/en-us/azure/api-center/set-up-api-center-portal)、[料金プラン](https://learn.microsoft.com/en-us/azure/api-center/overview#standard-plan-benefit-when-api-center-linked-to-api-management)

API説明はAPIMの公開契約から生成し、API Centerへ同期します。区分、Productと購読要件、公開URL、操作・上限・モデル、CMの接続条件を示し、内部URL・主体ID・秘密は掲載しません。説明は1,000文字までです。同期は最大24時間かかり得ます。公開ApiSources APIに強制同期actionはなく、再PUTによる即時同期も保証しません。説明・仕様Download・実runtime URIを別々に確認します。[APIM同期](https://learn.microsoft.com/en-us/azure/api-center/synchronize-api-management-apis)

公開接続には、説明とruntime URIに記載された`https://`／`wss://`を使います。標準同期のREST仕様に`http`も含まれる場合がありますが、これはAppGWから内部APIMへのHTTP区間もAPIが受け付けるためです。購読キーとCM接続セッションはTLSで保護された公開URLへ送信してください。

MCPとgRPCは、標準同期でAPIの説明・接続先が掲載されても、仕様Downloadが提供されるとは限りません。MCPの実tool定義は認証後の`tools/list`、gRPCのメッセージ定義は提供元のproto（公開登録の`grpc.definition`と同じ内容）で確認します。RESTのOpenAPIが取得できたことを、これらの仕様ファイルも取得できるという意味には扱いません。

native gRPCは、API説明に表示する完全な公開URL（例：`https://<GATEWAY_FQDN>:8443/`）、SNI、protoのservice名を使います。明示実行するAPI登録コマンドは、proto取り込み後にAPIMの説明を読み直し、必要な場合だけ説明をETag付きで更新します。API Centerの自動同期が管理する`deployment.server.runtimeUri`は、APIM側のHTTPS URLを示し、AppGWのTCP入口の公開ポートを含まない場合があります。この欄をAPI Center側で上書きしても維持される保証はないため、説明をAPIMから生成し、公開ポートを含む接続先を明示します。同期URLとの不一致と仕様ファイルの提供可否は、受入記録に残します。

gRPCの受入では、正常な4方式に加え、入力エラー・期限・取消の最終statusを実SDKで確認します。採用構成のAPIM中継では、本文のない早期エラーを初回HEADERSだけで終える標準形式（Trailers-Only）が、クライアントで`UNKNOWN`に変わる場合があります。backendは初期metadataと終了metadataを分け、最終`grpc-status`を`END_STREAM`付きのtrailing HEADERSで返す構成にします。認証前に終了するフレームワーク由来のエラーや未登録methodも、利用するbackendで別途確認してください。成功RPCだけで全エラーの中継を保証しません。[gRPCのHTTP/2応答形式](https://github.com/grpc/grpc/blob/master/doc/PROTOCOL-HTTP2.md#responses)
