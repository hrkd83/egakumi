# App Gateway WAFの互換性・遮断確認

GatewayとPortalは、OWASP 3.2のPreventionと本文検査を有効にしたまま使用します。SQL、HTML、シェルコード、不透明な機械生成文字列をモデル・PII等へ渡すことは正当な利用です。そのデータを実行命令として扱うURL、ヘッダー、APIの制御情報とは分け、正規の完全パス・データ欄に限り、個別ルールまたは承認したアプリケーション攻撃ルールグループを調整します。

コマンドはリポジトリルートで実行し、[第1章](../setup/01-prerequisites.md#1-5-環境別のローカル領域を準備する)で設定した`DEPLOY_PARAMS_FILE`と絶対パスの`DEPLOY_LOCAL_DIRECTORY`を使います。計画・実測記録は同じ環境の領域へ保存します。

モデル等のデータ欄除外の正本は `apps/gateway/waf-data-exclusions.json` です。各profileはGatewayまたはPortalの完全パスを指定し、`RequestArgValues`と、動的JSONキーで必要な場合の`RequestArgKeys`を使います。署名付き文書状態参照・通話終了だけは既存operationの可変ID位置を限定patternへ写し、その専用profileで正確なticket名の`RequestHeaderValues / Equals`を使います。任意のAPI templateやheaderを対象にしません。`StartsWith`は`state.`、`questions.`、JSON Schemaやobject形式の引数配下など、区切りを含むデータ構造に限定します。集計規則949110は除外しません。

## 受付容量と検査範囲

| 対象 | 現行資材の制御 |
| --- | --- |
| manifestのGateway／Portal AI完全パス、正本Speech・文書受付（次行の制御用途を除く） | Prevention・本文検査を維持。先頭2000 KiBまで検査し、本文・ファイルのサイズ強制は解除。超過部分は検査せず受け付ける |
| `g-document-status`・`g-realtime-ticket`・`p-document-status` | Prevention・本文検査とbaseの容量・サイズ強制を維持。Portal文書状態参照のアプリ側32 KiB上限も維持 |
| base・認証callback・管理系など上記以外 | 既定Prevention。configの本文検査枠・本文／ファイルサイズ強制を維持 |
| 外部管理WAF | 自動変更しない |

2000 KiBはAI入力の受付上限ではありません。通常AIのAPIM受付はClassicのバッファー上限500 MiB、文書は500,000,000 bytesで、提供元の制約が小さい場合はそちらが適用されます。WAFの検査枠を超えた本文まで検査したとは扱いません。容量緩和のAzure反映・実経路での通過確認は未実施です。

## 用途別のポリシー構成

名前はGatewayが`wafpol-g-<用途>-<ID>`、Portalが`wafpol-p-<用途>-<ID>`です。`<ID>`にはparamsの`services.apimInstanceId`に保存した6文字の構築識別子を共通利用します。Bicepと個別配備は同じ名前を計算し、更新や途中再開で識別子を生成し直しません。APIのURLは変更しません。

ポリシーの用途はGatewayが16個、Portalが12個の計28個です。Gatewayは公開対象に選んだ経路だけを関連付け、APIの登録だけで未知の用途のポリシーを追加生成しません。

| 用途 | Gatewayの用途名 | Portalの用途名 |
| --- | --- | --- |
| 既定のHTTP経路 | `base` | `base` |
| Microsoft認証callback | `auth` | `auth` |
| Chat Completions | `chat-completions` | `generate`（Responsesも同じ入口） |
| Responses | `responses` | 上記`generate` |
| 埋め込み | `embeddings` | `embeddings` |
| リランク | `rerank` | `rerank` |
| 意思決定 | `decision` | `decision` |
| 画像生成・編集 | `images` | `images` |
| 音声 | `speech`（Azure Speech） | `speech`（Portalの音声入口） |
| 音声文字起こし | `transcribe` | 上記`speech` |
| Realtime | `realtime`（HTTP calls） | `realtime`（停止用handle） |
| Realtimeの署名付き制御 | `realtime-ticket`（hangup・observer） | 上記`realtime` |
| Live | `live`（HTTP sessions） | 個別のデータ欄除外なし |
| PIIのAPI・MCP | `pii-redact` | `pii-redact` |
| 文書アップロード | `document-upload` | `document-upload` |
| 文書の状態参照 | `document-status` | `document-status` |

Gatewayの画像、埋め込み、PIIは複数の完全パスを1つのポリシーへまとめ、各パスに必要な除外の集合和を適用します。画像の`input_image`、埋め込みの`input`と`texts`、PIIの`text`と`params.arguments.text`の除外は、同じポリシーの全経路に作用します。PortalのPIIは両入口とも`text`と同じCookie除外なので、除外範囲を追加せず共有します。

用途名は検査するHTTP入口を表します。署名ticketを使うRealtime observer以外のRealtime／Live WebSocket入口など、個別のデータ欄除外がない経路は`base`を使います。データ欄の除外はmanifestに明記した条件から生成し、APIのMIMEや機能名によって別の除外を自動追加しません。外部管理ポリシーの指定時は、そのポリシーを再利用します。

## 保護条件の共通化

OpenAIのResponsesとChat Completionsは、同じ用途のデータ欄に同じアプリケーション攻撃グループ除外を適用します。[MicrosoftのAIチャット向けWAFチューニング指針](https://learn.microsoft.com/azure/architecture/ai-ml/architecture/baseline-microsoft-foundry-chat#web-application-firewall-tuning)に沿って、会話・指示・コード・ツール結果・引数・説明・宣言的Schemaの欄だけを選びます。SQLやコードの構文をモデルへ渡すことは、Gatewayでその構文を実行することとは別の処理です。WAFの評価はHTTP要求単位であり、履歴やツール結果が後続要求の本文に含まれることで検出対象が増えます。

manifest version 2は、validatorに列挙したprofile・経路・データ欄だけで、LFI（930）・RFI（931）・RCE（932）・PHP（933）・XSS（941）・SQLi（942）のグループ除外を許可します。Azureへは`ruleGroupName`を指定し`rules`を省略した除外を渡します。version 1の個別ID形式は引き続き読み取れます。全managed ruleの除外、集計949110/980130の除外、列挙外の用途・欄へのグループ除外は許可しません。既存の921120・921130・921150等の個別例外は保持し、プロトコル系グループを一括除外しません。

Responses／Chat Completionsの承認済み自由文Valuesと、Schema・構造化引数prefixのValuesは、この6グループに`800100`・`920230`・`920270`・`920271`を加えた共通セットにそろえます。従来の欄ごとの920系の差を統一し、summary、refusal、予測出力、形式説明など同じ性質の正規データにも適用します。これはMicrosoftが指定したルール一覧ではなく、AIチャット向けの指針をこのAPI契約へ適用する構成判断です。

このR/Cの通常本文Valuesには、HTTP要求の例文やログを文字列として含められるため、`921120`・`921130`も個別IDでそろえます。上記共通セットを使うexact自由文と、Schema・構造化引数の値prefixだけが対象です。JSONキー、不透明な暗号化・Base64・音声ID、画像URL、metadata、SDPや他profileへ、この2 IDを追加しません。本文中のHTTP構文と、実際のHTTP要求のheader・URI・query・framingは区別し、921グループ全体の除外やruleの無効化は行いません。

Schema・構造化引数のKeysは、下表の11個の承認prefixすべてに6グループと`800100`・`921150`を適用します。JSONのプロパティ名はエスケープした改行やコードを含められ、モデルへ渡すデータ名がpayload中のCR/LF検知に一致することがあります。このため`921150`は`RequestArgKeys / StartsWith`の有限prefixに限って除外します。既存Keysの`920270`・`920271`等の追加例外は保持し、`920230`等を全キーへ一律に追加しません。921のグループ全体、header、兄弟fieldへこの個別例外を広げません。引数除外は同名queryにも作用するため、APIMのquery禁止を維持して拒否します。

| 用途 | Responsesの例 | Chat Completionsの例 |
| --- | --- | --- |
| 会話・指示・ツール結果 | `input`、`input.content`、`input.content.text`、`input.summary.text`、`input.content.refusal`、`input.output`、`input.output.text`、`instructions` | `messages.content`、`messages.content.text`、`messages.refusal`、`messages.content.refusal`、`messages.reasoning_content` |
| 予測出力・形式説明 | `text.format.description` | `prediction.content`、`prediction.content.text`、`response_format.json_schema.description` |
| 文字列形式の呼び出し引数 | `input.arguments`、`input.input` | `messages.tool_calls.function.arguments`、`messages.function_call.arguments`、`messages.tool_calls.custom.input` |
| ツール説明・grammar | `tools.description`、`input.tools.description`、`input.tools.tools.description`、`input.tools.format.definition`、`input.tools.tools.format.definition` | `tools.function.description`、`functions.description`、`tools.custom.description`、`tools.custom.format.grammar.definition` |
| 引数Schema | `tools.parameters.`、`input.tools.parameters.`、`input.tools.tools.parameters.` | `tools.function.parameters.`、`functions.parameters.` |
| 出力Schema | `text.format.schema.`、`tools.output_schema.`、`input.tools.output_schema.`、`input.tools.tools.output_schema.` | `response_format.json_schema.schema.` |
| object形式の呼び出し引数 | `input.arguments.` | 個別prefixの追加なし |
| native toolの履歴データ | `input.action.command`、`input.action.commands`、`input.operation.diff`、`input.output.stdout`、`input.output.stderr` | 個別fieldの追加なし |

これは用途の対応表で、許可する実際のselectorの正本はmanifestとvalidatorです。値は明示した`Equals`、動的Schemaとobject形式の引数は区切り付き`StartsWith`で値とキーを選びます。`input.arguments`の文字列形式と`input.arguments.`のobject形式を分け、近い名前の兄弟fieldを含めません。tool search履歴で返る直置きのtool定義とnamespace内のtool定義は、それぞれ明示した説明・grammar・Schemaだけを対象にします。

`StartsWith`はAzureが抽出した引数名の文字列prefixであり、JSONPathや型検証ではありません。配列を含む正規本文と抽出後の引数名を対応させ、たとえば`input[].summary[].text`を`input.summary.text`で確認します。`input.`・`messages.`・`tools.`全体には広げません。通常本文・不透明値・URL兼用欄・metadataを下記の用途で分け、制御項目や取得先URLへグループ除外を自動追加しません。Schemaのプロパティ名をモデルや操作の制御項目と誤認せず、nativeのoptional fieldとclient-held Itemsの互換性を維持します。

| 種類 | 対象 | 除外の方針 |
| --- | --- | --- |
| 通常自由文・動的データ | rerankの`documents`・`query`、Decisionの`state`・`questions.`・`state.`、transcribeの`prompt`、Liveの`instructions`、PIIの`text`・`params.arguments.text`、Portalの`message`・speechの`text` | Valuesは上記6群＋800100・920230・920270・920271へ統一。Decision Keysは6群＋800100・921150と旧920系例外を保持 |
| SDP | `g-realtime`・`g-live`のexact `sdp` | 6群と既存の個別例外を保持。URI・ICE接続先の受付制御を緩める指定ではない |
| 不透明な履歴・ファイル値 | Responsesの`input.encrypted_content`・`input.content.file_data`、Chatの`messages.content.input_audio.data`・`messages.content.file.file_data`・`messages.audio.id` | exact Valuesへ6群。暗号化・Base64・参照IDを自然文と区別し、Known-CVEsや920の新規例外は追加せず、旧例外だけ保持 |
| Portalの封印handle | `p-realtime`・`p-document-status`のexact `handle` | 6群と旧例外だけ。復号・本人／購読の束縛・期限をアプリで検証 |
| URLとdata URLの兼用欄 | Responses／Chatの画像URL、FLUXの`input_image` | 既存個別IDを維持。R/Cの画像URL2exactに931130だけ追加し、FLUXの`input_image_2`には既存と同じSQL3規則だけ追加。URL兼用欄を6群へ一律統合しない |
| metadata・設定JSON | `client_metadata.x-codex-turn-metadata`、Portal speech／文書multipartの`input` | 旧個別IDを維持。設定内のmodel・台帳ID・制御値へ自然文用除外を広げない |

`encrypted_content`は[Responsesのreasoning履歴](https://platform.openai.com/docs/guides/reasoning)として後続要求へ返す正規値です。Chatの音声・ファイル・音声IDも[公式入力型](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)の区別を保ちます。WAF除外はHTTP入力の誤検知対策であり、そのmodalityが個別モデルで利用可能であることや、ファイル取得・推論成功の証明にはしません。

署名付き制御は次の既存入口に分離します。文書アップロード用のraw本文調整を状態参照へ流用しません。

| profile | 現行経路 | 限定selectorと入口条件 |
| --- | --- | --- |
| `p-document-status` | POST `/api/tools/documents/status` | JSON `handle`、queryなし、同一Origin・本人・購読・封印内容を照合。JSON MIMEの大小文字対応だけを合成 |
| `g-document-status` | GET `/tools/documents/v1/operations/{operationId}` | header `X-Core-Document-Ticket`、queryなし、UUID形式と署名・購読・対象・期限をAPIMで照合 |
| `g-realtime-ticket` | POST `/models/openai-realtime/v1/calls/{callId}/hangup`、GET `/models/openai-realtime/v1/observer/` | header `X-Core-Call-Ticket`、hangupはqueryなし、observerは単一`call_id`だけ。署名・購読・call ID・期限をAPIMで照合 |

この3profileは6群だけをexact値・headerから除外し、baseの本文容量設定を維持します。Portal停止入口も未知のqueryを認証後・本文読取り前に拒否します。AppGWのpatternは上記2operationのAPI名・操作名・method・template・kind・bodyFormatが正本と一致した場合だけ生成し、他templateを展開しません。observerの完全パスも正本のAPI名・GET操作・WebSocket・署名observer設定に一致する場合だけを選び、同じURLのcustom APIへ除外を追加しません。[AppGWのPathPattern](https://learn.microsoft.com/en-us/azure/application-gateway/url-route-overview#pathpattern)は途中のwildcardを扱えないため、関連付けは`/tools/documents/v1/operations/*`と`/models/openai-realtime/v1/calls/*`です。`/calls`作成の完全パスは既存`g-realtime`のままです。AppGW自体はmethodやIDの構文を判定しないため、未知の子経路・他verb・余分なpath成分は既存APIのoperation照合・APIMの検証で拒否します。一般header、他Cookie、query全体への除外は追加しません。

native toolの履歴は[Responsesの公式入力型](https://developers.openai.com/api/reference/resources/responses/methods/create)に基づき、local shellの`action.command`配列、shellの`action.commands`配列、patchの`operation.diff`、shell結果の`output[].stdout`・`stderr`だけを上表のexact Valuesで選びます。local shell／patchの文字列形式の結果は既存の`input.output`で扱います。Gatewayが実行しない会話データに対する除外であり、Responsesのtop-level `tools`をfunctionに限定する既存APIM制約、認証・API権限を変更しません。`env`、`user`、`working_directory`、`path`、timeout、outcome、exit code、type、IDや、媒体と衝突する`input.result`へ除外を追加しません。ツールを実行するクライアントの権限・承認・sandboxは別途維持します。

Chat Completionsの`messages.reasoning_content`も、モデルが生成した推論テキストを履歴へ戻す正規の自由文として同じValuesセットで扱います。[Azureのreasoning応答例](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-chat-reasoning)と[DeepSeekのthinking modeの履歴契約](https://api-docs.deepseek.com/guides/thinking_mode)は区別し、Azureの応答例だけで後続要求の受理を実証したとは扱いません。除外の根拠は公式型とデータの用途であり、特定モデルの推論成功を除外の前提にしません。実経路でのWAF通過と、モデルによる要求受理・推論成功を別々に検証します。

同名queryにも引数除外が効くため、標準OpenAIの2操作にはAPIMの入口制約を組み合わせます。認証済み購読を確認した後、POST、単一の`application/json`のContent-Type、query引数がないことを検査します。`charset`等のmedia typeパラメーターは許容し、`api-version`を含めてquery引数は全拒否します。APIMのモデル許可、受信byte制約、Responsesのstateless制約、下流認証は維持します。全本文の厳密Schema検証や、全購読でのrate/token制限が既に有効だとは扱いません。画像・ファイルのURL取得、ツール実行先の引数・権限・sandboxは別の実行境界です。

画像生成・編集では、`g-images`・`p-images`の`RequestArgValues / Equals / prompt`だけに同じ6カテゴリを適用します。各profileの既存prompt除外のうち、この6カテゴリに属する41個別IDをグループへ統合し、`800100`・`920230`・`920270`・`920271`の個別除外は保持します。`input_image`のSQL規則`942340`・`942390`・`942450`を維持し、同型の`input_image_2`にもこの3規則だけを適用します。PortalセッションCookieの除外は変更しません。画像データ、mask、model等の制御欄へグループ除外を追加しません。これは画像APIの正規データ欄に限定した構成判断であり、画像生成モデル専用のMicrosoft推奨ルール一覧を採用したものではありません。

| 用途 | 完全パス | 正規の本文形式 |
| --- | --- | --- |
| `g-images` | `/models/openai-images/v1/generations` | JSON |
| `g-images` | `/models/openai-images/v1/deployments/gpt-image-2.5-flare/edits` | multipart |
| `g-images` | `/models/openai-images/v1/deployments/gpt-image-2.5-sunburst/edits` | multipart |
| `g-images` | `/models/openai-images/v1/deployments/gpt-image-2/edits` | multipart |
| `g-images` | `/models/blackforestlabs/v1/flux-2/generations` | JSON |
| `g-images` | `/models/blackforestlabs/v1/flux-2/edits` | JSON |
| `p-images` | `/api/models/images` | JSON |

画像のGateway入口も、継承した共通認証の後にPOST、queryなし、単一の正規Content-Typeを検査します。OpenAI編集は`multipart/form-data`、OpenAI生成とFLUXは`application/json`で受け、boundaryやcharset等のmedia typeパラメーターを許容します。multipartをJSONへ変換せず、元本文を保持して既存の受信byte・モデル許可・購読権限の検査へ進みます。Portal画像入口はPOST・同一Origin・セッション認証・JSONを要求し、同名promptを含む全queryを拒否します。未知のbody fieldや隣接パスがpromptと同じ除外を受けるとは扱いません。

埋め込みでは、`g-embeddings`の2完全パス`/models/cohere/v1/embed`・`/models/openai-embeddings/v1/embeddings`にある`RequestArgValues / Equals / texts`と`input`、`p-embeddings`の`/api/models/embeddings`にある同条件の`texts`だけを同じ6カテゴリへ統合します。各欄の旧45個別IDのうち41 IDをグループで包含し、`800100`・`920230`・`920270`・`920271`は各欄の個別除外として保持します。Gatewayの旧90組とPortalの旧45組、PortalセッションCookieの942450をすべて維持し、共有ポリシーの両fieldを一方の経路から撤去しません。model、dimensions、input_type、encoding_format、画像やmetadataへグループ除外を追加しません。

埋め込みの入口制約は、公開契約のAPI名・操作・パスと`textEmbeddings`/JSON契約に一致する2操作だけを対象とし、共通認証の後にPOST、単一の`application/json`を検査します。OpenAIはqueryなし、または単一の`api-version=v1`/`preview`だけを許可し、元の値を転送します。未知名・同名input/texts・重複・不正なversionは拒否します。CohereとPortalはqueryを全拒否し、Cohereのrerank等の別操作へ広げません。Portalの既存のOrigin・セッション・JSON・入力schemaの検査を保ちます。OpenAIの単一文字列、文字列配列、token配列・二重token配列とCohereの文字列配列を変更せず、モデル・次元・provider固有オプションの検査を維持します。検査先頭2000 KiBと既存の受信byte制約は変更せず、検査枠を超えた本文を全検査したとは扱いません。これはチャット向け指針を本文の文書データに適用する構成判断であり、embedding専用のMicrosoft推奨ルール一覧ではありません。[公式APIの任意api-versionと入力型](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/embeddings)を維持します。

JSONを受ける用途別WAF 18 profileでは、MIMEの大小文字による誤検知を避けるため、そのpath policy内だけ920420の許可リスト判定をcustom Blockへ置き換えます。正本は`apps/gateway/waf-json-content-controls.json`で、PythonとBicepが同じ有限のprofile・scope・完全パス・MIME形式を使います。対象はGatewayの既存9 profileと、`p-document-status`を含むPortalの9 profileです。`g-document-status`・`g-realtime-ticket`、基底・認証・正本にないJSON入口へは自動展開しません。

`Content-Type`が非空の場合だけ照合用に小文字化し、単一の`application/json`と正規のtoken／quoted-stringパラメーターを許可します。`Application/JSON; charset=UTF-8`は同じJSONとして扱い、`application/jsonp`、`text/plain`、カンマで結合した複数のMIME、不正なパラメーターは許可しません。共有の`g-images`と`p-speech`はJSONと`multipart/form-data`の集合和を許可し、各操作のAPIM／Portalでの形式検査を維持します。MCPの本文なしGET/DELETE等に新たなMIME必須条件を追加せず、欠落ヘッダーは既存の920340等のプロトコル規則とアプリ側受付条件に従います。[HTTP仕様のMIME比較](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.3.1)に沿うAPI契約上の構成判断です。

custom Allowは使わず、920470等の他の管理規則・JSON本文検査・既存のデータ欄除外・認証・入口制約を維持します。照合変換はヘッダーの実値を書き換えません。非JSON専用の`g-transcribe`と、raw本文用の`p-document-upload`、Speech／Gateway文書の既存調整も変更しません。PortalのRealtime JSON handlerは他のJSON handlerと同じ大小文字非依存の判定を使い、認証と読取り順を保ちます。

受入確認では、大小文字両方のMIMEで同じ本文を試し、正規のデータ欄が通ることと、除外外fieldの攻撃対照が遮断されることを独立して照合します。画像編集・音声のmultipartと、本文なしMCP操作を別に確認します。匿名canaryでのWAF通過、認証付きの不正入力拒否、有効モデルによる埋め込み応答を区別し、通常MIMEと大小文字混在MIMEの推論結果は件数・数値配列・次元を検査します。APIキー、入力本文、埋め込みvectorは公開記録へ残しません。

既存除外をグループ除外へ統合する際は、同じvariable・ルールセットで既存の各rule/groupを包含することを照合します。selectorは同一範囲、または承認された区切り付きSchema・構造化引数prefixがexact子field・より狭いprefixを覆う場合だけを認めます。無関係なselectorの撤去や、認証・callbackへの波及は許可しません。管理規則の変更は上記の完全パス限定のMIME判定置換等を正本に明示し、それ以外へ追加しません。IaCと運用CLIは同じmanifestを使用し、Prevention、本文検査、既存の検査容量・マスク・企業のcustomRulesを維持します。

Azureがグループ全体の除外を読み戻す際に省略した`rules`を空配列へ補完する場合は、承認済みOWASP 3.2グループの除外に限り、比較上だけ同じ状態として扱います。manifestでは引き続き`rules`を省略し、空配列の入力を拒否します。個別ルール、管理規則のoverride、未知の属性や他の設定差は同一視せず、再実行で不要なPUTを避けます。

PortalのHTTPSセッションCookie値は、`apps/portal/waf-session-exclusions.json`を正本とし、正確な名前`__Secure-egakumi.portal.session_token`を`RequestCookieValues`・`Equals`で選び、規則942450だけから除外します。Portal HTTPS listener専用の既定policyと、置換関係にあるPortal callback・データ欄policyへ同じ除外を合成します。Gateway側のpolicy、他のCookie、他の規則へは追加しません。HTTPの開発用Cookieを除外へ追加せず、Portalの認証・認可とCookieの保護属性は維持します。

raw SSML/WAVと正本の文書受付では、正規本文の相対パス`../`が930110に一致しました。AzureはXML/raw本文の項目単位除外に対応しないため、Speechの2パス・Gateway文書受付・Portalの`/api/tools/documents`の計4完全パスだけ920420／930110を調整し、URI・query・全header・cookieには4本のcustom Blockを合成します。930100・920470等の他の管理規則と本文検査は残します。正本は`apps/gateway/waf-raw-body-controls.json`、Python生成は共通の`waf_body.raw_body_policy`です。CRSの全正規化処理と完全に同等の代替ではなく、本文内のパス文字列を許容する限定した判断です。Portalはrawストリーミングを使い、互換用multipartの`input`に対する既存5 SQL規則の値除外も維持します。Portalの状態確認・隣接パスへraw本文の調整は広げません。

Log4j/JNDIの例文で検知する`Known-CVEs / 800100`は、ResponsesとChat Completionsの上表の同じ用途のデータ欄へそろえます。会話・指示・文字列／配列形式のツール結果・呼び出し引数・ツール説明・grammar定義の値、および承認済みの区切り付きSchema・構造化引数prefixの値とキーが対象です。Decisionの`questions.`と`state.`も、任意の質問名・判断対象JSONのキーを値と同じデータとして扱います。画像prompt、埋め込み本文、リランク本文、Portalのmessage等の既存800100除外は保持します。これは確認済みの誤検知を同義のデータ構造へ展開する構成判断であり、Microsoftがこの個別ルールやselectorを指定したものではありません。

`Known-CVEs`全体の除外や800100の無効化は行いません。model等の制御欄、取得先URL、暗号化履歴、metadata、binaryには追加せず、既存の個別例外も変更しません。DecisionのGateway／Portal入口はPOST・JSON・queryなしに限定し、認証後・本文読取り前に拒否します。未知のfield・query・header・隣接パスの攻撃対照と、同名queryの入口検証を独立して確認します。Prevention、本文検査、集計949110/980130とプロトコル規則は維持します。

Chat Completionsの画像data URLは、`RequestArgValues / Equals / messages.content.image_url.url`だけをXSS規則941130・941170とSQL規則942340・942450から除外します。Responsesの画像data URLは、完全パス`/models/openai/v1/responses`の`RequestArgValues / Equals / input.content.image_url`だけをXSS規則941130・941170から除外します。このURL兼用2欄には、正規の画像URLに一致するRFI規則931130だけを個別追加し、RFIグループ全体へ広げません。取得先URLの形式・実際の取得先・アクセス権の検証を代替する除外ではありません。toolの動的JSON Schemaキーは、承認された引数Schema prefixの`RequestArgKeys / StartsWith`に限り、上記6カテゴリのグループ除外へ統合します。隣接する別fieldや制御情報、headerへ広げず、集計規則949110と共通の保護条件は維持します。

SSMLのDTD/ENTITY宣言は、WAF本文カスタム規則のMIME対応に依存せず、正規TTS操作のAPIMガードで400 `InvalidSpeechMarkup`として拒否します。正本は`apps/gateway/apim/policies/speech-ssml-guard.xml`です。XMLや外部エンティティのparserを呼ばず、UTF-8/16/32を認識して実宣言だけを検査し、escaped例文・コメント・CDATAは許容します。独自の8192byte枠は撤廃し、継承した認証・受信byte検査の後、上流へ転送する前に宣言を検査します。元本文と上流URIは保持します。匿名401だけではガード成功とせず、認証付きの正常音声生成と明示400応答も確認します。SSML全体のschema検証は下流Speech側の責務です。

`infra/modules/application-gateway.bicep`と`scripts/waf_policy.py`は同じ正本を使用します。個別Gateway／Portal配備も照合するため、Azure上だけの手動例外が次回配備で消える構成を避けます。専用ポリシーは親を継承しないため、Prevention・本文検査・ログのマスク・customRulesと、manifestのデータ欄除外・上記のAI専用サイズ設定を合成します。企業管理の外部WAFは変更しません。既存の固有保護を表現できない場合は停止します。

通常の配備と同じparamsを指定して、まずローカル計画を確認できます。計画ではAzureに接続しません。適用時は対象環境とETagを照合し、不要なPUTを行いません。

```sh
python3 scripts/waf_policy.py --config "$DEPLOY_PARAMS_FILE"
python3 scripts/waf_policy.py --config "$DEPLOY_PARAMS_FILE" --scope all --execute
```

## 再現する試験

ResponsesとChat Completionsの対応するデータ欄を、PHPの変数関数呼び出しを含む言語別の例と、ツール呼び出し・ツール結果を含む履歴構造で確認します。summary・refusal・予測出力・形式説明、tool search履歴の直置き／namespace内の説明・Schema、object形式の引数と動的キー、native toolのcommand配列・patch差分・stdout／stderr、`reasoning_content`も対象にし、クライアント・API・managed rule版を更新するときに再実行します。Keysは既存・新規の全承認prefixで、コードとエスケープしたCR/LFを含む同じ合成キーを試します。承認prefixと近い名前の兄弟field、同名query、未知のbody field・header・隣接パスを対照に含めます。匿名canaryの通過確認と、認証付きの複数ターンE2Eを区別します。

`waf_probe.py`は公開API契約・Portalの実ハンドラーから入口を列挙します。JSON／配列／Schema／関数引数／動的キー、Unicode・記号・コード・Base64、画像・PDF・音声、SSML、multipart、SDP、WebSocket Upgrade、callback、query・header、サイズ境界、攻撃対照を合成入力で試します。

既定corpusに含む`typed-`ケースは、証跡の`data_type`で次のデータ型を区別します。既存ケースは保持し、型を明示していない旧ケースだけで新しい型の検証を済ませません。

| 型 | fixtureと確認事項 |
| --- | --- |
| `conversation` | Responses／Chatそれぞれ614,400 bytesの正しいUTF-8 JSON。複数turn、3関数のSchema、引数・結果、HTTPログのCR/LF、SQL・HTML・シェル・Unicodeを同時に含み、Responsesは非空summaryと暗号化履歴欄も含む |
| `opaque-base64`／`opaque-base64url` | 複数の長さ・seedによる未選別の合成値と、942450のhex patternへ明示一致する値。文字種・実長・decode後の再encode一致を確認。Portalのhandleは`r1.`の有無と長さも区別 |
| `opaque-id`／`opaque-ticket` | 前回音声応答ID、JSONをBase64url化したpayloadと32-byte MAC枠からなるticket。ID／署名headerの正規な外形と、暗号・MAC・本人・期限の正当性は別に評価 |
| `media-base64`／`media-data-url`／`media-binary` | CRC付きPNG、object/xref付きPDF、PCMフレーム入りWAVを生成し、実際の媒体構造を確認。媒体のBase64文字列を直接書き換えて壊す試験とは分ける |
| `url`／`sdp`／`metadata` | URL、CR/LFを持つSDP、設定JSONを本文・媒体と区別。FLUXのURL受理は未確認なので、そのURLケースは入口検証用でありモデル互換性の合格にしない |

これらのopaque・handle・ID・ticketは独立した合成値で、実際の暗号鍵・暗号文・発行済み参照を使いません。復号や署名が成功する値だとは扱いません。Chatの音声／ファイル入力も正規fieldの外形を試すもので、全公開モデルがそのmodalityを受理するという主張ではありません。匿名要求がWAFを通過したこと、アプリの受付、提供元による入力受理、モデル推論成功を分けて記録します。

`exclusion_coverage`は、caseの`payload_field`ラベルから合格を作らず、実際のJSON、multipartのfield、header、Cookieを読み、profile・variable・operator・selectorごとの存在を照合します。値や動的キーの実文字列はcoverage記録へ保存しません。query対照を本文coverageへ加算せず、raw本文やJSONでないサイズblobもJSON selectorの検証に数えません。配列indexの省略やmultipart内JSONの展開はローカルの候補投影であり、`waf_extraction_verified`は常に`false`です。Azureが同じ項目を抽出した証明は、相関した実WAFログで別途確認します。

同じhex patternは、承認欄に近い名前の兄弟field、同名query、未知header、隣接パスへも移します。同名queryはWAFの値除外にも作用し得るため、WAF403を一律の期待値にせず、認証済み入口でqueryが拒否されることを別に確認します。匿名401だけではその入口検証に合格を付けません。従来の`size-boundary`の大容量`p`反復は形式検証用の不正JSONであり、600 KiBの正規履歴とは区別します。

実通信前に`--dry-run`と`--case-filter`で対象を絞り、経路・データ型・対照をレビューします。既定corpusの全件を一括送信した結果だけで、新規selectorや型の検証を置き換えません。

```sh
python3 scripts/waf_probe.py --dry-run --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-plan.json"
python3 scripts/waf_probe.py \
  --gateway-base-url https://gateway.example.com \
  --portal-base-url https://portal.example.com \
  --transport curl --workers 4 --requests-per-second 4 \
  --case-filter 'typed-reasoning-' \
  --phase verification --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-verification.json"

# Responses／Chatの正規本文・Schemaと、除外範囲の対照を独立して確認
python3 scripts/waf_probe.py --ai-coding-only \
  --gateway-base-url https://gateway.example.com --transport curl \
  --workers 2 --requests-per-second 2 --phase coding-verification \
  --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-ai-coding.json"

# 正規raw本文と、非本文のパストラバーサル対照を別途確認
python3 scripts/waf_probe.py --raw-body-guards-only \
  --gateway-base-url https://gateway.example.com \
  --portal-base-url https://portal.example.com --transport curl \
  --phase raw-verification --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-raw-guard.json"

# 画像の正規prompt・参照画像と、除外範囲の対照を独立して確認
python3 scripts/waf_probe.py --image-prompts-only \
  --gateway-base-url https://gateway.example.com \
  --portal-base-url https://portal.example.com --transport curl \
  --workers 2 --requests-per-second 2 --phase image-verification \
  --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-image-prompts.json"
```

`curl`はWindowsのSchannelで通常の証明書検証を行う環境でも利用できます。リダイレクトは追わず、本文、応答本文、キー、Cookieを証跡へ保存しません。各要求の`User-Agent`は`egakumi-waf-probe/1.0 <correlation>`とし、既存の証跡の`correlation`欄にある短い英数字の識別子を使います。アクセスログではqueryを保持しないため、相関用queryは追加しません。試験入力のqueryはそのまま送り、正確な`User-Agent`・所有AppGW・要求時刻でアクセスログを絞り、`TransactionId`でWAF規則ログと照合します。識別子が失われた要求を、順序だけで個別試験へ割り当てません。

FLUX画像編集はpromptだけでなく、`input_image`のraw Base64画像も確認します。OpenAIの画像URLと形式を混同して`data:image/png;base64,...`を付けず、正規のAPI契約に従った入力で評価します。契約外入力の遮断だけを理由にWAF除外を追加しません。

`--image-prompts-only`は既定の大きなコーパスと分け、PHP変数関数・SQL・HTML・シェル・URL・パスを画像化する合成prompt、OpenAI編集のPNG画像と透明mask、FLUXのraw Base64参照画像を使います。未知のbody field、queryの`id`、隣接パスは攻撃対照とし、同名promptのqueryは入口検証用に区別します。全件匿名で本文のmodelまたはmodelIdを不正モデルに固定し、画像生成の成功や認証済み入口ガードの実証には数えません。PortalでWAF通過後のセッション認証まで確認する場合は、保護された検証runnerから同一Originを付けます。Origin不足によるアプリ403をWAF遮断と取り違えません。

通常はキー無しで通信します。401、アプリの403／400はWAFを通過した参考証拠ですが、機能成功とは数えません。HTTP 403だけでWAF遮断とは判定せず、App Gatewayの応答シグネチャとAGWFirewallLogs／AGWAccessLogsを照合します。任意の`--key-file`は、通常推論を発生させない不正モデルの認証canaryにのみ使います。実際のモデル／Tool成功は、正規キーと応答形式を確認する少数のE2Eで別途検証します。

同じシグネチャを正規データ欄と、model等の制御欄・未知のJSON欄・query・header・隣接パスへ移します。正規本文の互換性と除外範囲の維持を別々に判定します。存在しない静的パスの404や、利用しないヘッダーの受入をWAF遮断成功とは数えません。

容量緩和の受入では、AIパスで従来の小容量枠と2000 KiBを超える正規入力が到達すること、base・auth・隣接パスのサイズ強制が残ることを確認します。文書は202受付・署名付き状態確認・完了結果を通し、未知チケット・別購読・期限切れも拒否することを確認します。ローカルの設定照合を、この実経路の通過結果へ読み替えません。

## 適用範囲と運用上の制約

Azureの引数除外は、同じURIのqueryにある同名引数にも適用されます。App Gatewayのpath ruleは大文字小文字を区別せず、HTTP方式でも絞れません。APIM／Portalの認証、公開method、モデル許可、形式・入力制約を維持し、queryの同名欄とmethod違いも対照試験に含めます。

WebSocketはHTTP Upgradeまでを試験します。接続後のframe、デコード後の画像・音声・PDFの意味内容をWAFが検査したとは扱いません。gRPCのTCP入口はHTTP WAFの対象外です。外部A2A・中継MCP・gRPC等を追加公開するときは、その実際の契約で同じ試験を追加します。

入力空間は無限であり、有限の合成試験で将来すべての連携や誤検知ゼロを保証することはできません。新しいAPI・field・managed rule版を追加した場合は、正規データと攻撃対照を再実行します。拒否時は集計規則だけでなく、同一TransactionIdの原因規則とfieldを確認します。942150等の二段判定ログには前段候補のキー名も含まれるため、すべてを除外候補にしません。

manifestからprofileを削除する操作は、現時点のCLIでは既存の関連付けの撤去を行いません。例外を撤去・ロールバックする場合は、変更前に保存した経路とポリシーを照合し、IaCで対象経路を明示的に復元します。バックアップやログには機密ヘッダーを含むApp Gatewayの全設定をそのまま出力せず、経路・WAF・変更識別子だけを保存します。

試行ごとの計画・実測・適用記録は、[共通の証跡ルール](../../DEVELOPMENT.md#テストの共通ルール)に従って環境別のGit管理外領域`.local/<environment>/`へ保存します。試験のrun ID、manifest hash、設定照合結果、E2Eと試験用購読の回収結果を記録します。受入条件は[動作テスト仕様書](integration-tests.md)で確認し、過去の試行の合格を現在の結果へ転記しません。

仕様の根拠: [Azure WAFの除外](https://learn.microsoft.com/azure/web-application-firewall/ag/application-gateway-waf-configuration)、[URI別ポリシー](https://learn.microsoft.com/azure/web-application-firewall/ag/per-site-policies)、[パスの照合](https://learn.microsoft.com/azure/application-gateway/url-route-overview)。
