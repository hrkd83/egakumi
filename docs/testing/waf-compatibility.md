# App Gateway WAFの互換性・遮断確認

GatewayとPortalは、OWASP 3.2のPreventionと本文検査を有効にしたまま使用します。SQL、HTML、シェルコード、不透明な機械生成文字列をモデル・PII等へ渡すことは正当な利用です。そのデータを実行命令として扱うURL、ヘッダー、APIの制御情報とは分け、承認した操作経路・データ欄に限り、個別ルールまたは承認したアプリケーション攻撃ルールグループを調整します。

コマンドはリポジトリルートで実行し、[第1章](../setup/01-prerequisites.md#1-5-環境別のローカル領域を準備する)で設定した`DEPLOY_PARAMS_FILE`と絶対パスの`DEPLOY_LOCAL_DIRECTORY`を使います。計画・実測記録は同じ環境の領域へ保存します。

入力型と用途分類の正本は `apps/apim/waf-input-contracts.json` です。`scripts/waf_contracts.py` が再利用する内容型を各出現位置へ展開し、Azureへ渡す `apps/apim/waf-data-exclusions.json` を生成・照合します。入力型、経路、通信方式で除外を決め、モデル名・世代・許可モデル数では分岐しません。

各profileはGatewayまたはPortalの完全パスと、下記の承認済み末尾パターンを指定し、`RequestArgValues`と、動的JSONキーで必要な場合の`RequestArgKeys`を使います。署名付き文書状態参照・通話終了は既存operationの可変ID位置を限定patternへ写し、その専用profileで正確なticket名の`RequestHeaderValues / Equals`を使います。画像編集・文字起こしの2操作は配備名の親パスを使います。任意のAPI templateやheaderを対象にしません。`StartsWith`は`state.`、`questions.`、JSON Schemaやobject形式の引数配下など、区切りを含むデータ構造に限定します。集計規則949110は除外しません。

Responsesの`input.call_id`は、ツール呼び出しと結果を結ぶ相関IDとして`control`分類を維持します。`g-responses`の完全パスだけで、`RequestArgValues / Equals / input.call_id`からSQL Hex検知の`942450`だけを除外します。7グループのデータ欄除外、他のSQL規則、Keys・Header・兄弟欄へは広げません。WAFはitemのtypeやIDの形式を検証する条件ではないため、この除外で特定のID形式だけが受理されるとは扱いません。同名queryにも引数除外が作用し得るため、WAFの適用範囲とAPIM後続処理の受付・拒否・転送を別に確認します。

APIM棚卸⑦では、query部品を`core-query-observation`へ観測専用化する方針を承認しました。この部品は有無・複数値の記録だけを行い、queryを理由とする400／500の分岐やmode設定を持ちません。その後の[入力検査](../../apps/apim/policies/fragments/request/core-api-input-validation/README.md)では、標準13操作のquery制限・HTTPメソッド再確認・入口MIME一覧比較の撤去を承認しました。操作登録とAPIMの照合、Content-Type宣言の存在・1値・非空MIME部分、URL資格情報拒否は維持します。後段の`core-model-limits`のMIME許可一覧比較と415も撤去しました。`operationConstraints.contentTypes`を使う個別処理、Speech転送、URI変換、WAFのルール・除外は別棚卸です。Documentの個別形式検査は[入力棚卸①〜㉝](../reference/document-analysis.md)で撤去し、原本と提供元JSONを本文変換なしで送る構成へ整理します。Document開始POSTはmodelをクエリから除いた後、pages・featuresなどをpathだけの標準URI書換えで転送した後、標準クエリ設定でapi-versionは公開Schemaの単一対応版へ上書きします。⑭ではstringIndexTypeの指定値を保持し、未指定時だけ`unicodeCodePoint`を補完する形へ整理します。不正値・複数値の適否は提供元へ任せ、APIMに新しい検査を追加しません。㉑では状態取得GETの追加クエリを無視して同じジョブを取得し、クエリの有無だけでは403にしません。チケット・UUID・本人・期限の照合と固定URI・対応版、URLへのAPIキー指定の共通拒否は維持します。㉒では未使用クエリの非転送を維持し、目的コメントを整えます。対応版の結果取得GETはapi-versionのみを使います。将来正式なGET追加クエリが必要になれば、公開結果契約と合わせて見直します。このDocumentブロックは対象APIM資材の一括反映・読戻し照合を完了し、実経路受入は一部確認です。APIM入力棚卸のまとめ反映ではWAF設定とクエリ名・値の監査出力を追加していません。Live／Realtime等の未一致queryの非転送も変更しません。観測専用化と共有MIME比較撤去のローカル改修・検証、Azureへのまとめ反映・読戻しは完了しました。[限定受入](../../apps/apim/policies/README.md)ではキー／BearerのResponses JSON・SSE、認証・入力拒否、TTS WAV・DTD拒否と対応する監査照合を確認しました。大容量413、他モデル・transport、Content-Typeの追加異常系は未実施です。以下の既存試験結果を、現在の入口MIME拒否の保証や、query転送・任意形式の下流受付・機能互換性の確認完了へ読み替えません。

Documentでは、独立して作成した小さい試験文書の原本と`base64Source`を、キー／Bearerの両方で202受付から本文なしGETの200まで確認しました。`pages=2`、指定した`utf16CodeUnit`・未指定時の`unicodeCodePoint`、API版固定、従来一覧外の`prebuilt-invoice`も確認済みです。本文なしGETの未使用クエリは200で、本文付きGETはWAFの920170により403でした。APIM入力棚卸の受入時点では、キー／Bearerの`urlSource`は931130（別ドメインへの参照）と920300などの検出、949110の異常スコア判定による403でした。この受入時点では920170・931130・949110を無効化せず、urlSourceの除外も適用していませんでした。APIMの本文消去処理はWAFより後に実行されるため、本文付きGETの403をAPIMの処理結果とは扱いません。空本文・短い本文・別MIME・gzip宣言は422、Content-Type欠落の403は原因を未切り分けです。[追加受入表](integration-tests.md#document入力棚卸の追加受入)に未実施条件を残し、小文書の通過を大容量・全入力方式の合格へ広げません。

### 入力文書URLの個別RFI除外

横断棚卸後に承認した追加対象は、次の2ポリシー・3項目だけです。正本は[入力文書URLの除外定義](../../apps/apim/waf-source-url-exclusions.json)で、BicepとPythonが同じ定義を各専用ポリシーへ合成します。ローカル合成検証と追加分のAzure反映・全ポリシーの読戻し照合を完了しました。追加対象以外のポリシーと経路の関連付けは変更していません。追加除外後、独立した小文書URLを使うDocumentのキー／Bearerは202受付から本文なしGETの200まで確認しました。model・API版・未指定時の`unicodeCodePoint`と、チケットの`exp - iat = 86400`も照合しました。Responsesの2項目は、提供元・APIM・App Gatewayの429を監査とアクセスログで照合しました。WAFやAPIM入力検査による拒否ではありませんが、提供元によるfile_urlの文書受付・取得・結果は未確認です。上記のURL入力403を、除外後の試験結果へ読み替えません。

| ポリシー | 完全パス | 正確な項目名 | 除外する規則 |
| --- | --- | --- | --- |
| `g-document-upload` | `/tools/documents/v1/analyze` | `urlSource` | OWASP 3.2の931130のみ |
| `g-responses` | `/models/openai/v1/responses` | `input.content.file_url`、`input.output.file_url` | OWASP 3.2の931130のみ |

`RequestArgValues / Equals`で項目の値を選び、規則グループは`REQUEST-931-APPLICATION-ATTACK-RFI`、個別IDは`931130`です。本文全体・Keys・Header・Cookie・他の項目へ広げず、931グループ全体や949110、GET本文の920170も無効化しません。App Gatewayの引数除外は同名query／formにも作用し、JSON本文だけには限定できません。除外はパス単位で、HTTPメソッドの操作照合はAPIMが行います。

Responsesの`file_url`は、入力文書とfunction結果の文書をモデルへ渡す用途です。[通常入力の公式型](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_input_file_param.py)と[function出力の公式型](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_input_file_content_param.py)に基づく選択で、Azure提供元の受付・文書取得・モデル対応の実証とは分けます。`waf-input-contracts.json`のcontrol分類と、そこから生成する自由文・媒体データの7グループ除外は変更しません。個別RFI除外は別正本から合成し、既存の除外・Prevention・本文検査・ログのマスク・URI等の保護を保持します。

Documentでは原本受付の専用ポリシーだけへ合成し、SpeechとPortalにも使う共通raw本文設定には追加しません。既存の画像・音声URLは既に媒体項目の除外があるため、今回の追加対象へ含めません。Portalの原本File受付とPII MCPのtext受付にも追加しません。remote MCPの`tools.server_url`はモデルが接続するツールの宛先であり、入力文書URLとは分けて後続の機能棚卸で扱います。

## 受付容量と検査範囲

| 対象 | 現行資材の制御 |
| --- | --- |
| manifestのGateway／Portal AI操作経路（承認2末尾パターンを含む）、正本Speech・文書受付（次行の制御用途を除く） | Prevention・本文検査を維持。先頭2000 KiBまで検査し、本文・ファイルのサイズ強制は解除。超過部分は検査せず受け付ける |
| `g-document-status`・`g-realtime-ticket`・`p-document-status` | Prevention・本文検査とbaseの容量・サイズ強制を維持。Portal文書状態参照のアプリ側32 KiB上限も維持 |
| base・認証callback・管理系など上記以外 | 既定Prevention。configの本文検査枠・本文／ファイルサイズ強制を維持 |
| 外部管理WAF | 自動変更しない |

2000 KiBはAI入力の受付上限ではありません。通常AIのAPIM受付はClassicのバッファー上限500 MiB、文書は500,000,000 bytesで、提供元の制約が小さい場合はそちらが適用されます。WAFの検査枠を超えた本文まで検査したとは扱いません。本文検査・容量設定はAzure反映後の全ポリシー読戻しで保持を確認しました。検査枠を超える本文や500 MB境界など、大容量の実経路受入は未実施です。

## 用途別のポリシー構成

名前はGatewayが`wafpol-g-<用途>-<ID>`、Portalが`wafpol-p-<用途>-<ID>`です。`<ID>`にはparamsの`services.apimInstanceId`に保存した6文字の構築識別子を共通利用します。Bicepと個別配備は同じ名前を計算し、更新や途中再開で識別子を生成し直しません。APIのURLは変更しません。

ローカルの最終候補では、ポリシーの用途はGatewayが17個、Portalが12個の計29個です。Gatewayは公開対象に選んだ経路だけを関連付け、APIの登録だけで未知の用途のポリシーを追加生成しません。この数はAzure上の配備済み資源数ではなく、PII分割後の用途数です。移行中は復旧用の旧PIIポリシーも保持します。

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
| PII REST | `pii-rest` | `pii-redact` |
| PII MCP | `pii-mcp` | 上記`pii-redact` |
| 文書アップロード | `document-upload` | `document-upload` |
| 文書の状態参照 | `document-status` | `document-status` |

Gatewayの画像と埋め込みは複数の操作経路を1つのポリシーへまとめ、各パスに必要な除外の集合和を適用します。画像の`input_image`、埋め込みの`input`と`texts`の除外は、同じポリシーの全経路に作用します。GatewayのPIIは、以下の承認した最終候補ではRESTとMCPを分けます。PortalのPIIは両入口とも`text`と同じCookie除外なので、`p-pii-redact`をそのまま共有します。

### 配備名付き2操作のWAF経路

画像編集と文字起こしは、モデル一覧から作っていた完全パスを次の2パターンへ置き換える方針です。正本は`waf-input-contracts.json`の`modelOperationWafRoutes`で、PythonとBicepが同じ操作署名を読みます。API名・公開prefix・操作名・HTTPメソッド・URL template・API種類・kind・bodyFormat・parameterと既存のMIME契約が一致した場合だけ生成します。`allowedModels`の増減や新しい配備名はWAF経路の生成条件に含めません。

| 用途 | APIMに登録する操作 | WAFの適用パターン |
| --- | --- | --- |
| `g-images` | `POST /models/openai-images/v1/deployments/{deployment}/edits` | `/models/openai-images/v1/deployments/*` |
| `g-transcribe` | `POST /models/openai-audio/v1/deployments/{deployment}/transcriptions` | `/models/openai-audio/v1/deployments/*` |

[MicrosoftのPathPattern仕様](https://learn.microsoft.com/en-us/azure/application-gateway/url-route-overview#pathpattern)は中間wildcardを非対応とし、末尾wildcardを対応形として示しています。このため`*/edits`や`*/transcriptions`で操作suffixだけをWAFから選ぶ構成にはしません。末尾パターンは配下の未知suffix・深い子パス・別HTTPメソッドにも、既存の専用WAF規則とデータ欄除外を適用します。要求を業務操作として受け付けるかはAPIMの登録済みメソッド・URL照合で決まり、WAFの経路一致だけでは受け付けません。匿名401だけを、この操作境界の確認成功として扱いません。

標準構成のローカル合成比較では、23データ用途と`g-base`・`g-auth`・`g-speech`・`g-document-upload`・`p-base`・`p-auth`を合わせた29用途の規則・除外・本文設定・名前・参照IDを保持し、27個のpathRulesのうち上記2個の`paths`だけが変わることを確認しました。新しいWAFポリシーは作りません。画像のFLUX生成・編集、OpenAI画像生成の3完全パスは保持し、画像編集の旧3完全パスを1パターンへ置換します。文字起こしの旧1完全パスも1パターンへ置換します。画像の`waf-json-content-controls.json`も同じ4経路へ揃え、既存のJSON／multipart制御を維持します。

公開する全HTTP操作・MCP受付と2パターンの交差を配備前に確認し、正式な2署名以外の入口が配下へ入り得る場合は停止します。モデル一覧だけの変更は許可します。運用reconcileでは、他の用途を変更する前に元の全pathRulesを確認し、独立・外部のexact／wildcardとの交差や解釈できない中間wildcardがあればPUT前に停止します。基底WAF・転送先・rewriteが既定と同じ、より広い親wildcardだけは保持します。移行対象の所有者・転送先・rewriteを照合し、旧経路が新しい範囲から失われる変更も停止します。

この2経路の資材はローカル実装段階です。最終ローカル試験の結果とAzureへのまとめ反映・読戻しは別に確認し、新しい匿名`--model-routes-only`試験は実Azureで未実施です。ここに記した合成比較や、以前の完全パスでの実WAF試験を、今回の末尾パターンの受入結果へ読み替えません。

### PII入口の除外をRESTとMCPへ分ける

今回の変更は**App GatewayのWAFポリシーと公開パスとの関連付け**です。APIMのAPI定義・キー／Bearer認証・本文変換・モデル機能・OBOを変更するものではありません。APIのURLと正規の送信本文も維持します。

| 最終候補のprofile | 公開パス | データ欄の除外 |
| --- | --- | --- |
| `g-pii-rest` | `/tools/pii/v1/redact` | `RequestArgValues / Equals / text`だけ |
| `g-pii-mcp` | `/tools/pii-v1-mcp/mcp` | `RequestArgValues / Equals / params.arguments.text`だけ |
| `p-pii-redact`（維持） | `/api/tools/pii`・`/api/tools/pii-mcp` | 従来の`text`とPortalセッションCookieの除外 |

これまでは`g-pii-redact`に2つのパスと2つのselectorをまとめ、両方の入口で除外の集合和が作用していました。分割後は、RESTへMCP用の`params.arguments.text`、MCPへREST用の`text`のデータ欄除外を追加しません。各正規欄に使う7グループと個別規則の組合せ、Prevention・本文検査・容量・機密値のマスクは維持します。

確認した現行testsと文書には、MCPの入れ子JSONに対して末端名`text`も必要になる根拠はありません。ただし、ローカルの生成・構成照合だけではAzureのJSON解析と正規本文の通過を証明できません。`params.arguments.text`だけの候補で正常なMCP初期化・ツール照会・PII実行を確認し、RESTの正常本文も別に確認します。

`RequestArgValues`は本文だけを選ぶ設定ではありません。分割後も、それぞれの入口で同名queryに引数除外が作用し得ます。queryの拒否・転送、URI制約、他のWAF規則は未棚卸のまま保持し、この変更でqueryを全面許可／拒否したとは扱いません。

PII分割のAzure反映・読戻しと、正常なREST要求およびMCP初期化・ツール照会・PII実行の通過・マスキング確認は完了しました。旧`g-pii-redact`は参照不在を確認して退役しました。未実施の異常系、同名queryへの除外の挙動とログ帰属は残件です。移行時は次の順序で適用・受入・旧資源退役を分け、一連の承認済み変更を一括コミットします。

1. 旧`g-pii-redact`は移行元として限定して認識し、通常reconcileでは削除しません。所有・現在値と既存bindingを照合し、復旧用に旧ポリシーを保持します。
2. 新しい2ポリシーを用意してから、各公開パスのbindingを新ポリシーへ切り替え、設定を読み戻します。
3. 正常なREST要求とMCP初期化・ツール照会・PII実行の実WAF通過を確認します。WAF通過、APIM認証、PII処理の成功を分けて記録します。失敗した場合は保持した旧bindingへ戻せる状態を維持します。
4. 両入口の確認が成功し、旧ポリシーへの参照がないことを照合した後にだけ、旧ポリシーを退役します。その後に保存した構成と期待値を整理します。

旧`g-pii-redact`は移行・復旧用途で保持した資源であり、正常な両入口と参照不在を確認して退役しました。通常の新規構成には選びません。移行時も旧資源の存在だけを理由に自動削除せず、反映・退役をローカル資材から推定しません。

用途名は検査するHTTP入口を表します。署名ticketを使うRealtime observer以外のRealtime／Live WebSocket入口など、個別のデータ欄除外がない経路は`base`を使います。データ欄の除外はmanifestに明記した条件から生成し、APIのMIMEや機能名によって別の除外を自動追加しません。外部管理ポリシーの指定時は、そのポリシーを再利用します。

## 保護条件の共通化

OpenAIのResponsesとChat Completionsは、同じ用途のデータ欄に同じアプリケーション攻撃グループ除外を適用します。[MicrosoftのAIチャット向けWAFチューニング指針](https://learn.microsoft.com/azure/architecture/ai-ml/architecture/baseline-microsoft-foundry-chat#web-application-firewall-tuning)に沿って、会話・指示・コード・ツール結果・引数・説明・宣言的Schemaの欄だけを選びます。SQLやコードの構文をモデルへ渡すことは、Gatewayでその構文を実行することとは別の処理です。WAFの評価はHTTP要求単位であり、履歴やツール結果が後続要求の本文に含まれることで検出対象が増えます。

manifest version 2は、validatorに列挙したprofile・経路・データ欄だけで、LFI（930）・RFI（931）・RCE（932）・PHP（933）・XSS（941）・SQLi（942）・Java（944）の7グループ除外と、プロトコル系の個別規則`921110`・`921130`の除外を許可します。グループ除外は`ruleGroupName`を指定し`rules`を省略し、`921110`・`921130`は`REQUEST-921-PROTOCOL-ATTACK`に個別IDを指定します。version 1の個別ID形式は引き続き読み取れます。全managed ruleの除外、集計949110/980130の除外、列挙外の用途・欄へのグループ除外は許可しません。既存の921120・921130・921150等の個別例外は保持し、921グループ全体は除外しません。

Responses／Chat Completionsの承認済み自由文Valuesと、Schema・構造化引数prefixのValuesには、この7グループと`921110`・`921130`を適用します。既存selectorの`800100`・`920230`・`920270`・`920271`等の個別除外は、その変数・演算子・selectorの範囲で保持します。新しい型や階層を追加しても、これらの旧個別除外は自動継承せず、台帳で承認した共有型の7グループと`921110`・`921130`を適用します。個別IDの選択はMicrosoftの指定一覧ではなく、このAPI契約で確認した誤検知への構成判断です。

HTTP要求の例文やログを含む既存のR/C通常本文Valuesには、`921120`の個別除外を従来の範囲で保持します。対象は従来指定したexact自由文とSchema・構造化引数の値prefixです。新規自由文、不透明な暗号化・Base64・音声ID、画像URL、metadata、SDPや他profileへ、`921120`を自動追加しません。`921130`は下記の既存117条件へ共通適用し、HTTPログ中の`HTTP/1.1`にも対応します。本文中のHTTP構文と実際のHTTP要求のheader・URI・query・framingを区別し、921グループ全体の除外やruleの無効化は行いません。

Responsesのtool結果本文には、HTTP要求の例文やJavaのクラス参照を含む正当なコード・ログが入り、後続要求の履歴でも検査されます。`input.output.text`だけの個別対処にせず、従来6グループを除外していた全117条件へ、同じvariable・operator・selectorのままJava（944）グループと個別規則`921110`・`921130`を共通適用します。PII分割後の最終候補では、この117条件は22profileに分かれます。`921130`はChatの18条件・Responsesの29条件の計47条件で既に除外されており、その条件を保持して残る70条件へ追加します。既存のSchema Keys、Decisionの従来のexact `state`、媒体URL・不透明値・SDP・封印handle・専用ticket headerも対象です。従来6グループを持たないmetadata・設定JSON・認証code・セッションCookie等へは追加しません。共通規則の追加では新しいselectorやprefix、URLパスを増やさず、既存の個別例外を包含します。PII入口の分割は前節で別に管理し、認証・query・MIME・本文形式・容量制約の受付挙動は変更しません。構成照合・合成本文の試験は、HTTP/WAFの実経路検証や提供元への受付確認と区別します。

| 今回の追加対象 | profile |
| --- | --- |
| Responses／Chat／Portal生成 | `g-responses`、`g-chat-completions`、`p-generate` |
| 埋め込み | `g-embeddings`、`p-embeddings` |
| リランク | `g-rerank`、`p-rerank` |
| Decision | `g-decision`、`p-decision` |
| 画像 | `g-images`、`p-images` |
| 音声の既存データ欄 | `g-transcribe`、`p-speech` |
| Realtime／Live | `g-realtime`、`g-live`、`p-realtime` |
| 署名付き状態参照・制御 | `g-document-status`、`g-realtime-ticket`、`p-document-status` |
| PII | `g-pii-rest`、`g-pii-mcp`、`p-pii-redact` |

Schema・構造化引数のKeysは、下表の既存11個の承認prefixに7グループと`921110`・`921130`・`800100`・`921150`を適用します。JSONのプロパティ名はエスケープした改行やコードを含められ、モデルへ渡すデータ名がpayload中のCR/LF検知に一致することがあります。このため`921150`は既存の`RequestArgKeys / StartsWith`の有限prefixに限って保持します。既存Keysの`920270`・`920271`等の追加例外も保持し、新規prefixや全キーへ一律に追加しません。921のグループ全体、header、兄弟fieldへこの個別例外を広げません。引数除外は同名queryにも作用するため、非本文への適用範囲とAPIM後続処理を独立して確認します。query観測部品を補償用の拒否機能として扱わず、後続のquery・URI制約の採否は別の棚卸で判断します。

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
| 通常自由文・動的データ | rerankの`documents`・`query`、Decisionの`state`・`questions.`・`state.`、transcribeの`prompt`、Liveの`instructions`、PII RESTの`text`・MCPの`params.arguments.text`、Portalの`message`・speechの`text` | Valuesは7群と`921110`・`921130`、既存の800100・920230・920270・920271の個別例外を保持。Decision Keysも7群と`921110`・`921130`、既存の800100・921150・920系例外を保持 |
| SDP | `g-realtime`・`g-live`のexact `sdp` | 7群と`921110`・`921130`、既存の個別例外を保持。URI・ICE接続先の受付制御を緩める指定ではない |
| 不透明な履歴・ファイル値 | Responsesの`input.encrypted_content`・`input.encrypted_function_args`、`input.content`と`input.output`内の`encrypted_content`・`file_data`、画像生成履歴の`input.result`、Chatの`messages.content.input_audio.data`・`messages.content.file.file_data`・`messages.audio.id` | exact Valuesへ7群と`921110`・`921130`。暗号化・Base64・参照IDを自然文と区別し、Known-CVEsや920の新規例外は追加せず、旧例外だけ保持 |
| Portalの封印handle | `p-realtime`・`p-document-status`のexact `handle` | 7群と`921110`・`921130`、旧例外だけ。復号・本人／購読の束縛・期限をアプリで検証 |
| URLとdata URLの兼用欄 | Responsesの`input.content`・`input.output`内の`image_url`・`audio_url`、画像toolのmask URL、Chatの画像URL、FLUXの`input_image`から`input_image_8`まで | 台帳で確認済みの媒体欄へ同じ7群と`921110`・`921130`を適用し、既存個別IDも保持。任意のURL、兄弟欄、未確認の画像スロット名へ広げない |
| metadata・設定JSON | `client_metadata.x-codex-turn-metadata`、Portal speech／文書multipartの`input` | 旧個別IDを維持。設定内のmodel・台帳ID・制御値へ自然文用除外を広げない |

`encrypted_content`は[Responsesのreasoning履歴](https://platform.openai.com/docs/guides/reasoning)として後続要求へ返す正規値です。Chatの音声・ファイル・音声IDも[公式入力型](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)の区別を保ちます。WAF除外はHTTP入力の誤検知対策であり、そのmodalityが個別モデルで利用可能であることや、ファイル取得・推論成功の証明にはしません。

署名付き制御は次の既存入口に分離します。文書アップロード用のraw本文調整を状態参照へ流用しません。

| profile | 現行経路 | 限定selectorと入口条件 |
| --- | --- | --- |
| `p-document-status` | POST `/api/tools/documents/status` | JSON `handle`、queryなし、同一Origin・本人・購読・封印内容を照合。JSON MIMEの大小文字対応だけを合成 |
| `g-document-status` | GET `/tools/documents/v1/operations/{operationId}` | header `X-Core-Document-Ticket`、追加queryはAPIMで無視し、UUID形式と署名・本人・対象・期限を照合 |
| `g-realtime-ticket` | POST `/models/openai-realtime/v1/calls/{callId}/hangup`、GET `/models/openai-realtime/v1/observer/` | header `X-Core-Call-Ticket`、hangupはqueryなし、observerは単一`call_id`だけ。署名・購読・call ID・期限をAPIMで照合 |

この3profileは7群と`921110`・`921130`を既存のexact値・headerから除外し、baseの本文容量設定を維持します。Portal停止入口も、既存どおり未知のqueryを認証後・本文読取り前に拒否します。AppGWのpatternは上記2operationのAPI名・操作名・method・template・kind・bodyFormatが正本と一致した場合だけ生成し、他templateを展開しません。observerの完全パスも正本のAPI名・GET操作・WebSocket・署名observer設定に一致する場合だけを選び、同じURLのcustom APIへ除外を追加しません。[AppGWのPathPattern](https://learn.microsoft.com/en-us/azure/application-gateway/url-route-overview#pathpattern)は途中のwildcardを扱えないため、関連付けは`/tools/documents/v1/operations/*`と`/models/openai-realtime/v1/calls/*`です。`/calls`作成の完全パスは既存`g-realtime`のままです。AppGW自体はmethodやIDの構文を判定しないため、未知の子経路・他verb・余分なpath成分は既存APIのoperation照合・APIMの検証で拒否します。一般header、他Cookie、query全体への除外は追加しません。

native toolの履歴は[Responsesの公式入力型](https://developers.openai.com/api/reference/resources/responses/methods/create)に基づき、local shellの`action.command`配列、shellの`action.commands`配列、patchの`operation.diff`、shell結果の`output[].stdout`・`stderr`を上表のexact Valuesで選びます。local shell／patchの文字列形式の結果は既存の`input.output`で扱い、画像生成履歴の`input.result`はBase64出力を持つ不透明値として別に7群と`921110`・`921130`を除外します。Gatewayが実行しない会話データに対する除外であり、Responsesのtop-level `tools`をfunctionに限定する既存APIM制約、認証・API権限を変更しません。`env`、`user`、`working_directory`、`path`、timeout、outcome、exit code、type、IDの制御欄へはグループ除外を追加しません。ツールを実行するクライアントの権限・承認・sandboxは別途維持します。

Chat Completionsの`messages.reasoning_content`も、モデルが生成した推論テキストを履歴へ戻す正規の自由文として同じValuesセットで扱います。[Azureのreasoning応答例](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-chat-reasoning)と[DeepSeekのthinking modeの履歴契約](https://api-docs.deepseek.com/guides/thinking_mode)は区別し、Azureの応答例だけで後続要求の受理を実証したとは扱いません。除外の根拠は公式型とデータの用途であり、特定モデルの推論成功を除外の前提にしません。実経路でのWAF通過と、モデルによる要求受理・推論成功を別々に検証します。

同名queryにも引数除外が効くため、そのWAFの効果とAPIMの受付・下流への転送は別に確認します。標準OpenAIの2操作はPOSTとURLの登録を維持し、APIMが操作照合を担当します。キー／Bearer認証後の入力検査はContent-Type宣言の1値・非空MIME部分だけを確認し、会社のMIME一覧と比較しません。この部品のメソッド再確認とquery拒否も撤去します。WAFのJSON形式規則は今回変更しません。APIMのJSON本文・モデル選択・受信byte制約・Responsesのstateless制約は個別棚卸の条件に従い、入口での通過を下流成功とは扱いません。全本文の厳密Schema検証や、全購読でのrate/token制限が既に有効だとは扱いません。画像・ファイルのURL取得、ツール実行先の引数・権限・sandboxは別の実行境界です。

画像生成・編集では、`g-images`・`p-images`の`RequestArgValues / Equals / prompt`へ7グループと`921110`・`921130`、既存の`800100`・`920230`・`920270`・`920271`の個別除外を適用します。`g-images`のFLUX参照画像欄`input_image`・`input_image_2`～`input_image_8`も、同じ不透明データ型としてexact Valuesで7グループと`921110`・`921130`を除外し、従来のSQL規則`942340`・`942390`・`942450`を包含します。未確認の9・10枚目のfield名は推測して追加しません。PortalセッションCookie、model等の制御欄は変更せず、編集multipartのimage／maskバイナリーpartへ新たな項目除外を追加しません。Responsesの画像tool用mask URLは別の本文型・profileで扱います。これは画像APIの正規データ欄に限定した構成判断であり、画像生成モデル専用のMicrosoft推奨ルール一覧を採用したものではありません。

| 用途 | 操作経路 | 正規の本文形式 |
| --- | --- | --- |
| `g-images` | `/models/openai-images/v1/generations` | JSON |
| `g-images` | `/models/openai-images/v1/deployments/{deployment}/edits`（WAFは上記の末尾パターン） | multipart |
| `g-images` | `/models/blackforestlabs/v1/flux-2/generations` | JSON |
| `g-images` | `/models/blackforestlabs/v1/flux-2/edits` | JSON |
| `p-images` | `/api/models/images` | JSON |

画像のGateway入口は共通認証、APIMの登録済みメソッド・URL照合と、Content-Type宣言の1値・非空MIME部分を確認します。APIM入口のquery禁止・メソッド再確認・MIME一覧比較は撤去した構成です。既存WAFはOpenAI編集のmultipart、OpenAI生成とFLUXのJSONを扱い、boundaryやcharset等のmedia typeパラメーターを許容します。multipartをJSONへ変換せず元本文を保持し、モデルの利用可否は提供元へ任せる方針です。Portal画像入口はPOST・同一Origin・セッション認証・JSONを要求し、同名promptを含む全queryを拒否します。未知のbody fieldへpromptの除外は追加しません。画像編集の末尾パターン内の隣接URLは同じpath policyを使うため、パターン外のWAF遮断対照と、パターン内のAPIM操作境界を分けて確認します。

埋め込みでは、`g-embeddings`の2完全パス`/models/cohere/v1/embed`・`/models/openai-embeddings/v1/embeddings`にある`RequestArgValues / Equals / texts`と`input`、`p-embeddings`の`/api/models/embeddings`にある同条件の`texts`だけに、同じ7グループと`921110`・`921130`を適用します。各欄の旧45個別IDのうち41 IDをグループで包含し、`800100`・`920230`・`920270`・`920271`は各欄の個別除外として保持します。Gatewayの旧90組とPortalの旧45組、PortalセッションCookieの942450をすべて維持し、共有ポリシーの両fieldを一方の経路から撤去しません。model、dimensions、input_type、encoding_format、画像やmetadataへグループ除外を追加しません。

APIMの埋め込み2操作は`inputContract={}`で対象を明示し、共通認証の後にContent-Type宣言の1値・非空MIME部分だけを確認します。POSTとURLは操作登録へ残し、APIMが照合します。入口のMIME一覧比較、メソッド再確認、OpenAIの版限定・Cohereのquery禁止は撤去します。URL資格情報拒否を維持し、query転送とWAFのJSON形式制御・除外は別の未棚卸項目です。Portalのquery拒否、Origin・セッション・JSON・入力schemaは変更しません。OpenAIの単一文字列、文字列配列、token配列・二重token配列とCohereの文字列配列を変更せず、モデル・次元・provider固有オプションの検査を維持します。検査先頭2000 KiBと受信byte制約は変更せず、検査枠を超えた本文を全検査したとは扱いません。これはチャット向け指針を本文の文書データに適用する構成判断であり、embedding専用のMicrosoft推奨ルール一覧ではありません。[公式APIの任意api-versionと入力型](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/embeddings)を参照してください。

PII分割後の最終候補では、JSONを受ける用途別WAF 19 profileで、MIMEの大小文字による誤検知を避けるため、そのpath policy内だけ920420の許可リスト判定をcustom Blockへ置き換えます。正本は`apps/apim/waf-json-content-controls.json`で、PythonとBicepが同じprofile・scope・承認した操作経路・MIME形式を使います。対象は`g-pii-rest`・`g-pii-mcp`を含むGatewayの10 profileと、`p-document-status`を含むPortalの9 profileです。`g-document-status`・`g-realtime-ticket`、基底・認証・正本にないJSON入口へは自動展開しません。

このWAF規則は`Content-Type`が非空の場合だけ照合用に小文字化し、単一の`application/json`と正規のtoken／quoted-stringパラメーターを許可します。`Application/JSON; charset=UTF-8`は同じJSONとして扱い、`application/jsonp`、`text/plain`、カンマで結合した複数のMIME、不正なパラメーターは許可しません。共有の`g-images`と`p-speech`はJSONと`multipart/form-data`の集合和を許可します。APIM入口のMIME一覧比較は撤去しますが、このWAF規則、後段の本文・MIME制御、Portalの形式検査は今回変更しません。MCPの本文なしGET/DELETE等へMIME必須条件を追加せず、欠落ヘッダーは既存の920340等とアプリ側受付に従います。[HTTP仕様のMIME比較](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.3.1)に沿うWAF側の構成判断です。

custom Allowは使わず、920470等の他の管理規則・JSON本文検査・既存のデータ欄除外・認証・入口制約を維持します。照合変換はヘッダーの実値を書き換えません。非JSON専用の`g-transcribe`と、raw本文用の`p-document-upload`、Speech／Gateway文書の既存調整も変更しません。PortalのRealtime JSON handlerは他のJSON handlerと同じ大小文字非依存の判定を使い、認証と読取り順を保ちます。

受入確認では、大小文字両方のMIMEで同じ本文を試し、正規のデータ欄が通ることと、除外外fieldの攻撃対照が遮断されることを独立して照合します。画像編集・音声のmultipartと、本文なしMCP操作を別に確認します。匿名canaryでのWAF通過、認証付きの不正入力拒否、有効モデルによる埋め込み応答を区別し、通常MIMEと大小文字混在MIMEの推論結果は件数・数値配列・次元を検査します。APIキー、入力本文、埋め込みvectorは公開記録へ残しません。

既存除外をグループ除外へ統合する際は、同じvariable・ルールセットで既存の各rule/groupを包含することを照合します。selectorは同一範囲、または承認された区切り付きSchema・構造化引数prefixがexact子field・より狭いprefixを覆う場合だけを認めます。無関係なselectorの撤去や、認証・callbackへの波及は許可しません。管理規則の変更は上記の承認経路限定のMIME判定置換等を正本に明示し、それ以外へ追加しません。IaCと運用CLIは同じmanifestを使用し、Prevention、本文検査、既存の検査容量・マスク・企業のcustomRulesを維持します。

Azureがグループ全体の除外を読み戻す際に省略した`rules`を空配列へ補完する場合は、承認済みOWASP 3.2グループの除外に限り、比較上だけ同じ状態として扱います。manifestでは引き続き`rules`を省略し、空配列の入力を拒否します。個別ルール、管理規則のoverride、未知の属性や他の設定差は同一視せず、再実行で不要なPUTを避けます。

PortalのHTTPSセッションCookie値は、`apps/portal/waf-session-exclusions.json`を正本とし、正確な名前`__Secure-egakumi.portal.session_token`を`RequestCookieValues`・`Equals`で選び、規則942450だけから除外します。Portal HTTPS listener専用の既定policyと、置換関係にあるPortal callback・データ欄policyへ同じ除外を合成します。Gateway側のpolicy、他のCookie、他の規則へは追加しません。HTTPの開発用Cookieを除外へ追加せず、Portalの認証・認可とCookieの保護属性は維持します。

raw SSML/WAVと正本の文書受付では、正規本文の相対パス`../`が930110に一致しました。AzureはXML/raw本文の項目単位除外に対応しないため、Speechの2パス・Gateway文書受付・Portalの`/api/tools/documents`の計4完全パスだけ920420／930110を調整し、URI・query・全header・cookieには4本のcustom Blockを合成します。930100・920470等の他の管理規則と本文検査は残します。正本は`apps/apim/waf-raw-body-controls.json`、Python生成は共通の`waf_body.raw_body_policy`です。CRSの全正規化処理と完全に同等の代替ではなく、本文内のパス文字列を許容する限定した判断です。Portalはrawストリーミングを使い、互換用multipartの`input`に対する既存5 SQL規則の値除外も維持します。Portalの状態確認・隣接パスへraw本文の調整は広げません。

今回のJava（944）と`921110`・`921130`の追加は、従来6グループがあったselectorだけが対象です。raw SSML/WAV・文書・multipartファイル本文にはそのselectorがないため、この追加で保護条件は変わりません。Portal speechのJSON `text`が通過しても、次にSSMLへ変換されて通るGateway Speechまで同じ除外で通過すると保証するものではありません。raw本文のJavaコード・HTTPログ等の互換性は別の検討・試験が必要であり、今回、新しい一律拒否やmanaged ruleの無効化は追加しません。

Log4j/JNDIの例文で検知する`Known-CVEs / 800100`は、従来のmanifestで承認したselectorに限って保持します。R/Cの会話・指示・ツール結果・引数・説明・grammar・Schema、Decisionの`questions.`と`state.`、画像prompt、埋め込み本文、リランク本文、Portalのmessage等の既存条件が対象です。新規の自由文や階層へ、この個別例外を自動展開しません。これは確認済みの誤検知への構成判断であり、Microsoftがこの個別ルールやselectorを指定したものではありません。

`Known-CVEs`全体の除外や800100の無効化は行いません。model等の制御欄、取得先URL、暗号化履歴、metadata、binaryには追加せず、既存の個別例外も変更しません。DecisionのGateway／Portal入口はPOST・JSON・queryなしに限定し、認証後・本文読取り前に拒否します。未知のfield・query・header・隣接パスの攻撃対照と、同名queryの入口検証を独立して確認します。Prevention、本文検査、集計949110/980130とプロトコル規則は維持します。

画像・音声のモデル内容参照と不透明データは、入力型台帳に定義した有限selectorで上記7グループと`921110`・`921130`を除外します。Chat Completionsの`messages.content.image_url.url`、Responsesの`input.content.image_url`・`input.output.image_url`等のURL兼用欄も同じ条件へ統合し、従来のXSS・SQLi・RFI個別IDを包含します。これらの媒体の本文欄は、承認された完全パスの`RequestArgValues / Equals`に限定します。取得先・制御用のURL欄、隣接field、headerへは広げず、URLの形式・実際の取得先・アクセス権の検証はアプリケーション／提供元で行います。Schemaキーの除外は前述の承認prefixに限定し、集計規則949110と共通の保護条件を維持します。

SSMLのDTD/ENTITY宣言は、WAF本文カスタム規則のMIME対応に依存せず、正規TTS操作のAPIMガードで400 `InvalidSpeechMarkup`として拒否します。正本は`apps/apim/policies/fragments/request/core-speech-ssml-guard/`の同名XML／C#です。XMLや外部エンティティのparserを呼ばず、UTF-8/16/32を認識して実宣言だけを検査し、escaped例文・コメント・CDATAは許容します。独自の8192byte枠は引き続き設けません。TTSの共有部品は申告サイズだけを確認し、次の専用部品が本文を一度読み、選択済み上限との実サイズ比較を文字列化前に行ってから宣言を検査します。生成済みの`speechSsmlOperationIds`で対象を選び、認証・共有サイズ確認の後、利用枠・下流認証準備・`accepted`監査より前で呼びます。結果の監査と400／413応答はHTTP API側が行い、元本文と上流URIは保持します。SSML全体のschema検証は下流Speech側の責務です。

このTTS集約と呼出位置の変更はAzure反映・読戻しを完了しました。キー／BearerのTTS正常WAV応答、DTD宣言の400と対応する監査照合を確認しました。実サイズ超過413と残る異常系は未実施です。従来の試験結果や匿名401を、新しい構成の未確認条件の受入完了へ読み替えません。

`infra/modules/application-gateway.bicep`と`scripts/waf_policy.py`は同じ正本を使用します。個別Gateway／Portal配備も照合するため、Azure上だけの手動例外が次回配備で消える構成を避けます。専用ポリシーは親を継承しないため、Prevention・本文検査・ログのマスク・customRulesと、manifestのデータ欄除外・上記のAI専用サイズ設定を合成します。企業管理の外部WAFは変更しません。既存の固有保護を表現できない場合は停止します。

通常の配備と同じparamsを指定して、まずローカル計画を確認できます。計画ではAzureに接続しません。適用時は対象環境とETagを照合し、不要なPUTを行いません。

```sh
python3 scripts/waf_policy.py --config "$DEPLOY_PARAMS_FILE"
python3 scripts/waf_policy.py --config "$DEPLOY_PARAMS_FILE" --scope all --execute
```

## 再現する試験

`wire_shape_variants`は除外manifestから生成せず、版を固定したCodex・公式入力型を基に別途記述したwire形状です。メッセージの`input.content[]`とツール結果の`input.output[]`の両方へ画像・音声・暗号化値・ファイル・本文を置き、出力の文字列形式と配列形式を分けます。画像生成履歴のresult・revised_prompt、暗号化関数引数、mask URLも個別に確認します。未知のJSON leafをfixtureへ追加すると、台帳で未分類である限り検査を失敗させます。このfixture自体も、公式型・利用クライアントの更新時に見直す必要があり、未知の全仕様変更を自動発見する仕組みではありません。

既定の`--suite smoke`は、従来別フラグだったコード・JNDI・画像prompt・raw本文の試験も含む、フィールド・型・境界ごとの必須セットです。`--suite incident`は今回の階層漏れと代表型だけを短時間で再確認する限定セット、`--suite exhaustive`は従来の内容・サイズ・対照を全て含むセットです。incidentの通過を全欄の検証完了には数えません。全件匿名で送り、任意の認証canaryは従来どおり、不正モデルを指定する2操作だけへ限定します。

証跡version 2には期待境界・観測分類・判定を分けて記録します。正規欄のWAF403、WAF遮断を期待した対照の通過、必須ケースの未実施・未帰属403・通信失敗は終了コード2です。WAFを通過した401はWAF境界だけの合格で、推論成功にはしません。同名queryなどアプリの拒否を期待するケースでは匿名401だけでは合格にならず、実ログで拒否処理を照合するまで未確認です。`evaluate_results`は欠落・重複結果も検査し、ログで帰属を確認した結果を再判定できます。

公開API JSONから「操作×profile×現在の公開モデル」の受付試験計画を毎回生成します。モデルの追加・廃止はこの計画へ自動反映されますが、WAF条件は変えません。`published_model_acceptance_plan`の初期状態はすべて`not-run`です。匿名WAF試験を、その全モデルの受付・推論試験へ読み替えません。WebSocketのフレームと、音声の実decode・画像生成・署名handleの正当性は別の認証付きE2Eで確認します。

Portal音声アップロードのfixtureは、現在のSTTモデルと`input`設定JSON＋PCM WAVの2partを使います。TTSをmultipartへ指定する不正な試験を正規ケースに含めません。DecisionのPortal入口はobjectのstateを受けるため、文字列・配列・null等のroot stateは契約外入力の拒否試験へ分類します。従来のexact `state`除外は包含のため保持しますが、現在のPortal正常入力でそのscalar欄を検証済みとは扱いません。

```sh
python3 -B scripts/waf_contracts.py --check
python3 -B scripts/waf_probe.py --suite smoke --dry-run \
  --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-required-plan.json"
python3 -B scripts/waf_probe.py --suite incident \
  --gateway-base-url https://gateway.example.com --transport curl \
  --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-incident.json"
```

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

同じhex patternは、承認欄に近い名前の兄弟field、同名query、隣接パスへも移します。同名queryはWAFの値除外にも作用し得るため、WAF403を一律の期待値にせず、認証済み入口の観測ログと後続処理による受付・拒否・転送を別に確認します。観測専用部品は拒否を担当しません。匿名401だけではその入口検証に合格を付けません。従来の`size-boundary`の大容量`p`反復は形式検証用の不正JSONであり、600 KiBの正規履歴とは区別します。

任意の未知headerにSQLやBase64を置くだけでは、WAFが検査・遮断すべき攻撃対照にはなりません。ヘッダーの独立した活性対照には、[CRS 3.2のXSSルール](https://github.com/coreruleset/coreruleset/blob/v3.2.0/rules/REQUEST-941-APPLICATION-ATTACK-XSS.conf)が明示的に検査する`Referer`と既知の合成XSS構文を使います。raw本文のLFI対照では、[CRS 930](https://github.com/coreruleset/coreruleset/blob/v3.2.0/rules/REQUEST-930-APPLICATION-ATTACK-LFI.conf)が`Referer`を除くヘッダー全体を検査するため、独立した`X-Header`のパストラバーサルを維持します。不透明な値のselectorが未知headerへ広がらないことは、ローカルの完全一致・対象variable不変条件でも確認します。

ticketの合成opaque値を兄弟headerや隣接経路のheaderへ置く6ケースは、実際の攻撃シグネチャとは限りません。これらは`expected_boundary: observation`・非必須として従来のexhaustive corpusへ残し、遮断の合格数へ加算しません。既定のrequired suiteでは、ticketのheader完全一致と経路限定をローカルの不変条件で確認し、同じ3経路に各1件の必須`Referer` XSS対照を置いて、ネットワーク上の検査有効性も確認します。CRS対象を確認せず、この6ケースを「検出すべき攻撃が通過した」と扱うこともありません。

SSMLのDTD・entity宣言はAPIMの`core-speech-ssml-guard`が拒否する境界です。その負例は`application_reject`を期待し、匿名401だけでは合格にしません。認証付きの空SSML負例で400と`InvalidSpeechMarkup`を確認し、WAFで遮断したという記録とは分けます。

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

# 新しい配備名のWAF通過と、未知suffix・別methodのAPIM境界を分けて確認（実Azure未実施）
python3 scripts/waf_probe.py --model-routes-only \
  --gateway-base-url https://gateway.example.com --transport curl \
  --workers 2 --requests-per-second 2 --phase model-route-verification \
  --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-model-routes.json"
```

`curl`はWindowsのSchannelで通常の証明書検証を行う環境でも利用できます。リダイレクトは追わず、本文、応答本文、キー、Cookieを証跡へ保存しません。各要求の`User-Agent`は`egakumi-waf-probe/1.0 <correlation>`とし、既存の証跡の`correlation`欄にある短い英数字の識別子を使います。アクセスログではqueryを保持しないため、相関用queryは追加しません。試験入力のqueryはそのまま送り、正確な`User-Agent`・所有AppGW・要求時刻でアクセスログを絞り、`TransactionId`でWAF規則ログと照合します。識別子が失われた要求を、順序だけで個別試験へ割り当てません。

FLUX画像編集はpromptだけでなく、`input_image`のraw Base64画像も確認します。OpenAIの画像URLと形式を混同して`data:image/png;base64,...`を付けず、正規のAPI契約に従った入力で評価します。契約外入力の遮断だけを理由にWAF除外を追加しません。

`--image-prompts-only`は既定の大きなコーパスと分け、PHP変数関数・SQL・HTML・シェル・URL・パスを画像化する合成prompt、OpenAI編集のPNG画像と透明mask、FLUXのraw Base64参照画像を使います。未知のbody field、queryの`id`、WAFパターン外の隣接パスは攻撃対照とし、同名promptのqueryは入口検証用に区別します。全件匿名で本文のmodelまたはmodelIdを不正モデルに固定し、画像生成の成功や認証済み入口ガードの実証には数えません。新しい`--model-routes-only`は画像編集・文字起こしの配備名と、パターン内外のsuffix・methodを小さい別集合で試します。WAF通過とAPIMの未登録操作の拒否をログ相関で分け、401では後者に合格を付けません。この追加集合は実Azureで未実施です。PortalでWAF通過後のセッション認証まで確認する場合は、保護された検証runnerから同一Originを付けます。Origin不足によるアプリ403をWAF遮断と取り違えません。

通常はキー無しで通信します。401、アプリの403／400はWAFを通過した参考証拠ですが、機能成功とは数えません。HTTP 403だけでWAF遮断とは判定せず、App Gatewayの応答シグネチャとAGWFirewallLogs／AGWAccessLogsを照合します。任意の`--key-file`は、通常推論を発生させない不正モデルの認証canaryにのみ使います。実際のモデル／Tool成功は、正規キーと応答形式を確認する少数のE2Eで別途検証します。

同じシグネチャを正規データ欄と、model等の制御欄・未知のJSON欄・query・header・隣接パスへ移します。正規本文の互換性と除外範囲の維持を別々に判定します。存在しない静的パスの404や、利用しないヘッダーの受入をWAF遮断成功とは数えません。

容量緩和の受入では、AIパスで従来の小容量枠と2000 KiBを超える正規入力が到達すること、base・auth・専用WAFパターン外の隣接パスのサイズ強制が残ることを確認します。文書は202受付・署名付き状態確認・完了結果を通し、未知チケット・別購読・期限切れも拒否することを確認します。ローカルの設定照合を、この実経路の通過結果へ読み替えません。

## 適用範囲と運用上の制約

Azureの引数除外は、同じURIのqueryにある同名引数にも適用されます。App Gatewayのpath ruleは大文字小文字を区別せず、HTTP方式でも絞れません。APIM／Portalの認証と公開操作の照合は維持し、モデル・形式・入力の受付条件は各APIの棚卸と提供元の契約に従います。queryの同名欄とmethod違いも対照試験に含めます。

WebSocketはHTTP Upgradeまでを試験します。接続後のframe、デコード後の画像・音声・PDFの意味内容をWAFが検査したとは扱いません。gRPC専用TCP入口とgRPCの新規API公開は廃止しました。外部A2A・中継MCP等を追加公開するときは、その実際のHTTP契約で同じ試験を追加します。

入力空間は無限であり、有限の合成試験で将来すべての連携や誤検知ゼロを保証することはできません。新しいAPI・field・managed rule版を追加した場合は、正規データと攻撃対照を再実行します。拒否時は集計規則だけでなく、同一TransactionIdの原因規則とfieldを確認します。942150等の二段判定ログには前段候補のキー名も含まれるため、すべてを除外候補にしません。

manifestからprofileを削除する操作は、現時点のCLIでは既存の関連付けの撤去を行いません。例外を撤去・ロールバックする場合は、変更前に保存した経路とポリシーを照合し、IaCで対象経路を明示的に復元します。バックアップやログには機密ヘッダーを含むApp Gatewayの全設定をそのまま出力せず、経路・WAF・変更識別子だけを保存します。

試行ごとの計画・実測・適用記録は、[共通の証跡ルール](../../DEVELOPMENT.md#テストの共通ルール)に従って環境別のGit管理外領域`.local/<environment>/`へ保存します。試験のrun ID、manifest hash、設定照合結果、E2Eと試験用購読の回収結果を記録します。受入条件は[動作テスト仕様書](integration-tests.md)で確認し、過去の試行の合格を現在の結果へ転記しません。

仕様の根拠: [Azure WAFの除外](https://learn.microsoft.com/azure/web-application-firewall/ag/application-gateway-waf-configuration)、[URI別ポリシー](https://learn.microsoft.com/azure/web-application-firewall/ag/per-site-policies)、[パスの照合](https://learn.microsoft.com/azure/application-gateway/url-route-overview)。
