# 環境別paramsの入力項目

[第07章](../../setup/07-params.md)で準備する環境別の設定を扱います。型・必須条件は[params schema](../../../params/params.schema.json)で確認します。標準のSKUと機能設定は[標準config](config-options.md)を参照してください。

## 企業・環境ごとに確認する値（params）

| 項目 | 公開例・制約 | 用途 |
| --- | --- | --- |
| `schemaVersion` | `1` | 入力形式の版 |
| `configFile` | `../../infra/config/<profile>.json`（`prod`・`stg`・`dev`） | 準備コマンドがprofileに合わせて設定する共通config参照 |
| `productName` | 最大12文字。公開例 `egakumi` | 資源名に使う製品名 |
| `environmentName` | 最大4文字。公開例 `prod`／`stg`／`dev` | 資源名に使う環境名。SKU選択と独立 |
| `subscriptionId` | 非ゼロGUID | Azure構築先 |
| `azureTenantId` | 非ゼロGUID | Azure構築・RBAC先 |
| `entraTenantId` | 非ゼロGUID、省略可 | OAuth・社員認証先。省略時Azure側tenant |
| `location` | 公開例のAzureリージョン | ネットワーク・APIM・管理VM等 |
| `foundryLocation` | 公開例のAzureリージョン | Foundryモデル提供・利用枠を別途確認 |
| `network.addressSpaces` | 公開例10.40系の独立した合成CIDR | VNet。利用者のIP計画を確認 |
| `network.stagingClientAddressPrefixes` | `stg` 必須。管理者用IPv4 CIDRを1件以上 | App GatewayのHTTP/HTTPS入口の許可元。稼働確認には管理者用の接続元を使う |
| `network.subnetPrefixes.agw` | VNet内IPv4 CIDR | AppGW |
| `network.subnetPrefixes.pep` | VNet内IPv4 CIDR | Private Endpoint |
| `network.subnetPrefixes.firewall` | VNet内IPv4 CIDR | AzureFirewallSubnet |
| `network.subnetPrefixes.apim` | VNet内IPv4 CIDR | Internal APIM |
| `network.subnetPrefixes.management` | VNet内IPv4 CIDR | 管理VM |
| `network.subnetPrefixes.portal` | VNet内IPv4 CIDR | Portal用ACA |
| `network.subnetPrefixes.broker` | VNet内IPv4 CIDR | Credential Broker専用ACA |
| `network.subnetPrefixes.certificate` | VNet内IPv4 CIDR | 証明書Job専用ACA |
| `services.publisherName` | 独立した架空例`Example Corporation` | API提供組織・部門・運用チームの表示名 |
| `services.publisherEmail` | `example.com`の架空メール | APIM発行者連絡先 |
| `services.apimInstanceId` | 初回prepare生成、6文字 | APIM・用途別WAF・戻り先・所有確認で保持 |
| `gateway.authentication.entra.audience` | egakumi-apiの非ゼロClient ID | v2アクセストークンの宛先。Object IDやscope URIではない |
| `gateway.authentication.credentialLogin.identity.clientId` | 任意。指定時非ゼロGUID | 独立した接続窓口アプリ |
| `gateway.publicHostName` | `gateway.example.com`の架空例 | Gateway公開DNS名 |
| `management.adminUsername` | 独立したLinuxアカウント名例 | 初期管理者 |
| `monitoring.operatorEmailAddresses` | 架空メール配列、1件以上 | Azure Monitor通知先 |
| `certificate.contactEmail` | 架空メール | ACME連絡先 |
| `certificate.azureDnsSubscriptionId` | 非ゼロGUID | 事前作成した公開DNSゾーンのSubscription |
| `certificate.azureDnsManagedIdentityResourceId` | 完全なResource ID | 事前付与済みDNS更新UAMI |
| `tags` | 文字列map。公開例の `purpose` は `egakumi` | Azureリソースに付ける任意の利用者タグ。システムタグは別途生成 |
| `portal.publicHostName` | `portal.example.com`の架空例 | Portal公開DNS名 |

## 環境ごとに確定する容量

[prod](../../../params/prod.example.json)・[stg](../../../params/stg.example.json)・[dev](../../../params/dev.example.json)の公開例は、`capacity` の全項目に標準値を記入しています。準備コマンドから作成した `params.local.json` を導入先の処理量に合わせて編集します。構築前の設定検証で、`capacity` の必須項目が欠落していればエラーになります。 [3環境の容量設計](environment-capacity.md)

| 必須項目 | `prod` | `stg` | `dev` |
| --- | --- | --- | --- |
| `capacity.applicationGateway.minInstances`・`maxInstances` | 20・30 | 1・2 | 1・2 |
| `capacity.apim.units` | 12（最低2） | 1 | 1固定 |
| `capacity.credentialBroker.minReplicas`・`maxReplicas` | 5・10 | 1・2 | 1・2 |
| `capacity.brokerSql.vCores`・`maxDataGiB` | 4・32 | 2・32 | 2・32 |

Broker SQLは固定のGeneral Purpose standard-series（Gen5）と `vCores` から配備SKU名を決めます。Foundryのモデル・版・配備SKUはサービス別configの `modelDeployments` で確認できます。初期配備は3環境共通で最小容量を使い、運用上の容量は構築後にMicrosoft Foundryポータルで調整します。 [SQLのvCore上限](https://learn.microsoft.com/en-us/azure/azure-sql/database/resource-limits-vcore-single-databases#general-purpose-standard-series-gen5-part-1-of-3) · [Foundryの容量とクォータ](capacity/foundry.md)

## ネットワーク・監査の接続設定

### 公開例のVNet割り当て

3環境の公開例は、初回から5つの `/24` をVNetの `addressSpaces` に登録します。8つのサブネットは最初の4区画に配置します。表は `prod` の合成CIDR例です。`stg` は10.41系、`dev` は10.42系を使用します。導入先では予備を含む全区画を、接続済み・接続予定のアドレス範囲と照合します。

| 区画 | paramsのキー | CIDRの公開例 | サブネット名 |
| --- | --- | --- | --- |
| 1 | `agw` | `10.40.0.0/24` | `AppGatewaySubnet` |
| 2 | `pep` | `10.40.1.0/24` | `PrivateEndpointSubnet` |
| 3 | `firewall` | `10.40.2.0/26` | `AzureFirewallSubnet` |
| 3 | `apim` | `10.40.2.64/26` | `ApiManagementSubnet` |
| 3 | `management` | `10.40.2.128/26` | `ManagementSubnet` |
| 3 | 予備 | `10.40.2.192/26` | 割り当てなし |
| 4 | `portal` | `10.40.3.0/26` | `PortalSubnet` |
| 4 | `broker` | `10.40.3.64/26` | `CredentialBrokerSubnet` |
| 4 | `certificate` | `10.40.3.128/27` | `CertificateJobSubnet` |
| 4 | 予備 | `10.40.3.160/27` | 割り当てなし |
| 4 | 予備 | `10.40.3.192/26` | 割り当てなし |
| 5 | 予備 | `10.40.4.0/24` | 割り当てなし |

各サブネット名はVNet内で用途を表します。配備、再開時の照合、撤去は同じ[命名の正本](../../../infra/modules/network/subnet-names.json)を使います。

標準構成の `azd up` は、次の内部ACA環境をそれぞれ専用の委任サブネットに作成します。`<stem>` は `<environmentName>-<productName>` です。

| 用途 | ACA環境名 | サブネット名 |
| --- | --- | --- |
| Portal | `cae-<stem>` | `PortalSubnet` |
| Credential Broker | `cae-<stem>-broker` | `CredentialBrokerSubnet` |
| 証明書Job | `cae-<stem>-certificate` | `CertificateJobSubnet` |

監査ログは既存の監視リソースへ集約します。 [基盤の構成](../../../infra/main.bicep)

3つ目にはFirewall・APIM・管理VMを配置し、4つ目はPortal、Broker、証明書Jobの順に割り当てます。この順序を別のアドレス空間に適用する場合も、3つのCIDRを `network.subnetPrefixes` に明示します。5つ目はVNetのアドレス空間に含まれるため、ピアリング先との重複確認にも含めます。 [VNetピアリング](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-peering-overview)

- App Gateway v2は推奨の `/24` を確保します。自動スケール上限を変更するときは、`capacity.applicationGateway.maxInstances` 分のIPとAzure予約分5個を見積もります。Private Endpoint用の `/24` には251 IPを割り当て可能です。 [App Gatewayのサブネット要件](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure#size-of-the-subnet) · [ACRのPrivate Endpoint](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-private-endpoints)
- Azure Firewallの `/26` は必須の最小サイズです。APIM Classicの `/26` は公開例の `prod` のPremium 12ユニット・`stg` のPremium 1ユニット・`dev` のDeveloper 1ユニットを収容します。Internalモードでは、Azure予約5個と内部VIP 1個に加え、Premiumは1ユニットあたり2個、Developerは1個のプライベートIPを使います。管理VMの `/26` は、現在のVM 1台に対して余裕を持たせています。 [Firewallのサブネット要件](https://learn.microsoft.com/en-us/azure/networking/design-guide/azure-firewall#subnet-requirements) · [APIMのサブネット要件](https://learn.microsoft.com/en-us/azure/api-management/virtual-network-injection-resources#subnet-size)
- 各ACAはWorkload Profiles環境でConsumptionプロファイルを使います。公開例の `/26` はPortalとBrokerそれぞれの常駐アプリとJobを、証明書用の `/27` は証明書Jobを収容します。レプリカ数やJobの同時実行を増やす場合は、初回構築前にリビジョン更新中の重複も踏まえて見積もります。 [ACAのサブネット要件](https://learn.microsoft.com/en-us/azure/container-apps/custom-virtual-networks?tabs=workload-profiles-env#subnet)

全サブネットをVNet範囲内へ重複なく割り当てます。SNAT・IDPS private範囲・bypass、監査公開IP、証明書DNS名を独立したparamsに追加しません。実効経路や適用範囲は[通信要件](../architecture/network-communication-requirements.md)、[NSG判断記録](../architecture/network-security-design.md)、[Firewall判断記録](../architecture/firewall-policy-design.md)、[ネットワーク受入](../../testing/network-security-validation.md)を参照してください。

管理VMは同環境の `secrets/management-ssh.pub` にある有効なRSA 2048bit以上またはEd25519の公開鍵を再利用できます。公開鍵と秘密鍵がともにない場合だけ、同じ `secrets/` に鍵対を生成します。失敗やdownでも鍵対を保持します。 [標準ファイルの参照先](config-options.md#環境の標準ファイル)

## OAuth providerの登録と定義

保護paramsの標準登録は次の形です。APIリソースのegakumi-api Client IDは`entra.audience`へ入れ、下流OAuth Client IDと混同しません。

```json
{
  "credentialProviders": {
    "graphDelegated": {
      "clientId": "<GRAPH_OAUTH_CLIENT_ID>"
    }
  }
}
```

共通configは表示名・scope・pool・標準ファイル名を定義します。

```json
{
  "credentialProviders": {
    "graphDelegated": {
      "displayName": "Delegated Graph connection",
      "scopes": "openid profile email offline_access https://graph.microsoft.com/User.Read",
      "pool": { "count": 12, "capacity": 8000 },
      "clientSecretFile": "./secrets/entra-client-secret.txt"
    }
  }
}
```

追加接続はconfigの`credentialProviders.additional[]`に`name`・`displayName`・`identityProvider`・`scopes`とプロトコル別定義を置き、paramsの同じ`additional[]`へ`name`・`clientId`・`clientSecretFile`・任意の`oauthTenantId`を置きます。aadは`resourceUri`、oauth2は`authorizationUrl`・`tokenUrl`・`refreshUrl`を定義し、必要に応じて`connectionNames`・`identityEndpoint`・`identityIdProperty`・`pool`を指定します。

追加定義と登録は論理名で1対1に照合します。未知名・重複・欠落・標準slotとの衝突は拒否し、大小文字を暗黙に変更しません。一般的な配列の全置換で部分providerを合成せず、専用処理で完全な内部provider配列へ戻して検証します。秘密参照の宣言元基準、現在のアプリID／secret、物理provider名・pool所属、本人の`tid+oid`を照合します。

`config.gateway.authentication.credentialLogin`は `providerNames`、共通セッション設定、独立接続窓口の秘密ファイル名を定義します。保護paramsへは登録したClient IDを記載します。

```json
{
  "gateway": {
    "authentication": {
      "credentialLogin": {
        "identity": {
          "clientId": "<IDENTITY_APP_CLIENT_ID>"
        }
      }
    }
  }
}
```

標準の構築手順では専用Credentialアプリを登録します。tenantは `entraTenantId` から導出し、秘密ファイルは共通configで定義した環境別のファイルを読みます。 [Entraアプリの役割](../identity/entra-apps.md)

認証テナントを省略するとAzure構築先のテナントを使います。
