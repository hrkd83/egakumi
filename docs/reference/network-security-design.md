# NSG設計・通信対応・判断記録

[通信要件](network-communication-requirements.md) · [Firewall設計](firewall-policy-design.md) · [実証方法と検証結果の管理](network-security-validation.md) · [NSG運用一覧](../operations/network-security-groups.md) · [NSGにしない基盤通信](platform-communications.md)

本書は初期構築に採用したNSGの通信対応と判断を記録する。定義の正本は[infra/nsg-rule-priorities.json](../../infra/nsg-rule-priorities.json)であり、5 NSG・カスタム76規則（Allow 70、Deny 6）を構築する。Azureの既定規則30件はこの件数に含めない。`AzureFirewallSubnet`にはNSGを関連付けない。

`FLOW-...`は通信要件の識別子、`POLICY-...`は拒否・既定規則の設計判断の識別子、規則の設計IDは資材の`designRecordId`である。同じ接続の送信・受信に同じFLOW IDを付ける。拒否規則や既定規則は通信の件数として加算しない。

## 初期設計の前提

- 独立VNet、Azure提供DNS、標準外部NTPを使用する。企業DNS、企業IP制限、ハブ＆スポーク等への移行は初期構築後に運用者がPortal等で手動変更する。
- 公開params／configでルールの作成有無、ポート、アドレス種別を切り替えない。サブネットCIDRと構築資源の割当IPを固定規則へ組み込む。
- 同一NSG・同一方向の優先度は120からクライアント要求由来、220からこの基盤固有の内部・保守、320からAzure製品仕様の基盤通信を1ずつ採番する。帯の先頭20番号を空け、終端カスタムDenyは4096とする。
- 名称は`allow/deny-送信元-用途-ingress`、`allow/deny-宛先-用途-egress`を基本とする。300番台の基盤規則はポート・プロトコルを名称から省き、詳細を規則本体で示す。終端Denyは`deny-any-ingress`／`deny-any-egress`とする。
- gRPC利用環境は初期要件から廃止した。8443 listener、専用TCP／TLS passthroughとgRPC専用許可を再作成しない。HTTPS保守は残す。AppGWの入口確認ヘッダー上書きとAPIMの検査は通常HTTP／HTTPS用として常設する。
- 公開HTTPSはAppGWで終端し、AppGW→APIMとAppGW→PortalはHTTP80とする。APIM→Credential BrokerのHTTPS443、PEへのHTTPS443は維持する。NSG許可だけでTLS／認証／認可の成立とは扱わない。
- ACAのmanaged environmentは`internal: true`で内部VIPを持つ。Appの`external: true` ingressはその環境の外から内部VIPへ到達させる設定であり、Internet側の業務入口はAppGWで提供する。PortalのコンテナーtargetPort 3000とBrokerの8000はACA内部の転送先で、表の入口80／443やnode portとは別である。

## アドレス参照と公開入力依存

表中の参照名は正本JSONの意味参照であり、実IPを表す記録ではない。送信元ポートは全規則Any。サービスタグの範囲はAzureが管理し、タグの許可はサービスの利用認可を付与しない。

| 参照 | 資材へ組み込む情報 | 依存・範囲 |
| --- | --- | --- |
| `subnet:agw`／`subnet:apim`／`subnet:aca`／`subnet:management` | 各サブネットCIDR | `network.subnetPrefixes`の対応キー。構築するVNet内資源の範囲を表す。 |
| `ip:firewallPublic` | Firewall公開IP | 構築FirewallのPublic IP出力を`/32`化。企業FirewallのIPを受ける公開入力ではない。 |
| `ip:appGatewayPublic` | AppGW公開IP | 構築AppGWのPublic IP出力を`/32`化。 |
| `ip:apim` | APIM内部VIP | 構築APIMのprivate VIP出力を`/32`化。 |
| `ip:aca` | ACA共有VIP | 構築managed environmentの`staticIp`出力を`/32`化。 |
| `ip:management` | 管理VM primary private IP | 構築NICの割当IP出力を`/32`化。 |
| `ips:keyVault`／`ips:foundry`／`ips:sql`／`ips:registry` | 対応サービスのPE NIC全IP | 構築PE NICの全IPを重複除去して`/32`化。ACRはregistry／data endpointを漏らさない。 |
| `ips:auditStorageJapan`／`ips:auditStorageFoundry` | 2つの監査StorageのBlob PE全IP | 構築Blob PE NICの全IPを`/32`化。監査コンテナーの読取認可と別に到達を許可する。 |
| `tag:...` | Azureサービスタグ | 名称・プロトコル・ポートは資材固定。タグのIP集合を公開入力で上書きしない。 |
| `literal:*`／`literal:0.0.0.0/0` | Any／初期IPv4公開範囲 | 資材固定。企業環境に合わせた変更は初期構築後の運用とする。 |

規則表の依存欄には、各規則へ入る構築情報を列挙する。規則の作成有無は全76件で固定であり、依存欄の値が作成スイッチを意味することはない。PEや資源の作成後に動的規則を設定し、終端Denyは必要なAllowが揃った後に設定する。

## 全カスタム規則と通信対応

すべての表で、名前・優先度・方向・プロトコル・ポート・アドレス参照・設計IDは正本の76件に対応する。用途と採用理由は通信要件への対応を示し、現在の経路でFirewallを通るかは[通信要件の経路](network-communication-requirements.md)と[Firewall設計](firewall-policy-design.md)で確認する。

### AppGW（`agw`）

#### Inbound

| 優先度 | 規則名／設計ID | 送信元 → 宛先 | プロトコル／宛先ポート | 通信・判断ID | 用途・採用理由 | 構築情報への依存 |
| --- | --- | --- | --- | --- | --- | --- |
| 120 | `allow-firewall-public-https-ingress`<br>`N-AGW-IN-01` | `ip:firewallPublic` → `subnet:agw` | TCP／443 | [FLOW-FIREWALL-PUBLIC-APPGW](network-communication-requirements.md#flow-firewall-public-appgw) | FirewallのSNAT後に公開入口へ到達するHTTPS。構築したFirewall公開IPだけを送信元として明示する。 | `ip:firewallPublic`, `subnet:agw` |
| 121 | `allow-client-http-ingress`<br>`N-AGW-IN-02-HTTP` | `0.0.0.0/0`（初期IPv4公開範囲） → `subnet:agw` | TCP／80 | [FLOW-CLIENT-HTTP](network-communication-requirements.md#flow-client-http) | 公開HTTP listenerへの到達とHTTPSリダイレクト。初期IPv4公開範囲を固定し、企業の接続元制限は構築後の手動変更とする。 | `subnet:agw` |
| 122 | `allow-client-https-ingress`<br>`N-AGW-IN-02-HTTPS` | `0.0.0.0/0`（初期IPv4公開範囲） → `subnet:agw` | TCP／443 | [FLOW-CLIENT-HTTPS](network-communication-requirements.md#flow-client-https) | API／Portalの公開HTTPS入口。AppGWのTLS終端とWAFで処理する入口をHTTPとは別に記録する。 | `subnet:agw` |
| 320 | `allow-gateway-manager-ingress`<br>`N-AGW-IN-04` | `tag:GatewayManager` → Any | TCP／65200-65535 | [FLOW-APPGW-GATEWAY-MANAGER](network-communication-requirements.md#flow-appgw-gateway-manager) | 標準AppGW v2の管理基盤通信。GatewayManagerの公式受信要件を維持する。 | 固定literal／Azureタグのみ |
| 321 | `allow-azure-load-balancer-ingress`<br>`N-AGW-IN-05` | `tag:AzureLoadBalancer` → Any | Any／Any | [FLOW-APPGW-LOAD-BALANCER](network-communication-requirements.md#flow-appgw-load-balancer) | Azure Load Balancerの基盤probe。送信元タグを固定し、宛先・ポート・プロトコルはAnyとする採用判断を反映する。 | 固定literal／Azureタグのみ |
| 4096 | `deny-any-ingress`<br>`N-AGW-IN-TERMINAL` | Any → Any | Any／Any | `POLICY-APPGW-IN-DENY` | 列挙した受信許可以外を既定許可より前で拒否する。既定Denyの再作成ではなくカスタム許可集合の終端である。 | 固定literal／Azureタグのみ |

#### Outbound

| 優先度 | 規則名／設計ID | 送信元 → 宛先 | プロトコル／宛先ポート | 通信・判断ID | 用途・採用理由 | 構築情報への依存 |
| --- | --- | --- | --- | --- | --- | --- |
| 120 | `allow-apim-http-egress`<br>`N-AGW-OUT-01` | `subnet:agw` → `ip:apim` | TCP／80 | [FLOW-APPGW-APIM](network-communication-requirements.md#flow-appgw-apim) | 通常APIとバックエンドprobeをAPIM内部VIPのHTTP80へ送る。構築VIPだけを指定し、再暗号化しない設計を記録する。 | `subnet:agw`, `ip:apim` |
| 121 | `allow-aca-http-egress`<br>`N-AGW-OUT-03` | `subnet:agw` → `ip:aca` | TCP／80 | [FLOW-APPGW-ACA](network-communication-requirements.md#flow-appgw-aca) | PortalとprobeをACA共有VIPのHTTP80へ送る。公開HTTPSはAppGWで終端し、ACAのHTTP受入設定と組み合わせる。 | `subnet:agw`, `ip:aca` |
| 220 | `allow-key-vault-https-egress`<br>`N-AGW-OUT-04` | `subnet:agw` → `ips:keyVault` | TCP／443 | [FLOW-APPGW-KV](network-communication-requirements.md#flow-appgw-kv) | AppGWが構築Key Vaultの証明書を取得・更新するHTTPS。PE NICの全IPを取得し、PEサブネット全体へ広げない。 | `subnet:agw`, `ips:keyVault` |

### APIM（`apim`）

#### Inbound

| 優先度 | 規則名／設計ID | 送信元 → 宛先 | プロトコル／宛先ポート | 通信・判断ID | 用途・採用理由 | 構築情報への依存 |
| --- | --- | --- | --- | --- | --- | --- |
| 120 | `allow-appgw-http-ingress`<br>`N-APIM-IN-01` | `subnet:agw` → `subnet:apim` | TCP／80 | [FLOW-APPGW-APIM](network-communication-requirements.md#flow-appgw-apim) | AppGWからの通常API／probeのHTTP80。入口確認ヘッダーを上書き・検査するL7保護も常設する。 | `subnet:agw`, `subnet:apim` |
| 220 | `allow-mgmt-vm-https-ingress`<br>`N-APIM-IN-VM-CANDIDATE` | `ip:management` → `subnet:apim` | TCP／443 | [FLOW-MGMT-APIM](network-communication-requirements.md#flow-mgmt-apim) | 管理VMからAPIMのHTTPS保守接続。gRPC撤去後も残す保守通信で、管理VMの構築IPだけを許可する。 | `ip:management`, `subnet:apim` |
| 320 | `allow-api-management-ingress`<br>`N-APIM-IN-03` | `tag:ApiManagement` → `subnet:apim` | TCP／3443 | [FLOW-APIM-MANAGEMENT](network-communication-requirements.md#flow-apim-management) | APIM VNet配置のAzure管理基盤TCP3443。ApiManagementタグにより管理基盤の動的アドレス範囲へ追従する。 | `subnet:apim` |
| 321 | `allow-apim-ratelimits-counter-sync-ingress`<br>`N-APIM-IN-COUNTER` | `subnet:apim` → `subnet:apim` | UDP／4290 | [FLOW-APIM-COUNTER-SYNC](network-communication-requirements.md#flow-apim-counter-sync) | APIMインスタンス間のレート制限counter同期。内部CIDR宛でもAzure製品仕様の通信として300番台に分類する。 | `subnet:apim` |
| 322 | `allow-apim-internal-cache-ingress`<br>`N-APIM-IN-CACHE` | `subnet:apim` → `subnet:apim` | TCP／6381-6383 | [FLOW-APIM-INTERNAL-CACHE](network-communication-requirements.md#flow-apim-internal-cache) | APIMインスタンス間の内部cache通信。TCP6381–6383を製品の基盤依存として明示する。 | `subnet:apim` |
| 323 | `allow-azure-load-balancer-ingress`<br>`N-APIM-IN-04` | `tag:AzureLoadBalancer` → Any | Any／Any | [FLOW-APIM-LOAD-BALANCER](network-communication-requirements.md#flow-apim-load-balancer) | Azure Load Balancerの基盤probe。AppGWと同じく送信元タグ以外をAnyとする採用判断を反映する。 | 固定literal／Azureタグのみ |
| 4096 | `deny-any-ingress`<br>`N-APIM-IN-TERMINAL` | Any → Any | Any／Any | `POLICY-APIM-IN-DENY` | AppGW、管理VM、Azure管理基盤と内部同期の列挙以外の新規受信を拒否する。 | 固定literal／Azureタグのみ |

#### Outbound

| 優先度 | 規則名／設計ID | 送信元 → 宛先 | プロトコル／宛先ポート | 通信・判断ID | 用途・採用理由 | 構築情報への依存 |
| --- | --- | --- | --- | --- | --- | --- |
| 120 | `allow-foundry-https-egress`<br>`N-APIM-OUT-01` | `subnet:apim` → `ips:foundry` | TCP／443 | [FLOW-APIM-FOUNDRY](network-communication-requirements.md#flow-apim-foundry) | クライアント要求に基づくFoundry APIへのHTTPS。構築Foundry PEの全IPを使い、業務通信として120番台に置く。 | `subnet:apim`, `ips:foundry` |
| 220 | `allow-aca-https-egress`<br>`N-APIM-OUT-02` | `subnet:apim` → `ip:aca` | TCP／443 | [FLOW-APIM-ACA](network-communication-requirements.md#flow-apim-aca) | Credential BrokerのHTTPS API呼出し。Portal向けHTTP80とは異なる接続であり、ACA共有VIPの443を維持する。 | `subnet:apim`, `ip:aca` |
| 221 | `allow-key-vault-https-egress`<br>`N-APIM-OUT-03` | `subnet:apim` → `ips:keyVault` | TCP／443 | [FLOW-APIM-KV](network-communication-requirements.md#flow-apim-kv) | 構築Key Vaultの証明書・秘密情報参照。PEへの接続を、AzureKeyVaultタグの基盤依存規則とは分ける。 | `subnet:apim`, `ips:keyVault` |
| 320 | `allow-storage-egress`<br>`N-APIM-OUT-04` | `subnet:apim` → `tag:Storage` | TCP／443 | [FLOW-APIM-STORAGE](network-communication-requirements.md#flow-apim-storage) | APIM製品のStorage基盤依存。Storageタグ全域を許可し、特定アプリの監査Blob閲覧とは分ける。 | `subnet:apim` |
| 321 | `allow-sql-egress`<br>`N-APIM-OUT-05` | `subnet:apim` → `tag:Sql` | TCP／1433 | [FLOW-APIM-SQL-PLATFORM](network-communication-requirements.md#flow-apim-sql-platform) | APIM製品のSQL基盤依存。SqlタグTCP1433であり、ACA／管理VMから構築SQL PEへの接続とは別通信である。 | `subnet:apim` |
| 322 | `allow-key-vault-egress`<br>`N-APIM-OUT-06` | `subnet:apim` → `tag:AzureKeyVault` | TCP／443 | [FLOW-APIM-KV-PLATFORM](network-communication-requirements.md#flow-apim-kv-platform) | APIM製品のKey Vault基盤依存。AzureKeyVaultタグを使用し、構築PE指定の220番台規則とは別に記録する。 | `subnet:apim` |
| 323 | `allow-monitor-egress`<br>`N-APIM-OUT-07` | `subnet:apim` → `tag:AzureMonitor` | TCP／1886, 443 | [FLOW-APIM-MONITOR](network-communication-requirements.md#flow-apim-monitor) | APIMの監視・診断送信。AzureMonitorタグの公式依存ポートTCP1886と443を維持する。 | `subnet:apim` |
| 324 | `allow-event-hubs-egress`<br>`N-APIM-OUT-08` | `subnet:apim` → `tag:EventHub` | TCP／443, 5671, 5672 | [FLOW-APIM-EVENT-HUBS](network-communication-requirements.md#flow-apim-event-hubs) | APIM製品のEvent Hubs依存。HTTPSとAMQP系の公式接続を別サービスの443許可へまとめない。 | `subnet:apim` |
| 325 | `allow-entra-egress`<br>`N-APIM-OUT-09` | `subnet:apim` → `tag:AzureActiveDirectory` | TCP／443 | [FLOW-APIM-ENTRA](network-communication-requirements.md#flow-apim-entra) | Entraによるトークン／認証基盤のHTTPS。AzureActiveDirectoryタグは認証先到達を許可し、操作権限は認可で制限する。 | `subnet:apim` |
| 326 | `allow-credential-manager-egress`<br>`N-APIM-OUT-10` | `subnet:apim` → `tag:AzureConnectors` | TCP／443 | [FLOW-APIM-CREDENTIAL-MANAGER](network-communication-requirements.md#flow-apim-credential-manager) | APIM Credential ManagerのAzure Connectors依存。Brokerへのprivate API接続とは別のAzure基盤通信である。 | `subnet:apim` |
| 327 | `allow-internet-egress`<br>`N-APIM-OUT-11` | `subnet:apim` → `tag:Internet` | TCP／80, 443 | [FLOW-APIM-INTERNET](network-communication-requirements.md#flow-apim-internet) | 証明書検証などAPIMの外部HTTP／HTTPS依存。NSGではInternetを許可し、Firewall側で現在通る用途別FQDNを管理する。 | `subnet:apim` |
| 328 | `allow-time-sync-egress`<br>`N-APIM-OUT-12` | `subnet:apim` → `tag:Internet` | UDP／123 | [FLOW-APIM-NTP](network-communication-requirements.md#flow-apim-ntp) | APIM基盤の外部NTP。Internet宛UDP123を許可し、FirewallのFQDN回答差に依存しない許可と組み合わせる。 | `subnet:apim` |
| 329 | `allow-apim-ratelimits-counter-sync-egress`<br>`N-APIM-OUT-COUNTER` | `subnet:apim` → `subnet:apim` | UDP／4290 | [FLOW-APIM-COUNTER-SYNC](network-communication-requirements.md#flow-apim-counter-sync) | 受信counter-sync規則と対になるインスタンス間の新規送信。確立済み要求の応答専用規則ではない。 | `subnet:apim` |
| 330 | `allow-apim-internal-cache-egress`<br>`N-APIM-OUT-CACHE` | `subnet:apim` → `subnet:apim` | TCP／6381-6383 | [FLOW-APIM-INTERNAL-CACHE](network-communication-requirements.md#flow-apim-internal-cache) | 受信internal-cache規則と対になるインスタンス間の新規送信。サービス仕様上の通信として300番台へ置く。 | `subnet:apim` |
| 4096 | `deny-any-egress`<br>`N-APIM-OUT-TERMINAL` | Any → Any | Any／Any | `POLICY-APIM-OUT-DENY` | 列挙したAPIM新規送信以外を拒否する。許可通信の応答はNSGの接続状態で扱う。 | 固定literal／Azureタグのみ |

### Private Endpoint（`pep`）

#### Inbound

| 優先度 | 規則名／設計ID | 送信元 → 宛先 | プロトコル／宛先ポート | 通信・判断ID | 用途・採用理由 | 構築情報への依存 |
| --- | --- | --- | --- | --- | --- | --- |
| 120 | `allow-apim-foundry-https-ingress`<br>`B-PE-05` | `subnet:apim` → `ips:foundry` | TCP／443 | [FLOW-APIM-FOUNDRY](network-communication-requirements.md#flow-apim-foundry) | APIMからFoundry PEへ到達する業務HTTPS。送信側規則と同じ構築PE全IPを宛先に使う。 | `subnet:apim`, `ips:foundry` |
| 220 | `allow-appgw-key-vault-https-ingress`<br>`B-PE-01` | `subnet:agw` → `ips:keyVault` | TCP／443 | [FLOW-APPGW-KV](network-communication-requirements.md#flow-appgw-kv) | AppGWからKey Vault PEへの証明書取得・更新。AppGWサブネットと構築PE全IPの組に限定する。 | `subnet:agw`, `ips:keyVault` |
| 221 | `allow-apim-key-vault-https-ingress`<br>`B-PE-02` | `subnet:apim` → `ips:keyVault` | TCP／443 | [FLOW-APIM-KV](network-communication-requirements.md#flow-apim-kv) | APIMから構築Key Vault PEへの参照。AzureKeyVaultタグへの基盤依存とは異なるprivate接続を許可する。 | `subnet:apim`, `ips:keyVault` |
| 222 | `allow-aca-key-vault-https-ingress`<br>`B-PE-03` | `subnet:aca` → `ips:keyVault` | TCP／443 | [FLOW-ACA-KV](network-communication-requirements.md#flow-aca-kv) | Credential Broker等のACAワークロードから構築Key Vault PEへのHTTPS。VNet直接経路の受信許可である。 | `subnet:aca`, `ips:keyVault` |
| 223 | `allow-mgmt-vm-key-vault-https-ingress`<br>`B-PE-04` | `ip:management` → `ips:keyVault` | TCP／443 | [FLOW-MGMT-KV](network-communication-requirements.md#flow-mgmt-kv) | 管理VMの証明書処理とKey Vault保守。管理VMの単IPと構築PEの全IPだけを組み合わせる。 | `ip:management`, `ips:keyVault` |
| 224 | `allow-mgmt-vm-foundry-https-ingress`<br>`B-PE-06` | `ip:management` → `ips:foundry` | TCP／443 | [FLOW-MGMT-FOUNDRY](network-communication-requirements.md#flow-mgmt-foundry) | 管理VMからFoundryを保守・確認するprivate HTTPS。APIM業務接続と実行主体を分ける。 | `ip:management`, `ips:foundry` |
| 225 | `allow-mgmt-vm-sql-1433-ingress`<br>`N-PE-IN-MGMT-SQL` | `ip:management` → `ips:sql` | TCP／1433 | [FLOW-MGMT-SQL](network-communication-requirements.md#flow-mgmt-sql) | 管理VMから構築したGateway／Portal DBを直接保守するSQL接続。MIのDB認可と保守clientは別途必要である。 | `ip:management`, `ips:sql` |
| 226 | `allow-aca-sql-1433-ingress`<br>`B-PE-07` | `subnet:aca` → `ips:sql` | TCP／1433 | [FLOW-ACA-SQL](network-communication-requirements.md#flow-aca-sql) | Broker／Portal／資材が作成するJobから共有SQL PEへの接続。SQL connection policyとDB認可を別に満たす。 | `subnet:aca`, `ips:sql` |
| 227 | `allow-aca-acr-https-ingress`<br>`B-PE-08` | `subnet:aca` → `ips:registry` | TCP／443 | [FLOW-ACA-ACR](network-communication-requirements.md#flow-aca-acr) | ACAのimage取得に使うACR PEへのHTTPS。registryとdata endpointを含むPE NICの全IPを使う。 | `subnet:aca`, `ips:registry` |
| 228 | `allow-mgmt-vm-acr-https-ingress`<br>`B-PE-09` | `ip:management` → `ips:registry` | TCP／443 | [FLOW-MGMT-ACR](network-communication-requirements.md#flow-mgmt-acr) | 管理VMのACR保守・image取得／送信。構築管理VM IPとACR PE全IPに限定する。 | `ip:management`, `ips:registry` |
| 229 | `allow-mgmt-vm-audit-storage-japan-https-ingress`<br>`N-PEP-IN-MGMT-AUDIT-STORAGE-JAPAN-HTTPS` | `ip:management` → `ips:auditStorageJapan` | TCP／443 | [FLOW-MGMT-AUDIT-JAPAN](network-communication-requirements.md#flow-mgmt-audit-japan) | 管理VMからcollectJapan監査コンテナーのBlob PEを読むHTTPS。Storageタグの包括依存とは別に記録する。 | `ip:management`, `ips:auditStorageJapan` |
| 230 | `allow-mgmt-vm-audit-storage-foundry-https-ingress`<br>`N-PEP-IN-MGMT-AUDIT-STORAGE-FOUNDRY-HTTPS` | `ip:management` → `ips:auditStorageFoundry` | TCP／443 | [FLOW-MGMT-AUDIT-FOUNDRY](network-communication-requirements.md#flow-mgmt-audit-foundry) | 管理VMからcollectFoundry監査コンテナーのBlob PEを読むHTTPS。Foundry API接続とは別の監査データ閲覧である。 | `ip:management`, `ips:auditStorageFoundry` |
| 4096 | `deny-any-ingress`<br>`B-PE-14` | Any → Any | Any／Any | `POLICY-PEP-IN-DENY` | 列挙したclient→PE接続以外を拒否する。PEサブネットのNSGポリシー有効化と組み合わせる。 | 固定literal／Azureタグのみ |

#### Outbound

カスタム規則は作成しない。Azure既定Outboundと許可接続のstateful応答を維持する。サービス側からの新規接続を仮定したPE用Allowは作成しない。

### 管理VM（`management`）

#### Inbound

| 優先度 | 規則名／設計ID | 送信元 → 宛先 | プロトコル／宛先ポート | 通信・判断ID | 用途・採用理由 | 構築情報への依存 |
| --- | --- | --- | --- | --- | --- | --- |
| 4096 | `deny-any-ingress`<br>`B-MGMT-19` | Any → Any | Any／Any | `POLICY-MGMT-IN-DENY` | 初期状態の通常受信を拒否する。SSH接続元を指定する公開入力は設けず、Serial／Run Commandは別の保守経路として扱う。 | 固定literal／Azureタグのみ |

#### Outbound

| 優先度 | 規則名／設計ID | 送信元 → 宛先 | プロトコル／宛先ポート | 通信・判断ID | 用途・採用理由 | 構築情報への依存 |
| --- | --- | --- | --- | --- | --- | --- |
| 220 | `allow-key-vault-https-egress`<br>`B-MGMT-02` | `ip:management` → `ips:keyVault` | TCP／443 | [FLOW-MGMT-KV](network-communication-requirements.md#flow-mgmt-kv) | 管理VMの証明書処理・秘密metadata確認・Key Vault保守。構築管理VM IPから構築PE全IPへ許可する。 | `ip:management`, `ips:keyVault` |
| 221 | `allow-acr-https-egress`<br>`B-MGMT-03` | `ip:management` → `ips:registry` | TCP／443 | [FLOW-MGMT-ACR](network-communication-requirements.md#flow-mgmt-acr) | 構築ACRのimage保守・取得／送信。registry/data endpointを含む構築PE全IPへ許可する。 | `ip:management`, `ips:registry` |
| 222 | `allow-apim-https-egress`<br>`B-MGMT-18` | `ip:management` → `subnet:apim` | TCP／443 | [FLOW-MGMT-APIM](network-communication-requirements.md#flow-mgmt-apim) | APIM private gatewayのHTTPS保守。gRPCは廃止し、残すHTTPS通信を名称にも反映する。 | `ip:management`, `subnet:apim` |
| 223 | `allow-foundry-https-egress`<br>`B-MGMT-21` | `ip:management` → `ips:foundry` | TCP／443 | [FLOW-MGMT-FOUNDRY](network-communication-requirements.md#flow-mgmt-foundry) | Foundryのprivate API保守。管理VMを全資源の保守拠点とするためAPIM以外の実行主体を許可する。 | `ip:management`, `ips:foundry` |
| 224 | `allow-sql-1433-egress`<br>`N-MGMT-OUT-SQL` | `ip:management` → `ips:sql` | TCP／1433 | [FLOW-MGMT-SQL](network-communication-requirements.md#flow-mgmt-sql) | Gateway／Portalの構築DBを管理VMのMIで直接保守するTCP1433。NSGはDB権限を付与しない。 | `ip:management`, `ips:sql` |
| 225 | `allow-aca-http-egress`<br>`N-MGMT-OUT-ACA-HTTP` | `ip:management` → `ip:aca` | TCP／80 | [FLOW-MGMT-ACA-HTTP](network-communication-requirements.md#flow-mgmt-aca-http) | ACA private ingressのHTTP確認・保守。AppGW→PortalのHTTP80化と整合する到達経路を用意する。 | `ip:management`, `ip:aca` |
| 226 | `allow-aca-https-egress`<br>`N-MGMT-OUT-ACA-HTTPS` | `ip:management` → `ip:aca` | TCP／443 | [FLOW-MGMT-ACA-HTTPS](network-communication-requirements.md#flow-mgmt-aca-https) | ACA private ingressのHTTPS確認・保守。リージョン共用Dev APIへのログ／exec通信とは別宛先である。 | `ip:management`, `ip:aca` |
| 227 | `allow-audit-storage-japan-https-egress`<br>`N-MGMT-OUT-AUDIT-STORAGE-JAPAN-HTTPS` | `ip:management` → `ips:auditStorageJapan` | TCP／443 | [FLOW-MGMT-AUDIT-JAPAN](network-communication-requirements.md#flow-mgmt-audit-japan) | collectJapan監査Blobのprivate閲覧。管理VMのMIへコンテナー単位の読取認可を常設する判断と組み合わせる。 | `ip:management`, `ips:auditStorageJapan` |
| 228 | `allow-audit-storage-foundry-https-egress`<br>`N-MGMT-OUT-AUDIT-STORAGE-FOUNDRY-HTTPS` | `ip:management` → `ips:auditStorageFoundry` | TCP／443 | [FLOW-MGMT-AUDIT-FOUNDRY](network-communication-requirements.md#flow-mgmt-audit-foundry) | collectFoundry監査Blobのprivate閲覧。Foundry API通信と分け、構築Blob PE全IPへ許可する。 | `ip:management`, `ips:auditStorageFoundry` |
| 320 | `allow-azure-resource-manager-egress`<br>`B-MGMT-06` | `subnet:management` → `tag:AzureResourceManager` | TCP／443 | [FLOW-MGMT-ARM](network-communication-requirements.md#flow-mgmt-arm) | 資源情報取得・更新、保守token／diagnostics取得等のARM操作。到達許可と操作ごとのRBACを分ける。 | `subnet:management` |
| 321 | `allow-entra-egress`<br>`B-MGMT-07` | `subnet:management` → `tag:AzureActiveDirectory` | TCP／443 | [FLOW-MGMT-ENTRA](network-communication-requirements.md#flow-mgmt-entra) | Entra認証基盤のHTTPS。VMのローカルIMDS取得とは異なる上流サービスへの接続である。 | `subnet:management` |
| 322 | `allow-monitor-egress`<br>`B-MGMT-08` | `subnet:management` → `tag:AzureMonitor` | TCP／443 | [FLOW-MGMT-MONITOR](network-communication-requirements.md#flow-mgmt-monitor) | AMAの監視送信と保存ログ閲覧等のMonitor接続。構築Workspace閲覧認可は別途設定する。 | `subnet:management` |
| 323 | `allow-storage-egress`<br>`B-MGMT-09` | `subnet:management` → `tag:Storage` | TCP／443 | [FLOW-MGMT-AGENT-STORAGE](network-communication-requirements.md#flow-mgmt-agent-storage) | VM Agent／拡張等のStorage依存。監査Blob PEの228／227とは別のタグ宛接続である。 | `subnet:management` |
| 324 | `allow-internet-egress`<br>`B-MGMT-10` | `subnet:management` → `tag:Internet` | TCP／80, 443 | [FLOW-MGMT-INTERNET](network-communication-requirements.md#flow-mgmt-internet) | client準備、証明書、承認済み宛先、ACA Dev API等の外部HTTP／HTTPS。Firewallで用途別FQDNを制限するためのNSG到達許可である。 | `subnet:management` |
| 325 | `allow-time-sync-egress`<br>`B-MGMT-11` | `subnet:management` → `tag:Internet` | UDP／123 | [FLOW-MGMT-NTP](network-communication-requirements.md#flow-mgmt-ntp) | 標準外部NTPのInternet宛UDP123。Azure hostのPHC／VMBus同期とは分け、guest設定とFirewall許可を揃える。 | `subnet:management` |
| 4096 | `deny-any-egress`<br>`B-MGMT-20` | Any → Any | Any／Any | `POLICY-MGMT-OUT-DENY` | 列挙した管理VM新規送信以外を拒否する。Azure提供DNS・IMDS等の基盤例外はこのAny拒否だけでは遮断されない。 | 固定literal／Azureタグのみ |

### ACA（`aca`）

#### Inbound

| 優先度 | 規則名／設計ID | 送信元 → 宛先 | プロトコル／宛先ポート | 通信・判断ID | 用途・採用理由 | 構築情報への依存 |
| --- | --- | --- | --- | --- | --- | --- |
| 120 | `allow-appgw-http-ingress`<br>`B-ACA-03` | `subnet:agw` → `subnet:aca` | TCP／80, 31080 | [FLOW-APPGW-ACA](network-communication-requirements.md#flow-appgw-aca) | AppGWからPortalのHTTP ingressと基盤node portへ到達する。80／31080を明示し、Portal向けHTTP80設計を支える。 | `subnet:agw`, `subnet:aca` |
| 220 | `allow-apim-https-ingress`<br>`B-ACA-04` | `subnet:apim` → `subnet:aca` | TCP／443, 31443 | [FLOW-APIM-ACA](network-communication-requirements.md#flow-apim-aca) | APIMからCredential BrokerのHTTPS ingressと基盤node portへ到達する。PortalのHTTP80とは別に443／31443を維持する。 | `subnet:apim`, `subnet:aca` |
| 221 | `allow-mgmt-vm-http-ingress`<br>`N-ACA-IN-MGMT-HTTP` | `ip:management` → `subnet:aca` | TCP／80, 31080 | [FLOW-MGMT-ACA-HTTP](network-communication-requirements.md#flow-mgmt-aca-http) | 管理VMからACA private HTTP ingressを確認・保守する。管理VM構築IPのみを送信元に使う。 | `ip:management`, `subnet:aca` |
| 222 | `allow-mgmt-vm-https-ingress`<br>`N-ACA-IN-MGMT-HTTPS` | `ip:management` → `subnet:aca` | TCP／443, 31443 | [FLOW-MGMT-ACA-HTTPS](network-communication-requirements.md#flow-mgmt-aca-https) | 管理VMからACA private HTTPS ingressを確認・保守する。Dev API gatewayへの外部HTTPSとは別通信である。 | `ip:management`, `subnet:aca` |
| 320 | `allow-azure-load-balancer-ingress`<br>`B-ACA-05` | `tag:AzureLoadBalancer` → `subnet:aca` | TCP／30000-32767 | [FLOW-ACA-LOAD-BALANCER](network-communication-requirements.md#flow-aca-load-balancer) | ACA基盤のLoad Balancer probe。ACAの公開仕様に沿ってTCP node port範囲を許可し、AppGW／APIMのAny判断を横展開しない。 | `subnet:aca` |

#### Outbound

| 優先度 | 規則名／設計ID | 送信元 → 宛先 | プロトコル／宛先ポート | 通信・判断ID | 用途・採用理由 | 構築情報への依存 |
| --- | --- | --- | --- | --- | --- | --- |
| 120 | `allow-appgw-https-egress`<br>`B-ACA-11` | `subnet:aca` → `ip:appGatewayPublic` | TCP／443 | [FLOW-ACA-APPGW](network-communication-requirements.md#flow-aca-appgw) | Portal等のACAワークロードから公開AppGW経由でAPI入口へ到達するHTTPS。構築AppGW公開IPだけを指定する。 | `subnet:aca`, `ip:appGatewayPublic` |
| 220 | `allow-key-vault-https-egress`<br>`B-ACA-06` | `subnet:aca` → `ips:keyVault` | TCP／443 | [FLOW-ACA-KV](network-communication-requirements.md#flow-aca-kv) | Broker等が構築Key Vault PEへ接続するHTTPS。private DNS、経路、認可と組み合わせる。 | `subnet:aca`, `ips:keyVault` |
| 221 | `allow-sql-1433-egress`<br>`B-ACA-07` | `subnet:aca` → `ips:sql` | TCP／1433 | [FLOW-ACA-SQL](network-communication-requirements.md#flow-aca-sql) | Broker／Portal／構築Jobから共有SQL PEへ接続するTCP1433。現在のVNet直接経路を記録する。 | `subnet:aca`, `ips:sql` |
| 222 | `allow-acr-https-egress`<br>`B-ACA-08` | `subnet:aca` → `ips:registry` | TCP／443 | [FLOW-ACA-ACR](network-communication-requirements.md#flow-aca-acr) | 構築ACRからimageを取得するHTTPS。PE NICのregistry／data用全IPを許可する。 | `subnet:aca`, `ips:registry` |
| 223 | `allow-arm-https-egress`<br>`B-ACA-13` | `subnet:aca` → `tag:AzureResourceManager` | TCP／443 | [FLOW-ACA-ARM](network-communication-requirements.md#flow-aca-arm) | Portal／Broker等がARM APIを利用する基盤固有の管理操作。アプリのMI／RBACにより操作対象を限定する。 | `subnet:aca` |
| 320 | `allow-aca-internal-egress`<br>`B-ACA-02` | `subnet:aca` → `subnet:aca` | Any／Any | [FLOW-ACA-INTERNAL](network-communication-requirements.md#flow-aca-internal) | ACAサブネット内の基盤通信。内部portの完全な固定契約を推測せず、同サブネット宛Anyを明示する。 | `subnet:aca` |
| 321 | `allow-entra-egress`<br>`B-ACA-12` | `subnet:aca` → `tag:AzureActiveDirectory` | TCP／443 | [FLOW-ACA-ENTRA](network-communication-requirements.md#flow-aca-entra) | Entraへの上流HTTPS依存。ACAが供給するローカルIDENTITY_ENDPOINTの取得処理とは分ける。 | `subnet:aca` |
| 322 | `allow-monitor-egress`<br>`B-ACA-14` | `subnet:aca` → `tag:AzureMonitor` | TCP／443 | [FLOW-ACA-MONITOR](network-communication-requirements.md#flow-aca-monitor) | ACAのログ／監視基盤へのHTTPS送信。NSG Allowだけでログ本文の収集成功とは判定しない。 | `subnet:aca` |
| 323 | `allow-mcr-egress`<br>`B-ACA-15` | `subnet:aca` → `tag:MicrosoftContainerRegistry` | TCP／443 | [FLOW-ACA-MCR](network-communication-requirements.md#flow-aca-mcr) | Microsoft Container Registryの基盤image／artifact取得。構築ACR PEからの取得とは別通信である。 | `subnet:aca` |
| 324 | `allow-afd-first-party-egress`<br>`B-ACA-16` | `subnet:aca` → `tag:AzureFrontDoor.FirstParty` | TCP／443 | [FLOW-ACA-PLATFORM-PACKAGES](network-communication-requirements.md#flow-aca-platform-packages) | Azure Front Door FirstParty経由の基盤配布・パッケージ依存。MCR等の配布依存とあわせて仕様を参照する。 | `subnet:aca` |

## 既定規則と拒否範囲

Azureは5 NSGそれぞれへ次の6既定規則を持たせる。資材で再作成・変更しない。カスタム規則が先に一致すれば既定規則には到達しない。

| 方向 | 優先度／既定名称 | 送信元 → 宛先 | プロトコル／ポート | 判断ID／意味 |
| --- | --- | --- | --- | --- |
| Inbound | 65000 `AllowVNetInBound` | VirtualNetwork → VirtualNetwork | Any／Any | `POLICY-DEFAULT-VNET-IN`。到達可能なVNet範囲を既定許可する。 |
| Inbound | 65001 `AllowAzureLoadBalancerInBound` | AzureLoadBalancer → Any | Any／Any | `POLICY-DEFAULT-LB-IN`。Azure LBの既定受信許可。 |
| Inbound | 65500 `DenyAllInBound` | Any → Any | Any／Any | `POLICY-DEFAULT-DENY-IN`。ここまで許可されない新規受信を拒否。 |
| Outbound | 65000 `AllowVnetOutBound` | VirtualNetwork → VirtualNetwork | Any／Any | `POLICY-DEFAULT-VNET-OUT`。到達可能なVNet範囲を既定許可する。 |
| Outbound | 65001 `AllowInternetOutBound` | Any → Internet | Any／Any | `POLICY-DEFAULT-INTERNET-OUT`。Internet向け既定送信許可。 |
| Outbound | 65500 `DenyAllOutBound` | Any → Any | Any／Any | `POLICY-DEFAULT-DENY-OUT`。ここまで許可されない新規送信を拒否。 |

`VirtualNetwork`は単に「このVNetに作った資源だけ」を示すタグではない。peering、接続ネットワーク、経路設定によって範囲が変わるため、ハブ＆スポーク移行時は既定規則の実効範囲も再確認する。

| NSG | Inboundの実効方針 | Outboundの実効方針 | 採用理由・判断 |
| --- | --- | --- | --- |
| AppGW | 明示Allowの後、4096で拒否 | 3件の明示Allowと既定許可を維持。カスタム末尾Denyなし | 標準v2のInternet向け包括送信許可という公式制約を維持する。Network Isolationの例外をSKU名だけで適用しない。明示Allowだけで送信先が3宛先へ限定されるとは解釈しない。 |
| APIM | 明示Allowの後、4096で拒否 | 明示Allowの後、4096で拒否 | 通常入口・保守・製品基盤依存・内部同期を列挙し、その外の新規接続を拒否する。 |
| PE | 明示Allowの後、4096で拒否 | カスタム規則なし、既定を維持 | `privateEndpointNetworkPolicies: NetworkSecurityGroupEnabled`でclient接続を評価する。PE応答のための新規送信Allowは作成しない。 |
| 管理VM | 4096で通常受信を拒否 | 明示Allowの後、4096で拒否 | 初期SSH接続元の公開入力を廃止。Run Command／Serial Consoleは通常Inbound TCP22を必要とする経路ではない。 |
| ACA | 明示Allowと既定許可を維持。カスタム末尾Denyなし | 明示Allowと既定許可を維持。カスタム末尾Denyなし | managed基盤の更新・scale・復旧通信を推測した完全Allow集合で置き換えない。内部受信Any専用Allow、追加Internet／Storage包括Allowも初期作成しない。 |

NSGはstatefulである。許可された接続への応答専用Allowを追加しない。一方、APIMのcounter-sync／cacheなど対向からも新規接続する製品通信は両方向で定義する。NSG規則の変更が既存接続へ直ちに再評価されるとは限らないため、拒否の確認は新規接続で行う。

Azure提供DNS、VM IMDS、DHCP、Agent host channel、ホスト時刻提供、loopback、managed service内部処理を通常のAny末尾Denyで完全遮断できるとは説明しない。[NSGにしない基盤通信](platform-communications.md)が、ポート・専用タグ・guest条件と例外を記録する。Azure提供DNS53への例外をカスタムDNS宛53全般へ一般化しない。

## 判断記録

| 判断 | 採用した設計 | 理由・確認条件 |
| --- | --- | --- |
| `DEC-NSG-FIXED-SET` | 5 NSG・76固定規則 | 規則を通信要件の参照として使うため、作成フラグを廃止し、構築CIDR・割当IPだけを差し込む。 |
| `DEC-NSG-PRIORITIES` | 120／220／320から1ずつ、終端4096 | 業務要求、基盤固有、Azure仕様を区別し、帯の先頭を将来の追加用に空ける。 |
| `DEC-NSG-APPGW-HTTP` | AppGW→APIM／PortalはHTTP80 | 閉域のbackend区間で再暗号化を避ける設計。通信はAppGW→VIPのUDRとFirewallのHTTP規則にも対応する。 |
| `DEC-NSG-GRPC-RETIRED` | gRPC専用通信を撤去、HTTPS保守を保持 | 提供サービスと初期要件を合わせ、複雑なpassthroughを再作成しない。入口確認ヘッダーの保護は通常API用に常設する。 |
| `DEC-NSG-STANDARD-DNS` | VNet／Firewall上流をAzure提供DNSに固定 | 通常のcustom DNS TCP／UDP53規則や作成スイッチを初期集合へ追加しない。 |
| `DEC-NSG-NTP` | APIM／管理VMのInternet宛UDP123を明示 | VNet標準DNSとFirewallの独立したFQDN回答差でNTPを遮断しない。daemon同期とAPIM時刻確認は通信許可と分けて実証する。 |
| `DEC-NSG-LB-ANY` | AppGW／APIMのLB受信は送信元以外Any | AzureLoadBalancerタグを明示し、両製品の基盤probe許可を統一する。ACAのnode port要件へは横展開しない。 |
| `DEC-NSG-TERMINAL-DENY` | AppGW／APIM／PE／管理VMの受信、APIM／管理VMの送信に終端拒否 | 列挙した許可以外を拒否する。AppGWの公式送信制約とACAのmanaged基盤依存に対する例外を残す。 |
| `DEC-SQL-PROXY` | 共有SQLはProxy、SQL向け送受信4規則はTCP1433 | 宛先を構築SQL PEへ限定し、Portalの同一接続・Sessionロックを維持する。[採用理由とライブラリ比較](sql-proxy-decision.md)を参照。 |
| `DEC-NSG-AUDIT-BLOB-PE` | 監査Storage2組へBlob PEと管理VM送受信4規則を常設 | 監査Blobのprivate閲覧を用意し、各構築監査コンテナーのData Reader認可と組み合わせる。 |
| `DEC-NSG-NO-FIREWALL-SUBNET` | AzureFirewallSubnetへ顧客NSGを関連付けない | Azure Firewallの公式制約を守り、顧客通信はFirewall Policyで管理する。 |

廃止した公開入力は`network.clientAddressPrefixes`、`network.certificatePublicProbeEnabled`、`network.operatorAddressPrefixes`、`network.operatorProtocol`、`network.operatorPorts`、`network.dnsServers`、`network.firewall.dnsServers`、`network.ntpAddressPrefixes`、`network.ntpFqdns`、`network.managementAdditionalSecurityRules`、`network.apimKeyVaultAccessEnabled`と`gateway.grpc`である。空値を渡す互換経路としても残さない。Broker本体や規則集合をcredential profileの空／非空で省略せず、初期常設とする。

## 資材、移行、再構築

| 正本・実装 | 役割 |
| --- | --- |
| [NSG JSON正本](../../infra/nsg-rule-priorities.json) | 規則tupleと設計ID。76件の照合基準。 |
| [network.bicep](../../infra/modules/network.bicep) | 5 NSG、サブネット関連付け、静的基盤規則、標準DNS。 |
| [network-bindings.bicep](../../infra/modules/network-bindings.bicep) | 割当IP・PE全IPを使う動的規則、監査Blobの4規則、終端Deny。 |
| [portal-network.bicep](../../infra/modules/portal-network.bicep)／[bindings](../../infra/modules/portal-network-bindings.bicep) | ACR PE全IP、ACA VIP、AppGW→ACAのHTTP80規則。 |
| [credential-broker-network.bicep](../../infra/modules/credential-broker-network.bicep) | 共有SQL PE全IP、APIM→ACAと管理VM→SQL。 |
| [nsg-fixed-upgrade.bicep](../../infra/modules/nsg-fixed-upgrade.bicep)／[fixed_nsg.py](../../scripts/deployment/helpers/fixed_nsg.py) | 既存環境の固定集合への限定移行と条件照合。新規構築用規則と別設計にはしない。 |

既存移行では親NSGの規則を一括消去せず、所有・条件の照合、旧子規則の安全な順位移動、新固定tupleの設定と読戻し、旧子規則の削除を順に行う。未解決の応答や競合がある場合は、保護stateを確認せずに再送しない。構築完了後の企業手動変更は運用責任として扱い、ハブ＆スポークの設定を公開パラメターへ逆輸入しない。

## 実証方法・判定・結果の管理

本書へ特定環境の配備記録・実IP・実ID・ログ原本を転記しない。公開する結果は合成条件の検証とし、実環境の検証結果原本はリポジトリ直下の保護領域で管理する。仕様照合、資材実装、合成検証、Azure読戻し、アプリ・保守操作の成功、新規再構築E2Eを区別する。読戻し成功を全規則のパケット到達の実証へ読み替えない。共通の実証範囲と結果管理は[network-security-validation.md](network-security-validation.md)を参照する。

| 検証 | 確認対象・合格条件 | 公開根拠・実環境での確認方法 |
| --- | --- | --- |
| `VERIFY-NSG-CATALOG` | 76規則の名前・方向・優先度・Allow/Deny・プロトコル・両ポート・アドレス参照・設計IDが正本と一致。 | [優先度試験](../../tests/test_nsg_rule_priorities.py)、[IP所有境界](../../tests/test_parameter_ip_ownership.py)、[固定upgrade](../../tests/test_fixed_nsg_upgrade.py)。合成試験結果は[共通検証記録](network-security-validation.md)を参照する。 |
| `VERIFY-NSG-MIGRATION` | 未所有規則、tuple違い、版変更、競合、未知応答を検出し、安全確認前に旧Allowを消さない。 | [固定移行試験](../../tests/test_fixed_nsg_migration.py)、[配備順序](../../tests/test_nsg_deployment_order.py)、[既存NW維持](../../tests/test_network_preservation.py)。 |
| `VERIFY-NSG-AUDIT` | 監査Blob PE全IPの送受信4規則、DNS／認可、管理VMのMIからの読取が対応する。 | [監査Storage合成試験](../../tests/test_audit_storage_network.py)。実環境では値を表示せず到達・container scope認可・Blob読取を別々に確認する。 |
| `VERIFY-NSG-READBACK` | 構築IPで展開した76tuple、NSG関連付け、PE policy、既定規則、終端Denyの有無をAzure GETで完全照合する。 | 保護stateに対象所有・展開条件・応答・照合判定を保存する。親NSGの更新成功だけで合格にしない。 |
| `VERIFY-NSG-FLOWS` | AppGW→APIM／Portal、APIM→Foundry／Broker、ACA／管理VM→PE、各保守clientのTLS／認可／操作が成立する。 | 名前解決、new connection、経路、Firewall、TLS、token、RBACを独立に確認する。利用していないサービス依存の規則まで全実通信を生成したとは扱わない。 |
| `VERIFY-NSG-NTP` | NSG／Firewall許可と時刻状態を分け、管理VMの選択source・同期、APIMの時刻成立と診断を確認する。 | Firewall AllowはUDP要求の許可の証拠であり、NTP応答やdaemon同期の証拠ではない。APIM内部daemon状態は公開APIにないため公開API時刻・関連診断と不可視の限界を区別する。 |
| `VERIFY-NSG-FRESH-BUILD` | 新規構築で同じ固定集合と運用機能を再現する。 | Bicepと合成試験で再現条件を確認する。既存環境への限定適用は、未実施の新規全環境再構築E2Eの成功を意味しない。 |

## 公式仕様と調査の位置づけ

| 仕様 | 参照・採用への影響 |
| --- | --- |
| NSG評価・stateful・既定規則 | [NSG overview](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview)。優先度、応答、新規接続、既定範囲を区別する。 |
| サービスタグ | [Service tags overview](https://learn.microsoft.com/en-us/azure/virtual-network/service-tags-overview)。タグへの到達と認可を区別する。 |
| AppGW v2 NSG／Network Isolation | [Infrastructure configuration](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure#network-security-groups)、[Private deployment](https://learn.microsoft.com/en-us/azure/application-gateway/application-gateway-private-deployment)。標準v2の制約とIsolation例外を混同しない。 |
| APIM VNet基盤依存 | [Virtual network reference](https://learn.microsoft.com/en-us/azure/api-management/virtual-network-reference)。管理3443、counter4290、cache6381–6383、Azure依存、NTP123を明示する。 |
| ACA NSG／送信 | [Firewall integration](https://learn.microsoft.com/en-us/azure/container-apps/firewall-integration#nsg-allow-rules)、[Use Azure Firewall](https://learn.microsoft.com/en-us/azure/container-apps/use-azure-firewall)。内部node portとmanaged基盤依存を保持する。 |
| Azure提供DNS等の共通IP | [Azure共通IPの仕様](https://learn.microsoft.com/en-us/azure/virtual-network/what-is-ip-address-168-63-129-16)。通常NSG／UDR例外を他DNSや全通信へ一般化しない。 |
| PEのNSG評価 | [Network policies](https://learn.microsoft.com/en-us/azure/private-link/disable-private-endpoint-network-policy)。PE subnetworkへのNSG評価を明示的に有効化する。 |
| Firewallサブネット | [Firewall FAQ](https://learn.microsoft.com/en-us/azure/firewall/firewall-faq)。AzureFirewallSubnetへ顧客NSGを関連付けない。 |

公式ソースコードは提供機構やclientの接続先解決を確認する補助、Issueは条件付きの再現例として使う。公開masterの実装を実配備版の証拠にしたり、Issueを完全な必要宛先の公式契約に置き換えたりしない。詳細な基盤例外とソース参照は[platform-communications.md](platform-communications.md)を参照する。
