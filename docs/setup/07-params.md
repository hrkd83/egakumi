# 07. paramsを完成させる

Core初回構築では、`portal.publicHostName`を設定し、`portal.authentication`は省略します。公開サンプルもこの形です。`azd up`で専用ACA Jobが証明書を準備した後、Entra担当が専用アプリを作成して公開証明書を登録し、`portal.authentication.tenantId`・`clientId`を環境別の保護paramsへ追加します。鍵版とthumbprintは同じ環境の保護stateから読みます。 [08. Core構築](08-provision-core.md) · [09. Core構築後のEntra登録](09-egakumi-portal.md)

前：[06. DNS用マネージドIDの準備](06-dns-identity.md) · [README](../../README.md#新規クローンから構築する) · 次：[08. Coreを構築する](08-provision-core.md)

## このページのゴール

第01章のひな型へ、導入先のID・名前・IP計画・公開名・連絡先と、第02〜06章で準備したEntraとDNSの設定を記入します。このページではAzure資源を作りません。公開入力71主項目の配置判断と、新しいparams/configの使い分けも記録します。

## 7-1. paramsとconfigの役割

| 場所 | 管理するもの |
| --- | --- |
| `.local/<environment>/params.local.json` | 導入先ごとのID・公開名・IP計画・連絡先、アプリClient IDと秘密ファイル参照 |
| `config/test.json`／`config/production.json` | 共通のSKU・モデル・保護機能・利用制限、OAuth表示名／scope／poolとセッション設定 |
| `.local/<environment>/secrets/` | 対話入力や保護された鍵生成で保存する秘密ファイル |
| 資材のコード・API正本 | 固定の通信・認証契約、標準API、params/configから導出する内部設定 |
| `schemas/` | 公開params、共通config、解決済み内部設定それぞれの型・制約 |

ファイル参照は**そのJSONのあるフォルダーを基準に解決**します。準備済みparamsはconfigを`../../config/test.json`または`../../config/production.json`、秘密を`./secrets/...`で参照します。`.local/`と`.azure/`はチェックアウト直下だけに置き、リンクと所有者専用権限を確認します。実値を公開example・コード・作業記録へ書き込まず、秘密値をJSONへ直接記載しません。

公開入力は[params schema](../../schemas/params.schema.json)と[config schema](../../schemas/options.schema.json)で検証します。[configuration schema](../../schemas/configuration.schema.json)は、API配列・完全なprovider・導出値を含む内部合成形式の参照です。内部形式の項目を、そのまま公開params/configへ追加することはできません。

## 7-2. 環境別paramsと自動生成IDを確認する

第1章の[準備コマンド](01-prerequisites.md#1-5-環境別のローカル領域を準備する)で、選んだ公開exampleからparamsを作成します。本番向けの標準構成は`--profile production`を選びます。環境名は任意で、SKUの選択とは独立です。

```sh
export DEPLOY_LOCAL_DIRECTORY="$(pwd)/.local/${DEPLOY_ENVIRONMENT}"
export DEPLOY_PARAMS_FILE="$DEPLOY_LOCAL_DIRECTORY/params.local.json"
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
test -f "$DEPLOY_PARAMS_FILE"
```

公開例の`services.apimInstanceId`は`<AUTO_GENERATED_APIM_INSTANCE_ID>`です。これはテンプレートの初回自動生成マーカーであり、固定の配備IDではありません。`prepare_local_environment.py`が初回に英小文字・数字6文字のIDへ置換し、paramsへ保存します。通常の再実行は既存params全体と保存済みIDを保持します。`azd up`、登録、更新、撤去の読込時にIDを再生成しません。未解決マーカーや不正な既存IDは検証で停止し、暗黙に修復しません。

APIM名・用途別WAF名・EntraのCM戻り先・ライフサイクル所有確認は同じ保存済みIDを使います。撤去後の新規構築だけは[新しい識別子の準備](../operations.md#撤去後に新しく構築する)とCM戻り先更新を先に行います。

公開例のGUID・ホスト・CIDR・会社名・メールは実値から独立した架空例です。導入先の値は生成した保護paramsに記入します。既存paramsへ公開例を重ねてコピーせず、別環境の入力・state・秘密も自動流用しません。

旧サンプルから作成したparamsでPortalが未登録の場合は、初回Coreの実行前にプレースホルダーを含む`portal.authentication`ブロックを取り除きます。Portal登録が済んだ環境では実値を維持します。

## 7-3. 環境の値を記入する

1. `subscriptionId`・`azureTenantId`・`entraTenantId`・製品／環境名・3リージョンを確定する。
2. `network.addressSpaces`と6つの`network.subnetPrefixes`をIP計画へ合わせる。公開例のCIDRが未使用だと推測しない。
3. Gateway／Portalの公開FQDN、APIM発行者、監視通知先、ACME連絡先を記入する。証明書DNS名は公開FQDN2件から自動生成する。
4. 第05章と第06章で確認したDNS側Subscription IDとUAMIの完全なResource IDを設定する。DNS-01プラグインは資材でAzureへ固定する。
5. `management.adminUsername`・SSH公開鍵参照・VMログイン主体を設定する。
6. `credentialProviders.graphDelegated.clientId`にToolsアプリのClient IDを設定し、`clientSecretFile`の保護参照を確認する。
7. `gateway.authentication.entra.audience`にAPIアプリのClient IDを設定する。`gateway.authentication.credentialLogin.identity`にはCredentialアプリの`clientId`と、Tools用とは異なる`clientSecretFile`を設定する。
8. Portal初回の管理グループObject IDと表示名正規表現を用意する。対話なしで実行する場合は`portalApiKeys.platformGroupId`と`groupNamePattern`を事前に設定する。

### 基盤用Entraアプリの登録情報を記入する

| 役割 | paramsに置く値 |
| --- | --- |
| API | `gateway.authentication.entra.audience` |
| Tools | `credentialProviders.graphDelegated.clientId`と`clientSecretFile` |
| Credential | `gateway.authentication.credentialLogin.identity.clientId`と`clientSecretFile`。Tools用とは別のファイル |
| Portal | 公開証明書登録後に`portal.authentication.tenantId`と`clientId`を追加 |

すべてのアプリでApplication (client) IDを使い、認証用`entraTenantId`に登録します。API試用はこの基盤構成で既定有効です。既存環境の切替中に試用を停止する場合は`portal.apiAuthentication.enabled`を`false`にしてPortalだけ再配備します。通常のOBO初回は保護stateの証明書を固定します。管理者が別版を発行・Entra登録済みの場合だけ、`gateway.authentication.entra.obo.keyId`と公開証明書DERのSHA-256の`certificateThumbprint`を組で指定します。

### 同一テナントと別テナントの指定

`azureTenantId`はAzure構築・RBAC先、`entraTenantId`は社員API認証、標準Graph OAuth、任意の接続窓口アプリの認証先です。同一なら同じID、別ならそれぞれのIDを設定します。`entraTenantId`を省略した場合だけ`azureTenantId`を使います。標準Graphの`oauthTenantId`と接続窓口identityの`tenantId`は公開入力から導出し、独立した上書き欄を設けません。

追加providerが別の認証テナントを使う場合は、`params.credentialProviders.additional[].oauthTenantId`を設定できます。管理VMに接続する担当者へは、保守時に [本人のAzure RBAC](../operations/management-vm-access.md) を付与して回収します。認証先の変更と、Azure配置先の変更は別の操作です。

### Gateway全体の認証とAPIごとの認可

購読キーヘッダーは`api-key`、社員委任scopeは`Gateway.Invoke`で資材固定です。キーあり要求はAPIMの購読検証、キーなしBearer要求はEntra JWT検証へ進みます。対象はAPI正本から求め、認証方式の切替、許可Client ID一覧、対象API一覧をparamsへ入力しません。APIの`subscriptionRequired:true`と専用open Productによる社員経路の既存契約を維持します。

公開paramsに置くのはAPIアプリの`audience`です。`userLimits`はconfigに置き、初期値を移設前と同じ1000 requests／60秒、`tokensPerMinute:null`、`tokenQuota:null`に保ちます。1000／60秒は暫定防御値であり、推奨値の確定や通常利用の受入結果ではありません。

```json
{
  "gateway": {
    "authentication": {
      "entra": { "audience": "<API_APP_CLIENT_ID>" }
    }
  }
}
```

上は保護paramsの抜粋です。共通configの対応部分は次の形です。

```json
{
  "gateway": {
    "authentication": {
      "entra": {
        "userLimits": {
          "requestRate": { "calls": 1000, "renewalPeriodSeconds": 60 },
          "tokensPerMinute": null,
          "tokenQuota": null
        }
      }
    }
  }
}
```

`userLimits`の3項目はまとめて指定し、無効化には`null`を使います。空object・欠落・未知項目・不正値を無制限へ読み替えません。要求回数はプロンプトの入力回数ではなく、retry・並列・ツール継続等も含むHTTP要求数です。提供元の技術上限やWAFを解除する設定でもありません。

共通Named Valuesのtenant／audienceはparams、scopeは固定資材、userLimitsはconfigから内部生成します。手入力欄やNamed Valuesの手動変更を増やしません。API正本・policyの読戻し、revision、Product所属、キー範囲と復旧の確認は[Entra作業計画](../reference/entra-app-rollout-plan.md)と[追加受入項目](../testing/integration-tests.md#entra-gateway-oauthと専用open-productの追加受入)に従います。

### 基盤作成とAPI利用を分けて選ぶ

標準APIは[API正本](../../apps/api-gateway/apis/)から常用で解決します。公開`apiDefinitions`と`publishApis`による置換・切替は廃止しました。解決したAPI配列や環境固有URLをparamsへ書き戻しません。

| 入口 | 実行範囲 |
| --- | --- |
| `azd up`（`azd provision`と同じ） | CoreとPortal初期サンプル、認証用公開証明書2枚を用意する |
| 個別配備コマンド、`--execute`なし | 計画を確認する |
| 個別配備コマンド、`--execute`あり | 指定したアプリの反映を実行する |

初期構築と構築済み環境の運用更新は[運用手順](../operations.md)で区別します。公開入力の整理だけで認証方式・アプリClient ID・secret・既存APIを切り替えません。

## 7-4. ローカルで設定を検査する

```sh
python3 -B scripts/deployment/helpers/configuration.py validate --config "$DEPLOY_PARAMS_FILE"
```

未知項目・旧入力・ID形式・IP重複・認証の組合せ等を検査します。secret値、Azure権限、実モデルの配備可否、通信の受入はこの検査で確認しません。秘密ファイルがない場合は、正式手順の保護された対話入力を使います。

`configFile`のない生成済み内部JSONは、API・provider配列や検証用の公開フラグを引き続き読み込めます。ただし、廃止した外部WAF選択・監査IP許可・追加閲覧者／管理者・IDPS検査除外・独自SNAT／IDPS範囲を指定した旧JSONは、Bicep向けの資材出力時に停止します。入力の読込成功だけで移行済みとは判断せず、既存の意図を確認してから固定構成へ移行します。

| 確認段階 | 今回の扱い |
| --- | --- |
| 配置判断 | 71主項目と分割項目の判断を確定し、下表へ記録 |
| 実装 | 公開入力境界、固定値／導出値の合成、config初期値の移設、例・説明を実装 |
| ローカル検証 | 合成データで設定読込・内部生成・新規cloneのCLI・Bicepコンパイル・公開情報ガードを確認 |
| Azure反映 | 今回の一括改修は未実施 |
| 全環境再構築E2E・実受入 | 未実施。ローカル確認と区別する |

確認した試験群は、設定読込91件、固定入力・内部互換31件、Portal初期設定24件、新規clone／分割CLI9件、Bicep関連38件、公開情報ガード65件です。群ごとの件数であり、重複を除いた総件数ではありません。追加のAPI・認証・配備回帰も確認し、共有HTTP/2クライアントの実行環境が必要な6件は未実行として区別します。

## paramsリファレンス

以下は保護paramsに残す35主項目と、分割項目から残る登録情報の参照です。`<...>`は用途付きプレースホルダーで、そのまま実入力へ使いません。ファイル参照は準備済みparams基準です。

### 企業・環境ごとに確認する値（params）

| 項目 | 公開例・制約 | 用途 |
| --- | --- | --- |
| `schemaVersion` | `1` | 入力形式の版 |
| `configFile` | `../../config/test.json`または`../../config/production.json` | 共通config選択 |
| `productName` | 公開例`egakumi` | 資源名の製品接頭辞 |
| `environmentName` | 公開例`test`／`production` | 環境名。SKU選択と独立 |
| `subscriptionId` | 非ゼロGUID | Azure構築先 |
| `azureTenantId` | 非ゼロGUID | Azure構築・RBAC先 |
| `entraTenantId` | 非ゼロGUID、省略可 | OAuth・社員認証先。省略時Azure側tenant |
| `location` | 公開例のAzureリージョン | ネットワーク・APIM・管理VM等 |
| `foundryLocation` | 公開例のAzureリージョン | Foundryモデル提供・利用枠を別途確認 |
| `apiCenterLocation` | 公開例のAzureリージョン | API Center |
| `network.addressSpaces` | 公開例10.40系の独立した合成CIDR | VNet。利用者のIP計画を確認 |
| `network.subnetPrefixes.agw` | VNet内IPv4 CIDR | AppGW |
| `network.subnetPrefixes.pep` | VNet内IPv4 CIDR | Private Endpoint |
| `network.subnetPrefixes.firewall` | VNet内IPv4 CIDR | AzureFirewallSubnet |
| `network.subnetPrefixes.apim` | VNet内IPv4 CIDR | Internal APIM |
| `network.subnetPrefixes.management` | VNet内IPv4 CIDR | 管理VM |
| `network.subnetPrefixes.aca` | VNet内IPv4 CIDR | ACA |
| `services.publisherName` | 独立した架空例`Example Corporation` | API提供組織・部門・運用チームの表示名 |
| `services.publisherEmail` | `example.com`の架空メール | APIM発行者連絡先 |
| `services.apimInstanceId` | 初回prepare生成、6文字 | APIM・用途別WAF・戻り先・所有確認で保持 |
| `gateway.authentication.entra.audience` | egakumi-apiの非ゼロClient ID | v2アクセストークンの宛先。Object IDやscope URIではない |
| `gateway.authentication.credentialLogin.identity.clientId` | 任意。指定時非ゼロGUID | 独立した接続窓口アプリ |
| `gateway.authentication.credentialLogin.identity.clientSecretFile` | identity指定時必須 | 上記アプリ専用の保護秘密参照 |
| `gateway.publicHostName` | `gateway.example.com`の架空例 | Gateway公開DNS名 |
| `management.adminUsername` | 独立したLinuxアカウント名例 | 初期管理者 |
| `management.sshPublicKeyFile` | `./secrets/management-ssh.pub` | 既存保護公開鍵参照または同環境内の鍵対生成先 |
| `monitoring.operatorEmailAddresses` | 架空メール配列、1件以上 | Azure Monitor通知先 |
| `certificate.contactEmail` | 架空メール | ACME連絡先 |
| `certificate.azureDnsSubscriptionId` | 非ゼロGUID | 事前作成した公開DNSゾーンのSubscription |
| `certificate.azureDnsManagedIdentityResourceId` | 完全なResource ID | 事前付与済みDNS更新UAMI |
| `tags` | 文字列map | 利用者タグ。システムタグは別途生成 |
| `portal.publicHostName` | `portal.example.com`の架空例 | Portal公開DNS名 |
| `portalApiKeys.platformGroupId` | 初回対話のため省略可 | 管理画面と検証用購読の管理グループObject ID |
| `portalApiKeys.groupNamePattern` | 初回未設定は`""` | 所有候補の表示名RE2正規表現。空欄は全許可ではない |

#### ネットワーク・監査の接続設定

全サブネットをVNet範囲内へ重複なく割り当てます。SNAT・IDPS private範囲・bypass、監査公開IP、証明書DNS名を独立したparamsに追加しません。通信の詳細・既存環境の適用は[通信要件](../reference/network-communication-requirements.md)、[NSG判断記録](../reference/network-security-design.md)、[Firewall判断記録](../reference/firewall-policy-design.md)、[検証記録](../reference/network-security-validation.md)を参照してください。

SSH公開鍵は、利用者が明示した保護入力にある有効なRSA 2048bit以上／Ed25519の1件を再利用できます。公開鍵と秘密鍵がともにない場合だけ、同環境の`.local/<environment>/secrets/`へ鍵対を生成します。リンク・権限・既存秘密鍵・無効な公開鍵・外部の未作成先を確認し、上書きや別環境からのコピーはしません。失敗やdownでも鍵対を保持します。

### configへ移した共通値

移設で初期値を変更していません。セッション／利用制限の変更は、既存接続・利用中セッションへの影響を別途確認します。

| 共通設定 | test／production初期値 |
| --- | --- |
| `gateway.authentication.entra.userLimits.requestRate.calls` | 1000 |
| `gateway.authentication.entra.userLimits.requestRate.renewalPeriodSeconds` | 60秒 |
| `gateway.authentication.entra.userLimits.tokensPerMinute` | `null` |
| `gateway.authentication.entra.userLimits.tokenQuota.tokens` | 親`tokenQuota:null`で無効 |
| `gateway.authentication.entra.userLimits.tokenQuota.period` | 親`tokenQuota:null`で無効 |
| `gateway.authentication.credentialLogin.providerNames` | `["graph-delegated"]` |
| `gateway.authentication.credentialLogin.idleTimeoutSeconds` | 604800秒 |
| `gateway.authentication.credentialLogin.absoluteLifetimeSeconds` | 2592000秒 |
| `gateway.authentication.credentialLogin.rotationIntervalSeconds` | 86400秒 |
| `gateway.authentication.credentialLogin.rotationGraceSeconds` | 300秒 |
| `gateway.authentication.credentialLogin.rotationHandoffSeconds` | 600秒 |
| `gateway.authentication.credentialLogin.rotationCandidateTtlSeconds` | 600秒 |
| `gateway.authentication.credentialLogin.transactionLifetimeSeconds` | 600秒。省略時の内部既定値300秒と区別 |
| `gateway.authentication.credentialLogin.loginCallsPerMinute` | 10 |

Graph表示名・scope・poolの4末端値とPortal共通購読数上限は、下の分割項目で記録します。これらを主項目14件へ加算しません。

## 公開入力71主項目の判断記録

主項目は、固定objectのleaf、配列全体、任意keyのmapを数えます。test／productionの重複は数えず、`$schema`・`$comment`は補足metadataとして除外しました。任意入力も対象に含みます。APIや運用payloadの内部契約全体を71件で網羅したという意味ではありません。

| 分類 | 主項目数 |
| --- | ---: |
| params保持 | 35 |
| 資材固定・内部導出 | 20 |
| config移設 | 14 |
| 分割 | 2 |
| 合計 | 71 |

63の内部16項目と71の内部3項目は、分割判断の内訳です。主項目総数へ二重加算せず、末端の配置数は別に数えます。下表の項目名は棚卸時の入力名であり、資材固定とした旧入力を現行params/configへ再追加する一覧ではありません。

| 番号 | 棚卸対象 | 配置 | 判断・初期動作 |
| ---: | --- | --- | --- |
| 1 | `schemaVersion` | params | 形式の版1を保持 |
| 2 | `configFile` | params | 参照元paramsから共通configを選択 |
| 3 | `productName` | params | 環境所有者が資源名接頭辞を指定 |
| 4 | `environmentName` | params | 命名・state所有の環境識別 |
| 5 | `subscriptionId` | params | 構築先Subscriptionを明示 |
| 6 | `azureTenantId` | params | 構築先Azure tenantを明示 |
| 7 | `entraTenantId` | params | OAuth・社員認証tenantを保持 |
| 8 | `location` | params | 共通資源のリージョン |
| 9 | `foundryLocation` | params | Foundry提供リージョン |
| 10 | `apiCenterLocation` | params | API Centerリージョン |
| 11 | `network.addressSpaces` | params | VNetのIP計画 |
| 12 | `network.subnetPrefixes.agw` | params | AppGWサブネット |
| 13 | `network.subnetPrefixes.pep` | params | PEサブネット |
| 14 | `network.subnetPrefixes.firewall` | params | Firewallサブネット |
| 15 | `network.subnetPrefixes.apim` | params | APIMサブネット |
| 16 | `network.subnetPrefixes.management` | params | 管理VMサブネット |
| 17 | `network.subnetPrefixes.aca` | params | ACAサブネット |
| 18 | `network.firewall.snatPrivateRanges` | 資材固定 | IANAPrivateRanges＋構築VNet範囲からSNAT除外を生成 |
| 19 | `network.firewall.idpsPrivateRanges` | 資材固定 | PremiumではRFC1918と構築VNetの範囲からAppGWサブネットを除いて生成 |
| 20 | `network.firewall.bypassTrafficSettings` | 資材固定 | 初期IDPS bypassを空に固定 |
| 21 | `services.publisherName` | params | API提供組織の表示名 |
| 22 | `services.publisherEmail` | params | API運用の連絡先 |
| 23 | `services.apimInstanceId` | params | 初回生成・params保存・通常更新保持 |
| 24 | `gateway.authentication.subscriptionKeyHeader` | 資材固定 | api-keyへ固定 |
| 25 | `gateway.authentication.entra.audience` | params | egakumi-apiのClient ID |
| 26 | `gateway.authentication.entra.requiredScope` | 資材固定 | Gateway.Invokeへ固定 |
| 27 | `gateway.authentication.entra.userLimits.requestRate.calls` | config | 共通要求回数1000を保持 |
| 28 | `gateway.authentication.entra.userLimits.requestRate.renewalPeriodSeconds` | config | 共通期間60秒を保持 |
| 29 | `gateway.authentication.entra.userLimits.tokensPerMinute` | config | 初期nullを保持 |
| 30 | `gateway.authentication.entra.userLimits.tokenQuota.tokens` | config | 親tokenQuotaの初期nullを保持 |
| 31 | `gateway.authentication.entra.userLimits.tokenQuota.period` | config | 親tokenQuotaの初期nullを保持 |
| 32 | `gateway.authentication.credentialLogin.identity.tenantId` | 資材固定 | entraTenantIdから内部導出 |
| 33 | `gateway.authentication.credentialLogin.identity.clientId` | params | 任意の独立接続窓口アプリClient ID |
| 34 | `gateway.authentication.credentialLogin.identity.clientSecretFile` | params | 上記アプリの保護秘密ファイル参照 |
| 35 | `gateway.authentication.credentialLogin.providerNames` | config | graph-delegatedの明示選択を保持 |
| 36 | `gateway.authentication.credentialLogin.idleTimeoutSeconds` | config | 604800秒を保持 |
| 37 | `gateway.authentication.credentialLogin.absoluteLifetimeSeconds` | config | 2592000秒を保持 |
| 38 | `gateway.authentication.credentialLogin.rotationIntervalSeconds` | config | 86400秒を保持 |
| 39 | `gateway.authentication.credentialLogin.rotationGraceSeconds` | config | 300秒を保持 |
| 40 | `gateway.authentication.credentialLogin.rotationHandoffSeconds` | config | 600秒を保持 |
| 41 | `gateway.authentication.credentialLogin.rotationCandidateTtlSeconds` | config | 600秒を保持 |
| 42 | `gateway.authentication.credentialLogin.transactionLifetimeSeconds` | config | 明示値600秒を保持 |
| 43 | `gateway.authentication.credentialLogin.loginCallsPerMinute` | config | 毎分10回を保持 |
| 44 | `gateway.publicHostName` | params | Gateway公開DNS名 |
| 45 | `gateway.existingWafPolicyId` | 資材固定 | 資材所有の用途別WAFへ固定 |
| 46 | `gateway.rateLimits.subscriptionKey.overrides` | 資材固定 | 静的caller別上書きを廃止し購読SQL profileを使用 |
| 47 | `gateway.rateLimits.application.overrides` | 資材固定 | 追加アプリ区分の静的上書きを廃止 |
| 48 | `management.adminUsername` | params | 初期Linux管理者名 |
| 49 | `management.sshPublicKeyFile` | params | 既存保護鍵参照と同環境内生成 |
| 50 | 管理VMの本人ログイン | 保守時に手動 | 対象VMだけに本人のログインロールを付与・回収 |
| 51 | `monitoring.operatorEmailAddresses` | params | 監視通知先メール |
| 52 | `monitoring.auditAllowedIpAddresses` | 資材固定 | 初期Storage公開ipRulesを空に固定 |
| 53 | `monitoring.auditReaders` | 資材固定 | 構築時に監査readerを追加しない。担当者の読取権限は保守時に付与 |
| 54 | `certificate.contactEmail` | params | ACME連絡先 |
| 55 | `certificate.domainNames` | 資材固定 | Gateway／Portal公開名2件から正確なDNS名を生成 |
| 56 | `certificate.plugin` | 資材固定 | Azure Public DNS DNS-01へ固定 |
| 57 | `certificate.azureDnsSubscriptionId` | params | 事前作成したDNS zoneのSubscription |
| 58 | `certificate.azureDnsManagedIdentityResourceId` | params | 事前作成したDNS更新UAMI Resource ID |
| 59 | `authorization.projectAdministrators` | 資材固定 | 追加Project管理者を初期空に固定 |
| 60 | `authorization.projectDevelopers` | 資材固定 | 追加Project開発者を初期空に固定 |
| 61 | `authorization.foundryReaders` | 資材固定 | 追加Foundry readerを初期空に固定 |
| 62 | `authorization.catalogReaders` | 資材固定 | 追加API Center readerを初期空に固定 |
| 63 | `credentialProviders` | 分割 | 標準Graph固定契約／共通定義／環境登録へ分離 |
| 64 | `apiDefinitions` | 資材固定 | API正本を常用し公開置換入力を廃止 |
| 65 | `tags` | params | 利用者タグmapを保持 |
| 66 | `publishApis` | 資材固定 | 内部trueとし実行入口の計画／反映境界を保持 |
| 67 | `portal.publicHostName` | params | Portal公開DNS名 |
| 68 | `portalApiKeys.platformGroupId` | params | 初回の管理グループObject ID |
| 69 | `portalApiKeys.groupNamePattern` | params | 環境別の所有候補表示名正規表現 |
| 70 | `portalApiKeys.verificationSubscriptions` | 資材固定 | 安定した検証購読を内部生成、同一IDの既存キー保持 |
| 71 | `portalApiKeys.groups` | 分割 | 管理グループと全標準製品を導出し共通上限をconfig化 |


### 63. 標準Graph providerの内部16項目

標準`graphDelegated`スロットに対してparams 2末端、config 4末端、資材固定10末端です。追加providerの汎用契約には別の定義／登録境界を適用し、標準Graphの固定化を理由に汎用OAuth2や別tenantのaad接続を廃止しません。

| 番号 | 旧標準providerの末端 | 配置・現行形 |
| --- | --- | --- |
| 63.1 | `name` | 資材固定：論理名`graph-delegated` |
| 63.2 | `displayName` | config：`graphDelegated.displayName`、既存表示名保持 |
| 63.3 | `identityProvider` | 資材固定：`aad` |
| 63.4 | `clientId` | params：`graphDelegated.clientId` |
| 63.5 | `clientSecretFile` | params：`graphDelegated.clientSecretFile` |
| 63.6 | `oauthTenantId` | 資材固定：トップレベル`entraTenantId`から導出 |
| 63.7 | `scopes` | config：`graphDelegated.scopes`、権限文字列保持 |
| 63.8 | `resourceUri` | 資材固定：`https://graph.microsoft.com` |
| 63.9 | `authorizationUrl` | 標準aadに不要な自由入力を削除 |
| 63.10 | `tokenUrl` | 標準aadに不要な自由入力を削除 |
| 63.11 | `refreshUrl` | 標準aadに不要な自由入力を削除 |
| 63.12 | `connectionNames` | 資材固定：初期`[]`。本人接続は動的作成 |
| 63.13 | `identityEndpoint` | 資材固定：`https://graph.microsoft.com/v1.0/me` |
| 63.14 | `identityIdProperty` | 資材固定：`id` |
| 63.15 | `pool.count` | config：12を保持 |
| 63.16 | `pool.capacity` | config：8000を保持 |

12×8000は設定上の割当枠であり、保証される利用者数・スループットやAzure公式quotaではありません。pool数・容量の変更は、既存接続・suffix・割当済み数を別途確認し、値の移設と同時に変更しません。

### 71. 検証用所有グループの内部3項目

| 番号 | 旧末端 | 配置・現行形 |
| --- | --- | --- |
| 71.1 | `groups[].groupId` | 資材固定：paramsの`platformGroupId`から管理グループ1件を生成 |
| 71.2 | `groups[].products[].productId` | 資材固定：標準API正本にある全承認製品を管理グループへ適用 |
| 71.3 | `groups[].products[].maxSubscriptions` | config：`portalApiKeys.verificationMaxSubscriptions`、初期5 |

管理グループへの全標準製品承認は初期構成の判断です。すべての利用者へ検証用製品を許可する意味ではありません。既存の追加所有グループ・製品subset・異なる件数上限は、意図した移行を確認するまで自動で置換しません。

## OAuth providerの登録と定義

保護paramsの標準登録は次の形です。APIリソースのegakumi-api Client IDは`entra.audience`へ入れ、下流OAuth Client IDと混同しません。

```json
{
  "credentialProviders": {
    "graphDelegated": {
      "clientId": "<GRAPH_OAUTH_CLIENT_ID>",
      "clientSecretFile": "./secrets/entra-client-secret.txt"
    }
  }
}
```

共通configの標準定義は次の形です。表示名・scope・poolは今回変更していません。

```json
{
  "credentialProviders": {
    "graphDelegated": {
      "displayName": "Delegated Graph connection",
      "scopes": "openid profile email offline_access https://graph.microsoft.com/User.Read",
      "pool": { "count": 12, "capacity": 8000 }
    }
  }
}
```

追加接続はconfigの`credentialProviders.additional[]`に`name`・`displayName`・`identityProvider`・`scopes`とプロトコル別定義を置き、paramsの同じ`additional[]`へ`name`・`clientId`・`clientSecretFile`・任意の`oauthTenantId`を置きます。aadは`resourceUri`、oauth2は`authorizationUrl`・`tokenUrl`・`refreshUrl`を定義し、必要に応じて`connectionNames`・`identityEndpoint`・`identityIdProperty`・`pool`を指定します。

追加定義と登録は論理名で1対1に照合します。未知名・重複・欠落・標準slotとの衝突は拒否し、大小文字を暗黙に変更しません。一般的な配列の全置換で部分providerを合成せず、専用処理で完全な内部provider配列へ戻して検証します。秘密参照の宣言元基準、現在のアプリID／secret、物理provider名・pool所属、本人の`tid+oid`検証を保持します。

`config.gateway.authentication.credentialLogin`は`providerNames`と共通セッション設定です。任意の独立接続窓口を使う場合の保護paramsは次の抜粋です。

```json
{
  "gateway": {
    "authentication": {
      "credentialLogin": {
        "identity": {
          "clientId": "<IDENTITY_APP_CLIENT_ID>",
          "clientSecretFile": "./secrets/entra-credential-secret.txt"
        }
      }
    }
  }
}
```

identityは任意で、指定時はClient IDと保護秘密参照を両方設定します。tenantは`entraTenantId`から導出します。省略時の選択CM provider由来の本人確認と、Portalが`portalAuthentication.credentialProviderName`で選ぶ既存アプリ／secretは維持します。今回の配置整理だけでTools用アプリや新しい認証方式へ切り替えません。アプリ移行・権限・同意・旧取引排出・既存refresh tokenの扱いは[別のEntra作業計画](../reference/entra-app-rollout-plan.md)で確認します。

## PortalのAPIキー所有グループ（portalApiKeys）

公開paramsは`platformGroupId`と`groupNamePattern`だけです。公開例は`groupNamePattern:""`で、`platformGroupId`を省略しています。初回Portal配備の対話でこの2項目を入力し、同じ保護paramsへ保存します。表示名・製品・購読数を対話入力しません。

```json
{
  "portalApiKeys": {
    "platformGroupId": "<PLATFORM_GROUP_OBJECT_ID>",
    "groupNamePattern": "^ORG_"
  }
}
```

`^ORG_`は独立した架空の命名規則例で、既定値ではありません。Graphから取得した表示名・現在の所属・`securityEnabled=true`を確認します。空欄は初回入力待ちであり全許可ではありません。無人実行では2項目を事前に設定します。

configは`portalApiKeys.verificationMaxSubscriptions:5`を共通初期値にします。管理グループ1件・標準API正本の全製品・この上限から内部`groups`を生成し、製品ごとに`platform-<productId>`の安定した内部検証購読を生成します。`groups`と`verificationSubscriptions`を公開paramsへ保存しません。同一ID・同一製品スコープの既存APIM購読はキーを保持して採用し、明示的404だけを新規作成の根拠にします。

通常の新規キーは固定の`released-apis`製品に紐づき、利用者に製品を選ばせません。通常キーの所有候補の発見と購読数上限は、管理グループ向け検証購読の設定とは別です。会社情報はPortalの会社管理から登録し、paramsに会社台帳を作りません。[Portal配備・受入手順](12-deploy-portal.md)を参照してください。

本文監査が有効な場合は中継の認証に同じAzure・Entraテナントが必要です。異なる認証テナントを使う環境での本文監査は残作業です。 [本文監査の構築条件](../reference/request-body-audit.md#初回構築)

## 既存入力の移行を確認する

資材固定とした旧入力は、空配列・空object・空文字・`false`・`null`でも公開params/configで拒否します。配置を移した共通値も旧paramsの位置では受け付けません。検査を通すために未知項目の除外やschemaの緩和を行いません。

既存paramsとstateを保護保存してから、旧値と新しい内部生成値を比較します。標準と一致する空／固定値の削除、共通値のconfig移設、標準provider slotへの登録分離を人が確認し、現在のID・secret参照・認証方式・pool値を保持します。既存の非標準／非空権限、WAF参照、IP例外、caller上書き、API置換、別tenant、Portal所有グループ／製品subset／購読IDを自動変換・削除しません。標準との差があれば、影響・所有者・復旧と対象限定の移行方法を確定してから進みます。

新しい公開例のコピーで稼働中設定を置換したり、公開入力整理の一括反映だけを理由に認証方式やClient IDを切り替えたりしません。Azure反映と全環境再構築E2Eは未実施であり、既存環境の受入完了を意味しません。

## API・権限の記述形式

標準APIの内容は[API正本](../../apps/api-gateway/apis/)で管理します。以下の詳細契約を公開params入力の一覧と混同しません。独自APIの追加・変更は対象限定の運用計画で扱い、接続先・認証・WAF・監査と既存APIへの影響を確認します。

#### API正本の定義

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
| `A.released` | 省略時false、標準19 APIはtrue | 通常キー用`released-apis`製品への所属。生成MCPも同じ状態です。falseへ変更してGateway配備すると通常製品との関連だけを解除し、API本体・検証用所属・購読を保持します。 |
| `A.subscriptionKeyHeader` | 固定の`api-key` | 内部共通認証と一致させる。公開params/configにヘッダー選択入力は設けない。 |
| `A.backendHeaders` | 通常{} | 非秘密の固定ヘッダー。鍵やtokenは格納不可。 |
| `A.keySubscriptions` | 省略する場合は`[]` | このAPIのProductに作成するAPIM購読の配列。 |
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

各検査・転送・認証ポリシーの役割は[APIMポリシー一覧](../reference/apim-policy-catalog.md)を参照してください。

`queryObservation`はAPI正本で指定する観測ラベルです。paramsに観測／拒否modeや専用Named Valueを設けません。旧`gateway.queryGuards`・`queryGuard`参照は現行入力に使いません。観測設定と、下流URI変換・WAF・通信の受入は別に確認します。[対象設定と移行手順](../operations/apim.md#操作mcp受付のurlクエリ観測)を参照してください。

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

CMアプリ登録と共通セッション設定の公開形は、本ページの[OAuth providerの登録と定義](#oauth-providerの登録と定義)を参照してください。API側の`credentialConnection`は入口認証とは別の下流接続契約です。

#### 操作制約（C）

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
| `C.allowedModels` | モデル例ごとに指定 | 設定対象のモデル一覧。配備時は基盤モデルを配備定義と照合する。モデル選択部品ではこの一覧による先行403を撤去し、後段の照合は続く棚卸の対象とする。 |
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

標準ChatはGPT群の`settingId`が`chat`、互換モデル群が`compatible`、model-routerが`router`です。互換モデル群だけ出力項目を`max_tokens`へ置き換えます。Responsesの`settingId`は`response`です。同じモデルの画像・PDF用候補を重複登録する構造を廃止し、本文のモデル名から設定を一意に選びます。FLUXは`flex`／`pro`それぞれのモデル別差分に固定上流URIを保持します。モデル別の小さい本文上限がある場合は、モデル選択前の操作上限を最大値にまとめても、そのモデルの上限を差分に残します。

画像・PDFにもprofileヘッダーは不要です。この構造整理では既存の受付条件を維持し、登録外モデルや本文・ツールに残る制御は続く棚卸で個別に判断します。新しい画像・PDF入力の実機受入結果を示すものではありません。`settingId`は購読利用枠やCMの接続profileとは別の内部設定です。

AI専用WAFは受付容量と検査枠を分離し、先頭2000 KiBだけ検査して超過部分も受け付けます。認証・管理系は既存のサイズ強制を維持します。文書は開始POSTの202受付後に所有者付きGETで結果を取得します。[WAFの範囲](../testing/waf-compatibility.md)・[文書解析](../reference/document-analysis.md)

接続先が異なる操作には`operations[].backendOverride`を指定できます。指定できるのは`backendService`・`backendUrl`・`backendAudience`・`backendAuthentication`・`backendHeaders`で、省略値はAPI既定を継承します。下流URLは固定の登録値だけを使います。secretを使う認証のoperation overrideは扱わず、API既定に設定します。公開prefixと下流のパス（例`/rest`）は別に管理します。

#### MCP・WebSocket・本人接続

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


## 次へ進む前の確認

- 導入先ID・公開名・IP計画・連絡先とアプリ登録を保護paramsに設定した。
- 初回生成済みAPIM IDが6文字で、未解決マーカーが残っていない。
- secret値をJSONへ記載せず、明示した保護参照を確認した。
- 共通値はconfig、固定契約は資材に置き、旧入力の移行を確認した。
- Portal初回対話の2項目を用意した。非対話なら事前に設定した。
- ローカル入力検証と、Azure反映・実接続受入を区別した。

次：[08. Coreを構築する](08-provision-core.md) · [標準configのリファレンス](../reference/config-options.md)
