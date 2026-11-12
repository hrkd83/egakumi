# 5. configの調整・リファレンス

前：[4. params](04-params.md) · [README](../../README.md) · 次：[6. azd upでの構築](06-deploy.md)

## このページのゴール

導入する標準構成を選び、必要な場合だけSKU・モデル・保護機能を調整します。企業固有のID・IP・公開名をconfigへ移しません。この段階ではAzure設備を作成しません。

## 5-1. 標準構成を選ぶ

| 正本 | 主な初期構成 | 選ぶときの確認 |
| --- | --- | --- |
| `config/test.json` | Firewall Standard、検証向けのSKU・保護設定 | 検証後の撤去を想定した費用・保持設定か |
| `config/production.json` | Firewall Premium＋IDPS Alert、AppGW／Public IPの3ゾーン | 対象地域のSKU・ゾーン、可用性・監査保持・削除保護の運用に合うか |

第4章のparamsの`configFile`を、選ぶ正本への相対パスにします。`environmentName`を変えてもSKU・保持・保護は自動で切り替わりません。configのexampleやlocalコピーは作らず、必要な変更は選んだ正本へ記録します。

## 5-2. 必要な項目だけを調整する

1. FirewallのSKU・IDPS、AppGW／APIM／VM等のSKUとゾーンを確認する。StandardでIDPSを有効にしない。
2. `services.modelDeployments`で配備するモデル・版・SKU・capacityを選ぶ。paramsのAPIごとの許可モデルと対応させる。
3. 音声等の利用機能、WAF上限、回数制限、監査保持、削除保護、証明書更新の動作を確認する。gRPCの共通入口は公開8443／APIM443で常設する。
4. 同じparamsでローカル検査し、整合を確認する。

```sh
python3 scripts/deployment/helpers/configuration.py validate --config "$DEPLOY_PARAMS_FILE"
```

公開configの`services.modelDeployments`は初期セットです。すべての導入先に、そのモデルの提供地域・利用枠・購入資格があるとは限りません。モデルのカタログ、退役日、SKU、必要TPM／RPMと料金を確認します。capacityの単位・換算はモデルとSKUごとに異なります。APIを公開するときは、削除したモデルを許可一覧に残さず、配備名・公開操作と同時に揃えます。

Claudeはazdの構築対象外です。専用の配備・Marketplace確認・購入主体情報・規約同意フラグの処理はありません。`services.claude`やClaudeのモデル配備を構築用config・paramsへ指定すると、Azureへの変更前に入力検査で停止します。構築後にGUIで手動追加します。

## 公開証明書の設定

証明書はAzure Public DNSのDNS-01認証で自動発行・更新します。`certificate.mode`は`dnsPlugin`、paramsの`certificate.plugin`は`Azure`を使用します。DNS用UAMIと権限は[公開DNSの準備](03-public-dns.md)で確認します。

## configリファレンス

Schema：[標準config](../../schemas/options.schema.json)／[合成設定](../../schemas/configuration.schema.json)。下表は資材の初期値と動作で、導入先のtenant・domain・IPを指定する欄ではありません。


### 標準値を変える場合だけ確認する項目（config）

以下はconfig側のSKU・ゾーン・ポート・モデル配備・通信と保護機能の標準設定です。リージョン・タグ・IP計画・許可元・DNS・追加接続先・権限は前節のparamsへ記載します。API・権限の要素形式は[paramsの記述形式](04-params.md#api権限の記述形式)で説明します。`environmentName`は命名用です。test/dev/prodという文字列でSKU・保持・保護を切り替えません。

`[]`は空配列、`{}`は空オブジェクトです。空配列の意味は項目ごとに異なります。相対ファイル参照は**それを記載したJSONのフォルダー基準**で解決し、2ファイルを合成しても参照先を変えません。両ファイルの`$schema`はエディター補助、`$comment`は説明で、Azureへ送信しません。

初期Projectの表示名は`core-platform`、実リソース名は`proj-<環境>-<製品>`です。表示名とリソース名は[MicrosoftのProject定義](https://learn.microsoft.com/en-us/azure/templates/microsoft.cognitiveservices/2025-06-01/accounts/projects)でも別の属性です。

真偽値は肯定形で命名します。`network.bgpRoutePropagation`配下の各値はtrueで経路を伝搬し、falseで伝搬しません。

#### ネットワークの通信機能（network）

| 設定項目 | 標準値 | 意味 |
| --- | --- | --- |
| `network.operatorProtocol` | `"Tcp"` | 管理VMへ許可するプロトコル。Tcp/Udp/*。 |
| `network.operatorPorts` | `["22"]` | 管理VMへ許可する宛先ポート。Linux SSHは22。 |
| `network.bgpRoutePropagation.agw` | `true` | AppGW用RTへGateway由来経路を伝搬。true=伝搬する、false=しない。 |
| `network.bgpRoutePropagation.apim` | `true` | APIM用RTのルート伝搬。 |
| `network.bgpRoutePropagation.management` | `true` | 管理VM用RTのルート伝搬。デフォルトルートのnext hopやピアリングとは別。 |
| `network.appGatewayZones` | テスト：`[]`／本番：`["1","2","3"]` | AppGW用公開IPの可用性ゾーン番号。gateway.zonesと一致させる。[]は番号の明示なし。 |
| `network.apimKeyVaultAccessEnabled` | `true` | APIM→Key Vault PEの通信許可。常設gRPCの証明書・入口証明のためtrueが必須。Azure RBACは別設定。 |
| `network.certificatePublicProbeEnabled` | `true` | この環境のFW公開IPからAppGW:443を通す規則。実際のHTTP／TLS／API検査の実行は別。 |
| `network.dnsTtl` | `300` | 作成するPrivate DNSレコードのTTL、秒。1〜86400。 |

ルート伝搬はGateway由来経路だけを対象とし、UDR・ピアリング・デフォルトルート変更とは別です。独立VNetには伝搬元Gatewayがないため、既定trueでもGateway由来の経路はありません。Azureの否定形属性へはBicep内部で反転します。[ルート伝搬](https://learn.microsoft.com/en-us/azure/virtual-network/manage-route-table)

AppGWと公開IPのゾーン指定は両方同じ値にします。[]はゾーン冗長性の無効化ではありません。[AppGW v2の可用性ゾーン](https://learn.microsoft.com/en-us/azure/reliability/reliability-application-gateway-v2)

企業ハブへの接続・DNS・中央ゾーン・出口・ルート伝搬の変更は[ハブ＆スポークの参考資料](07-hub-spoke.md)で確認します。

#### Firewallの機能設定（network.firewall）

| 設定項目 | 標準値 | 意味 |
| --- | --- | --- |
| `network.firewall.sku` | テスト：`"Standard"`／本番：`"Premium"` | Standard/Premium。IDPSはPremiumのみ。省略値はPremiumで、安価な検証はStandardを明示。 |
| `network.firewall.threatIntelMode` | `"Alert"` | 脅威情報のAlert=検知、Deny=遮断、Off=無効。 |
| `network.firewall.idpsMode` | テスト：`"Off"`／本番：`"Alert"` | PremiumのIDPSはAlert/Deny。Standardの見本はOffを明記し、Bicepでは対応しないIDPS設定自体を作らない。 |
| `network.firewall.zones` | テスト：`[]`／本番：`["1","2","3"]` | Firewallと公開IPの可用性ゾーン。 |
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
| `services.grantApimSecretAccess` | テスト：`true`／本番：`false` | バックエンド資格情報のためAPIMへKey Vaultのsecret読取権限を付与。常設gRPCの証明書・入口証明等に必要なKV読取権限は、この指定と別にAPIM自身へ付与。通信許可とは別。 |
| `services.modelDeployments` | [初期28モデル](../reference/platform.md#初期モデルと公開api)（固定版・SKUはconfig参照） | 単一FoundryへBicepで配備。32件の上限内で選定し、実カタログと利用枠を配備前に照合。 |

初回は32件の上限のうち28件を使用します。モデル追加時は、配備数の上限・対象モデルの提供条件・利用枠を確認します。
TTSはAzure Speech、文書OCRはAzure Document Intelligenceを使います。
文書解析の`prebuilt-read`・`prebuilt-layout`は標準APIに含み、Foundryモデルの追加配備ではありません。Speech・Language・モデル配備の区分は
[モデルと専用サービスの整理](../reference/foundry-model-catalog.md#モデル配備とazureの専用サービス)を参照してください。

#### 構築後にClaudeを手動追加する場合

Claudeは標準の28モデル・19 APIに含めません。Claude用のparamsや有効化・規約同意フラグ、設定生成コマンドの`--claude`オプションはサポートしません。

Core構築後、AzureポータルのGUIからモデルを配備し、APIMにもAPIを手動登録する運用です。購入条件・利用枠・接続先と認証方式を確認し、[手動追加時の公開制御と再配置の注意点](../operations.md#構築後にclaudeをguiで追加する)に従います。

`azd up`は基盤構築後にGateway工程でAPI定義・バックエンドを登録し、続いてPortalを配備します。基盤だけの構築・更新には`azd provision`を使います。構築済み環境はGUIや専用コマンドでも更新でき、GUIでのAPI追加にCoreの一覧への転記は不要です。

#### 公開入口（gateway）

共通認可の`gateway.authentication.subscriptionKeyHeader`は企業固有のparamsで設定します。全体ポリシーが購読キーと利用範囲を検証し、API側が操作・モデル・入力の制約と下流認証を設定します。本人接続が必要なAPIはCMを利用します。[共通認証の設定](04-params.md#gateway全体の認証とapiごとの認可)を参照してください。

native gRPC用のTCP入口は共通仕様として常設し、その経路は通常のHTTPS本文WAF検査の対象外です。他APIの迂回防止は実機試験で確認します。

WAFの用途別ポリシーとデータ欄の除外は[WAFの公開仕様](../testing/waf-compatibility.md#用途別のポリシー構成)で確認します。Chatの画像URL、ResponsesのPDF入力などの条件は`apps/gateway/waf-data-exclusions.json`を正本とし、APIの入力profileから別のポリシーや除外を自動追加しません。


APIMのTLS証明書と入口／RealtimeのKey Vault参照は、APIM自身のシステム割り当てマネージドIDを使い、対象KVだけに組み込みのKey Vault Secrets Userを付与します。[KV firewall有効時にAPIMのユーザー割り当てIDを使用できない制約](https://learn.microsoft.com/azure/api-management/api-management-howto-use-managed-service-identity)に合わせた構成です。AppGWは証明書用のユーザー割り当てIDを引き続き使います。IDの権限とPE経由の到達性は別々に実機確認します。

APIMが参照するKVは、`publicNetworkAccess: Disabled`と`defaultAction: Deny`を維持し、`bypass: AzureServices`を管理処理用の例外として設定します。APIMのカスタムドメイン証明書取得には、VNetからKVへ到達できる場合でもこの例外が必要です。named valueも管理面の機能ですが、ネットワーク到達性があれば例外なしの接続を選べます。この資材はgRPCの共通入口を常設し、同じKVから証明書と入口証明を参照するため、この管理用例外を有効化します。一般利用者や任意のAzureアプリへの公開許可ではなく、trusted serviceにも対象KVのRBACを要求します。通常のAPIMバックエンド呼出し・管理VM・AppGWの証明書読取はPE経由で確認します。[APIM管理面と通常のAPI通信の区別](https://learn.microsoft.com/en-us/azure/api-management/breaking-changes/trusted-service-connectivity-retirement-march-2026#scenarios-that-are-not-affected-by-the-breaking-change)、[公開アクセス無効時も残るtrusted services例外](https://learn.microsoft.com/en-us/azure/key-vault/general/network-security#key-vault-firewall-enabled-trusted-services-only)

| 設定項目 | 標準値 | 意味 |
| --- | --- | --- |
| `gateway.skuName` | `"WAF_v2"` | AppGWのWAF_v2/Standard_v2。 |
| `gateway.autoscaleMinCapacity` | テスト：`0`／本番：`2` | 自動スケール最小値。0でも固定費があり無課金停止ではない。 |
| `gateway.autoscaleMaxCapacity` | テスト：`2`／本番：`5` | 自動スケール最大値。 |
| `gateway.zones` | テスト：`[]`／本番：`["1","2","3"]` | AppGW本体の可用性ゾーン番号。公開IP側のnetwork.appGatewayZonesと同じ値。 |
| `gateway.wafMode` | `"Prevention"` | Prevention=遮断、Detection=検知。 |
| `gateway.wafCustomRules` | `[]` | 追加WAF custom rule配列。 |
| `gateway.wafExclusions` | `[]` | WAF検査除外配列。 |
| `gateway.backendRequestTimeoutSeconds` | `600` | AppGWのバックエンド応答待ち秒、1〜86400。今回の緩和で既定値は変更しない。 |
| `gateway.wafMaxRequestBodySizeInKb` | `2000` | base・認証等の本文上限、KiB。管理対象AIパスではサイズ強制を解除。 |
| `gateway.wafRequestBodyInspectLimitInKb` | `2000` | base・認証等の本文検査枠、KiB。AIパスは専用設定で2000 KiB。 |
| `gateway.wafFileUploadLimitInMb` | `4` | base・認証等のmultipartファイル上限、MiB。管理対象AIパスでは強制しない。 |
| `gateway.realtimeControlEnabled` | `true` | Realtimeの所有者確認・停止制御用の鍵等を準備。 |
| `gateway.rateLimits.subscriptionKey.enabled` | `false` | APIM購読ID単位の共通上限を明示的に有効にするか。既定は無制限。 |
| `gateway.rateLimits.subscriptionKey.calls` | `60` | 有効化した場合のみ適用する期間内要求数、整数1〜1,000,000。 |
| `gateway.rateLimits.subscriptionKey.renewalPeriodSeconds` | `60` | 有効化した場合のみ適用する計数期間、整数1〜300秒。 |

gRPCの入口は基盤の共通仕様として、常に有効・公開TCP8443・APIM側TCP443に固定します。configやparamsで無効化・ポート変更する調整項目ではありません。標準configからこれらの指定を除き、構築処理が固定値を補完して既存の配備処理へ渡します。公開ホスト名は従来どおりparamsで指定します。

`gateway.grpc.enabled`・`publicPort`・`backendPort`は入力項目ではありません。config・params・合成設定ファイルで指定すると、固定値と同じ`true`・`8443`・`443`でも構築前に拒否します。公開ホスト名だけをparamsで指定でき、検査後に構築処理が内部用の固定値を生成します。`clientAddressPrefixes`による利用者の接続元制限と、Realtime・文書ジョブの署名鍵の準備条件は別の設定です。

API公開では、基盤の配備出力が共通仕様の公開ホスト名・TCP8443・入口証明の準備状態と一致することを確認します。一致しない基盤へは公開せず、先にCoreを配備してください。入口を常設することと、登録した各gRPCサービスの実通信が検証済みであることは別です。

#### Linux管理VM（management）

管理VMはUbuntu 24.04 LTS amd64＋SSH公開鍵認証です。現在の自動構築が対応するイメージはCanonical / ubuntu-24_04-lts / serverで、別ディストリビューション・custom imageを指定して動作保証するものではありません。パスワード認証・RDPは構成しません。証明書取得・更新は固定版PowerShell 7.6.7をLinuxで動かし、定期実行はsystemd timer、OSログ収集はLinux AMA/DCRで行います。

時刻同期は既存Chronyを優先し、Ubuntu標準の`/etc/chrony/sources.d/90-core-platform.sources`に`network.ntpFqdns`を設定します。既存PHC/refclock・confdir/sourcedir・OS側のsource設定は保持し、未知の企業設定や既定poolを自動削除しません。外部NTPの到達先はNSGとFirewallの許可先でも制限します。timesyncdのみのイメージでは専用drop-inを使い、両方のサービスが無い場合は明示的に失敗します。サービス稼働・構文確認と、実際の同期元・精度の確認は別に行います。[Azure Linuxの時刻同期](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/time-sync)

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
| `certificate.mode` | `"dnsPlugin"` | Azure Public DNSとUAMIを使い、Linux管理VMでDNS-01による取得・更新を行う。dnsPlugin固定。 |
| `certificate.certificateName` | `"tls-public"` | Key Vault内の証明書名。 |
| `certificate.directory` | `"production"` | production/staging。staging証明書は公開ブラウザーで信頼されない。 |
| `certificate.renewBeforeDays` | `30` | 期限の何日前から更新するか。 |
| `certificate.checkEveryHours` | `12` | 更新要否を確認する間隔、時間。 |
| `certificate.moduleVersion` | `"4.34.0"` | 固定するPosh-ACMEの版。 |
| `certificate.dnsSleepSeconds` | `60` | DNS反映待ち秒。 |
| `certificate.validationTimeoutSeconds` | `120` | DNS検証待ちの上限秒。 |
| `certificate.minimumValidDays` | `7` | 取り込む証明書に必要な残日数。renewBeforeDays以下。 |

#### モデル配列の要素（services.modelDeployments[]）

この表の短い名前はconfigの`services.modelDeployments[]`配下です。[初期モデル](../reference/platform.md#初期モデルと公開api)の28モデルの配備設定は、テスト・本番configが正本です。[標準API定義](../../apps/gateway/apim/apis/)は19 APIを1ファイルずつ管理し、モデル配備の設定を重複して持ちません。API側には、そのAPIで呼び出しを許可するモデル名・操作・要求上限を定義します。外部サンプルAPIやTools/Hosted Agent用bindingsは含みません。版・SKU・利用枠名の一覧はconfigを参照してください。

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

## 次へ進む前の確認

- 選んだ正本をparamsの`configFile`から参照している。
- 選択モデル・SKU・版・capacityと、APIの公開許可モデルが一致している。
- 監査保持・削除保護を、構築後の運用／撤去方針に合わせて確認した。
- 同じparamsで`configuration.py validate`が成功する。

次：[6. azd upでの構築](06-deploy.md)
