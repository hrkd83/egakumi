# NSGの構成と優先度

[共通確認](common.md) · [基盤設定](platform-settings.md) · [NSG設計・通信対応](../reference/architecture/network-security-design.md) · [非NSG通信](../reference/architecture/platform-communications.md)

AppGW、APIM、Private Endpoint、管理VM、Portal用ACA、Broker用ACA、証明書用ACAでそれぞれNSGを使う。AzureFirewallSubnetにはNSGを関連付けない。通信の用途を名前と設計IDで記録する。

- 120から: クライアント要求から発生する通信。
- 220から: この基盤の設計で追加した内部接続・保守通信。
- 320から: Azure製品の仕様上の基盤依存・管理通信。
- 同じNSG・同じ方向・同じ帯では1ずつ採番する。各帯の先頭20番号は追加用に空ける。
- 末尾のカスタム拒否は4096。Azure既定規則を再作成しない。

この分類は基本構成の設計方針であり、Azureの予約番号ではない。同期・キャッシュは内部IP宛てでも
Azure仕様上の通信として300番台へ置く。300番台の名前にはポート・プロトコルを含めない。
[NSGの評価順序](https://learn.microsoft.com/azure/virtual-network/network-security-groups-overview#security-rules)

## 正本とアドレス参照

基本構成の正本は[infra/modules/network/nsg/core-rules.json](../../infra/modules/network/nsg/core-rules.json)のversion 5で、
名称・方向・優先度・Allow/Deny・両ポート・プロトコル・アドレス参照・設計IDを定義する。
以下の表は固定集合の用途を示す。送信元ポートは全件Any。

サブネットCIDRは構築入力から、単IPは構築した資源の割当IPから、PEは構築したPE NICの全IPから
動的に設定する。単IP・PE IPは/32とし、同じサブネットの他資源へ無条件に広げない。
公開クライアントの初期IPv4範囲は`0.0.0.0/0`固定。サービス・タグはAzureが管理する範囲であり、
そのサービスへの認証・認可を意味しない。同じタグを使う複数の用途は説明で区別する。

企業のIP制限、独自DNS、ハブ＆スポーク、管理VMのSSH接続元は、基盤構築後に環境ごとの要件に合わせてAzureポータルで設定する。 [企業ネットワークとの接続](../setup/10-hub-spoke.md) · [管理VMへの本人接続](management-vm-access.md)

| Bicep | 定義区間 |
| --- | --- |
| [network.bicep](../../infra/modules/network/network.bicep) | NSG、サブネット関連付け、静的基盤ルール、NSG診断 |
| [network-bindings.bicep](../../infra/modules/network/network-bindings.bicep) | AppGW／APIM／管理VM割当IP・Foundry／Key Vault全PE IP・終端拒否 |
| [portal-network.bicep](../../infra/modules/network/portal-network.bicep)と[bindings](../../infra/modules/network/portal-network-bindings.bicep) | ACA VIP・ACR全PE IP |
| [credential-broker-network.bicep](../../infra/modules/network/credential-broker-network.bicep) | 共有SQL全PE IP・APIMからPortal／Brokerそれぞれの内部VIPへの許可 |
| [certificate-network.bicep](../../infra/modules/certificates/certificate-network.bicep) | 証明書用ACA、公開TLS・Graph OBO用Key Vault、証明書の状態保存BlobのPE間の許可 |

## AppGW（agw）

### Inbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 120 | `allow-firewall-public-https-ingress` | Firewall公開IP /32 | AppGWサブネットCIDR | TCP／443 | `N-AGW-IN-01` |
| 121 | `allow-client-http-ingress` | `0.0.0.0/0` | AppGWサブネットCIDR | TCP／80 | `N-AGW-IN-02-HTTP` |
| 122 | `allow-client-https-ingress` | `0.0.0.0/0` | AppGWサブネットCIDR | TCP／443 | `N-AGW-IN-02-HTTPS` |
| 320 | `allow-gateway-manager-ingress` | `GatewayManager`タグ | Any | TCP／65200-65535 | `N-AGW-IN-04` |
| 321 | `allow-azure-load-balancer-ingress` | `AzureLoadBalancer`タグ | Any | Any／Any | `N-AGW-IN-05` |
| 4096 | `deny-any-ingress` | Any | Any | Any／Any | `N-AGW-IN-TERMINAL` |

### Outbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 120 | `allow-apim-https-egress` | AppGWサブネットCIDR | APIM内部VIP /32 | TCP／443 | `N-AGW-OUT-01` |
| 121 | `allow-aca-https-egress` | AppGWサブネットCIDR | ACA共有VIP /32 | TCP／443 | `N-AGW-OUT-03` |
| 220 | `allow-key-vault-https-egress` | AppGWサブネットCIDR | Key Vault PE全IP /32 | TCP／443 | `N-AGW-OUT-04` |

## APIM（apim）

### Inbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 120 | `allow-firewall-https-ingress` | FirewallサブネットCIDR | APIMサブネットCIDR | TCP／443 | `N-APIM-IN-01` |
| 220 | `allow-mgmt-vm-https-ingress` | 管理VM private IP /32 | APIMサブネットCIDR | TCP／443 | `N-APIM-IN-VM-CANDIDATE` |
| 320 | `allow-api-management-ingress` | `ApiManagement`タグ | APIMサブネットCIDR | TCP／3443 | `N-APIM-IN-03` |
| 321 | `allow-apim-ratelimits-counter-sync-ingress` | APIMサブネットCIDR | APIMサブネットCIDR | UDP／4290 | `N-APIM-IN-COUNTER` |
| 322 | `allow-apim-internal-cache-ingress` | APIMサブネットCIDR | APIMサブネットCIDR | TCP／6381-6383 | `N-APIM-IN-CACHE` |
| 323 | `allow-azure-load-balancer-ingress` | `AzureLoadBalancer`タグ | Any | Any／Any | `N-APIM-IN-04` |
| 4096 | `deny-any-ingress` | Any | Any | Any／Any | `N-APIM-IN-TERMINAL` |

### Outbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 120 | `allow-foundry-https-egress` | APIMサブネットCIDR | Foundry PE全IP /32 | TCP／443 | `N-APIM-OUT-01` |
| 221 | `allow-key-vault-https-egress` | APIMサブネットCIDR | 業務用Key Vault PE全IP /32 | TCP／443 | `N-APIM-OUT-03` |
| 223 | `allow-broker-https-egress` | APIMサブネットCIDR | Broker内部VIP /32 | TCP／443 | `N-APIM-OUT-BROKER-HTTPS` |
| 320 | `allow-storage-egress` | APIMサブネットCIDR | `Storage`タグ | TCP／443 | `N-APIM-OUT-04` |
| 321 | `allow-sql-egress` | APIMサブネットCIDR | `Sql`タグ | TCP／1433 | `N-APIM-OUT-05` |
| 322 | `allow-key-vault-egress` | APIMサブネットCIDR | `AzureKeyVault`タグ | TCP／443 | `N-APIM-OUT-06` |
| 323 | `allow-monitor-egress` | APIMサブネットCIDR | `AzureMonitor`タグ | TCP／1886, 443 | `N-APIM-OUT-07` |
| 324 | `allow-event-hubs-egress` | APIMサブネットCIDR | `EventHub`タグ | TCP／443, 5671, 5672 | `N-APIM-OUT-08` |
| 325 | `allow-entra-egress` | APIMサブネットCIDR | `AzureActiveDirectory`タグ | TCP／443 | `N-APIM-OUT-09` |
| 326 | `allow-credential-manager-egress` | APIMサブネットCIDR | `AzureConnectors`タグ | TCP／443 | `N-APIM-OUT-10` |
| 327 | `allow-internet-egress` | APIMサブネットCIDR | `Internet`タグ | TCP／80, 443 | `N-APIM-OUT-11` |
| 328 | `allow-time-sync-egress` | APIMサブネットCIDR | `Internet`タグ | UDP／123 | `N-APIM-OUT-12` |
| 329 | `allow-apim-ratelimits-counter-sync-egress` | APIMサブネットCIDR | APIMサブネットCIDR | UDP／4290 | `N-APIM-OUT-COUNTER` |
| 330 | `allow-apim-internal-cache-egress` | APIMサブネットCIDR | APIMサブネットCIDR | TCP／6381-6383 | `N-APIM-OUT-CACHE` |
| 4096 | `deny-any-egress` | Any | Any | Any／Any | `N-APIM-OUT-TERMINAL` |

### NTPとFirewallのDNS依存

APIMの[公式NTP要件](https://learn.microsoft.com/en-us/azure/api-management/virtual-network-reference#internal-infrastructure-and-diagnostics)はOutbound UDP123です。NSGの`allow-time-sync-egress`はInternet宛UDP123を許可します。構築資材のFirewallは、APIM用collectionの`allow-apim-ntp`でAPIMサブネットから宛先`*`へのUDP123を許可します。送信元は構築APIMのサブネットCIDRから取得し、宛先・ポート・ルール作成は固定です。APIMの公式要件は`time.windows.com`への限定を保証していません。

VNetとFirewall上流はAzure標準DNSを使用します。FirewallのDNS Proxyを有効にするだけでは、APIMのDNS問い合わせ先はProxyへ切り替わりません。[Firewallの公式DNS仕様](https://learn.microsoft.com/en-us/azure/firewall/dns-settings#dns-proxy)では、Proxyを使わないクライアントとFirewallの名前解決結果に差が出る可能性を示しています。この差によるNTP遮断を避けるため、FirewallではNTP宛先をFQDNで限定せず、NSGのInternet宛UDP123制限と組み合わせます。

NTPの確認では、対象FirewallとAPIM送信元CIDRからのUDP123を全Actionで検索し、許可と拒否を確認します。`allow-apim-ntp`に一致するAllowは、FirewallのAPIM用UDP123規則で通信が許可されたことを示します。実際のNTP応答・時計同期は別に確認します。

## Private Endpoint（pep）

### Inbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 120 | `allow-apim-foundry-https-ingress` | APIMサブネットCIDR | Foundry PE全IP /32 | TCP／443 | `B-PE-05` |
| 220 | `allow-appgw-key-vault-https-ingress` | AppGWサブネットCIDR | 公開TLS用Key Vault PE全IP /32 | TCP／443 | `B-PE-01` |
| 221 | `allow-apim-key-vault-https-ingress` | APIMサブネットCIDR | 業務用Key Vault PE全IP /32 | TCP／443 | `B-PE-02` |
| 222 | `allow-aca-key-vault-https-ingress` | Portal・BrokerサブネットCIDR | 業務用Key Vault PE全IP /32 | TCP／443 | `B-PE-03` |
| 223 | `allow-mgmt-vm-key-vault-https-ingress` | 管理VM private IP /32 | 業務用・公開TLS用Key VaultのPE全IP /32 | TCP／443 | `B-PE-04` |
| 224 | `allow-mgmt-vm-foundry-https-ingress` | 管理VM private IP /32 | Foundry PE全IP /32 | TCP／443 | `B-PE-06` |
| 225 | `allow-mgmt-vm-sql-1433-ingress` | 管理VM private IP /32 | 共有SQL PE全IP /32 | TCP／1433 | `N-PE-IN-MGMT-SQL` |
| 226 | `allow-aca-sql-1433-ingress` | Portal・BrokerサブネットCIDR | 共有SQL PE全IP /32 | TCP／1433 | `B-PE-07` |
| 227 | `allow-aca-acr-https-ingress` | Portal・BrokerサブネットCIDR | ACR PE全IP /32 | TCP／443 | `B-PE-08` |
| 228 | `allow-mgmt-vm-acr-https-ingress` | 管理VM private IP /32 | ACR PE全IP /32 | TCP／443 | `B-PE-09` |
| 229 | `allow-mgmt-vm-audit-platform-blob-https-ingress` | 管理VM private IP /32 | 基盤監査Storage（`collectPlatform`）のBlob PE全IP /32 | TCP／443 | `N-PEP-IN-MGMT-AUDIT-PLATFORM-BLOB-HTTPS` |
| 230 | `allow-mgmt-vm-audit-foundry-blob-https-ingress` | 管理VM private IP /32 | collectFoundry監査Storage Blob PE全IP /32 | TCP／443 | `N-PEP-IN-MGMT-AUDIT-FOUNDRY-BLOB-HTTPS` |
| 233 | `allow-mgmt-vm-certificate-blob-https-ingress` | 管理VM private IP /32 | 証明書ジョブの状態Storage Blob PE全IP /32 | TCP／443 | [証明書ジョブのNSG定義](../../infra/modules/network/nsg/certificate-rules.json) |
| 234 | `allow-certificate-obo-vault-https-ingress` | 証明書用ACAサブネットCIDR | OBO署名用Key Vault PE全IP /32 | TCP／443 | [OBOのNSG定義](../../infra/modules/network/nsg/obo-rules.json) |
| 235 | `allow-broker-obo-vault-https-ingress` | BrokerサブネットCIDR | OBO署名用Key Vault PE全IP /32 | TCP／443 | [OBOのNSG定義](../../infra/modules/network/nsg/obo-rules.json) |
| 236 | `allow-management-obo-vault-https-ingress` | 管理VM private IP /32 | OBO署名用Key Vault PE全IP /32 | TCP／443 | [OBOのNSG定義](../../infra/modules/network/nsg/obo-rules.json) |
| 4096 | `deny-any-ingress` | Any | Any | Any／Any | `B-PE-14` |

### Outbound

カスタムルールは作成しない。Azure既定規則を維持する。

## 管理VM（management）

### Inbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 4096 | `deny-any-ingress` | Any | Any | Any／Any | `B-MGMT-19` |

### Outbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 220 | `allow-key-vault-https-egress` | 管理VM private IP /32 | 業務用・公開TLS用Key VaultのPE全IP /32 | TCP／443 | `B-MGMT-02` |
| 221 | `allow-acr-https-egress` | 管理VM private IP /32 | ACR PE全IP /32 | TCP／443 | `B-MGMT-03` |
| 222 | `allow-apim-https-egress` | 管理VM private IP /32 | APIMサブネットCIDR | TCP／443 | `B-MGMT-18` |
| 223 | `allow-foundry-https-egress` | 管理VM private IP /32 | Foundry PE全IP /32 | TCP／443 | `B-MGMT-21` |
| 224 | `allow-sql-1433-egress` | 管理VM private IP /32 | 共有SQL PE全IP /32 | TCP／1433 | `N-MGMT-OUT-SQL` |
| 226 | `allow-aca-https-egress` | 管理VM private IP /32 | ACA共有VIP /32 | TCP／443 | `N-MGMT-OUT-ACA-HTTPS` |
| 227 | `allow-audit-platform-blob-https-egress` | 管理VM private IP /32 | 基盤監査Storage（`collectPlatform`）のBlob PE全IP /32 | TCP／443 | `N-MGMT-OUT-AUDIT-PLATFORM-BLOB-HTTPS` |
| 228 | `allow-audit-foundry-blob-https-egress` | 管理VM private IP /32 | collectFoundry監査Storage Blob PE全IP /32 | TCP／443 | `N-MGMT-OUT-AUDIT-FOUNDRY-BLOB-HTTPS` |
| 229 | `allow-certificate-blob-https-egress` | 管理VM private IP /32 | 証明書ジョブの状態Storage Blob PE全IP /32 | TCP／443 | [証明書ジョブのNSG定義](../../infra/modules/network/nsg/certificate-rules.json) |
| 230 | `allow-obo-vault-https-egress` | 管理VM private IP /32 | OBO署名用Key Vault PE全IP /32 | TCP／443 | [OBOのNSG定義](../../infra/modules/network/nsg/obo-rules.json) |
| 320 | `allow-azure-resource-manager-egress` | 管理VMサブネットCIDR | `AzureResourceManager`タグ | TCP／443 | `B-MGMT-06` |
| 321 | `allow-entra-egress` | 管理VMサブネットCIDR | `AzureActiveDirectory`タグ | TCP／443 | `B-MGMT-07` |
| 322 | `allow-monitor-egress` | 管理VMサブネットCIDR | `AzureMonitor`タグ | TCP／443 | `B-MGMT-08` |
| 323 | `allow-storage-egress` | 管理VMサブネットCIDR | `Storage`タグ | TCP／443 | `B-MGMT-09` |
| 324 | `allow-internet-egress` | 管理VMサブネットCIDR | `Internet`タグ | TCP／80, 443 | `B-MGMT-10` |
| 4096 | `deny-any-egress` | Any | Any | Any／Any | `B-MGMT-20` |

管理VMはUbuntu 24.04のChronyで、AzureホストのPHCを唯一の同期元にします。`/dev/ptp_hyperv`のcharacter deviceと`clock_name=hyperv`、PHC／`AZUR`の選択と`Normal`の同期状態を確認します。VMBus／PHCはIPを使いません。APIM向けUDP123はAPIM専用の規則で許可します。資材・Azure設定の読戻し・実際の時計同期は別に記録します。

### ACA Dev API向けFirewall許可

管理VMのACAライブ保守通信は、NSGのInternet宛TCP443許可に加え、Firewallの
`rcg-egress`（400）／`allow-vm-egress`（210）にある`allow-mgmt-vm-aca-dev-https`で許可します。
送信元は構築した管理サブネットのCIDR、宛先はACAと同じ構築リージョンから生成する
単一の`<region>.azurecontainerapps.dev`、プロトコルはHTTPS443です。初期常設とし、
作成フラグや追加FQDNの公開入力は設けません。TLS inspectionは無効です。

この宛先はリージョン共用のDev gatewayです。Firewallは資源パスやテナントを限定しないため、
構築したApp・環境・Jobへの操作は、担当者本人へ作業時に付与したAzure RBACと保守clientのinventoryで制限します。
[公式CLI](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/containerapp/custom.py)が扱う
metadata返却先・replicaの`logStreamEndpoint`を照合し、TLS、ライブログ本文、execを個別に検証します。
規則の作成・読戻しだけで、各保守操作の成功とは判定しません。

## Portal用ACA（portal）

APIM→ACAのHTTPS443、ACA→SQL PEのTCP1433、ACA→Key Vault PEのHTTPS443は、
初期構成ではVNet内の直接経路を使います。ACA／APIMの既定の`0.0.0.0/0`経路より、
VNet・Private Endpointへの具体的な経路が優先されるため、Firewall規則には登録しません。
通信の許可は送信元と宛先のNSGに記録します。Firewallには現在の経路で通過する通信だけを登録し、
将来の経路変更用の許可は含めません。

Portal用のACA環境は、必要な受信と送信を明示して両方向の残りを拒否します。
同じPortalサブネット内の基盤通信は両方向で許可し、他サブネットとの接続は個別の規則で許可します。
[ACA基盤のNSG要件](https://learn.microsoft.com/en-us/azure/container-apps/firewall-integration#nsg-allow-rules)

### Inbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 120 | `allow-firewall-https-ingress` | FirewallサブネットCIDR | PortalサブネットCIDR | TCP／443, 31443 | `B-ACA-03` |
| 222 | `allow-mgmt-vm-https-ingress` | 管理VM private IP /32 | PortalサブネットCIDR | TCP／443, 31443 | `N-ACA-IN-MGMT-HTTPS` |
| 320 | `allow-azure-load-balancer-ingress` | `AzureLoadBalancer`タグ | PortalサブネットCIDR | TCP／30000-32767 | `B-ACA-05` |
| 321 | `allow-aca-internal-ingress` | PortalサブネットCIDR | PortalサブネットCIDR | Any／Any | `B-ACA-INTERNAL-IN` |
| 4096 | `deny-any-ingress` | Any | Any | Any／Any | `N-ACA-IN-TERMINAL` |

### Outbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 120 | `allow-appgw-https-egress` | PortalサブネットCIDR | AppGW公開IP /32 | TCP／443 | `B-ACA-11` |
| 220 | `allow-key-vault-https-egress` | PortalサブネットCIDR | 業務用Key Vault PE全IP /32 | TCP／443 | `B-ACA-06` |
| 221 | `allow-sql-1433-egress` | PortalサブネットCIDR | 共有SQL PE全IP /32 | TCP／1433 | `B-ACA-07` |
| 222 | `allow-acr-https-egress` | PortalサブネットCIDR | ACR PE全IP /32 | TCP／443 | `B-ACA-08` |
| 320 | `allow-aca-internal-egress` | PortalサブネットCIDR | PortalサブネットCIDR | Any／Any | `B-ACA-02` |
| 321 | `allow-entra-egress` | PortalサブネットCIDR | `AzureActiveDirectory`タグ | TCP／443 | `B-ACA-12` |
| 322 | `allow-monitor-egress` | PortalサブネットCIDR | `AzureMonitor`タグ | TCP／443 | `B-ACA-14` |
| 323 | `allow-mcr-egress` | PortalサブネットCIDR | `MicrosoftContainerRegistry`タグ | TCP／443 | `B-ACA-15` |
| 324 | `allow-afd-first-party-egress` | PortalサブネットCIDR | `AzureFrontDoor.FirstParty`タグ | TCP／443 | `B-ACA-16` |
| 325 | `allow-firewall-routed-public-https-egress` | PortalサブネットCIDR | `Internet`タグ | TCP／443 | `B-ACA-21` |
| 4096 | `deny-any-egress` | Any | Any | Any／Any | `N-ACA-OUT-TERMINAL` |

## Broker専用ACA（`broker`）

Brokerの業務入口をAPIMのサブネットに限定する。専用環境の基盤通信と必要な送信を許可し、受信・送信とも末尾で拒否する。

### Inbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 220 | `allow-apim-https-ingress` | APIMサブネットCIDR | BrokerサブネットCIDR | TCP／443, 31443 | `N-BROKER-APIM-HTTPS-INGRESS` |
| 320 | `allow-azure-load-balancer-ingress` | `AzureLoadBalancer`タグ | BrokerサブネットCIDR | TCP／30000-32767 | `N-BROKER-AZURE-LOAD-BALANCER-INGRESS` |
| 321 | `allow-broker-internal-ingress` | BrokerサブネットCIDR | BrokerサブネットCIDR | Any／Any | `N-BROKER-ACA-INTERNAL-INGRESS` |
| 4096 | `deny-any-ingress` | Any | Any | Any／Any | `N-BROKER-DENY-ANY-INGRESS` |

### Outbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 220 | `allow-key-vault-https-egress` | BrokerサブネットCIDR | 業務用Key Vault PE全IP /32 | TCP／443 | `N-BROKER-KEY-VAULT-HTTPS-EGRESS` |
| 224 | `allow-obo-vault-https-egress` | BrokerサブネットCIDR | OBO署名用Key Vault PE全IP /32 | TCP／443 | [OBOのNSG定義](../../infra/modules/network/nsg/obo-rules.json) |
| 221 | `allow-sql-1433-egress` | BrokerサブネットCIDR | 共有SQL PE全IP /32 | TCP／1433 | `N-BROKER-SQL-1433-EGRESS` |
| 222 | `allow-acr-https-egress` | BrokerサブネットCIDR | ACR PE全IP /32 | TCP／443 | `N-BROKER-ACR-HTTPS-EGRESS` |
| 223 | `allow-arm-https-egress` | BrokerサブネットCIDR | `AzureResourceManager`タグ | TCP／443 | `N-BROKER-ARM-HTTPS-EGRESS` |
| 320 | `allow-broker-internal-egress` | BrokerサブネットCIDR | BrokerサブネットCIDR | Any／Any | `N-BROKER-ACA-INTERNAL-EGRESS` |
| 321 | `allow-entra-egress` | BrokerサブネットCIDR | `AzureActiveDirectory`タグ | TCP／443 | `N-BROKER-ENTRA-EGRESS` |
| 322 | `allow-monitor-egress` | BrokerサブネットCIDR | `AzureMonitor`タグ | TCP／443 | `N-BROKER-MONITOR-EGRESS` |
| 323 | `allow-mcr-egress` | BrokerサブネットCIDR | `MicrosoftContainerRegistry`タグ | TCP／443 | `N-BROKER-MCR-EGRESS` |
| 324 | `allow-afd-first-party-egress` | BrokerサブネットCIDR | `AzureFrontDoor.FirstParty`タグ | TCP／443 | `N-BROKER-AFD-FIRST-PARTY-EGRESS` |
| 4096 | `deny-any-egress` | Any | Any | Any／Any | `N-BROKER-DENY-ANY-EGRESS` |

## NSGの診断

各NSGの `NetworkSecurityGroupEvent` と `NetworkSecurityGroupRuleCounter` をLog Analyticsと監査Storageへ送る。モデルAPIはAPIMからFoundry Private Endpointへ接続し、管理VMから監査Storageを調査するときも対象のPrivate EndpointとBlobの閲覧権限を使う。 [監査ログの設計](../reference/audit/audit-log-design.md)

## 既定規則と末尾拒否の扱い

全NSGでAzure既定のInbound 65000/65001/65500、Outbound 65000/65001/65500を維持する。
NSGはstatefulなので、受信で許可した接続の応答のための逆方向Allowは作成しない。
[既定規則と接続状態](https://learn.microsoft.com/azure/virtual-network/network-security-groups-overview)

AppGWとAPIMのAzureLoadBalancer受信は、送信元タグ以外をAnyに統一して明示する。
全NSGの受信末尾にDenyを置く。
APIM、管理VM、Portal・Broker・証明書の各ACAの送信末尾にもDenyを置く。AppGWとPEの送信末尾には置かない。

標準AppGW v2はInternet向け送信包括許可を必要とするため、既定Outbound許可を維持する。
AppGWの3件の明示送信Allowは通信設計の記録でもあり、これらだけで送信範囲を限定したとは解釈しない。
Network Isolationの例外をSKU名だけで適用しない。
[AppGW NSG要件](https://learn.microsoft.com/azure/application-gateway/configuration-infrastructure#network-security-groups)・
[Network Isolation](https://learn.microsoft.com/azure/application-gateway/application-gateway-private-deployment)

ACAの公開HTTPSは、NSGの`Internet`タグ宛TCP443とFirewallの宛先制御を組み合わせる。
基盤パッケージ配布のCDNを個別サービスタグで網羅できないため、固定の`0.0.0.0/0` UDRで構築Firewallへ転送し、
Firewallの許可条件に一致しない通信はそこで拒否する。ACAサブネットの既定送信アクセスとルート表のBGP伝搬は無効にする。
基盤とNSG限定配備は、既存の関連付けと経路を検査し、Firewallを迂回できる設定を検出した場合は停止する。
ACRのログイン・専用データエンドポイントにはPrivate Endpointの全IP、共有SQLにはProxy接続のTCP1433を使う。
[ACAのFirewall要件](https://learn.microsoft.com/en-us/azure/container-apps/use-azure-firewall) · [ACRのデータエンドポイント](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-firewall-rules#allow-access-to-rest-and-data-endpoints)

PEはサービス側から新規接続を開始しない。PEサブネットのNSGポリシーは
`NetworkSecurityGroupEnabled`とし、受信Allowと末尾Denyで制御する。同サブネットへ別NICを置く場合は
そのNICの送信を別途設計する。
[PEポリシー](https://learn.microsoft.com/azure/private-link/disable-private-endpoint-network-policy)

標準DNSはVNet DHCP・Firewall上流を空配列へ固定する。Azure提供DNS、VM IMDS、Agent host channel、
DHCP、host time等を通常NSGのAllowへ置き換えない。[非NSG通信と運用条件](../reference/architecture/platform-communications.md)を参照する。

## 新規構築と構築の再開

新規構築は同じ固定Bicepと[正式構築手順](../setup/08-build-infra.md)を使う。
同じNSGの子ルールへの書込は、`batchSize(1)`と`dependsOn`で直列化する。
モジュールは`network → networkBindings → portalNetwork → portal → portalNetworkBindings → credentialNetwork`の
順で実行し、各モジュール内でも同じNSGを更新する子ルールに依存関係を置く。
ACAの末尾Denyは、共有SQL向けNSG許可と所有Firewall規則が完了した後に設定する。
既存NSGの親は再PUTせず、タグは専用Tags API、ルールは子リソースで更新する。
この順序と固定ルールの全条件をコンパイル済み資材で確認し、新規構築と再適用で同じ定義を使う。

構築を再開する場合、`azd up`の基盤配備前に、作成済みNSGの規則名と条件を現行の定義と照合する。各規則の照合に必要な割当IPとPrivate Endpointだけを読み取るため、後続資源の未作成は許容する。現行定義にない規則や、優先度・ポート・アドレスが異なる規則を検知した場合は、規則に変更を加えず停止する。基盤配備後は、全規則・関連付け・実IPを読み戻して一致を確認する。
