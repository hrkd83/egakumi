# 環境別paramsの入力項目

[第07章](../../setup/07-params.md)で準備する環境別の設定を扱います。型・必須条件は[params schema](../../../schemas/params.schema.json)で確認します。標準のSKUと機能設定は[標準config](config-options.md)を参照してください。

## 企業・環境ごとに確認する値（params）

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
| `network.subnetPrefixes.aca` | VNet内IPv4 CIDR | 共用ACA |
| `network.subnetPrefixes.broker` | VNet内IPv4 CIDR | Credential Broker専用ACA |
| `network.subnetPrefixes.certificate` | VNet内IPv4 CIDR | 証明書Job専用ACA |
| `network.subnetPrefixes.audit` | VNet内IPv4 CIDR | 本文監査専用ACA |
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

## ネットワーク・監査の接続設定

全サブネットをVNet範囲内へ重複なく割り当てます。SNAT・IDPS private範囲・bypass、監査公開IP、証明書DNS名を独立したparamsに追加しません。実効経路や適用範囲は[通信要件](../architecture/network-communication-requirements.md)、[NSG判断記録](../architecture/network-security-design.md)、[Firewall判断記録](../architecture/firewall-policy-design.md)、[ネットワーク受入](../../testing/network-security-validation.md)を参照してください。

SSH公開鍵は、利用者が明示した保護入力にある有効なRSA 2048bit以上／Ed25519の1件を再利用できます。公開鍵と秘密鍵がともにない場合だけ、同環境の`.local/<environment>/secrets/`へ鍵対を生成します。リンク・権限・既存秘密鍵・無効な公開鍵・外部の未作成先を確認し、上書きや別環境からのコピーはしません。失敗やdownでも鍵対を保持します。

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

追加定義と登録は論理名で1対1に照合します。未知名・重複・欠落・標準slotとの衝突は拒否し、大小文字を暗黙に変更しません。一般的な配列の全置換で部分providerを合成せず、専用処理で完全な内部provider配列へ戻して検証します。秘密参照の宣言元基準、現在のアプリID／secret、物理provider名・pool所属、本人の`tid+oid`を照合します。

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

identityは任意で、指定時はClient IDと保護秘密参照を両方設定します。tenantは`entraTenantId`から導出します。省略時の選択CM provider由来の本人確認は維持します。標準の構築手順では専用Credentialアプリを登録します。 [Entraアプリの役割](../identity/entra-apps.md)

標準configは本文監査を有効にするため、`azureTenantId`と`entraTenantId`を揃えます。別の認証テナントを指定する構成では、本文監査を無効にします。 [本文監査の構築条件](../audit/request-body-audit.md#初回構築)
