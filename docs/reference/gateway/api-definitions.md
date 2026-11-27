# API正本の定義

標準APIの内容は[API正本](../../../apps/api-gateway/apis/)で管理します。以下の詳細契約を公開params入力の一覧と混同しません。独自APIの追加・変更は対象限定の運用計画で扱い、接続先・認証・WAF・監査と既存APIへの影響を確認します。

## API・権限の記述形式

以下は資材開発者向けのAPI正本と解決済み内部配列の契約参照です。AはAPI定義1件、Oは`A.operations[]`、Cは`A.authorization.operationConstraints.<操作名>`を表します。公開paramsへ`apiDefinitions`を記入する手順ではありません。公開API正本には公式共通URLと独立した架空例だけを置き、実環境のURL・ID・秘密は転記しません。

| パラメタ名 | 公開サンプル値 | 意味 |
| --- | --- | --- |
| `A.name` | 追加API例ごとに設定 | API識別名。 |
| `A.displayName` | 追加API例ごとに設定 | API表示名。 |
| `A.path` | 追加API例ごとに設定 | 公開パス。 |
| `A.apiType` | 通常http | API種類。 |
| `A.backendService` | 追加API例ごとに設定 | foundryOpenAi/foundryCognitive/foundryServices/external。外部ツールはexternalを指定。 |
| `A.backendUrl` | 公式共通URLまたは独立した架空例 | externalのTLS接続先。公開資材へ実環境のURLを転記しない。基盤Foundryは内部生成。 |
| `A.backendAudience` | 下流ごとの例 | APIMが下流tokenを取得する宛先。 |
| `A.productId` | 各API例のProduct | 検証用APIM Product。`released-apis`は予約名のためここへ指定しません。契約・配賦先IDとは別。 |
| `A.released` | 省略時false | 通常キー用`released-apis`製品への所属。生成MCPも同じ状態です。falseへ変更してGateway配備すると通常製品との関連だけを解除し、API本体・検証用所属・購読を保持します。 |
| `A.subscriptionKeyHeader` | 固定の`api-key` | 内部共通認証と一致させる。公開params/configにヘッダー選択入力は設けない。 |
| `A.backendHeaders` | 通常{} | 非秘密の固定ヘッダー。鍵やtokenは格納不可。 |
| `A.operations` | 操作ごとの例 | 公開操作配列。 |
| `O.name` | 操作ごとの例 | 操作識別名。 |
| `O.displayName` | 操作ごとの例 | 操作表示名。 |
| `O.method` | 操作ごとの例 | HTTPメソッド。 |
| `O.urlTemplate` | 操作ごとの例 | 公開操作パス。 |
| `O.queryObservation` | 観測する操作だけ指定 | URLクエリの観測ラベル。ASCII英字で始まる1～64文字の英数字・`_`・`-`。小文字の`none`／`unknown`は予約。省略時は観測対象外で、許可／拒否や認証を切り替える設定ではない。 |
| `A.mcp.queryObservation` | 観測する生成MCP表口だけ指定 | 生成MCPのメソッド・公開相対pathから選ぶ観測ラベル。HTTP操作と同じラベル書式。 |
| `A.mcpProxy.endpoints[].queryObservation` | 観測する外部MCP受付だけ指定 | 外部MCPのHTTPメソッド・公開相対pathから選ぶ観測ラベル。下流の`uriTemplate`とは区別する。 |
| `O.templateParameters` | 不要なら[] | URL変数の配列。 |
| `O.templateParameters[].name` | URL変数名 | URL変数名。 |
| `O.templateParameters[].type` | 記入例はstring | URL変数の型。 |
| `O.templateParameters[].required` | 記入例はtrue | URL変数を必須とする。 |
| `O.templateParameters[].description` | 説明文 | URL変数の説明。 |
| `O.request` | 省略→{} | APIMの要求定義。Azure形式。 |
| `O.backendUrlTemplate` | 必要な例だけ指定 | 下流の相対パス/queryへの変換。動的authorityや秘密を含めない。 |
| `O.staticResponse.body` | 通常未使用 | 固定JSON応答。GETのみ。 |
| `O.staticResponse.contentType` | 使用時application/json | 固定応答形式。 |
| `A.backendAuthentication.mode` | 省略→managedIdentity | managedIdentity/none/keyVaultHeader/speechEntra。noneは承認済みexternalのみ。 |
| `A.backendAuthentication.headerName` | 必要なAPI例だけ設定 | Key Vault秘密を渡す専用ヘッダー。 |
| `A.backendAuthentication.headerValuePrefix` | 使用時は空またはBearer | 資格情報に付ける接頭辞。 |
| `A.backendAuthentication.secretIdentifier` | Key Vault URIの置換欄 | secret URI。秘密値そのものを含めない。 |
| `A.backendAuthentication.resourceId` | SpeechリソースIDの置換欄 | Speech Entraの対象Resource ID。 |
| `A.authorization.mode` | `subscriptionKey` | APIMの購読キーとProduct/APIの利用範囲を検査する。 |
| `A.authorization.operationConstraints` | 全操作の制約例 | 本文・モデル・出力等の制約。 |
| `C.modelSettings.<モデル名>.backendUrlTemplate` | FLUX flex／proの固定URI | 本文のモデル名から選ぶ固定の上流相対URI。モデル別転送先を使う操作では、`allowedModels`の全モデルに設定する。 |
| `A.websocket.session.kind` | `live`／`transcription` | 接続後イベントの契約。接続時に固定パスとqueryを検査する。 |
| `A.websocket.session.models` | 配備済みモデル名の配列 | クライアントがsessionイベントで指定するモデル。イベントの処理はアプリと上流サービスが担当する。 |

各検査・転送・認証ポリシーの役割は[APIMポリシー一覧](apim-policy-catalog.md)を参照してください。

`queryObservation`はAPI正本で指定する観測ラベルです。paramsに観測／拒否modeや専用Named Valueを設けません。観測設定と、下流URI変換・WAF・通信の受入は別に確認します。[クエリ観測の確認](../../operations/apim.md#操作mcp受付のurlクエリ観測)を参照してください。

## 認証設定と購読

標準AI APIの購読認証・容量設定の抜粋です。ProductにAPIを所属させ、APIM購読のキーをサーバー側へ配布します。キー値はparamsへ記載しません。独自の業務APIでは、その契約と適用先WAFに合わせて容量を指定します。

```json
"authorization": {
  "mode": "subscriptionKey",
  "operationConstraints": { "<操作名>": { "kind": "registeredOperation", "maxRequestBytes": 524288000 } }
}
```

| 設定 | 値の入手先・決め方 |
| --- | --- |
| `A.productId` | 利用範囲をまとめるAPIM製品ID。例：`model-users` |
| `__CM_CLIENT_ID__` | CM providerが利用するEntraアプリのApplication（client）ID |
| `__TENANT_ID__` | CMのOAuth認証先テナント |

CMアプリ登録と共通セッション設定の公開形は、[OAuth providerの登録と定義](../configuration/parameters.md#oauth-providerの登録と定義)を参照してください。API側の`credentialConnection`は入口認証とは別の下流接続契約です。

## 操作制約（C）

| パラメタ名 | 公開サンプル値 | 意味 |
| --- | --- | --- |
| `C.modelSettings` | 必要なモデルだけ指定 | モデル名をキーにした設定差分のオブジェクト。本文のモデル名から一意に取得し、未指定モデル・項目には操作の基本設定を使う。 |
| `C.modelSettings.<モデル名>.settingId` | `chat`／`compatible`／`router`／`response`／`flex`／`pro`など | 任意の内部設定識別名。監査で設定を区別するために使い、クライアントの選択や認可には使わない。 |
| `C.modelAliases` | `{ "codex-auto-review": "gpt-5.6-luna" }` | Foundry OpenAIのJSONモデル操作で使う、操作直下のモデル別名表。対象は同じ操作の`allowedModels`にある配備モデル。許可範囲の追加・配備モデル名の上書き・別名の連鎖は不可。 |
| `C.kind` | 操作ごとの例 | registeredOperation/pathModel/bodyModel/textEmbeddings/rerank/statelessResponses/hostedAgentPost/a2aJsonRpc/realtimeCreate/realtimeHangup/liveCreate/pii/documentAnalysis/documentAnalysisStatus。 |
| `C.bodyFormat` | HTTP書込はjson、GET等はnone | 本文形式。native protocolはnone。 |
| `C.requestBodySizeCheck` | 未指定／`actual`、非JSONの指定操作は`declared` | 共有部品のサイズ確認方法。`actual`は申告・実サイズ、`declared`は申告サイズだけを確認する。`declared`はbinary／multipart／textに限定する。標準のSTT・転記・画像編集・TTSが指定し、TTSは専用SSML部品で実サイズも確認する。 |
| `C.maxRequestBytes` | 通常AIは524288000 | APIM Classicの500 MiBバッファー枠。文書は500000000、PIIは1000000。提供元の制約が先に適用される場合がある。管理対象AI以外は既定WAFの容量とも整合させる。 |
| `C.contentTypes` | 必要な操作だけ設定 | 許可するmedia type。binary/multipart/textで必須。 |
| `C.allowedModels` | モデル例ごとに指定 | 設定対象のモデル一覧。配備時は基盤モデルを配備定義と照合する。実行時のモデル検査の範囲は操作のポリシーで確認する。 |
| `C.parameter` | pathModel例だけ指定 | モデル名を持つURL変数。 |
| `C.maxOutputTokens` | 操作・モデルごとに指定 | モデル別上限の外側の最大値。標準APIは`model-capabilities.json`から生成し、不定値だけの場合はnull。 |
| `C.maxOutputTokensByModel` | 標準APIは生成 | モデル名ごとの出力上限。不定・未対応・未確認はnullとして区別。 |
| `C.outputTokenLimitKinds` | 標準APIは生成 | `documented`／`dynamic`／`unsupported`／`unverified`で上限の根拠を区別。 |
| `C.initialOutputTokens` | Realtime作成時のみ | 接続作成時の初期設定。モデルの最大出力とは別の値。 |
| `C.outputTokenParameter` | モデルAPIに応じ指定 | 上流APIの出力上限項目名。 |
| `C.allowedMethods` | A2A例だけ指定 | 許可JSON-RPC method。 |
| `C.integerLimits` | {}または操作例 | JSON整数項目の制約。 |
| `C.integerLimits.<項目>.minimum` | 操作例ごとに指定 | 整数下限。 |
| `C.integerLimits.<項目>.maximum` | 操作例ごとに指定 | 整数上限。 |
| `C.integerLimits.<項目>.required` | 省略→false | 当該JSON項目の必須化。 |
| `C.arrayLimits` | {}または操作例 | 配列件数上限。 |
| `C.stringChoices` | {}または操作例 | 文字列の許可値。 |
| `C.embeddingDimensionsByModel` | textEmbeddings例で指定 | 許可モデル別の既定次元`default`・上限`maximum`と、必要な場合の選択肢`allowed`。 |
| `C.maxResponseBytes` | 文書は524288000 | 文書の上流結果と公開応答のbyte上限。 |
| `C.apiVersion` | 文書は2024-11-30 | 文書解析と状態確認の提供元API版。 |
| `C.modelVersion` | PIIの固定評価版 | PII処理版。モデル配備版とは別。 |
| `C.allowedLanguages` | PII例はja/en | PIIの許可言語。 |
| `C.allowedWarningCodes` | [] | 許容PII warning。 |
| `C.model` | Realtime例のモデル名 | Realtimeモデル。 |
| `C.sessionTtlSeconds` | Realtime例は600 | セッション有効秒。 |
| `C.publicCallPath` | Realtime公開パス例 | 通話制御APIの公開パス。 |

`textEmbeddings`は登録済みのOpenAI `POST /embeddings`またはCohere `POST /embed`のテキスト埋め込み用です。固定16件の上限は設けません。`embeddingDimensionsByModel`は`allowedModels`と同じモデル名を過不足なく持ち、各モデルの`default`と`maximum`を指定します。`allowed`を省略した場合は1から`maximum`までの整数を許可し、指定する場合は重複なく`default`と`maximum`を含めます。Cohereでは`allowed`が必須です。ベクトル次元は生成の出力トークン上限とは別のため、出力トークンの設定は使用しません。

`rerank`はFoundryのJSON `POST /rerank`用です。標準定義では独自の文書件数枠・`top_n`必須指定を設けません。query・各文書は非空白の文字列、文書は1件以上、`top_n`を指定した場合は文書数以下としてGatewayでも検査します。本文を切り詰めたり返却件数を補正したりしません。出力トークンの設定や`modelSettings`は使用しません。

`openai-inference-v1`では、クライアントがAPI・操作と本文の`model`を指定し、APIMがモデル名・別名から内部設定を選びます。`X-Egakumi-Profile`は設定例・Portal・試験CLIへ追加しません。残る旧ヘッダーは選択や値の検査に使わず、下流転送前に削除します。

内部設定は、操作直下の基本設定へ`modelSettings.<モデル名>`の差分を重ねます。差分に指定した項目を項目単位で置き換え、例えば`integerLimits`を指定した場合はその制限表全体を置き換えます。`modelSettings`は省略・空・部分指定が可能で、`allowedModels`とは役割を分けます。モデル別差分に`allowedModels`・`modelAliases`・入れ子の`modelSettings`は指定しません。`maxOutputTokensByModel`と`outputTokenLimitKinds`も操作直下へ一度だけ保持します。モデル名を使わない業務APIは操作の基本設定だけで扱います。

標準ChatはGPT群の`settingId`が`chat`、互換モデル群が`compatible`、model-routerが`router`です。互換モデル群だけ出力項目を`max_tokens`へ置き換えます。Responsesの`settingId`は`response`です。FLUXは`flex`／`pro`それぞれのモデル別差分に固定上流URIを保持します。モデル別の小さい本文上限がある場合は、モデル選択前の操作上限を最大値にまとめても、そのモデルの上限を差分に残します。

`settingId`は監査でモデル設定を識別します。

AI専用WAFは受付容量と検査枠を分離し、先頭2000 KiBだけ検査して超過部分も受け付けます。認証・管理系は既存のサイズ強制を維持します。文書は開始POSTの202受付後に所有者付きGETで結果を取得します。[WAFの範囲](../../testing/waf-compatibility.md)・[文書解析](document-analysis.md)

接続先が異なる操作には`operations[].backendOverride`を指定できます。指定できるのは`backendService`・`backendUrl`・`backendAudience`・`backendAuthentication`・`backendHeaders`で、省略値はAPI既定を継承します。下流URLは固定の登録値だけを使います。secretを使う認証のoperation overrideは扱わず、API既定に設定します。公開prefixと下流のパス（例`/rest`）は別に管理します。

## MCP・WebSocket・本人接続

| パラメタ名 | 公開サンプル値 | 意味 |
| --- | --- | --- |
| `A.mcp.name` | PII MCP例 | RESTから生成するMCP識別名。 |
| `A.mcp.displayName` | PII MCP例 | MCP表示名。 |
| `A.mcp.path` | tools/pii-v1-mcp | 生成MCPの公開パス。 |
| `A.mcp.operationNames` | pii_redact_text | MCPで公開するREST操作。 |
| `A.mcpProxy.transport` | 方式別streamable/sse | 既存MCPのtransport。 |
| `A.mcpProxy.endpoints` | 方式別endpoint例 | 転送先endpoint配列。 |
| `A.mcpProxy.endpoints[].name` | message、旧方式はsseも | endpointの役割。 |
| `A.mcpProxy.endpoints[].uriTemplate` | 相対パス例 | 下流MCPパス。 |
| `A.websocket.allowedModels` | Realtime通常/miniの配列 | 同じWSS URLで選択できる登録モデル。 |
| `A.websocket.defaultModel` | allowedModels内のモデル | 公開queryのmodelを省略した場合のモデル。 |
| `A.websocket.queryParameters` | 固定query例 | 上流へ渡す固定query。秘密や動的宛先は不可。動的モデル選択時のmodelはallowedModels/defaultModelで管理。 |
| `A.websocket.queryParameters.api-version` | 固定API版の例 | 上流API版。 |
| `A.websocket.queryParameters.model` | モデル例 | 上流モデル。 |
| `A.websocket.queryParameters.language` | 言語例 | 言語。 |
| `A.websocket.queryParameters.voice` | voice例 | 音声voice。 |
| `A.websocket.realtimeObserver.sessionTtlSeconds` | 600 | Realtime監視/制御の有効秒。 |
| `A.websocket.realtimeObserver.publicCallPath` | Realtime公開パス例 | 対応する通話制御パス。 |
| `A.credentialConnection.mode` | `personal`／`service` | 本人接続セッションか、管理者が固定したサービス接続を選ぶ。 |
| `A.credentialConnection.providerName` | Graph本人接続例 | OAuth provider名。 |
| `A.credentialConnection.connectionName` | service方式だけ設定 | providerに事前登録した固定サービス接続名。 |
| `A.credentialConnection.enabled` | 記入例はtrue | falseは利用拒否。匿名接続へ切替しない。 |

`A.mcpProxy.endpoints`はAPI正本では配列です。Azureへの送信時だけ、Streamable HTTPは`mcp`、旧SSEは`sse`（GET）と`messages`（POST）をキーにした辞書へ変換し、作成済みbackendを`backendId`で関連付けます。利用者が設定する接続先パスはこの変換で変更しません。配備の受理と、各transportの実接続・tool実行は別に確認します。
