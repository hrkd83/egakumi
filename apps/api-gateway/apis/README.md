# 標準APIの配備定義

このフォルダーのJSONは、Gatewayへ実際に配備する標準APIの定義です。`<API名>.json`の1 API 1ファイルで管理し、ファイル名とJSON内の`name`を一致させます。

`scripts/deployment/deploy_gateway.py`は19ファイルの標準API正本を常用で読み込みます。公開params/configの`apiDefinitions`・`publishApis`による一覧置換・切替は廃止し、旧項目は空配列やfalseでも拒否します。ディレクトリ内のJSONはAPIオブジェクトだけを持ち、`apiDefinitions`等の包みは付けません。今回の公開入力整理は既存アプリID・secret・認証fallbackの切替を伴わず、Azure反映と全環境再構築E2Eは未実施です。[入力配置と移行](../../../docs/setup/07-params.md#公開入力71主項目の判断記録)を参照してください。

| ファイル | 主な機能 |
| --- | --- |
| [openai-inference-v1.json](openai-inference-v1.json) | Chat Completions・Responses |
| [openai-embeddings-v1.json](openai-embeddings-v1.json) | OpenAI埋め込み |
| [cohere-inference-v1.json](cohere-inference-v1.json) | Cohere埋め込み・リランキング |
| [openai-images-v1.json](openai-images-v1.json) | OpenAI画像生成・編集 |
| [blackforest-images-v1.json](blackforest-images-v1.json) | FLUX画像生成・編集 |
| [azure-speech-v1.json](azure-speech-v1.json) | 音声合成・音声認識 |
| [openai-audio-v1.json](openai-audio-v1.json) | 音声ファイルの文字起こし |
| [microsoft-decision-v1.json](microsoft-decision-v1.json) | Microsoft Decision |
| [openai-realtime-control-v1.json](openai-realtime-control-v1.json) | Realtimeの接続作成・終了 |
| [openai-realtime-ws-v1.json](openai-realtime-ws-v1.json) | RealtimeのWebSocket接続 |
| [openai-realtime-observer-v1.json](openai-realtime-observer-v1.json) | Realtime通話のサーバー側監視接続 |
| [azure-voice-live-ws-v1.json](azure-voice-live-ws-v1.json) | Voice LiveのWebSocket接続 |
| [azure-voice-live-webrtc-v1.json](azure-voice-live-webrtc-v1.json) | Voice LiveのWebRTC制御接続 |
| [openai-live-control-v1.json](openai-live-control-v1.json) | Liveのセッション作成 |
| [openai-live-ws-v1.json](openai-live-ws-v1.json) | LiveのWebSocket接続 |
| [openai-transcription-ws-v1.json](openai-transcription-ws-v1.json) | リアルタイム文字起こしのWebSocket接続 |
| [pii-v1.json](pii-v1.json) | 個人情報の検出・マスキング |
| [graph-profile-v1.json](graph-profile-v1.json) | Microsoft Graph /meの12属性取得。キーはCM本人接続、BearerはOBO |
| [document-analysis-v1.json](document-analysis-v1.json) | PDF・画像の文字認識と文書レイアウト・表の解析 |

## 埋め込みAPIの入口

OpenAIの`POST /models/openai-embeddings/v1/embeddings`とCohereの`POST /models/cohere/v1/embed`は、JSON本文のモデル・入力型・次元等の契約を使います。この共通の入口検査はContent-Typeの1値・非空MIME部分だけを確認し、`application/json`という会社一覧との比較は行いません。メソッドとURLはAPIMが操作登録から照合し、クエリによる受付制限も撤去します。WAFや下流のJSON受付は別の処理です。

OpenAIの`api-version`例外とCohere Embedのquery禁止を、この入力検査には残しません。共通認証によるURLへの`api-key`／`subscription-key`指定の拒否は維持します。Cohereの既存の接続先`/providers/cohere/v2/embed`を含むURI転送とWAFの除外は別に管理し、未棚卸です。この改修だけで任意のqueryが下流へ転送されることや、指定したAPI版をモデルが受け付けることは保証しません。[OpenAI Embeddings v1のAPI版](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/embeddings)

本文は既存のモデル・入力型・次元・受信byte制約を維持します。OpenAIの`input`は文字列、文字列配列、非負整数のtoken配列、token配列の配列を扱い、Cohereの`texts`は文字列配列を扱います。許可されたnative optionの扱いと、本文を変更せず転送する動作は維持します。

## 変更する場所

操作の基本設定と、任意のモデル名別差分`modelSettings`を対象APIのJSONで管理します。クライアントはAPI・操作と本文のモデル名を指定し、APIMがモデル名・別名から内部設定を自動選択します。Portalや試験CLIへprofile選択や`X-Egakumi-Profile`の付与を追加しません。残る旧ヘッダーは選択・値数/ID検査に使わず、下流転送前に削除します。一つのモデルの設定は一意に取得し、profiles・defaultProfile・modalitiesは使いません。別名は操作直下のmodelAliasesへまとめます。購読利用枠やCM接続profileは対象外です。

非JSON操作の本文サイズ確認は、操作の基本設定の`requestBodySizeCheck`で指定します。未指定／`actual`は共有部品で申告・実受信サイズを、`declared`は申告サイズだけを確認します。標準の音声認識・音声ファイルの文字起こし・画像編集・音声合成・Document解析の5操作に`declared`を指定します。前の3操作はサイズ検査だけの本文全量読取りを省き、申告がない場合などの実サイズによる独自413は行いません。音声合成は[SSML部品](../policies/fragments/request/core-speech-ssml-guard/README.md)で本文を一度読み、実サイズと既存の宣言条件を確認します。共有部品の申告超過による早期413は維持します。Document解析もサイズ確認だけの全量読取りを撤去し、原本と提供元JSONを本文変換なしで転送します。[共有の本文サイズ確認](../policies/fragments/request/core-model-limits/README.md)を参照してください。

TTSの対象操作は、既存の`requiresSpeechSsmlGuard`による選択から配備時に`speechSsmlOperationIds`へ生成し、`gatewayControlSettings`に保持します。これはクライアント指定や名前付きの値ではありません。HTTP APIが共有サイズ検査直後、利用枠・下流認証準備・`accepted`監査より前で部品を呼び、結果の監査と400／413応答も担当します。C#へAPI名・操作名による分岐を追加せず、TTS集約時点では未棚卸の形式・転送条件やDocument・JSON処理を変更していません。TTSのAzure反映・読戻しは完了し、キー／Bearerの正常WAV応答とDTD宣言拒否の400、対応する監査照合を実接続で確認しました。実サイズ超過413など残る受入は未実施です。

HTTP操作の入力検査は`operations[].inputContract = {}`で対象を明示します。OpenAI embeddingsを含む従来の13操作はすべてこの指定を使います。Bicepは`gatewayControlSettings.inputContracts`へ対象の操作IDだけを記録し、各値を`{}`にします。空の値は、全対象でContent-Type宣言の存在・1値・非空MIME部分を確認する指定です。WAFの型別ルールが参照するヘッダーの存在を、このAPIMの確認で保証します。MIME一覧比較、C#でのメソッド再確認、query拒否は行いません。操作のmethod・URL登録とAPIMの操作照合は維持し、未指定の操作へ検査を自動追加しません。後段用の`operationConstraints.contentTypes`、query観測・URL資格情報拒否、監査、本文制約・URI書換え・Speechの転送設定・WAFはこの入口検査の整理では変更していません。入口検査のAzure反映・読戻しは完了し、Content-Type未指定の400と対応する拒否監査を一部確認しました。追加のContent-Type異常系や未知メソッドの実APIM照合などは未実施です。[入力検査の使い方](../policies/fragments/request/core-api-input-validation/README.md)を参照してください。

URLクエリの観測対象は、HTTPの`operations[].queryObservation`、生成MCP表口の`mcp.queryObservation`、外部MCPの`mcpProxy.endpoints[].queryObservation`にラベルを指定します。同じラベルを複数の操作・受付で共有でき、未指定の入口は観測対象外です。標準ではLive・Realtimeの作成操作とPIIのMCP表口に、従来の`liveCreate`・`realtimeCreate`・`piiMcp`を指定しています。ラベルはASCII英字で始まる1～64文字の英数字・`_`・`-`とし、小文字の`none`／`unknown`は完全一致で予約します。

BicepはHTTPの操作ID辞書`queryObservations`、またはMCPのHTTPメソッドと公開相対path辞書`mcpQueryObservations`を生成します。公開pathと下流サーバーの`uriTemplate`は区別します。[クエリ観測部品](../policies/fragments/request/core-query-observation/README.md)は有無・複数値を記録するためのJSONを作り、拒否modeや共有設定は使いません。観測はキー／Bearer認証やAPI入力検査の代わりにはならず、URI変換・WAFも変更しないため、観測ラベルを付けるだけでqueryが下流へ通るとは扱いません。観測専用化のローカル改修・検証とAzure反映・読戻しは完了しました。キー／BearerのResponses JSON・SSEなど共通処理の限定受入は確認済みですが、観測を含む全入口の受入は未完了です。旧`queryGuard`参照・旧fragment・旧Named Valueは参照消滅を確認して退役し、不在の読戻しも完了しました。現行定義には使いません。

- APIの公開パス、操作、許可モデル、要求上限、下流接続、製品・購読との関連は、対象APIのJSONで変更します。
- `released: true`のAPIと生成MCPは、通常キーで使う`released-apis`製品へ関連付けます。省略・`false`は検証用製品のみで、標準19 APIはすべて`true`を明示しています。リリースを取り消す場合は`false`へ変更してGatewayを配備します。`productId`は既存の検証用製品を指定したままにします。
- モデルの配備名・版・SKU・容量は、`config/test.json`または`config/production.json`で管理します。APIファイルへモデルの配備設定を重複させません。
- CM共通定義・ログインセッション設定はconfig、アプリ登録・秘密参照と環境固有のID・公開ホスト名はparamsで管理します。標準Graphは双方の`credentialProviders.graphDelegated`、追加providerは`additional[]`で定義／登録を分けます。APIファイルには、そのAPIが必要とする本人接続の条件だけを持たせます。
- 共通ポリシーは[../policies/](../policies/)、APIMへ登録する入出力スキーマは[../schemas/](../schemas/)で管理します。

APIを追加する場合は、重複しない`name`で1ファイルを追加し、公開パス・バックエンド・認可条件・製品との関連をそろえます。APIを変更した後は、ローカルの登録計画を確認してからGateway配備を実行します。ファイルを編集・削除するだけで、既存Azureリソースが自動更新・削除されるわけではありません。

PIIから生成するMCPと、CMのサインイン・ブラウザー用の補助APIは登録処理が生成します。この19ファイルには含めません。
