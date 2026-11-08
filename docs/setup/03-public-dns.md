# 03. Azure Public DNSと証明書用IDを準備する

[← 02. Core用Entraアプリを登録・設定する](02-entra.md) · [次へ：04. paramsを設定する →](04-params.md)

Gateway・Portalの公開名を管理するAzure Public DNSゾーンと、証明書の発行・自動更新に使うユーザー割り当てマネージドID（UAMI）を事前に用意します。CoreのIaCは、この公開ゾーン・DNS用ID・DNS側のロール割り当てを作成しません。

証明書はAzure Public DNSでDNS-01認証を行い、管理VMがDNS用UAMIで取得・更新します。

公開ゾーン・ドメイン・DNS用IDはCoreとは独立した前提資源です。既存資源は対象と権限を照合して再利用します。新規に準備する場合はDNS担当者が費用・保持条件を確認します。これらはCoreの`azd down`では削除しません。

## 1. 公開名を決める

[01. 前提](01-prerequisites.md)・[02. Entra](02-entra.md)で決めたGateway・Portalの公開名を引き継ぎます。`<...>`は導入先で確認した値へ置き換える記号です。

| 控える値 | 確認すること | 後で使う場所 |
| --- | --- | --- |
| `<PUBLIC_ZONE>` | Azure Public DNSで管理する公開ゾーン | DNSゾーン・委任・DNS権限の対象 |
| `<GATEWAY_FQDN>` | Gatewayの完全なホスト名。`https://`やパスを含めない | `gateway.publicHostName`、本人確認のWeb戻り先 |
| `<PORTAL_FQDN>` | Gatewayと異なる、`portal.`で始まる公開名 | `portal.publicHostName`、PortalのWeb戻り先 |
| `<CERTIFICATE_DNS_NAMES>` | Gateway・Portal両方を覆うSAN | `certificate.domainNames` |
| `<ACME_CONTACT_EMAIL>` | 証明書を管理する連絡先 | `certificate.contactEmail` |
| DNS担当者 | 委任・ゾーン・ID・権限の管理者 | 事前準備と継続運用 |

ワイルドカード`*.<PUBLIC_ZONE>`は、そのゾーン直下の名前を覆います。ゾーン自身や、さらに深い階層の名前は自動では含みません。native gRPCの公開ホスト名もGatewayと同じにし、専用TCPポートで区別します。

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
| `_acme-challenge`のTXT | 証明書の取得・更新時に管理VMが自動作成・回収 | DNS-01認証 |
| Gateway・PortalのAエイリアス | CoreのAppGW Public IP作成後にhookが設定 | 対象環境の公開入口へ接続 |

DNS-01はTXTで検証するため、AレコードをAppGWへ向ける前でも証明書を取得できます。VNet内部の名前解決や`network.dnsServers`とは別の準備です。

## 3. DNS更新用UAMIを準備する

[Azure DNS用マネージドIDの事前手順](03a-azure-dns-managed-identity.md)に従い、次を準備します。

1. Coreの構築・撤去対象とは別のRGに、DNS用UAMIを作成します。DNSと管理VMで同じEntraテナントを使います。
2. 公開ゾーンがあるDNS専用RGで、UAMIへ組み込みの**DNS Zone Contributor**を付与します。このロールはRG内の全公開ゾーンとTXT以外のレコード編集にも及ぶため、対象スコープを確認します。
3. Core構築実行者が管理VMを作成・変更し、既存UAMIを割り当てられることを確認します。ID側の権限例は**Managed Identity Operator**です。
4. DNS側Subscription IDと、UAMIの完全なリソースIDを控え、構築前にparamsへ記入します。UAMIのClient IDやObject IDを指定する欄ではありません。

| 控える値 | paramsの保存先 |
| --- | --- |
| `<DNS_SUBSCRIPTION_ID>` | `certificate.azureDnsSubscriptionId` |
| `<DNS_MANAGED_IDENTITY_RESOURCE_ID>` | `certificate.azureDnsManagedIdentityResourceId` |

DNS用のアプリ登録やclient secretは作成しません。Coreは事前作成したUAMIを管理VMへ追加し、実行時にIMDSでそのIDを明示してARMトークンを取得します。Key Vaultへのアクセスには管理VMのシステム割り当てIDを維持します。

## 4. azd upへ渡す入力を確認する

[04. params](04-params.md)・[05. config](05-config.md)を設定し、[06. 構築](06-deploy.md)で`azd up`を実行します。証明書設定は`certificate.mode: dnsPlugin`、`certificate.plugin: Azure`です。

DNS側Subscription IDとUAMIのリソースIDは、[04. params](04-params.md)で環境別paramsへ必ず事前設定します。`azd up`は準備済みの参照を検査し、不足・不正があれば構築前に停止します。

CM用Entraアプリのclient secretが必要な構成では、その秘密入力を案内します。DNS用IDの形式検査は、実際のDNS権限・委任・証明書発行の成功確認とは分けます。

## 5. 構築後のDNSと証明書を確認する

管理VMは固定版Posh-ACMEで検証用TXTを作成し、認証後に同じ検証値だけを回収します。有効な認証を再利用する場合は、新しいTXTを作成しないこともあります。失敗後のTXT残存はDNS担当者が当該注文と照合します。Entra所有確認TXTや、無関係な検証値を一括削除しません。

証明書とACMEの更新状態はCoreのKey Vaultへ保存します。DNSの公開IDはparamsと管理VMの設定に保存します。定期更新は管理VMからUAMIで認証するため、構築端末での再サインインは不要です。

`postprovision`は、公開ホスト名に最も長く一致するAzure DNSゾーンを探し、Gateway・PortalのAエイリアスを対象環境のAppGW Public IPリソースへ接続します。構築実行者には、ゾーンの読取とAレコード編集の権限が必要です。既存TTL・メタデータは保持し、別サービスを参照するレコードや同名CNAMEがあれば停止します。結果は`.local/<environment>/state/public-dns.json`へ保存します。

構築後は公開DNS、実際のブラウザーからのTLS接続、Portal初期画面、証明書のSAN・期限、管理VMの更新timerを確認します。標準configは12時間ごとに確認し、残日数30日以内で更新します。UAMIの存続、VMへの関連付け、DNS権限、管理VMからDNS API・Key Vaultへの接続を維持してください。証明書の更新と、AppGW／APIMが返す証明書の反映は別々に確認します。[運用手順](../operations.md#dns用マネージドidと権限を更新する)

## このページの完了状態

- Azure Public DNSゾーンが存在し、親ゾーンまたは登録事業者からのNS委任を外部照会で確認した。
- Gateway・Portalの公開名、SAN、ACME連絡先を決めた。
- DNS用UAMI、DNS側のロール、構築実行者のID割り当て・Aレコード編集権限を準備した。
- DNS側Subscription IDとUAMIのリソースIDを、構築前に環境別paramsへ設定できる。
- 公開ゾーン・DNS用IDをCoreの`azd down`後も保持する方針を確認した。
