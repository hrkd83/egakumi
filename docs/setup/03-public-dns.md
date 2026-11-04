# 03. 公開DNSと証明書用の資格情報を準備する

[← 02. Core用Entraアプリを登録・設定する](02-entra.md) · [次へ：04. paramsを設定する →](04-params.md)

このページでは、Gateway・Portalの公開名を管理するDNSゾーンと、証明書の発行・自動更新に使うID・資格情報を用意します。DNS担当者が事前に準備し、構築担当者は後の`azd up`で必要な値を対話入力します。AzureはマネージドIDの公開参照を使い、Cloudflare／Route53の秘密値は非表示入力します。**資格情報のJSONや`.local/<environment>/secrets/`フォルダーを手作業で作る必要はありません。**

標準の`dnsPlugin`方式を説明します。用意済みのPFXを使う`prepared`方式は[05. config](05-config.md)と[運用手順](../operations.md)で確認してください。CoreのIaCは公開DNSゾーンやDNS管理用のアカウント・権限を作成しません。

公開DNSゾーンとドメインはCoreとは独立した前提資源です。既存の利用権・ゾーンがあれば再利用します。新規にドメインを購入したり、Route53／Azure Public DNSのゾーンを作成したりする場合は、作成前にその事業者のゾーン保持料・クエリ料金等を確認してください。これらはCoreの`azd down`では削除されず、継続するDNSの費用も別管理です。

## 1. 公開名とDNS担当者を決める

[01. 前提](01-prerequisites.md)・[02. Entra](02-entra.md)で決めたGateway・Portalの公開名を引き継ぎます。`<...>`は自社の実際の値へ置き換える記号です。DNS事業者は、ドメインを購入した会社ではなく、現在その名前の**権威DNSを運用している事業者**を選びます。

| 控える値 | 意味・確認すること | 後で使う場所 |
| --- | --- | --- |
| `<PUBLIC_ZONE>` | 管理する公開DNSゾーン。インターネットから参照できること | DNS事業者の管理画面と権限の対象 |
| `<GATEWAY_FQDN>` | Gatewayの完全なホスト名。URLの`https://`やパスを含めない | paramsの`gateway.publicHostName`、本人確認のWeb戻り先 |
| `<PORTAL_FQDN>` | Portalの完全なホスト名。Gatewayと異なる`portal.`で始まる名前 | paramsの`portal.publicHostName`、PortalサインインのWeb戻り先 |
| `<CERTIFICATE_DNS_NAMES>` | 証明書に含める名前の一覧（SAN） | paramsの`certificate.domainNames` |
| `<ACME_CONTACT_EMAIL>` | 証明書の発行・更新を管理する連絡先 | paramsの`certificate.contactEmail` |
| DNS担当者・資格情報の管理者 | ゾーンの編集、権限、失効・更新を扱う担当者 | 構築時の問い合わせと継続運用 |

証明書SANはGatewayとPortalの両方を覆う名前にします。ワイルドカード`*.<PUBLIC_ZONE>`を使う場合、そのゾーン直下の名前だけを覆います。ゾーン自身の名前や、さらに深い階層の名前は自動では含まれません。native gRPCを有効にする場合も公開ホスト名はGatewayと同じにし、専用TCPポートで区別します。

公開DNSには、用途の異なるレコードがあります。

| レコード | 誰が・いつ用意するか | 何のためか |
| --- | --- | --- |
| NSの委任 | DNS担当者が事前に設定 | 対象ゾーンへの問い合わせを、選んだDNS事業者へ届ける |
| Entra所有確認のTXT | [02. Entra](02-entra.md)で必要になった場合に設定 | Entraテナントでのドメイン所有確認。証明書処理では消さない |
| `_acme-challenge`のTXT | 構築・証明書更新時に管理VMが自動作成・回収 | Let's Encryptへドメイン所有を証明するDNS-01認証 |
| GatewayのA | AppGWのPublic IPが決まった後にDNS担当者が設定 | 利用者をGatewayの公開入口へ接続する |

DNS-01はTXTで検証するため、GatewayのAレコードをAppGWへ向ける前でも証明書を取得できます。VNet内部の名前解決や`network.dnsServers`とは別の準備です。

## 2. 利用するDNS事業者の準備を行う

次のうち、実際に`<PUBLIC_ZONE>`を管理する事業者の手順だけを行います。既存の有効な公開ゾーンと専用資格情報がある場合は、作り直さず対象と権限を照合します。資格情報は企業のパスワード保管庫など安全な場所で管理し、リポジトリやチャットへ値を貼り付けません。

### Cloudflare

#### 公開ゾーンを用意する

1. Cloudflareの管理画面で対象ドメインを開き、`<PUBLIC_ZONE>`のDNSを管理できることを確認します。
2. 新規にCloudflareへ移す場合は、ドメインを追加し、既存レコードを確認してから、ドメイン登録事業者でCloudflare指定のネームサーバーへ委任します。既存サービスのDNSを移す作業なのでDNS担当者が行います。
3. ゾーンが有効になり、外部からそのゾーンのNSがCloudflareを指すことを確認します。画面にゾーンを追加しただけでは委任完了とは限りません。[Cloudflareの公開DNS設定](https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/)

#### 対象ゾーン用のAPI tokenを発行する

1. **My Profile → API Tokens → Create Token**を開きます。
2. **Edit zone DNS**のテンプレート、または同等のカスタムtokenを選びます。
3. 権限を`Zone / DNS / Edit`と`Zone / Zone / Read`にし、**Zone Resourcesを対象の`<PUBLIC_ZONE>`だけ**にします。全アカウント用のGlobal API Keyは、この対話入力では使いません。
4. 有効期限を設定する場合は、DNS担当者が期限前に更新できる日程にします。送信元IP制限を使う場合は、構築端末だけでなく、構築後にDNS APIへ接続する管理VMの外向き経路も考慮します。
5. 内容を確認して作成し、表示された**API tokenの値**を安全な管理場所へ保存します。値は作成時だけ表示されるため、後から同じ値を見直す前提にはしません。[Cloudflareのtoken作成](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)

控えるのは**API tokenの値と期限**です。Zone ID・Account ID・Cloudflareのログインパスワードを`azd up`へ入力する必要はありません。tokenの編集権限は対象ゾーンのDNS全体へ及び、TXT一件だけに限定する設定ではありません。[Posh-ACMEのCloudflareプラグイン](https://poshac.me/docs/v4/Plugins/Cloudflare/)

### AWS Route 53

#### Public Hosted Zoneを用意する

1. AWS管理コンソールで**Route 53 → Hosted zones**を開きます。
2. `<PUBLIC_ZONE>`のHosted Zoneを確認します。新規作成なら**Create hosted zone**で名前を指定し、種類を**Public hosted zone**にします。Private hosted zoneでは外部の認証局がTXTを確認できません。
3. ゾーンに割り当てられたNSを、ドメイン登録事業者または親ゾーンへ委任します。同名のHosted Zoneが複数ある場合、実際に委任されている方を選びます。[AWSの公開Hosted Zone作成](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/CreatingHostedZone.html)

#### DNS更新専用IAMユーザーとアクセスキーを用意する

1. AWS担当者に、対象ゾーンのDNS-01用IAMユーザーを用意してもらいます。AWSのrootアカウントのキーは使いません。
2. IAMポリシーで、次の操作を許可します。`ListHostedZones`はゾーンを探すための一覧参照です。残りの操作は対象Hosted ZoneのARNへ限定します。

   | 操作 | 用途・スコープ |
   | --- | --- |
   | `route53:ListHostedZones` | ゾーン一覧を参照。IAMのResourceは`*` |
   | `route53:GetHostedZone` | 対象ゾーンの情報を参照 |
   | `route53:ListResourceRecordSets` | 既存のTXT値を参照し、他の値を壊さず更新 |
   | `route53:ChangeResourceRecordSets` | 対象ゾーンへ検証用TXTを追加・削除 |

   基本の権限構成は[Posh-ACMEのRoute53手順](https://poshac.me/docs/v4/Plugins/Route53/)を参照します。AWS担当者は必要に応じて`ChangeResourceRecordSets`をTXT型と対象の`_acme-challenge`名に限定できます。SANに含めたすべての名前と、検証後の削除も許可する必要があります。[AWSのレコード名・型・操作による権限制限](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/specifying-conditions-route53.html)
3. IAMの対象ユーザーで**Security credentials → Access keys → Create access key**を開きます。用途の案内を確認し、この仕組みがAWS外の管理VMから明示キーで接続する用途であることを確認します。
4. 発行された**Access key IDとSecret access keyの両方**を安全な管理場所へ保存します。Secret access keyは作成時の値を控えます。[AWSのアクセスキー作成](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-key-self-managed.html)

控える値は`<AWS_ACCESS_KEY_ID>`・`<AWS_SECRET_ACCESS_KEY>`です。Hosted Zone IDは権限設定のためDNS担当者が控えますが、通常の`azd up`入力欄では求めません。

この対話入力は**Access key ID＋Secret access keyの2値**に対応します。STSのSession token、AWS profile名、IAM Roleを指定する欄はありません。Posh-ACME自体が別の認証方式を持つことと、この標準対話が対応する範囲を区別してください。3値必要な一時資格情報の一部だけを貼り付けて進めないでください。

### Azure Public DNS

#### 公開ゾーンを用意する

1. Azure Portalで**DNSゾーン**を開き、`<PUBLIC_ZONE>`を確認します。作成する場合は**DNSゾーン → 作成**でDNS用Subscription・RG・ゾーン名を指定します。**プライベートDNSゾーン**は選びません。
2. DNSゾーンを置くSubscription IDを`<DNS_SUBSCRIPTION_ID>`として控えます。Coreの構築先と別のSubscriptionでも、DNS側の値を明示して利用できます。
3. ゾーンに割り当てられたネームサーバーを確認し、ドメイン登録事業者または親ゾーンへNSを委任します。公開ゾーンを作成しただけでは、外部から参照するDNSは切り替わりません。[Azureの公開DNSゾーン作成](https://learn.microsoft.com/en-us/azure/dns/dns-getstarted-portal)・[ドメインの委任](https://learn.microsoft.com/en-us/azure/dns/dns-delegate-domain-azure-dns)

#### DNS更新用のユーザー割り当てマネージドIDを事前に準備する

Azure Public DNSは、**事前作成したユーザー割り当てマネージドID（UAMI）**を使います。DNS用SPのアプリ登録やclient secretは用意しません。Coreの`azd up`はIDやDNS側のロール割り当てを新規作成せず、指定したIDを管理VMへ紐づけます。

Azure Portalの作成フォーム、DNS用RGのIAM、マネージドIDの選択、`azd up`へ渡すリソースIDの確認は、[画面付きの事前手順](03a-azure-dns-managed-identity.md)にまとめています。下記はその要点です。

1. Azure Portalの「マネージドID」から、CoreのRGとは別の事前準備用RGにユーザー割り当てIDを作成します。DNSと管理VMと同じEntraテナントを使います。
2. 公開ゾーンがある**DNS専用リソースグループ**の「アクセス制御（IAM）」で、組み込みの **DNS Zone Contributor** をそのマネージドIDへ付与します。ここでいうグループはAzureのリソースグループです。
3. このロールはDNS専用RG内の全公開ゾーンと、TXT以外のレコード編集も許可します。対象ゾーンだけへ範囲を絞る場合は、ゾーン単位の編集権限とプラグインのゾーン探索に必要な読取権限を確認してください。TXTだけを扱うカスタムロールも[プラグインの公式ガイド](https://poshac.me/docs/v4/Plugins/Azure/)にあります。
4. マネージドIDの「プロパティ」から**リソースID**を控えます。Client IDやObject（Principal）IDではありません。DNSゾーン側のSubscription IDも控えます。ID自体を置くSubscriptionとDNS側のSubscriptionは異なっても構いません。
5. Coreの構築実行者には、管理VMを作成・変更できる権限に加え、既存IDをVMへ割り当てる権限（例: そのIDに対する **Managed Identity Operator**）が必要です。ID作成とDNSロール付与は、それぞれ必要なAzure RBAC権限を持つ担当者が事前に行います。

| 控える値 | `azd up`のAzure選択後に対話入力するもの |
| --- | --- |
| `<DNS_SUBSCRIPTION_ID>` | 公開DNSゾーンが存在するSubscriptionのGUID |
| `<DNS_MANAGED_IDENTITY_RESOURCE_ID>` | `/subscriptions/<ID>/resourceGroups/<RG>/providers/Microsoft.ManagedIdentity/userAssignedIdentities/<NAME>` |

入力した公開IDはparamsの`certificate.azureDnsSubscriptionId`と`certificate.azureDnsManagedIdentityResourceId`へ保存します。次回は再利用するため再入力しません。変更時は保存済みの該当項目を編集または削除してから実行します。CIや`azd provision`単独実行ではこの2項目を事前設定してください。DNSのRG名・ゾーン名はプラグインがSubscriptionの公開ゾーン一覧から探索するため、別の対話入力はありません。

管理VMはKey Vault用のシステム割り当てIDを維持し、DNS用UAMIを追加します。初回取得・定期更新のたびに、VM内のIMDSでDNS用IDを明示してARMトークンを取得し、Posh-ACMEへ渡します。固定版4.34.0の`AZUseIMDS`にはUAMIを選ぶ引数がないため、公式の`AZAccessTokenSecure`引数を使います。DNS用client secretやトークンをローカル入力ファイルとして準備する必要はありません。`pluginArgumentsFile`やKey VaultのDNS用SP資格情報はAzure方式では読みません。

[IDの作成](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/manage-user-assigned-managed-identities-azure-portal)・[VMへのID割り当て](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/how-to-configure-managed-identities?pivots=qs-configure-portal-windows-vm)・[Azure DNSの権限](https://learn.microsoft.com/en-us/azure/dns/dns-protect-zones-recordsets)

## 3. 後のazd upで何を入力するか確認する

ここまでで準備するのはDNS事業者側の設定と安全な保管場所です。次の[04. params](04-params.md)・[05. config](05-config.md)を設定してから、[06. 構築](06-deploy.md)で`azd up`を実行します。

`dnsPlugin`方式の通常の対話端末では、Azureの課金リソースを作る前に次の順で案内します。

1. 端末の依存関係を確認した後、DNS事業者の選択を表示します。

   ```text
   1. Cloudflare
   2. AWS Route53
   3. Azure Public DNS
   ```

2. 番号を選びます。Enterはparamsに設定済みの事業者を維持し、`q`は中止します。別の事業者を選ぶと、paramsの`certificate.plugin`へ非秘密のプラグイン名を保存します。
3. Azureでは不足するDNS側Subscription IDとUAMIのリソースIDを入力し、事業者の選択とまとめてparamsへ保存します。入力を中止・検査に失敗した場合は保存しません。
4. 必要な秘密情報の一覧・用途・取得元・保存先を案内します。設定したAPIがCM用のCoreアプリsecretを必要とする場合は、その値を非表示入力します。Cloudflare／Route53では続いてDNS資格情報を入力します。Azureの公開ID入力も含めたDNS側の入力内容は次のとおりです。

   | 選択 | paramsへ保存する`certificate.plugin` | 入力順 |
   | --- | --- | --- |
   | 1 | `Cloudflare` | Cloudflare API tokenの値 |
   | 2 | `Route53` | Access key ID → Secret access key |
   | 3 | `Azure` | DNSゾーンのSubscription ID → DNS用UAMIのリソースID（公開IDとしてparamsへ保存） |

5. hookが必要な保護ファイルを作成・検査してから、Azureの前提確認・構築へ進みます。Azure DNSの資格情報ファイルは不要です。入力時の形式検査は、DNS APIへの認証・権限や委任の成功を保証するものではありません。

値は1項目ずつ貼り付け、Enterで確定します。表示用の`<...>`記号ごと入力せず、取得した実際の値だけを入れてください。秘密値をparams・config・コマンド引数・環境変数の設定例・パイプへ書きません。

Azureは上記の公開IDだけを保存し、DNS資格情報ファイルを作成しません。CloudflareとRoute53についても、利用者がJSONを作成する必要はありません。clone直後に`.local/<environment>/secrets/`がなくても自動作成します。内部保存先はparamsの`certificate.pluginArgumentsFile`で参照し、第1章の準備コマンドが環境別の`./secrets/...`参照へ変換するため、その作成済みparamsを使います。POSIXではディレクトリー700・ファイル600で、ローカルの値は構築後も再実行用に残ります。**Key Vaultだけに保存されるわけではありません。**

既存の入力は値を表示せず再利用します。事業者を変更した場合は、旧事業者の資格情報を流用せず、新しい事業者の入力を検査した後だけDNS用の内部ファイルを置き換えます。途中で中止すると旧資格情報は保持します。ただし、既に保存されたparamsの事業者選択は残ります。同じ事業者のtokenが期限切れかどうかは、この形式検査だけでは判断しません。

CIなど非対話の実行では選択・入力画面を表示せず、paramsの事業者と事前に渡した保護入力を使います。不足時は構築前に停止します。その他のPosh-ACMEプラグインや別の認証方式は、このページの簡単な対話の範囲外です。

## 4. 証明書の発行時と構築後の流れ

### 一時TXTの自動処理

管理VMは固定版Posh-ACMEを使い、対象名の`_acme-challenge`へ検証用TXT値を作成します。Let's Encryptがその値を確認した後、プラグインが**同じ検証の値だけ**を回収します。有効な認証を再利用できる場合は、新しいTXTを作らないこともあります。

発行や接続に失敗した場合も注文状態を保管し、次回に同じ証明書や発行済みの結果を再利用できるようにします。これは、すべての障害でTXT削除が必ず成功する保証ではありません。DNS権限やAPI接続を失うと後片付けも失敗し得るため、DNS担当者が当該注文のTXT残存を確認します。公開ゾーン全体のTXT、Entra所有確認TXT、他の証明書の検証値を一括削除しません。

### 証明書・更新用資格情報の保存先

以下のDNS資格情報ファイル・Key Vault secretの説明はCloudflare／Route53などの資格情報方式です。Azure DNSはparamsと管理VMの公開設定にID参照を保存し、実行時にUAMIで認証します。証明書・ACME状態のKey Vault保管は共通です。

| 時点 | 保存先と使い方 |
| --- | --- |
| 対話入力直後 | このcloneのGit管理外の保護ファイル。入力しただけではAzureへ送信しない |
| 続くCore構築時 | 管理VMがDNS資格情報をCoreのKey Vaultの`<CERTIFICATE_NAME>-dns-plugin-args`というsecretへ登録。証明書とACMEの更新状態もKey Vaultへ保管 |
| 定期更新時 | 管理VMのMIでKey VaultからDNS資格情報を読み、選んだ事業者のAPIへ接続。構築担当者のPCや再サインインは不要 |

`<CERTIFICATE_NAME>`は採用したconfigの`certificate.certificateName`です。対話の案内には実際のKey Vault・RG・secretの登録予定先が表示されます。登録した証明書は、設定したCoreの公開名と対応する入口で使用します。

### 公開Aレコードを設定する

`certificate.plugin`が`Azure`のとき、`azd up`の`postprovision`がGatewayとPortalの公開Aエイリアスを設定します。参照先は対象環境のApp GatewayのPublic IPリソースです。再構築で参照先が解除された場合も、次の構築後に接続し直します。

DNSゾーンは`certificate.azureDnsSubscriptionId`内から、公開ホスト名に最も長く一致するゾーンを探します。構築実行者には、対象ゾーンの読取とAレコードの編集権限が必要です。証明書更新時は引き続き管理VMのDNS用マネージドIDを使います。

両ホストを先に検査してから更新し、既存のTTL・メタデータを保持します。別のサービスを参照するレコードや同名CNAMEがある場合は停止するため、対象を確認してから再実行してください。結果は`.local/<environment>/state/public-dns.json`へ保存します。paramsファイルへの書戻しは行いません。

Cloudflare・Route 53・証明書持込方式では、DNS担当者がGatewayとPortalのAレコードを`.local/<environment>/state/latest.json`の`outputs.gateway.publicIp`へ設定します。Cloudflareは**DNS only（灰色の雲）**を使います。

公開DNSの保存後、実際に利用するブラウザ・ネットワークからTLS接続とPortalの初期画面を確認します。DNSの保存成功と、端末や社内プロキシを含む経路での表示成功は、それぞれ確認します。

### 自動更新と資格情報の期限を管理する

標準configでは、Linux管理VMのsystemd timerが12時間ごとに確認し、証明書の残日数が30日以内になると更新します。継続運用には管理VM、Key Vaultへの接続、DNS APIへの接続・権限、有効なDNS資格情報が必要です。証明書がKey Vaultへ登録されたことと、AppGW／APIMの公開入口へ新しい証明書が反映されたことは分けて確認します。

DNS担当者はCloudflare token・AWS access keyの失効予定や組織の更新期限を管理します。証明書の更新日は、これらの資格情報の期限を延長しません。期限前に新しい値を安全に準備し、ローカル保管値とKey Vaultの更新用secretを整合させ、新値で更新できることを確認してから旧値を失効させます。同じ事業者を選んで`azd up`を再実行するだけでは、既存の形式が有効な値を必ず聞き直すわけではありません。具体的な資格情報更新・証明書確認・撤去時の扱いは[運用手順](../operations.md)を参照してください。

Azure DNSのUAMIはclient secretの期限管理が不要ですが、IDの存続・VMへの紐づけ・DNS権限を維持してください。

公開ゾーン、DNS管理用アカウント・マネージドID、Entra所有確認TXT、恒久AレコードはCoreのRGとは別管理です。`azd down`で自動削除される前提にせず、サービス廃止時はDNS担当者が対象と影響を確認して処置します。

## このページの完了状態

- `<PUBLIC_ZONE>`が実際に委任され、外部から公開DNSとして参照できる。
- `<GATEWAY_FQDN>`・証明書の対象名・連絡先を、Entraの設定と同じ前提で控えた。
- 選んだ事業者の資格情報と必要権限が準備され、値と期限を安全な管理場所へ保管した。
- 構築担当者が`azd up`で入力する値と、DNS担当者が構築後にAレコードを設定する役割を理解した。

次は[04. paramsを設定する](04-params.md)へ進みます。

[← 02. Core用Entraアプリを登録・設定する](02-entra.md) · [次へ：04. paramsを設定する →](04-params.md)
