# 05. Azure Public DNSゾーンを準備する

前：[04. egakumi-credentialの登録](04-entra-credential.md) · [README](../../README.md) · 次：[06. DNS用マネージドIDの準備](06-dns-identity.md)

Gateway・Portalの公開名を管理するAzure Public DNSゾーンと、証明書の発行・自動更新に使うユーザー割り当てマネージドID（UAMI）を事前に用意します。CoreのIaCは、この公開ゾーン・DNS用ID・DNS側のロール割り当てを作成しません。

証明書はAzure Public DNSでDNS-01認証を行い、専用ACAの公開TLS JobがDNS用UAMIで取得・更新します。

公開ゾーン・ドメイン・DNS用IDはCoreとは独立した前提資源です。既存資源は対象と権限を照合して再利用します。新規に準備する場合はDNS担当者が費用・保持条件を確認します。これらはCoreの`azd down`では削除しません。

## 1. 公開名を決める

[第01章](01-prerequisites.md)で決めたGateway・Portalの公開名を引き継ぎます。`<...>`は導入先で確認した値へ置き換える記号です。

| 控える値 | 確認すること | 後で使う場所 |
| --- | --- | --- |
| `<PUBLIC_ZONE>` | Azure Public DNSで管理する公開ゾーン | DNSゾーン・委任・DNS権限の対象 |
| `<GATEWAY_FQDN>` | Gatewayの完全なホスト名。`https://`やパスを含めない | `gateway.publicHostName`、本人確認のWeb戻り先 |
| `<PORTAL_FQDN>` | Gatewayと異なる、`portal.`で始まる公開名 | `portal.publicHostName`、PortalのWeb戻り先 |
| 公開2名から生成するSAN | Gateway・Portalの正確な公開名2件を確認 | 上記2つのpublicHostNameから内部生成。独立したdomainNames入力は設けない |
| `<ACME_CONTACT_EMAIL>` | 証明書を管理する連絡先 | `certificate.contactEmail` |
| DNS担当者 | 委任・ゾーン・ID・権限の管理者 | 事前準備と継続運用 |

証明書のDNS名はGateway・Portalの正確な公開名2件から生成します。GatewayとPortalの公開名をHTTPSの入口に使います。

## 2. Azureの公開ゾーンを用意し、NSを委任する

1. Azure Portalで**DNSゾーン**を開き、`<PUBLIC_ZONE>`を確認します。作成する場合は**DNSゾーン → 作成**でDNS用Subscription・RG・ゾーン名を指定します。プライベートDNSゾーンは選びません。
2. ゾーンの概要で、DNS側のSubscription IDと、Azureが割り当てたネームサーバーを控えます。
3. ドメイン登録事業者または親ゾーンで、そのネームサーバーへNSを委任します。サブドメインをAzureへ委任する場合は、親ゾーンに子ゾーンのNSを登録します。
4. 外部のDNS照会で、対象ゾーンの権威NSがAzureのネームサーバーと一致することを確認します。Azureでゾーンを作成しただけでは委任完了ではありません。

親ゾーンのDNS管理画面で、子ゾーンに割り当てられたAzureのNSを登録します。Gateway・Portalを配置する子ゾーンの権威DNSはAzure Public DNSです。

[Azureの公開ゾーン作成](https://learn.microsoft.com/en-us/azure/dns/dns-getstarted-portal) · [ドメインの委任](https://learn.microsoft.com/en-us/azure/dns/dns-delegate-domain-azure-dns)

公開DNSの主なレコードは次のように分けます。

| レコード | 準備・更新する時点 | 用途 |
| --- | --- | --- |
| NS | 構築前にDNS担当者が委任 | 対象ゾーンへの問い合わせをAzureへ届ける |
| Entra所有確認TXT | Entraのドメイン確認で必要な場合 | テナントのドメイン所有確認。証明書処理では削除しない |
| `_acme-challenge`のTXT | 証明書の取得・更新時に公開TLS Jobが自動作成・回収 | DNS-01認証 |
| Gateway・PortalのAエイリアス | CoreのAppGW Public IP作成後にhookが設定 | 対象環境の公開入口へ接続 |

DNS-01はTXTで検証するため、AレコードをAppGWへ向ける前でも証明書を取得できます。config側でAzure提供DNSに固定する、VNetやFirewallの名前解決とは別の準備です。

## 3. DNS更新用UAMIを準備する

[Azure DNS用マネージドIDの事前手順](06-dns-identity.md)に従い、次を準備します。

1. Coreの構築・撤去対象とは別のRGに、DNS用UAMIを作成します。DNSと証明書用ACA Jobで同じEntraテナントを使います。
2. 公開ゾーンがあるDNS専用RGで、UAMIへ組み込みの**DNS Zone Contributor**を付与します。このロールはRG内の全公開ゾーンとTXT以外のレコード編集にも及ぶため、対象スコープを確認します。
3. Core構築実行者がACA Jobを作成・更新し、既存UAMIを割り当てられることを確認します。ID側の権限例は**Managed Identity Operator**です。
4. DNS側Subscription IDと、UAMIの完全なリソースIDを控え、構築前にparamsへ記入します。UAMIのClient IDやObject IDを指定する欄ではありません。

| 控える値 | paramsの保存先 |
| --- | --- |
| `<DNS_SUBSCRIPTION_ID>` | `certificate.azureDnsSubscriptionId` |
| `<DNS_MANAGED_IDENTITY_RESOURCE_ID>` | `certificate.azureDnsManagedIdentityResourceId` |

Coreは事前作成したUAMIを証明書用ACA Jobへ割り当てます。JobはそのIDを指定してAzure DNSを更新し、用途別の実行IDでKey Vaultへアクセスします。DNS用のアプリ登録やclient secretは不要です。

## 4. 基盤構築に渡す入力を確認する

[第07章](07-params.md)でparamsを完成させ、[第08章](08-provision-core.md)で`azd up`を実行します。証明書modeはconfigの`dnsPlugin`、DNS-01プラグインは資材固定のAzureです。公開plugin・domainNames入力は空でも拒否し、DNS名はGateway／Portal公開名から生成します。

DNS側Subscription IDとUAMIのリソースIDは、[07. params](07-params.md)で環境別paramsへ必ず事前設定します。`azd up`では準備済みの参照を検査し、不足・不正があれば構築前に停止します。

CM用Entraアプリのclient secretが必要な構成では、その秘密入力を案内します。DNS用IDの形式検査は、実際のDNS権限・委任・証明書発行の成功確認とは分けます。

## 5. 構築後のDNSと証明書を確認する

公開TLS Jobは固定版Posh-ACMEで検証用TXTを作成し、認証後に同じ検証値だけを回収します。有効な認証を再利用する場合は、新しいTXTを作成しないこともあります。失敗後のTXT残存はDNS担当者が当該注文と照合します。Entra所有確認TXTや、無関係な検証値を一括削除しません。

証明書とACMEの更新状態は公開TLS用Key Vaultへ保存します。DNS用UAMIはparamsで指定し、公開TLS Jobへ関連付けます。定期更新時もそのIDで認証するため、構築端末での再サインインは不要です。

`postprovision`は、公開ホスト名に最も長く一致するAzure DNSゾーンを探し、Gateway・PortalのAエイリアスを対象環境のAppGW Public IPリソースへ接続します。構築実行者には、ゾーンの読取とAレコード編集の権限が必要です。既存TTL・メタデータは保持し、別サービスを参照するレコードや同名CNAMEがあれば停止します。結果は`.local/<environment>/state/public-dns.json`へ保存します。

Core完了後は公開DNS、ブラウザーからのTLS接続、証明書のSAN・期限、公開TLS Jobの実行履歴を確認します。Portalの画面とヘルスは本体配備後に確認します。 [初回構築の受入](13-verify-deployment.md)

標準configは12時間ごとに確認し、残日数30日以内で更新します。UAMIの存続、公開TLS Jobへの関連付け、DNS権限、専用ACA環境からDNS API・Key Vaultへの接続を維持してください。証明書の更新と、AppGWが公開ホストごとに返す証明書の反映は別々に確認します。 [運用手順](../operations/secrets-and-certificates.md#dns用マネージドidと権限を更新する)

## このページの完了状態

- Azure Public DNSゾーンが存在し、親ゾーンまたは登録事業者からのNS委任を外部照会で確認した。
- Gateway・Portalの公開名、SAN、ACME連絡先を決めた。
- DNS用UAMI、DNS側のロール、構築実行者のID割り当て・Aレコード編集権限を準備した。
- DNS側Subscription IDとUAMIのリソースIDを、構築前に環境別paramsへ設定できる。
- 公開ゾーン・DNS用IDをCoreの`azd down`後も保持する方針を確認した。
