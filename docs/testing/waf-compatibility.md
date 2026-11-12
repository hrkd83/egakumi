# App Gateway WAFの互換性・遮断確認

GatewayとPortalは、OWASP 3.2のPreventionと本文検査を有効にしたまま使用します。SQL、HTML、シェルコード、不透明な機械生成文字列をモデル・PII等へ渡すことは正当な利用です。そのデータを実行命令として扱うURL、ヘッダー、APIの制御情報とは分け、正規の完全パス・データ欄に限り、個別ルールまたは承認したアプリケーション攻撃ルールグループを調整します。

コマンドはリポジトリルートで実行し、[第1章](../setup/01-prerequisites.md#1-5-環境別のローカル領域を準備する)で設定した`DEPLOY_PARAMS_FILE`と絶対パスの`DEPLOY_LOCAL_DIRECTORY`を使います。計画・実測記録は同じ環境の領域へ保存します。

モデル等のデータ欄除外の正本は `apps/gateway/waf-data-exclusions.json` です。各profileはGatewayまたはPortalの完全パスを指定し、`RequestArgValues`と、動的JSONキーで必要な場合の`RequestArgKeys`を使います。`StartsWith`は`state.`、`questions.`、JSON Schema配下など、区切りを含むデータ構造に限定します。集計規則949110は除外しません。

## 受付容量と検査範囲

| 対象 | 現行資材の制御 |
| --- | --- |
| manifestのGateway／Portal AI完全パス、正本Speech・文書受付 | Prevention・本文検査を維持。先頭2000 KiBまで検査し、本文・ファイルのサイズ強制は解除。超過部分は検査せず受け付ける |
| base・認証callback・管理系など上記以外 | 既定Prevention。configの本文検査枠・本文／ファイルサイズ強制を維持 |
| 外部管理WAF | 自動変更しない |

2000 KiBはAI入力の受付上限ではありません。通常AIのAPIM受付はClassicのバッファー上限500 MiB、文書は500,000,000 bytesで、提供元の制約が小さい場合はそちらが適用されます。WAFの検査枠を超えた本文まで検査したとは扱いません。容量緩和のAzure反映・実経路での通過確認は未実施です。

## 用途別のポリシー構成

名前はGatewayが`wafpol-g-<用途>-<ID>`、Portalが`wafpol-p-<用途>-<ID>`です。`<ID>`にはparamsの`services.apimInstanceId`に保存した6文字の構築識別子を共通利用します。Bicepと個別配備は同じ名前を計算し、更新や途中再開で識別子を生成し直しません。APIのURLは変更しません。

ポリシーの用途はGatewayが14個、Portalが11個の計25個です。Gatewayは公開対象に選んだ完全パスだけを関連付け、条件付きで別用途のポリシーを追加生成しません。

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
| Live | `live`（HTTP sessions） | 個別のデータ欄除外なし |
| PIIのAPI・MCP | `pii-redact` | `pii-redact` |
| 文書アップロード | `document-upload` | `document-upload` |

Gatewayの画像、埋め込み、PIIは複数の完全パスを1つのポリシーへまとめ、各パスに必要な除外の集合和を適用します。画像の`input_image`、埋め込みの`input`と`texts`、PIIの`text`と`params.arguments.text`の除外は、同じポリシーの全経路に作用します。PortalのPIIは両入口とも`text`と同じCookie除外なので、除外範囲を追加せず共有します。

用途名は検査するHTTP入口を表します。Realtime／LiveのWebSocket入口など、個別のデータ欄除外がない経路は`base`を使います。データ欄の除外はmanifestに明記した条件から生成し、APIのMIMEや機能名によって別の除外を自動追加しません。外部管理ポリシーの指定時は、そのポリシーを再利用します。

## 保護条件の共通化

OpenAIのResponsesとChat Completionsは、同じ用途のデータ欄に同じアプリケーション攻撃グループ除外を適用します。[MicrosoftのAIチャット向けWAFチューニング指針](https://learn.microsoft.com/azure/architecture/ai-ml/architecture/baseline-microsoft-foundry-chat#web-application-firewall-tuning)に沿って、会話・指示・コード・ツール結果・引数・説明・宣言的Schemaの欄だけを選びます。SQLやコードの構文をモデルへ渡すことは、Gatewayでその構文を実行することとは別の処理です。WAFの評価はHTTP要求単位であり、履歴やツール結果が後続要求の本文に含まれることで検出対象が増えます。

manifest version 2は、限定した`g-responses`と`g-chat-completions`の完全パス・セレクターだけで、LFI（930）・RFI（931）・RCE（932）・PHP（933）・XSS（941）・SQLi（942）のグループ除外を許可します。Azureへは`ruleGroupName`を指定し`rules`を省略した除外を渡します。version 1の個別ID形式は引き続き読み取れます。全managed ruleの除外、集計949110/980130の除外、他用途へのグループ除外は許可しません。既存の920/921等の個別例外は保持し、プロトコル系グループを一括除外しません。

| 用途 | Responsesの例 | Chat Completionsの例 |
| --- | --- | --- |
| 会話・指示・ツール結果 | `input`、`input.content`、`input.content.text`、`input.output`、`input.output.text`、`instructions` | `messages.content`、`messages.content.text` |
| シリアライズされた呼び出し引数 | `input.arguments`、`input.input` | `messages.tool_calls.function.arguments` |
| ツール説明 | `tools.description`、`input.tools.tools.description` | `tools.function.description` |
| 引数Schema | `tools.parameters.`、`input.tools.tools.parameters.` | `tools.function.parameters.` |
| 出力Schema | `text.format.schema.` | `response_format.json_schema.schema.` |

これは用途の対応表で、許可する実際のselectorの正本はmanifestとvalidatorです。値は明示した`Equals`、動的Schemaは区切り付き`StartsWith`で値とキーを選びます。`input.`・`messages.`・`tools.`全体には広げません。制御項目、画像・ファイルの取得先URL、暗号化履歴、metadataにグループ除外を自動追加しません。Schemaのプロパティ名をモデルや操作の制御項目と誤認せず、nativeのoptional fieldとclient-held Itemsの互換性を維持します。

同名queryにも引数除外が効くため、標準OpenAIの2操作にはAPIMの入口制約を組み合わせます。認証済み購読を確認した後、POST、単一の`application/json`のContent-Type、query引数がないことを検査します。`charset`等のmedia typeパラメーターは許容し、`api-version`を含めてquery引数は全拒否します。APIMのモデル許可、受信byte制約、Responsesのstateless制約、下流認証は維持します。全本文の厳密Schema検証や、全購読でのrate/token制限が既に有効だとは扱いません。画像・ファイルのURL取得、ツール実行先の引数・権限・sandboxは別の実行境界です。

既存除外をグループ除外へ統合する際は、同じvariable・ルールセットで既存の各rule/groupを包含することを照合します。selectorは同一範囲、または承認された区切り付きSchema prefixがexact子field・より狭いprefixを覆う場合だけを認めます。無関係なselectorの撤去、管理規則のaction変更、認証・callback・他のAI用途への波及は許可しません。IaCと運用CLIは同じmanifestを使用し、Prevention、本文検査、既存の検査容量・マスク・customRulesを維持します。

PortalのHTTPSセッションCookie値は、`apps/portal/waf-session-exclusions.json`を正本とし、正確な名前`__Secure-egakumi.portal.session_token`を`RequestCookieValues`・`Equals`で選び、規則942450だけから除外します。Portal HTTPS listener専用の既定policyと、置換関係にあるPortal callback・データ欄policyへ同じ除外を合成します。Gateway側のpolicy、他のCookie、他の規則へは追加しません。HTTPの開発用Cookieを除外へ追加せず、Portalの認証・認可とCookieの保護属性は維持します。

raw SSML/WAVと正本の文書受付では、正規本文の相対パス`../`が930110に一致しました。AzureはXML/raw本文の項目単位除外に対応しないため、Speechの2パス・Gateway文書受付・Portalの`/api/tools/documents`の計4完全パスだけ920420／930110を調整し、URI・query・全header・cookieには4本のcustom Blockを合成します。930100・920470等の他の管理規則と本文検査は残します。正本は`apps/gateway/waf-raw-body-controls.json`、Python生成は共通の`waf_body.raw_body_policy`です。CRSの全正規化処理と完全に同等の代替ではなく、本文内のパス文字列を許容する限定した判断です。Portalはrawストリーミングを使い、互換用multipartの`input`に対する既存5 SQL規則の値除外も維持します。Portalの状態確認・隣接パスへraw本文の調整は広げません。

Log4j/JNDIの例文で検知した`Known-CVEs / 800100`も、実測した正規データ欄だけへ限定します。未知のfield・query・headerの攻撃対照と、同名queryへの影響を独立して検証します。

Chat Completionsの画像data URLは、`RequestArgValues / Equals / messages.content.image_url.url`だけをXSS規則941130・941170とSQL規則942340・942450から除外します。Responsesの画像data URLは、完全パス`/models/openai/v1/responses`の`RequestArgValues / Equals / input.content.image_url`だけをXSS規則941130・941170から除外します。toolの動的JSON Schemaキーは、承認された引数Schema prefixの`RequestArgKeys / StartsWith`に限り、上記6カテゴリのグループ除外へ統合します。隣接する別fieldや制御情報、headerへ広げず、集計規則949110と共通の保護条件は維持します。

SSMLのDTD/ENTITY宣言は、WAF本文カスタム規則のMIME対応に依存せず、正規TTS操作のAPIMガードで400 `InvalidSpeechMarkup`として拒否します。正本は`apps/gateway/apim/policies/speech-ssml-guard.xml`です。XMLや外部エンティティのparserを呼ばず、UTF-8/16/32を認識して実宣言だけを検査し、escaped例文・コメント・CDATAは許容します。独自の8192byte枠は撤廃し、継承した認証・受信byte検査の後、上流へ転送する前に宣言を検査します。元本文と上流URIは保持します。匿名401だけではガード成功とせず、認証付きの正常音声生成と明示400応答も確認します。SSML全体のschema検証は下流Speech側の責務です。

`infra/modules/application-gateway.bicep`と`scripts/waf_policy.py`は同じ正本を使用します。個別Gateway／Portal配備も照合するため、Azure上だけの手動例外が次回配備で消える構成を避けます。専用ポリシーは親を継承しないため、Prevention・本文検査・ログのマスク・customRulesと、manifestのデータ欄除外・上記のAI専用サイズ設定を合成します。企業管理の外部WAFは変更しません。既存の固有保護を表現できない場合は停止します。

通常の配備と同じparamsを指定して、まずローカル計画を確認できます。計画ではAzureに接続しません。適用時は対象環境とETagを照合し、不要なPUTを行いません。

```sh
python3 scripts/waf_policy.py --config "$DEPLOY_PARAMS_FILE"
python3 scripts/waf_policy.py --config "$DEPLOY_PARAMS_FILE" --scope all --execute
```

## 再現する試験

ResponsesとChat Completionsの対応するデータ欄を、PHPの変数関数呼び出しを含む言語別の例と、ツール呼び出し・ツール結果を含む履歴構造で確認します。ネストしたツール説明・Schema、動的キーも対象にし、クライアント・API・managed rule版を更新するときに再実行します。匿名canaryの通過確認と、認証付きの複数ターンE2Eを区別します。

`waf_probe.py`は公開API契約・Portalの実ハンドラーから入口を列挙します。JSON／配列／Schema／関数引数／動的キー、Unicode・記号・コード・Base64、画像・PDF・音声、SSML、multipart、SDP、WebSocket Upgrade、callback、query・header、サイズ境界、攻撃対照を合成入力で試します。

```sh
python3 scripts/waf_probe.py --dry-run --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-plan.json"
python3 scripts/waf_probe.py \
  --gateway-base-url https://gateway.example.com \
  --portal-base-url https://portal.example.com \
  --transport curl --workers 4 --requests-per-second 4 \
  --phase verification --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-verification.json"

# 正規raw本文と、非本文のパストラバーサル対照を別途確認
python3 scripts/waf_probe.py --raw-body-guards-only \
  --gateway-base-url https://gateway.example.com \
  --portal-base-url https://portal.example.com --transport curl \
  --phase raw-verification --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-raw-guard.json"
```

`curl`はWindowsのSchannelで通常の証明書検証を行う環境でも利用できます。リダイレクトは追わず、本文、応答本文、キー、Cookieを証跡へ保存しません。各要求の`User-Agent`は`egakumi-waf-probe/1.0 <correlation>`とし、既存の証跡の`correlation`欄にある短い英数字の識別子を使います。アクセスログではqueryを保持しないため、相関用queryは追加しません。試験入力のqueryはそのまま送り、正確な`User-Agent`・所有AppGW・要求時刻でアクセスログを絞り、`TransactionId`でWAF規則ログと照合します。識別子が失われた要求を、順序だけで個別試験へ割り当てません。

FLUX画像編集はpromptだけでなく、`input_image`のraw Base64画像も確認します。OpenAIの画像URLと形式を混同して`data:image/png;base64,...`を付けず、正規のAPI契約に従った入力で評価します。契約外入力の遮断だけを理由にWAF除外を追加しません。

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
