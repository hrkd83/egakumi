# 標準configのリファレンス

Portal認証証明書はparamsの`portal.certificate`で設定し、KV自己署名・非exportable RSA、既定12か月・60日前更新準備です。Portal Client IDは初期構築後に手動登録してから入力します。[構築順序](../../setup/09-egakumi-portal.md)と[更新運用](../../operations/portal-authentication.md)を参照してください。

[初回構築の手順](../../../README.md#新規クローンから構築する) · [環境別params](../../setup/07-params.md)


検証・本番の標準configは初回構築でそのまま使います。このページは既定値と、SKU・モデル・保護機能を見直す場合の参照資料です。環境固有のID・IP・公開名はparamsで管理します。

## 検証・本番の標準構成

| 正本 | 主な初期構成 | 選ぶときの確認 |
| --- | --- | --- |
| `infra/config/test.json` | Firewall Premium・IDPS Deny、検証向けのSKU・保護設定 | 検証後の撤去を想定した費用・保持設定か |
| `infra/config/production.json` | Firewall Premium・IDPS Deny（Alert and Deny）、AppGW／Public IPの3ゾーン | 対象地域のSKU・ゾーン、可用性・監査保持・削除保護の運用に合うか |

[第07章](../../setup/07-params.md)の準備コマンドが、profileに対応する正本の相対パスをparamsの`configFile`に設定します。`environmentName`を変えてもSKU・保持・保護は自動で切り替わりません。configのexampleやlocalコピーは作らず、必要な変更は選んだ正本へ記録します。

## 標準設定を調整する場合

1. FirewallのSKU・IDPS、AppGW／APIM／VM等のSKUとゾーンを確認する。Firewallの両環境はPremiumで、IDPSはDeny（Alert and Deny）から開始する。
2. `services.modelDeployments`で配備するモデル・版・SKU・capacityを選ぶ。API正本の許可モデルと対応させる。
3. 音声等の利用機能、WAF上限、回数制限、監査保持、削除保護、証明書更新の動作を確認する。通常HTTP／HTTPSの入口保護と、初期NSGの固定集合を確認する。
4. 同じparamsでローカル検査し、整合を確認する。

```sh
python3 -B scripts/deployment/helpers/configuration.py validate --config "$DEPLOY_PARAMS_FILE"
```

公開configの`services.modelDeployments`は初期セットです。すべての導入先に、そのモデルの提供地域・利用枠・購入資格があるとは限りません。モデルのカタログ、退役日、SKU、必要TPM／RPMと料金を確認します。capacityの単位・換算はモデルとSKUごとに異なります。APIを公開するときは、削除したモデルを許可一覧に残さず、配備名・公開操作と同時に揃えます。

Claudeは手動公開するため、初回構築のモデル配備一覧に含めません。

## 公開証明書の設定

証明書はAzure Public DNSのDNS-01認証で自動発行・更新します。`certificate.mode`は`dnsPlugin`、プラグインは資材固定の`Azure`を使用します。証明書DNS名はparamsのGateway／Portal公開名2件から内部生成し、入力する項目とIDの権限は [環境別params](../../setup/07-params.md) と [公開DNSの準備](../../setup/05-public-dns.md) を参照してください。

## configリファレンス

スキーマは [標準config](../../../infra/config/options.schema.json)／[合成設定](../../../infra/config/configuration.schema.json)。下表は資材の初期値と動作で、導入先のtenant・domain・IPを指定する欄ではありません。


### 標準値を変える場合だけ確認する項目（config）

以下はconfig側のSKU・ゾーン・ポート・モデル配備・通信と保護機能の標準設定です。リージョン・タグ・このVNet内のIP計画・アプリ登録・VMログイン主体は前節のparamsへ記載します。標準APIはAPI正本を常用し、追加Foundry／API Center権限と監査readerは初期空に固定します。VNetとFirewallのDNSはBicep内でAzure提供DNSに固定し、JSONの入力項目にはしません。NSG／Firewallの初期規則、APIMの外部NTP許可と管理VMのAzureホスト時刻同期も資材内で固定します。API・権限の要素形式は[paramsの記述形式](../gateway/api-definitions.md#api権限の記述形式)で説明します。`environmentName`は命名用です。test/dev/prodという文字列でSKU・保持・保護を切り替えません。

`[]`は空配列、`{}`は空オブジェクトです。空配列の意味は項目ごとに異なります。相対ファイル参照は**それを記載したJSONのフォルダー基準**で解決し、2ファイルを合成しても参照先を変えません。両ファイルの`$schema`はエディター補助、`$comment`は説明で、Azureへ送信しません。

初期Projectの表示名は`core-platform`、実リソース名は`proj-<環境>-<製品>`です。表示名とリソース名は[MicrosoftのProject定義](https://learn.microsoft.com/en-us/azure/templates/microsoft.cognitiveservices/2025-06-01/accounts/projects)でも別の属性です。

真偽値は肯定形で命名します。`network.bgpRoutePropagation`配下の各値はtrueで経路を伝搬し、falseで伝搬しません。

#### ネットワークの通信機能（network）

| 設定項目 | 標準値 | 意味 |
| --- | --- | --- |
| `network.bgpRoutePropagation.agw` | `true` | AppGW用RTへGateway由来経路を伝搬。true=伝搬する、false=しない。 |
| `network.bgpRoutePropagation.apim` | `true` | APIM用RTのルート伝搬。 |
| `network.bgpRoutePropagation.management` | `true` | 管理VM用RTのルート伝搬。デフォルトルートのnext hopやピアリングとは別。 |
| `network.appGatewayZones` | テスト：`[]`／本番：`["1","2","3"]` | AppGW用公開IPの可用性ゾーン番号。gateway.zonesと一致させる。[]は番号の明示なし。 |
| `network.dnsTtl` | `300` | 作成するPrivate DNSレコードのTTL、秒。1〜86400。 |

NSGは全サブネットの通信要件に対応する固定の初期集合をBicepで作成し、送信元・宛先には構築したサブネットCIDRと割当IPを組み込みます。AppGWは公開TCP80／443を別々に許可し、同じVNetのFirewall公開IP `/32`からのHTTPSも独立した規則にします。gRPC専用のTCP8443入口は作成しません。`network.clientAddressPrefixes`・`certificatePublicProbeEnabled`・`dnsServers`・`operatorAddressPrefixes`・`operatorProtocol`・`operatorPorts`・`ntpAddressPrefixes`・`ntpFqdns`・`managementAdditionalSecurityRules`・`apimKeyVaultAccessEnabled`と`network.firewall.dnsServers`は入力項目から削除しました。以前のJSONから取り除いてください。管理VMのSSH許可は初期不作成とし、APIM→Key Vaultの許可は常設します。企業のIP制限・ハブ接続・独自DNSに合わせる変更は構築後にAzureポータルで行い、再構築・再適用時には初期集合との差を再確認します。[AppGWの固定NSG規則](../../operations/network-security-groups.md#appgwagw)

ルート伝搬はGateway由来経路だけを対象とし、UDR・ピアリング・デフォルトルート変更とは別です。独立VNetには伝搬元Gatewayがないため、既定trueでもGateway由来の経路はありません。Azureの否定形属性へはBicep内部で反転します。[ルート伝搬](https://learn.microsoft.com/en-us/azure/virtual-network/manage-route-table)

AppGWと公開IPのゾーン指定は両方同じ値にします。[]はゾーン冗長性の無効化ではありません。[AppGW v2の可用性ゾーン](https://learn.microsoft.com/en-us/azure/reliability/reliability-application-gateway-v2)

企業ハブへの接続・DNS・中央ゾーン・出口・ルート伝搬の変更は[ハブ＆スポークの参考資料](../../setup/10-hub-spoke.md)で確認します。

#### Firewallの機能設定（network.firewall）

規則の作成有無・任意追加規則・追加FQDNは公開設定にしません。廃止した`apimAdditionalNetworkRules`・`managementAdditionalNetworkRules`・`apimAdditionalApplicationRules`・`managementAdditionalApplicationRules`・`apimAllowedFqdns`・`managementAllowedFqdns`は、config／paramsの空値も拒否します。[固定初期集合と内部宛先生成](parameters.md#ネットワーク監査の接続設定)を確認してください。

APIMの承認済み証明書到達性と正規API宛先、管理VMの構築先と、証明書用の専用ACA環境からACME・DNS管理API・Private Endpointへ接続する規則を組み込みます。API生成先0でもAPIM規則を空宛先で送信せず、既存証明書規則と重複する基底を維持します。証明書directory、API正本、アプリ登録と以下のSKU／IDPSモード等は維持します。独立した`snatPrivateRanges`・`idpsPrivateRanges`・`bypassTrafficSettings`入力は空値でも拒否します。ネットワーク規則のSNAT除外は`IANAPrivateRanges`と構築VNetの`addressSpaces`です。Premium IDPSの内部分類はRFC1918と構築VNetのCIDRからAppGWサブネットを除いて生成し、初期bypassは空に固定します。

| 設定項目 | 標準値 | 意味 |
| --- | --- | --- |
| `network.firewall.sku` | 全環境：`"Premium"` | TLS検査とIDPSを有効にするためPremiumを使う。 |
| `network.firewall.threatIntelMode` | `"Alert"` | 脅威情報のAlert=検知、Deny=遮断、Off=無効。 |
| `network.firewall.idpsMode` | 全環境：`"Deny"` | PortalではAlert and Deny。検知した通信を遮断する。 |
| `network.firewall.zones` | 全環境：`[]` | Firewallとその公開IPのゾーン指定を省略し、Azureに割り当てを任せる。明示指定する場合は両方に同じゾーンを渡す。 |

Firewallのゾーン対応と新規配備の容量条件は構築するリージョンで確認します。 [Firewallの容量制約](https://learn.microsoft.com/en-us/troubleshoot/azure/firewall/firewall-known-issues#current-capacity-constraints) · [可用性ゾーンの設定](https://learn.microsoft.com/en-us/azure/firewall/deploy-availability-zone-powershell)
| `network.firewall.signatureOverrides` | `[]` | Premium IDPSの署名ごとの設定。 |

#### サービス（services）

| 設定項目 | 標準値 | 意味 |
| --- | --- | --- |
| `services.apimSkuName` | テスト：`"Developer"`／本番：`"Premium"` | APIM classicのDeveloper/Premium。環境名による切替なし。 |
| `services.apimCapacity` | `1` | APIMユニット数。Developerは1のみ。 |
| `services.apiCenterSkuName` | `"Free"` | API CenterのFree/Standard。基本Portal検索・閲覧はFreeで利用可能。Standardの料金・APIM連携特典を確認し、採用時はこの値も変更。 |
| `services.vaultSkuName` | `"standard"` | Key Vaultのstandard/premium。 |
| `services.vaultRetentionDays` | テスト：`7`／本番：`90` | Key Vault論理削除の保持日数、7〜90。 |
| `services.vaultPurgeProtection` | テスト：`false`／本番：`true` | Key Vaultの完全削除保護。trueは保護。撤去でも保護を回避しない。 |
| `services.enableApiCenterIntegration` | `true` | APIM→API Centerの連携を構成。PortalのGUI設定・公開は別工程。 |
| `services.catalogEnvironmentKind` | テスト：`"testing"`／本番：`"production"` | API Centerの環境区分。development/production/staging/testing。 |
| `services.catalogLifecycleStage` | テスト：`"testing"`／本番：`"production"` | API Centerのライフサイクル区分。候補はSchema参照。 |
| `services.projectDisplayName` | `"core-platform"` | 初期Foundry Projectの表示名。core-platformは役割が分かる名前として採用。実リソース名proj-<環境>-<製品>とは別。 |
| `services.projectDescription` | `"Core platform project for shared model, agent, and tool capabilities."` | 初期Projectの説明。 |
| `services.speechEnabled` | `true` | APIMのMIへ対象FoundryのSpeech権限を付与。音声モデル全体の配備フラグではない。 |
| `services.grantApimFoundryModelsAccess` | `true` | APIMへ対象Foundryの非OpenAIモデル用の組み込み利用権限を付与。 |
| `services.grantApimSecretAccess` | テスト：`true`／本番：`false` | バックエンド資格情報のためAPIMへKey Vaultのsecret読取権限を付与。常設L7入口証明等に必要なKV読取権限は、この指定と別にAPIM自身へ付与。通信許可とは別。 |
| `services.modelDeployments` | [初期28モデル](../gateway/api-contracts.md#初期28モデルとapioperationの対応)（固定版・SKUはconfig参照） | 単一FoundryへBicepで配備。32件の上限内で選定し、実カタログと利用枠を配備前に照合。 |

初回は32件の上限のうち28件を使用します。モデル追加時は、配備数の上限・対象モデルの提供条件・利用枠を確認します。
TTSはAzure Speech、文書OCRはAzure Document Intelligenceを使います。
文書解析Gatewayは提供元のモデルIDを受け取り、モデルの利用可否を提供元へ任せます。Portal画面の標準選択肢は`prebuilt-read`・`prebuilt-layout`の2種類を維持し、Gatewayの受付とは分けて扱います。これらはDocument Intelligenceのサービスモデルであり、Foundryモデルの追加配備ではありません。Speech・Language・モデル配備の区分は
[モデルと専用サービスの整理](../gateway/foundry-model-catalog.md#モデル配備とazureの専用サービス)を参照してください。

#### 構築後にClaudeを手動追加する場合

Claudeのモデル・APIはAzureポータルで追加します。配備条件、APIM、WAF、監査は [Claudeの手動公開手順](../../operations/claude-messages.md#構築後にclaudeをguiで追加する) を参照してください。

#### 公開入口（gateway）

共通認可の購読キーヘッダーは資材固定の`api-key`です。Bearerは認証時に`Gateway.Invoke`を検証します。例外の購読キーはその購読と利用範囲を検証します。Graphの本人接続にはBearerでOBO、購読キーでCMを利用します。[共通認証の設定](../../setup/07-params.md#gateway全体の認証とapiごとの認可)を参照してください。

公開入口はAppGWのHTTP／HTTPSです。AppGWが`X-Core-L7-Ingress`を上書きし、APIMの共通ポリシーが入口証明を常設で検査します。APIMへ直接届く要求の拒否は実機試験でも確認します。

WAFの用途別ポリシーとデータ欄の除外は[WAFの公開仕様](../../testing/waf-compatibility.md#用途別のポリシー構成)で確認します。Chatの画像URL、ResponsesのPDF入力などの条件は`apps/api-gateway/waf-data-exclusions.json`を正本とし、APIの入力profileから別のポリシーや除外を自動追加しません。


APIMはシステム割り当てマネージドIDで業務用Key VaultのNamed Valueを解決します。業務用Vaultは`publicNetworkAccess: Disabled`、`defaultAction: Deny`、`bypass: AzureServices`を設定し、APIM管理面の信頼済みサービス例外と対象VaultのRBACを組み合わせます。AppGWは専用のユーザー割り当てIDで公開TLS用Vaultの対象証明書を参照します。 [Vaultと証明書の受入確認](../../testing/network-security-validation.md#公開tls用key-vaultの分離) · [APIMの管理IDとKey Vault](https://learn.microsoft.com/azure/api-management/api-management-howto-use-managed-service-identity)

| 設定項目 | 標準値 | 意味 |
| --- | --- | --- |
| `gateway.skuName` | `"WAF_v2"` | AppGWのWAF_v2/Standard_v2。 |
| `gateway.autoscaleMinCapacity` | テスト：`0`／本番：`2` | 自動スケール最小値。0でも固定費があり無課金停止ではない。 |
| `gateway.autoscaleMaxCapacity` | テスト：`2`／本番：`5` | 自動スケール最大値。 |
| `gateway.zones` | テスト：`[]`／本番：`["1","2","3"]` | AppGW本体の可用性ゾーン番号。公開IP側のnetwork.appGatewayZonesと同じ値。 |
| `gateway.wafMode` | `"Prevention"` | Prevention=遮断、Detection=検知。 |
| `gateway.wafCustomRules` | `[]` | 追加WAF custom rule配列。 |
| `gateway.wafExclusions` | `[]` | WAF検査除外配列。 |
| `gateway.backendRequestTimeoutSeconds` | `600` | AppGWのバックエンド応答待ち秒、1〜86400。 |
| `gateway.wafMaxRequestBodySizeInKb` | `2000` | base・認証等の本文上限、KiB。管理対象AIパスではサイズ強制を解除。 |
| `gateway.wafRequestBodyInspectLimitInKb` | `2000` | base・認証等の本文検査枠、KiB。AIパスは専用設定で2000 KiB。 |
| `gateway.wafFileUploadLimitInMb` | `4` | base・認証等のmultipartファイル上限、MiB。管理対象AIパスでは強制しない。 |
| `gateway.realtimeControlEnabled` | `true` | Realtimeの所有者確認・停止制御用の鍵等を準備。 |

AppGWは公開側のHTTP/2を受け付け、内部APIMとPortalへはFirewallのTLS検査を経由してHTTPS 443で転送します。APIMの`Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2`は文字列`"false"`に固定します。

API公開前には、通常HTTP用の配備出力`ingressSecrets`と、常設L7入口証明のKey Vault参照・Named Value・AppGW rewrite・APIM検査が整合することを確認します。不一致の基盤へは公開せず、Coreの入口設備を先に揃えます。Realtime・文書ジョブの所有者署名鍵の準備条件は維持します。

#### 共通認証・OAuth・Portal初期設定


| config側の設定 | test／production初期値・扱い |
| --- | --- |
| `gateway.authentication.entra.userLimits` | requestRate=1000／60秒、tokensPerMinute=null、tokenQuota=null |
| `gateway.authentication.credentialLogin` | providerNames=`["graph-delegated"]`と共通セッション値。明示transactionLifetimeSeconds=600秒を保持 |
| `credentialProviders.graphDelegated` | displayName=`Delegated Graph connection`、定義済みのGraph委任scope、pool.count=12／capacity=8000 |
| `credentialProviders.additional[]` | name・displayName・identityProvider・scopesとプロトコル別定義。params側登録と1対1照合 |

Graphと本人接続窓口のClient ID・秘密ファイル参照はparamsに設定します。標準Graph名・プロトコル・tenant・Graph URL・本人ID契約は資材固定です。追加の接続設定は、対応するプロバイダー登録と合わせて確認します。

APIの初回公開は標準定義を使います。Azureポータルから業務APIを追加する手順は [運用手順](../../operations/README.md) を参照してください。

#### Linux管理VM（management）

管理VMはUbuntu 24.04 LTS amd64＋SSH公開鍵認証です。現在の自動構築が対応するイメージはCanonical / ubuntu-24_04-lts / serverで、別ディストリビューション・custom imageを指定して動作保証するものではありません。SSHのパスワード認証・RDPは構成しません。初期VMのSerial Console用ローカル認証は[配備工程](../../setup/08-provision-core.md)で別に準備します。OSログ収集はLinux AMA/DCRで行います。公開TLSの取得・更新は固定版PowerShellを含むイメージで動く、専用ACAのJobが担当します。

管理VMはUbuntu 24.04のChronyでAzureホストのPHCを同期元にします。ゲスト設定、管理VM側で観測する時刻とAPIMの外部NTPは [時刻同期の設計](../architecture/platform-communications.md#管理vmのazureホスト時刻とapimの外部ntp) を参照してください。

認証監査はSSH・PAMの認証/account/sessionを保持します。コマンド本文を含むsudoの`COMMAND=`行とcron本文は、AMA転送前のrsyslog規則でイベントごと収集対象から除外し、マスク済み行は新造しません。DCR側の追加マスクは維持します。rsyslogの構文検査とサービス操作は失敗段階を分けて返します。

| 設定項目 | 標準値 | 意味 |
| --- | --- | --- |
| `management.vmSize` | `"Standard_D2as_v6"` | VMサイズ。リージョン・利用枠・固定イメージとの適合を配備前に確認。 |
| `management.diskControllerType` | `"NVMe"` | SCSI/NVMe。D2as_v6はNVMe。 |
| `management.imageReference.publisher` | `"Canonical"` | Linuxイメージの発行者。 |
| `management.imageReference.offer` | `"ubuntu-24_04-lts"` | Linuxイメージの製品。 |
| `management.imageReference.sku` | `"server"` | LinuxイメージのSKU。 |
| `management.imageReference.version` | `"24.04.202610160"` | 固定版。latest不可。 |
| `management.imageReference.id` | 省略→未使用（Marketplaceの固定イメージを指定） | 承認済みイメージのResource ID形式。上記4項目と排他。ただし現時点の自動構築ではmanual-checkとなり配備を止める。 |
| `management.osDiskSizeGB` | `128` | OSディスク容量、GiB。現資材は128〜4095。 |
| `management.osDiskSku` | `"StandardSSD_LRS"` | OSディスクのStandard_LRS/StandardSSD_LRS/Premium_LRS。 |
| `management.enableAcceleratedNetworking` | `true` | 高速ネットワーク。サイズ・イメージの対応を検査。 |
| `management.encryptionAtHost` | `true` | ホストでの暗号化。 |
| `management.patchAssessmentMode` | テスト：`"ImageDefault"`／本番：`"AutomaticByPlatform"` | Linux更新の評価。ImageDefault/AutomaticByPlatform。 |
| `management.patchMode` | テスト：`"ImageDefault"`／本番：`"AutomaticByPlatform"` | Linux更新の適用。ImageDefault/AutomaticByPlatform。 |

#### 監視・監査（monitoring）

FoundryのApplication Insights接続は、`AppInsights` category専用の`ProjectManagedIdentity`認証を使います。`AAD`という汎用名は、この接続種別ではサービスに拒否されます。接続先のARM IDに加え、metadataには`ApiType`・`ResourceId`・`ApplicationInsightsConnectionString`の3項目を登録します。接続文字列は先に作成したApplication Insightsから取得し、secureなモジュール引数で渡します。これは送信先を特定する情報であり、connectionの認証用credentialは作りません。[Microsoft公式Bicepの接続例](https://github.com/microsoft-foundry/foundry-samples/blob/main/samples/python/foundry-autopilot-agent/infra/modules/project.bicep)

`useWorkspaceManagedIdentity:true`はARMのフィールド名で、ここではFoundry projectのMIを使う接続です。資材の`monitoring-bindings.bicep`は、そのproject MIへ対象Application Insightsの組み込み`Monitoring Metrics Publisher`を明示的に付与します。接続の作成と実際のtrace送信は別判定です。[公式のEntra trace送信要件](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-ingestion-entra-authentication)

| 設定項目 | 標準値 | 意味 |
| --- | --- | --- |
| `monitoring.retentionInDays` | `90` | Log Analytics保持日数、90〜730。 |
| `monitoring.workspaceDailyQuotaGb` | `-1` | 日次取り込み上限、GB。-1は上限なし。 |
| `monitoring.auditStorageSku` | テスト：`"Standard_LRS"`／本番：`"Standard_GRS"` | 監査Storageの冗長化SKU。 |
| `monitoring.auditImmutabilityMode` | テスト：`"Disabled"`／本番：`"Locked"` | Disabled/Unlocked/Locked。Lockedは保持期間中解除できず、撤去が停止する。 |
| `monitoring.auditRetentionDays` | `2557` | 不変保管で削除を保護する日数。ファイルの自動削除期限ではない。Disabled時はポリシー未適用。 |
| `monitoring.appInsightsIngestionWarningGb` | `80` | 日次取り込み量の警告値。課金停止上限ではない。 |
| `monitoring.enableLogAlerts` | テスト：`true`／本番：`false` | ログベースのアラートを作成。 |

#### 公開証明書（certificate）

| 設定項目 | 標準値 | 意味 |
| --- | --- | --- |
| `certificate.mode` | `"dnsPlugin"` | Azure Public DNSとUAMIを使い、専用ACAの公開TLS JobでDNS-01による取得・更新を行う。dnsPlugin固定。 |
| `certificate.certificateName` | `"tls-public"` | Key Vault内の証明書名。 |
| `certificate.directory` | `"production"` | production/staging。staging証明書は公開ブラウザーで信頼されない。 |
| `certificate.renewBeforeDays` | `30` | 期限の何日前から更新するか。 |
| `certificate.checkEveryHours` | `12` | 更新要否を確認する間隔、時間。 |
| `certificate.moduleVersion` | `"4.34.0"` | 固定するPosh-ACMEの版。 |
| `certificate.dnsSleepSeconds` | `60` | DNS反映待ち秒。 |
| `certificate.validationTimeoutSeconds` | `120` | DNS検証待ちの上限秒。 |
| `certificate.minimumValidDays` | `7` | 取り込む証明書に必要な残日数。renewBeforeDays以下。 |

#### モデル配列の要素（services.modelDeployments[]）

この表の短い名前はconfigの`services.modelDeployments[]`配下です。[初期モデル](../gateway/api-contracts.md#初期28モデルとapioperationの対応)の28モデルの配備設定は、テスト・本番configが正本です。[標準API定義](../../../apps/api-gateway/apis/)は19 APIを1ファイルずつ管理し、モデル配備の設定を重複して持ちません。API側には、そのAPIで呼び出しを許可するモデル名・操作・要求上限を定義します。外部サンプルAPIやTools/Hosted Agent用bindingsは含みません。版・SKU・利用枠名の一覧はconfigを参照してください。

| 設定項目 | 標準値 | 意味 |
| --- | --- | --- |
| `name` | モデルごとの正式名。Decisionのみ`decision-1` | 実配備名。原則modelNameと一致させ、Microsoft形式だけ予約語回避の別名を許す。英数字開始、英数字・`_`・`.`・`-`の1〜64文字。 |
| `modelName` | モデルごとの正式名 | カタログ上の正式モデル名。 |
| `modelFormat` | OpenAI／Cohere／Black Forest Labs／Microsoft | 提供元の正式な大文字小文字。Claudeはazdの構築対象外。 |
| `modelVersion` | configに記載した固定版 | 評価対象の版。配備前にライフサイクルと退役日も確認する。 |
| `skuName` | 主にGlobalStandard。WhisperはStandard、DecisionはDataZoneStandard | モデル・リージョンが対応するPAYG配備SKU。 |
| `capacity` | GPT-6.1 Solは50、Lunaは5、他は1 | 初期割当。認定最小値ではなく、TPM／RPM換算もモデル・SKUごとに異なる。 |
| `versionUpgradeOption` | NoAutoUpgrade | 自動版更新を行わない。 |
| `quotaUsageName` | 各モデルの実metadataに対応する値を明記 | 同じ利用枠プールの配備は合算して検査。 |

## 監査ログの設定

標準configの `monitoring` でLog Analyticsの保持日数、監査Storageの冗長性、保持日数とWORMを設定します。APIの監査メタデータとFoundryの本文トレースの収集範囲は[監査設計](../audit/audit-log-design.md)を参照します。

## FirewallのTLS検査を準備する

1. Azure Portalの **Managed identities** で対象Azureテナントに専用のユーザー割り当てマネージドIDを作ります。DNSやアプリ用IDと共用せず、配備主体にこのIDをFirewall Policyへ割り当てる権限を付与します。
2. **Key vaults** でこの中間CA専用のVaultを用意します。**Access configuration** ではVault access policyを選び、**Access policies** で専用IDにSecretのGet／Listを付与します。**Networking** では接続元を制限し、Azure Firewallの信頼済みサービス経由の取得を許可します。インポート作業に使う接続元は必要な時間だけ許可します。[Firewall PremiumのKey Vault要件](https://learn.microsoft.com/en-us/azure/firewall/premium-certificates#azure-key-vault)、[Key Vaultのネットワーク制御](https://learn.microsoft.com/en-us/azure/key-vault/general/network-security)
3. 本番は組織PKI、テストは分離した検証用PKIで中間CAを準備します。公式例はRSA 4096ビット、エクスポート可能な鍵、クリティカルな`KeyUsage`の`keyCertSign`、クリティカルな`BasicConstraints`の`CA:TRUE`と`pathlen:1`を使います。有効期限まで1年以上ある中間CAを、当該中間証明書と秘密鍵だけを含むパスワードなしPFXにします。公開ルートで中間証明書を検証し、ルートの秘密鍵はPKI側で保護します。[中間CA要件とテスト用証明書の作成例](https://learn.microsoft.com/en-us/azure/firewall/premium-certificates#intermediate-ca-certificate-requirements)
4. Vaultの **Certificates** で中間CAのPFXをインポートします。有効な証明書バージョンに対応する **Secret Identifier** を取得します。識別子が`/secrets/<name>/<version>`になっていることを確認します。**Certificate Identifier** を指定すると失敗します。
5. `params.local.json`の`firewallTlsInspection`に専用IDの`managedIdentityResourceId`、バージョン付き`intermediateCaSecretId`、公開ルートへの`rootCaCertificateFile`を設定します。初期準備が生成した参照は`./secrets/firewall-root-ca.cer`です。ルートは単一のX.509公開証明書（DERまたはPEM）を保護ファイルに保存します。構築資材は参照と公開証明書を渡します。
6. [新規テスト環境](../../setup/08-provision-core.md)で、Firewallが指定バージョンを取得し、APIMのApp Gatewayプローブが正常になり、Portal本体配備後にはPortalのプローブも正常になることを確認します。独立したテストでは、存在しないバージョン、無許可の専用ID、対応しない有効なルートを使った場合に検査が成立しないことを確認します。証明書検証を無効化せず、正しい参照へ戻して正常化を確認します。Portal認証の実試用前に監査ログの[クエリ保護](../../testing/log-privacy.md#firewallのtls検査ログ)も確認します。[App GatewayのバックエンドTLS](https://learn.microsoft.com/en-us/azure/application-gateway/ssl-overview)

中間CA更新時はKey Vaultの新しいバージョンを明示し、チェーンや発行元が変わる場合はApp Gatewayの信頼も含めて切替を計画します。プローブと業務通信、IDPSログをテスト環境で確認してから本番へ適用します。[既知のTLS検査制約](https://learn.microsoft.com/en-us/troubleshoot/azure/firewall/firewall-known-issues)

## configを変更する場合の確認

- 選んだ正本をparamsの`configFile`から参照している。
- 選択モデル・SKU・版・capacityと、APIの公開許可モデルが一致している。
- 監査保持・削除保護を、構築後の運用／撤去方針に合わせて確認した。
- 同じparamsで`configuration.py validate`が成功する。

[初回構築の手順に戻る](../../../README.md#新規クローンから構築する)
