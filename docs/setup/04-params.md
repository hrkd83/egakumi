# 4. paramsの準備・リファレンス

前：[3. 公開DNSの準備](03-public-dns.md) · [README](../../README.md) · 次：[5. configの調整](05-config.md)

## このページのゴール

導入先のID・名前・IP計画・公開名・利用するAPIを、一つのローカルparamsへ設定します。ここではAzure資源を作りません。第1章の変数と、Entra／DNS担当者から受領した情報を手元に用意します。

## 4-1. paramsとconfigの役割

| ファイル | 管理するもの |
| --- | --- |
| `.local/<environment>/params.local.json` | 導入先の企業・環境ごとに確認する値。Subscription／Tenant、製品・環境名、公開名、IP・DNS、担当者、利用者・API登録 |
| `config/test.json`／`config/production.json` | 標準構成の正本。SKU、ゾーン、モデル、ポート、保護機能、回数制限等。調整は次のページで行う |
| `.local/<environment>/secrets/` | `azd up`が対話入力から自動保存する保護ファイル。利用者がJSONを作成する場所ではない |
| `schemas/` | 設定の型・指定可能値を検査するSchema。実値を設定する場所ではない |

ファイル参照は**そのJSONのあるフォルダを基準に解決**します。準備コマンドで作成したparamsは、configを`../../config/test.json`または`../../config/production.json`、秘密ファイルを`./secrets/...`で参照します。`params/`内の公開exampleは配布用の架空値の正本として保持し、実値を直接書き込みません。秘密値はJSONへ記載しません。

`services.apimInstanceId`は第1章の準備コマンドがparamsへ保存した6文字の識別子です。APIM名と用途別WAFポリシー名に共通利用し、EntraのCM戻り先・構築・更新・撤去で同じ値を使います。`azd up`はこの値を生成し直しません。撤去後に新しく構築するときは、[新しい識別子の準備](../operations.md#撤去後に新しく構築する)と第2章のCM戻り先更新を先に行います。

## 4-2. 環境別paramsを確認する

第1章の[準備コマンド](01-prerequisites.md#1-5-環境別のローカル領域を準備する)で、選んだ公開exampleから作成したparamsを使います。環境名自体は任意で、SKUの選択とは独立です。別ターミナルでは同じ環境名から参照を設定し直します。

```sh
export DEPLOY_LOCAL_DIRECTORY="$(pwd)/.local/${DEPLOY_ENVIRONMENT}"
export DEPLOY_PARAMS_FILE="$DEPLOY_LOCAL_DIRECTORY/params.local.json"
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
test -f "$DEPLOY_PARAMS_FILE"
```

ファイルが存在しなければ第1章の準備を完了してから進みます。本番向け標準構成は準備時に`--profile production`を選びます。作成済みの実paramsへ公開例を重ねてコピーしません。configファイルはコピーせず、paramsの`configFile`から選んだ正本を参照します。

公開例のゼロGUID・名前・ドメイン・会社名・メール・IPは見本です。以後の手順で自社の値へ書き換えてください。

## 4-3. 環境の値を記入する

コピーしたファイルをエディターで開き、次の順に編集します。

1. `subscriptionId`・`azureTenantId`・`entraTenantId`・`productName`・`environmentName`・3つのリージョンを、第1章の確定値へ揃える。
2. `network.addressSpaces`と全`network.subnetPrefixes`を自社のIP計画へ設定する。接続元CIDR、運用者CIDR、DNS・追加宛先も確認する。
3. `gateway.publicHostName`と`gateway.grpc.publicHostName`をGatewayの公開FQDNへ、`portal.publicHostName`をPortalの公開FQDNへ設定する。`gateway.authentication.subscriptionKeyHeader`は既定の`api-key`を使用する。証明書SANは両方の公開名を覆う値にし、通知先と連絡先を記入する。
4. `certificate.plugin`は`Azure`とし、第3章で確認したDNS側Subscription IDとUAMIのリソースIDを`certificate.azureDnsSubscriptionId`・`certificate.azureDnsManagedIdentityResourceId`へ必ず記入する。`azd up`は準備済みの参照を検査し、不足・不正があれば構築前に停止する。
5. Azure RBACの対象Object IDを設定する。CM providerの`clientId`を第2章で作成したEntraアプリのIDへ置き換え、本人接続の有効期間等を確認する。

公開例にはGraph本人接続用の`credentialProviders`と`gateway.authentication.credentialLogin`を含めています。`azd up`は共通基盤を作成した後、Gateway、Portalの順に配備します。Gateway工程でCM provider・credentials DB・認証窓口とAPIを設定し、Portal工程で認証DB・管理画面と検証用購読を準備します。`apiDefinitions: []`なら標準19 API、配列に定義があれば指定したAPIを登録します。公開例の`publishApis`は`true`です。既存のlocal paramsは自動変更しません。


### 同一テナントと別テナントの指定

検証用・本番用の公開例には、トップレベルに`azureTenantId`と`entraTenantId`の入力欄があります。同一テナントなら両方に同じIDを、別テナントならそれぞれのIDを設定します。`entraTenantId`の項目自体を省略した場合は`azureTenantId`と同じ値を使います。ゼロGUIDは見本のため、項目を残す場合は実値へ置き換えてください。下記のGUIDは架空の例です。

```json
{
  "azureTenantId": "11111111-1111-4111-8111-111111111111",
  "entraTenantId": "22222222-2222-4222-8222-222222222222"
}
```

これは該当2項目だけの抜粋です。公開例のCM providerは、トップレベルの`entraTenantId`を認証先に使います。providerごとに別の認証先を使う場合は、そのproviderへ`oauthTenantId`を追加します。`authorization`、`management.loginAssignments`、`monitoring.auditReaders`等のAzure RBACにはAzure側のObject IDを指定します。`AZURE_TENANT_ID`というazd環境変数もAzure側です。`entraTenantId`を変えても、Azureの配置先は変わりません。

### Gateway全体の認証とAPIごとの認可

`gateway.authentication.subscriptionKeyHeader`に購読キーの受信ヘッダー名を指定します。既定は`api-key`です。APIMの標準機能で有効な購読とProduct/APIの利用範囲を確認し、全体ポリシーを`<base />`で各APIへ継承します。

APIの`authorization.mode`は`subscriptionKey`です。`operationConstraints`で操作・モデル・入力・出力等を制限します。本人接続が必要なAPIにはCM接続セッションの条件を設定します。

公開例の`gateway.authentication.credentialLogin`と対応providerを使って、本人の下流認証を準備します。後続のAPI登録処理でCM共通窓口の保護APIとブラウザーAPIを追加し、本人接続APIでは同じ購読へ結び付けた接続セッションも検査します。[CM共通接続手順](credential-manager-login.md)

キーはクライアントのバックエンドへ保管します。画面からはBFF等を利用し、ブラウザーJavaScriptへキーを配布しません。

### 基盤作成とAPI利用を分けて選ぶ

公開paramsの初期値は`apiDefinitions:[]`・`publishApis:true`で、Graph本人接続用のCM設定を含みます。導入先のID等を記入して`azd up`を実行すると、5RG、ネットワーク、APIM、共通SQLサーバー、Foundry・Project・選択モデル、証明書、管理VM、監視・API Center、ACA・ACRを作成し、GatewayとPortalまで順に配備します。

| paramsの状態 | 構築・利用の結果 |
| --- | --- |
| `azd up`、`publishApis:true` | 共通基盤・選択モデル・共有Entra secretを構築し、Gateway→Portalの順でアプリ・API・検証用購読を配備する |
| `azd up`、`publishApis:false` | `preup`の冒頭で課金資源の作成前に停止する。入力値を自動でtrueへ変えない |
| `azd provision` | 共通基盤だけを構築する。Gateway・Portalの追加配備は実行しない |
| 基盤作成後にGUIでAPI登録 | CoreのAPI一覧への登録は不要。API側で共通認証を継承し、公開パス・操作・追加認可と接続先を設定する |
| Gateway配備コマンドの`--execute`、`publishApis:true` | credentials DB・CM・認証窓口を設定に応じて配備し、共通認証ポリシーと標準19 APIまたは明記したAPIを登録・更新する |
| 登録コマンド、`publishApis:false` | 登録計画を生成する。`--execute`によるAzureへの反映は停止する |

標準一式は`apiDefinitions: []`のまま初回の`azd up`で登録でき、定義のコピーやAPIごとの選択は不要です。接続先は作成済み基盤の出力からメモリー内で解決し、paramsの空配列は書き換えません。基盤に必要なWAFの経路・本文形式と監査ログ等の情報も、同じ標準定義から事前に反映します。基盤だけを作りGUIで登録する場合は`azd provision`を使い、APIの正本を別途管理します。OAuth用secretは共通基盤で準備し、CM providerはGateway配備で設定します。

以下は、標準一式ではなく、対象APIや独自APIを明示する場合の手順です。

1. [標準API定義](../../apps/gateway/apim/apis/)から必要なAPIファイルを選び、そのJSONオブジェクトをparamsの`apiDefinitions`配列へ記載します。モデルだけなら[openai-inference-v1.json](../../apps/gateway/apim/apis/openai-inference-v1.json)、PIIなら[pii-v1.json](../../apps/gateway/apim/apis/pii-v1.json)を選べます。各ファイルは1つのAPI定義です。
2. 利用するProductと購読を設定し、CM接続の条件が必要なAPIにはその設定を加えます。下の省略条件と[認証設定と購読](04-params.md#認証設定と購読)を確認し、`__NAME__`の置換欄を実値へ置換します。
3. Graph等の正式な外部接続APIでは、実backend URL・認証と`network.firewall.apimAllowedFqdns`をparamsへ設定します。`services.modelDeployments`、SKU、WAF上限等はconfig正本側の項目で、APIファイルには含めません。paramsへ転記すると構造検査で拒否します。gRPCの入口は有効・公開8443・APIM443に固定し、paramsで有効状態やポートを変更する項目はありません。モデル名と許可モデルの整合も確認します。
4. ファイル参照をparams基準へ直し、`python3 scripts/deployment/helpers/configuration.py validate --config "$DEPLOY_PARAMS_FILE"`で確認します。`publishApis:true`で初期の`azd up`を実行すると、指定したAPIもGateway工程で登録します。構築済み環境では、同じparamsとCore stateを使って[個別の配備コマンド](../operations.md#同じ環境を更新する)から更新できます。

| 利用する方式 | 必要な定義と省略できるもの |
| --- | --- |
| 購読キーだけ | `authorization.mode:"subscriptionKey"`と`operationConstraints`。`keySubscriptions`で作成するProduct購読を指定する。既存購読を再利用する場合はその利用範囲を確認する |
| モデル・PII・生成MCP | 同じ購読キーを使い、APIM MIでFoundryへ接続する。これらのAPIの下流認証にはCMを使わない。PortalのEntra設定は別に準備する |
| 本人のCM接続 | `graph-profile-v1`等に`credentialConnection.mode:"personal"`を設定。providerと共通`credentialLogin`を合わせて登録する |
| 固定サービスCM接続 | `credentialConnection.mode:"service"`と、providerの`connectionNames`に存在する固定接続名を設定する |

Gatewayだけを個別配備し、本人接続を使わない場合は、`credentialProviders`を`[]`にし、`gateway.authentication.credentialLogin`を省略できます。Portalも配備する通常の`azd up`では、`portalAuthentication.credentialProviderName`が参照するEntra用profileとsecretを用意します。標準19 API一式では、Graph本人接続用の`graph-delegated`と共通`credentialLogin`も必要です。

配列は追加マージではなく**全置換**です。設定ファイルの参照を重ねると配列は置き換わります。同じAPIの操作や認証設定はそのAPIの全体を保存しますが、APIM上の全APIを列挙する必要はありません。[標準API定義](../../apps/gateway/apim/apis/)は推論・PII・Graph・音声・画像等の19 APIを1つずつ管理します。正式endpointと認証設定を確認してから公開します。

### 外部ツールの旧SSEをAPIMへ接続する

CoreはMCP Streamable HTTP/旧SSEの汎用中継に対応します。接続するMCPサーバーの公開契約に合わせて入口を定義します。

旧SSE連携では、接続を開くGETと`endpoint`イベントで案内されるPOST先を分けて扱います。登録した`mcpProxy.endpoints`のSSE/messageパスと実サーバーの待受を照合し、公開POSTのsuffixは登録済みmessageパスを末尾`/`も含めて保持します。公開prefix・認証・入口の管理責任は外部側の定義と合わせます。サーバーから通知されたsession IDや任意URLを、未登録の接続先として受け入れません。

`tests/fixtures/external-api-definitions.json`はこの経路をローカルで回帰検証する合成fixtureであり、導入先へ配備する設定例ではありません。


## 4-4. ローカルで設定を検査する

```sh
python3 scripts/deployment/helpers/configuration.py validate --config "$DEPLOY_PARAMS_FILE"
```

設定の欠落・未知の項目・IP重複・認証の組合せ等があれば、示された項目を直します。この検査はsecretの値・Azure権限・実モデルの配備可否・疎通を確認しません。secretファイルがまだ無いこと自体は、事前に手作りする理由にはなりません。

## paramsリファレンス

下表の`<...>`は導入先の入力値です。CIDR・ポート等の具体例は公開ファイルに含まれる初期例で、自社へ適用できると確認した値だけを使用します。利用者IDやAPI定義内の実endpointも、各担当者から取得して明示指定します。Schema：[params](../../schemas/params.schema.json)／[合成設定](../../schemas/configuration.schema.json)。


### 企業・環境ごとに確認する値（params）

この表が最初に確認する範囲です。ファイル参照の例は準備後の`.local/<environment>/params.local.json`を基準にしています。ID・連絡先・公開名・IP計画・権限と秘密ファイル参照を確定します。CM providerと、個別指定する`apiDefinitions`は初期構築前に設定します。標準一式は空配列のまま使います。

| パラメタ名 | 指定値・公開例 | 意味 |
| --- | --- | --- |
| `schemaVersion` | `1` | 設定形式の版。整数1。 |
| `configFile` | テスト：`"../../config/test.json"`／本番：`"../../config/production.json"` | 標準configの正本への相対パス。このparamsファイルのフォルダーを基準に解決。 |
| `productName` | `"<PRODUCT_NAME>"` | リソース名に使う製品名。先頭英小文字、英小文字・数字・内部ハイフン、最大24字。 |
| `environmentName` | `"<ENVIRONMENT_NAME>"` | 命名とタグに使う任意の環境名。test/dev/prodは参考例。先頭英小文字、英小文字・数字・内部ハイフン、最大12字。SKU・保持・保護の切替には使わない。 |
| `subscriptionId` | `"<SUBSCRIPTION_ID>"` | 構築先Subscription ID。公開例のゼロGUIDを企業の実値へ置換。 |
| `azureTenantId` | `"<AZURE_TENANT_ID>"` | Azureリソースの構築先Tenant ID。Subscription・Key Vault・Azure RBAC・管理APIに使用。 |
| `entraTenantId` | `"<ENTRA_TENANT_ID>"`（省略可） | OAuth接続の既定認証先。省略時はazureTenantId。 |
| `services.apimInstanceId` | 準備コマンドが生成 | APIM名と用途別WAFポリシー名の末尾に使う英小文字・数字6文字。構築・更新・途中再開・撤去で保持。configには記載しない。 |
| `services.publisherName` | `"<APIM_PUBLISHER_NAME>"` | APIMの発行者表示名。 |
| `services.publisherEmail` | `"<APIM_PUBLISHER_EMAIL>"` | APIMの発行者メール。導入先の実値へ変更。 |
| `gateway.publicHostName` | `"<GATEWAY_FQDN>"` | API専用の公開FQDN。公開DNSと証明書に一致させる。 |
| `gateway.authentication.subscriptionKeyHeader` | `"api-key"` | 全API共通の購読キー受信ヘッダー名。APIの設定と一致させる。 |
| `gateway.authentication.credentialLogin` | `graph-delegated`を対象に設定済み | CM本人接続の対象provider・セッション有効期間・ログイン取引の期限・回数制限。詳細は後述。 |
| `management.adminUsername` | `"<MANAGEMENT_USERNAME>"` | Linuxの初期管理者名。root/admin等の予約名を除く、最大32字。 |
| `management.sshPublicKeyFile` | `"./secrets/management-ssh.pub"` | 必須の公開鍵入力兼生成先。有効な既存RSA 2048bit以上/Ed25519公開鍵は外部保存でも再利用。公開鍵・秘密鍵が両方ない場合だけ、同じ環境の`.local/<environment>/secrets/`へパスフレーズなしRSA4096鍵対をpreupで生成。秘密鍵はAzureへ送らない。 |
| `management.loginAssignments` | `[]` | Azure RBACのVMログイン許可。各要素principalId/principalType/role。roleはUser/Administrator。 |
| `monitoring.operatorEmailAddresses` | `["<OPERATOR_EMAIL>"]` | 監視通知先メール配列、1件以上。 |
| `certificate.contactEmail` | `"<ACME_CONTACT_EMAIL>"` | ACME連絡先。 |
| `certificate.domainNames` | `["<CERTIFICATE_SAN>"]` | 証明書のSAN。ワイルドカードは直下の一階層のみ。省略時はGatewayの公開名。 |
| `certificate.azureDnsSubscriptionId` | `"<DNS_SUBSCRIPTION_ID>"` | 構築前に必須設定。公開DNSゾーンがあるSubscription ID。 |
| `certificate.azureDnsManagedIdentityResourceId` | `"<DNS_MANAGED_IDENTITY_RESOURCE_ID>"` | 構築前に必須設定。DNS操作権限を事前付与したUAMIの完全なリソースID。管理VMへ紐づける。Client IDではない。 |
| `authorization.projectAdministrators` | `[]` | 初期Foundry Project管理者。 |
| `authorization.projectDevelopers` | `[]` | 初期Project開発者。 |
| `authorization.foundryReaders` | `[]` | FoundryのReader。 |
| `authorization.catalogReaders` | `[]` | API CenterのData Reader。 |
| `credentialProviders` | Graph本人接続用の`graph-delegated` | 本人OAuth接続のprovider設定。構築前に`clientId`等を記入する。詳細は後述。 |
| `apiDefinitions` | `[]` | `azd up`のGateway工程と個別のGateway配備で、空配列なら標準19 API、定義があれば指定したAPIを登録。標準APIはメモリー内で解決し、paramsは書き換えない。 |
| `publishApis` | `true` | 全体の`azd up`とGatewayの`--execute`を許可する。falseではupを課金前に停止し、個別の登録計画だけを生成できる。基盤だけなら`azd provision`を使う。falseへの変更で稼働中APIを停止しない。 |
| `gateway.existingWafPolicyId` | `""` | 既存WAF policyのResource ID。空なら資材が作成。外部policyの実効設定は別途確認。 |
| `gateway.grpc.publicHostName` | `"<GATEWAY_FQDN>"` | gRPCの公開FQDN。現実装では親のgateway.publicHostNameと同じ値を指定。 |
| `gateway.rateLimits.subscriptionKey.overrides` | `[]` | 有効時の呼出主体別calls/renewalPeriodSeconds。区分内のcallerId重複不可。 |
| `gateway.rateLimits.application.overrides` | `[]` | 追加アプリ区分は無効のまま保持する。共通上限はsubscriptionKeyへ設定する。 |
| `monitoring.auditReaders` | `[]` | 監査コンテナーの読取主体。各要素principalId/principalType/collector/containerName。 |
| `certificate.plugin` | `"Azure"` | Azure Public DNSのDNS-01認証に使用するPosh-ACMEプラグイン。 |

証明書はAzure Public DNSによるDNS-01で自動発行・更新します。構築前に、公開DNSゾーンのSubscription IDとDNS用UAMIのリソースIDを設定してください。

#### リージョンとタグ

| パラメタ名 | 指定値・公開例 | 意味 |
| --- | --- | --- |
| `location` | `"<LOCATION>"` | 必須。ネットワーク・APIM・管理VM等の配置リージョン。 |
| `foundryLocation` | `"<FOUNDRY_LOCATION>"` | 必須。Foundryの配置リージョン。モデルの地域別提供・利用枠を確認。 |
| `apiCenterLocation` | `"<API_CENTER_LOCATION>"` | 必須。API Centerの配置リージョン。 |
| `tags` | `{"purpose":"<PURPOSE_TAG>"}` | 文字列のタグ。product/environmentタグは製品名・環境名から追加。 |

#### ネットワーク・監査の接続設定

paramsの`network`・`network.addressSpaces`・`network.subnetPrefixes`は必須です。全サブネットをVNet範囲内へ重複なく割り当て、企業の既存IP計画・接続予定先と照合して指定します。公開例の10.40系CIDRを、導入先でも未使用と推測しません。

選択項目も公開params例に配列で記載しています。`[]`は`operatorAddressPrefixes`では管理者の許可元なし、追加規則では追加なしを表します。利用者の`clientAddressPrefixes`は1件以上が必要で、`[]`はAzure変更前の入力検査で拒否します。`snatPrivateRanges`の公開例は`["IANAPrivateRanges"]`で、空配列とは異なる指定です。外部NTP先と追加接続先FQDNも、企業の接続先に合わせる値なのでparamsへ記載します。FirewallのSKU・ゾーン・標準動作はconfigへ記載します。

VNetの`network.dnsServers`とFirewallの`network.firewall.dnsServers`は、[標準config](05-config.md#ネットワークの通信機能network)でどちらも`[]`に固定し、Azure提供DNSを使います。paramsには指定できず、残っていると入力検査で拒否します。以前のparamsにこれらの項目がある場合は、両方とも削除してください。既存の独自DNSを使う環境では、Azure提供DNSへの切り替えによる影響を確認してから移行します。実ファイルの自動移行は行いません。

| パラメタ名 | 指定値・公開例 | 意味 |
| --- | --- | --- |
| `network.addressSpaces` | `["10.40.0.0/24","10.40.1.0/24","10.40.2.0/24","10.40.3.0/24"]` | 必須のVNet IPv4 CIDR配列。/24×4、最後の10.40.3.0/24はサブネット未作成の予備。 |
| `network.subnetPrefixes.agw` | `"10.40.0.0/24"` | AppGW専用サブネット。 |
| `network.subnetPrefixes.pep` | `"10.40.1.0/24"` | Private Endpoint用サブネット。 |
| `network.subnetPrefixes.firewall` | `"10.40.2.0/26"` | AzureFirewallSubnet。 |
| `network.subnetPrefixes.apim` | `"10.40.2.64/26"` | Internal APIM専用サブネット。 |
| `network.subnetPrefixes.management` | `"10.40.2.128/26"` | Linux管理VM用サブネット。 |
| `network.subnetPrefixes.aca` | `"10.40.2.192/26"` | Core管理ポータルのACA専用サブネット。基本/26、最小/27。他の5区画と同じ一覧で指定。 |
| `portal.publicHostName` | `"portal.example.com"` | Core管理ポータルの公開FQDN。`portal.`で始まるGatewayとは別の名前を指定し、証明書SANにも含める。 |
| `network.clientAddressPrefixes` | `["0.0.0.0/0"]` | API利用者の送信元IPv4/CIDRを1件以上指定。省略時の0.0.0.0/0は全IPv4、特定CIDRはその範囲を許可。明示した`[]`はAzure変更前の入力検査で拒否。Web受信120・gRPC受信125のAllowを使い、認証・認可は別に維持。 |
| `network.operatorAddressPrefixes` | `[]` | 管理VMから見える管理接続元のIPv4/CIDR。private範囲も指定できる。指定ありの場合だけAllow・220を作成し、[]または省略時は作成しない。設定を外した場合は構築前処理で現行Allow・220を削除し、未許可通信は4096で拒否。接続経路は別途必要。 |
| `network.ntpAddressPrefixes` | `["Internet"]` | Linux管理VMの時刻同期宛先範囲／サービス・タグ。空配列は不可。 |
| `network.ntpFqdns` | `["ntp.ubuntu.com"]` | Linux管理VMに設定する外部NTP先FQDN。既存Chrony/PTPは保持し、外部通信をFWでも制限する。 |
| `network.firewall.apimAllowedFqdns` | `[]` | APIMから追加で接続するHTTPS宛先。標準のplatform通信は別に作成。 |
| `network.firewall.managementAllowedFqdns` | `[]` | 管理VMから追加で接続するHTTPS宛先。証明書取得・PowerShell導入に必要な共通宛先は資材が追加。 |
| `network.managementAdditionalSecurityRules` | `[]` | 管理VM用NSGに追加するAzure形式の規則配列。名前・優先度の重複を避ける。 |
| `network.firewall.snatPrivateRanges` | `["IANAPrivateRanges"]` | SNATせずprivate宛先とみなす範囲。API往復の送信元保持も考慮。 |
| `network.firewall.idpsPrivateRanges` | `[]` | Premium IDPSのprivate判定範囲を上書き。[]は独自の範囲を指定しない。 |
| `network.firewall.bypassTrafficSettings` | `[]` | Premium IDPSの検査除外。[]は追加除外なし。Standardでは指定しない。 |
| `network.firewall.apimAdditionalNetworkRules` | `[]` | APIMの追加Network規則。Azure Firewall API形式。 |
| `network.firewall.managementAdditionalNetworkRules` | `[]` | 管理VMの追加Network規則。 |
| `network.firewall.apimAdditionalApplicationRules` | `[]` | APIMの追加Application規則。 |
| `network.firewall.managementAdditionalApplicationRules` | `[]` | 管理VMの追加Application規則。 |
| `monitoring.auditAllowedIpAddresses` | `[]` | 監査Storageへの公開アクセス許可元IPv4/CIDR。[]は公開IP許可を追加しない。API入口の制限とは別。 |

予備の10.40.3.0/24にはサブネット・NSG・ルートテーブルを作りません。管理VMはprivate接続で、接続元CIDRを指定しただけではインターネットからSSH接続できません。

管理VMのNICにpublic IPはありません。この資材はBastion・VPN・ExpressRoute・管理VMへのDNAT入口を自動構築しません。
公開入口のあるBastionを別途利用する場合は「管理者PC → Bastion → 管理VMのprivate IP」の経路になり、
`operatorAddressPrefixes` には通常AzureBastionSubnetのCIDRを指定します。管理者PCやBastionのpublic IPをVM側の送信元として指定するものではありません。
VPN・既存踏み台・NATを利用する場合も、VMに届く実際の送信元IPv4/CIDRと往復経路を確認します。
[MicrosoftのBastion対象VM側NSG要件](https://learn.microsoft.com/en-us/azure/bastion/bastion-nsg#target-vm-subnet)

資材の証明書処理などは、VM Agent経由のManaged Run Commandを使います。これらの標準処理のためにSSHの受信許可を追加する必要はありません。
[MicrosoftのManaged Run Command](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/run-command-managed)

IP制限は、設定済みCIDRの許可／拒否を[動作テスト](../testing/integration-tests.md)で、全IPv4許可・省略・有効なCIDR間の再配備と、clientの空配列をAzure変更前に拒否する入力検査を[構築テスト](../testing/construction-tests.md)で確認します。`operatorAddressPrefixes:[]`による管理VMの拒否は別の有効な条件です。APIの認証・認可は全IPv4許可時も維持します。

### API・権限の記述形式

API、初期権限、監査読取主体の`monitoring.auditReaders`はすべてparamsへ記載します。

#### 権限配列の要素

| パラメタ名 | 公開サンプル値 | 意味 |
| --- | --- | --- |
| `authorization.<配列>[].principalId` | 配列空 | 対象User/Group/ServicePrincipalのObject ID。 |
| `authorization.<配列>[].principalType` | 配列空 | User/Group/ServicePrincipal。Azure RBACを付与し、Entra管理ロールを付与しない。 |
| `management.loginAssignments[].principalId` | 配列空 | VMログイン主体Object ID。 |
| `management.loginAssignments[].principalType` | 配列空 | User/Group/ServicePrincipal。 |
| `management.loginAssignments[].role` | 配列空 | User/Administrator。NSG許可・接続経路とは別。 |
| `monitoring.auditReaders[].principalId` | 配列空 | 監査読取主体。 |
| `monitoring.auditReaders[].principalType` | 配列空 | 読取主体の種類。 |
| `monitoring.auditReaders[].collector` | 配列空 | 監査の地域別収集先。 |
| `monitoring.auditReaders[].containerName` | 配列空 | 読取対象コンテナー名。Linux OSログはam-syslog。Japan側のAPIMログはinsights-logs-gatewaylogs／insights-logs-gatewayllmlogs／insights-logs-websocketconnectionlogs。 |

#### APIの定義（apiDefinitions[]）

以下の短縮名はA=`apiDefinitions[]`、O=`A.operations[]`、C=`A.authorization.operationConstraints.<操作名>`です。空配列なら[標準API定義](../../apps/gateway/apim/apis/)の19ファイルを登録時に自動展開します。以下の各項目は、APIを個別指定する場合に使います。

| パラメタ名 | 公開サンプル値 | 意味 |
| --- | --- | --- |
| `A.name` | 追加API例ごとに設定 | API識別名。 |
| `A.displayName` | 追加API例ごとに設定 | API表示名。 |
| `A.path` | 追加API例ごとに設定 | 公開パス。 |
| `A.apiType` | 通常http | API種類。 |
| `A.backendService` | 追加API例ごとに設定 | foundryOpenAi/foundryCognitive/foundryServices/external。外部ツールはexternalを指定。 |
| `A.backendUrl` | 外部API例で置換用URL | externalのTLS接続先。採用するbackendの実URLを指定。基盤Foundryは自動算出。 |
| `A.backendAudience` | 下流ごとの例 | APIMが下流tokenを取得する宛先。 |
| `A.productId` | 各API例のProduct | 検証用APIM Product。`released-apis`は予約名のためここへ指定しません。契約・配賦先IDとは別。 |
| `A.released` | 省略時false、標準19 APIはtrue | 通常キー用`released-apis`製品への所属。生成MCPも同じ状態です。falseへ変更してGateway配備すると通常製品との関連だけを解除し、API本体・検証用所属・購読を保持します。 |
| `A.subscriptionKeyHeader` | 省略時は共通値 | 明示時は`gateway.authentication.subscriptionKeyHeader`と一致させる。ヘッダー名の大文字・小文字は区別しない。 |
| `A.backendHeaders` | 通常{} | 非秘密の固定ヘッダー。鍵やtokenは格納不可。 |
| `A.keySubscriptions` | 省略する場合は`[]` | このAPIのProductに作成するAPIM購読の配列。 |
| `A.operations` | 操作ごとの例 | 公開操作配列。 |
| `O.name` | 操作ごとの例 | 操作識別名。 |
| `O.displayName` | 操作ごとの例 | 操作表示名。 |
| `O.method` | 操作ごとの例 | HTTPメソッド。 |
| `O.urlTemplate` | 操作ごとの例 | 公開操作パス。 |
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
| `C.profiles[].backendUrlTemplate` | FLUX flex／proの固定URI | モデルとprofileを検査した後に選ぶ上流相対URI。同じOperationの全profileに設定する。 |
| `A.websocket.session.kind` | `live`／`transcription` | 接続後イベントの契約。接続時に固定パスとqueryを検査する。 |
| `A.websocket.session.models` | 配備済みモデル名の配列 | クライアントがsessionイベントで指定するモデル。イベントの処理はアプリと上流サービスが担当する。 |

各検査・転送・認証ポリシーの役割は[APIMポリシー一覧](../reference/apim-policy-catalog.md)を参照してください。

#### 認証設定と購読

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
| `A.keySubscriptions[].subscriptionId` | 作成する購読リソース名。例：`sales-models` |
| `A.keySubscriptions[].displayName` | 購読の表示名 |
| `A.keySubscriptions[].enabled` | trueで有効、falseで停止 |
| `__CM_CLIENT_ID__` | CM providerが利用するEntraアプリのApplication（client）ID |
| `__TENANT_ID__` | CMのOAuth認証先テナント |

CMの共通入力例です。provider側に`identityEndpoint`等も設定します。

```json
"authentication": {
  "subscriptionKeyHeader": "api-key",
  "credentialLogin": {
    "providerNames": ["graph-delegated"],
    "idleTimeoutSeconds": 604800,
    "absoluteLifetimeSeconds": 2592000,
    "rotationIntervalSeconds": 86400,
    "rotationGraceSeconds": 300,
    "rotationHandoffSeconds": 600,
    "rotationCandidateTtlSeconds": 600,
    "transactionLifetimeSeconds": 300,
    "loginCallsPerMinute": 10
  }
}
```

| `credentialLogin`の項目 | 既定値 | 制約・動作 |
| --- | --- | --- |
| `idleTimeoutSeconds` | 604800（7日） | 300〜31536000秒。最終の本人検証済み業務利用から数え、最長期限を超えない |
| `absoluteLifetimeSeconds` | 2592000（30日） | 300〜31536000秒。無操作期限以上。認証完了を起点に固定する |
| `rotationIntervalSeconds` | 86400（24時間） | 300〜2592000秒、最長期限未満。経過後の最初の業務利用で更新候補を返す |
| `rotationGraceSeconds` | 300（5分） | 1〜3600秒、更新間隔未満。更新確定から旧値を受け付ける固定猶予 |
| `rotationHandoffSeconds` | 600（10分） | 60〜3600秒。候補を初めて発行してから更新確定が必要になるまでの時間 |
| `rotationCandidateTtlSeconds` | 600（10分） | 60〜3600秒、引き渡し猶予以下。保存・確定する候補の有効期間 |

これらは認証完了時にセッションへ固定します。設定変更で既存セッションの最長期限は延長しません。ポーリング・ヘルスチェック・更新確定は無操作期限を延長せず、期限切れ・失効済みのセッションも復活させません。

`/auth/connections`のstart/status/logout/logout-all/disconnectと、`/auth/connections/session/commit`は購読キーで保護します。認証取引の`transactionId`・`pollSecret`と、バックエンドに保存する本人接続セッションは独立しています。`/auth/connections/browser`のGET/POST launch・callbackは一時取引・起動秘密・Cookie・期限で照合します。SQLは本人・接続の世代と証明ハッシュを管理し、Microsoftのアクセストークン・更新トークンはCMが管理します。[呼出手順](credential-manager-login.md)を参照してください。

#### 操作制約（C）

| パラメタ名 | 公開サンプル値 | 意味 |
| --- | --- | --- |
| `C.defaultProfile` | `chat`または`response` | profile選択headerを省略した場合の入力profile。 |
| `C.profiles` | profile定義の配列 | 各entryにprofileId、modalitiesと既存のkind・許可モデル・本文/出力上限を保持。 |
| `C.kind` | 操作ごとの例 | registeredOperation/pathModel/bodyModel/textEmbeddings/rerank/statelessResponses/hostedAgentPost/a2aJsonRpc/realtimeCreate/realtimeHangup/liveCreate/pii/documentAnalysis/documentAnalysisStatus。 |
| `C.bodyFormat` | HTTP書込はjson、GET等はnone | 本文形式。native protocolはnone。 |
| `C.maxRequestBytes` | 通常AIは524288000 | APIM Classicの500 MiBバッファー枠。文書は500000000、PIIは1000000。提供元の制約が先に適用される場合がある。管理対象AI以外は既定WAFの容量とも整合させる。 |
| `C.contentTypes` | 必要な操作だけ設定 | 許可するmedia type。binary/multipart/textで必須。 |
| `C.allowedModels` | モデル例ごとに指定 | 許可モデル。基盤モデルは配備定義と照合。 |
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

`rerank`はFoundryのJSON `POST /rerank`用です。標準定義では独自の文書件数枠・`top_n`必須指定を設けません。query・各文書は非空白の文字列、文書は1件以上、`top_n`を指定した場合は文書数以下としてGatewayでも検査します。本文を切り詰めたり返却件数を補正したりしません。出力トークンの設定やprofilesは使用しません。

`openai-inference-v1`の入力profileは`X-Egakumi-Profile` headerで選びます。`chat-completions`は`chat`（default）・`vision`・`compatible`・`router`、`responses-create`は`response`（default）・`pdf`です。ヘッダーを省略した場合はdefaultを使い、選択したprofileのモデル・入力形式・本文サイズを照合します。出力トークン項目は省略でき、指定時だけモデル別上限を検査します。提供元の必須条件・利用クォータ・認証は維持します。

AI専用WAFは受付容量と検査枠を分離し、先頭2000 KiBだけ検査して超過部分も受け付けます。認証・管理系は既存のサイズ強制を維持します。文書は開始POSTの202受付後に所有者付きGETで結果を取得します。[WAFの範囲](../testing/waf-compatibility.md)・[文書解析](../reference/document-analysis.md)

接続先が異なる操作には`operations[].backendOverride`を指定できます。指定できるのは`backendService`・`backendUrl`・`backendAudience`・`backendAuthentication`・`backendHeaders`で、省略値はAPI既定を継承します。下流URLは固定の登録値だけを使います。secretを使う認証のoperation overrideは扱わず、API既定に設定します。公開prefixと下流のパス（例`/rest`）は別に管理します。

#### MCP・gRPC・WebSocket・本人接続

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
| `A.grpc.definition` | 自己完結したproto例 | service/rpc定義。外部proto import未対応。 |
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

`A.mcpProxy.endpoints`は公開設定では配列です。Azureへの送信時だけ、Streamable HTTPは`mcp`、旧SSEは`sse`（GET）と`messages`（POST）をキーにした辞書へ変換し、作成済みbackendを`backendId`で関連付けます。利用者が設定する接続先パスはこの変換で変更しません。配備の受理と、各transportの実接続・tool実行は別に確認します。

#### OAuth provider（credentialProviders[]）

[Entraアプリの準備](02-entra.md)で控えた値を、`.local/<environment>/params.local.json`へ記入します。公開例にはGraph本人接続用のproviderがあるため、その`clientId`のゼロGUIDを作成したアプリのIDへ置き換えます。

| Entraの準備で控えた値 | 構築設定の記入先 |
| --- | --- |
| アプリケーション（クライアント）ID | `credentialProviders[].clientId` |
| ディレクトリ（テナント）ID | トップレベルの`entraTenantId`。providerごとに認証先を変える場合は`credentialProviders[].oauthTenantId` |
| client secretを保存する保護ファイルのパス | `credentialProviders[].clientSecretFile`。入力例は`./secrets/entra-client-secret.txt`。秘密の値はファイル参照で渡す |

Graphの本人接続では、公開例の`identityEndpoint`は`https://graph.microsoft.com/v1.0/me`、`identityIdProperty`は`id`です。シークレットファイルがまだなければ、`azd up`開始時に値を非表示で入力し、`clientSecretFile`の保護ファイルへ保存します。Gateway配備時に、その値をCM providerへ設定します。本人接続の窓口を有効にする`gateway.authentication.credentialLogin`と、対象APIの`credentialConnection`は、[CM共通接続手順](credential-manager-login.md#entraと基盤の準備)を参照してください。

| パラメタ名 | 公開サンプル値 | 意味 |
| --- | --- | --- |
| `name` | Graph接続例 | provider識別名。 |
| `displayName` | Graph接続例 | provider表示名。 |
| `identityProvider` | Graph例はaad | aad/oauth2。 |
| `clientId` | 接続アプリIDの置換欄 | 下流認証用アプリ。 |
| `clientSecretFile` | 保護ファイル参照例 | Git管理外のsecretファイル。 |
| `oauthTenantId` | 下流OAuth認証先のTenant | 省略時は`entraTenantId`。明示したproviderのテナント設定は維持する。 |
| `resourceUri` | Graph例はhttps://graph.microsoft.com | aadの対象resource。 |
| `scopes` | `openid profile email offline_access https://graph.microsoft.com/User.Read` | 下流で要求するscope。Entraアプリで設定・同意した権限と照合する。 |
| `authorizationUrl` | 汎用OAuth例で設定 | OAuth2認可endpoint。 |
| `tokenUrl` | 汎用OAuth例で設定 | OAuth2 token endpoint。 |
| `refreshUrl` | 汎用OAuth例で設定 | OAuth2更新endpoint。 |
| `connectionNames` | 本人接続は`[]` | 構築時に作成する固定接続名配列。personal接続は共通窓口で動的作成する。 |
| `identityEndpoint` | `https://graph.microsoft.com/v1.0/me` | personal接続の本人確認先。resourceUriと同じホストの承認済みHTTPS URL。 |
| `identityIdProperty` | `id` | 本人確認応答から取得する不変IDのプロパティ名。 |

### PortalのAPIキー所有グループ（portalApiKeys）

test・productionの公開例には、初回の対話入力用に`groupNamePattern: ""`と`groups: []`を用意しています。空欄でも`azd up`の基盤・Gateway工程は進みますが、全体の完了には最後のPortal工程で入力が必要です。`postup`から呼ぶ`deploy_portal.py --execute`が、グループ名の正規表現・基盤管理グループのObject ID・許可する製品・製品ごとの購読数上限を同じparamsへ保存します。個別のPortal配備でも同じ入力を使います。

| パラメタ名 | 初期値・入力方法 | 意味 |
| --- | --- | --- |
| `portalApiKeys.groupNamePattern` | 初回は空欄から対話入力 | 所有候補にするセキュリティグループの表示名を選ぶRE2互換の正規表現。例：`^ORG_`、`^(ORG_|DEPT_)`。企業の規則を既定値にはしません。 |
| `portalApiKeys.platformGroupId` | 対話入力後に保存 | 検証用購読の所有グループのうち、管理ダッシュボードを利用できる基盤管理グループのObject ID。 |
| `portalApiKeys.groups[].groupId` | 対話入力 | 検証用購読の初期所有グループのObject ID。組織の全グループを列挙する欄ではありません。 |
| `portalApiKeys.groups[].products[].productId` | API定義から選択 | 検証用購読のAPIM製品。通常発行の製品選択には使いません。未選択のまま全許可しません。 |
| `portalApiKeys.groups[].products[].maxSubscriptions` | 省略時5 | グループが製品ごとに保持できる購読数。利用回数・トークン・金額の上限とは別です。 |
| `portalApiKeys.verificationSubscriptions` | 初回は`[]` | 基盤管理グループへ登録する検証用購読の宣言。空配列・省略時は、そのグループに許可した製品ごとに`platform-<productId>`という安定した購読IDを用います。無効化の指定ではありません。 |
| `portalApiKeys.verificationSubscriptions[].subscriptionId` | 既存を使う場合は実APIM購読ID | 既存購読を採用する場合は実際のIDを指定します。対象製品・状態を確認して台帳へ登録し、キー値は変更しません。`master`は指定できません。 |
| `portalApiKeys.verificationSubscriptions[].productId` | 基盤管理グループに許可した製品 | 購読が利用する製品。既存購読の製品と違う場合は停止します。 |
| `portalApiKeys.verificationSubscriptions[].displayName` | 1〜80文字 | Portalの台帳に表示する購読の用途名。グループの表示名とは別です。 |

表示名はEntraが正本のためparamsには保存せず、Graphから取得します。正規表現の空欄は未設定であり、全グループの許可にはなりません。非対話で初期構築する場合は、[Portal配備手順](portal.md)の記入例を使って`groups`・`platformGroupId`・`groupNamePattern`を事前設定します。入力が足りない場合はPortal工程が停止し、up全体も失敗で終了します。

通常の新規キーは固定製品`released-apis`に紐づき、利用者に製品を選ばせません。Gateway配備資材がこの製品と`released: true`のAPIの関連を管理し、Portal配備は通常発行カタログをこの製品1件に設定します。通常キーの購読数上限の初期値はグループごとに5件、利用回数・トークンの制限は無制限です。検証用の`groups[].products`や`verificationSubscriptions`とは分けて扱います。

会社名とクライアント種別等の利用情報はparamsに登録しません。会社は基盤管理者の会社管理ページから登録し、通常キー発行時に登録済み会社を選びます。

検証用購読はPortal配備のSQL初期化工程で準備します。宣言した購読がAPIMに存在すればそのまま採用し、存在しない場合だけ作成します。既に台帳に登録済みの購読は、所有者の移管・削除状態・利用制限を再配備で上書きしません。全API向けの組み込み購読や未宣言の既存購読を一括取り込みする処理はありません。

## 次へ進む前の確認

- 導入先の公開ID・名前・IP・公開名・連絡先を、公開例の見本から置き換えた。
- secretの値を書かず、必要な参照先だけを設定した。
- 初期の`azd up`に必要な`publishApis:true`、Product/購読範囲・下流URLと、採用するCMの接続条件を確認した。
- Portalの初回対話入力を行える。非対話なら`groups`・`platformGroupId`・`groupNamePattern`を事前設定した。
- `configuration.py validate`が成功し、利用するparamsのパスを控えた。

次：[5. configの調整](05-config.md)
