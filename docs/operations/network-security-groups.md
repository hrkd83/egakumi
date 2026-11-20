# NSGの構成と優先度

[共通確認](common.md) · [基盤設定](platform-settings.md) · [非NSG通信](../reference/platform-communications.md)

初期構築は5 NSG・カスタム72ルールの固定集合とする。AzureFirewallSubnetにはNSGを関連付けない。
通信の用途を1ルールずつ名前と設計IDで記録し、ルールの作成有無を公開params／configで制御しない。

- 120から: クライアント要求から発生する通信。
- 220から: この基盤の設計で追加した内部接続・保守通信。
- 320から: Azure製品の仕様上の基盤依存・管理通信。
- 同じNSG・同じ方向・同じ帯では1ずつ採番する。各帯の先頭20番号は追加用に空ける。
- 末尾のカスタム拒否は4096。Azure既定規則を再作成しない。

この分類は資材の設計方針であり、Azureの予約番号ではない。同期・キャッシュは内部IP宛てでも
Azure仕様上の通信として300番台へ置く。300番台の名前にはポート・プロトコルを含めない。
[NSGの評価順序](https://learn.microsoft.com/azure/virtual-network/network-security-groups-overview#security-rules)

## 正本とアドレス参照

正本は[infra/nsg-rule-priorities.json](../../infra/nsg-rule-priorities.json)のversion 2で、
名称・方向・優先度・Allow/Deny・両ポート・プロトコル・アドレス参照・設計IDを定義する。
以下の表はその72件に対応する。送信元ポートは全件Any。

サブネットCIDRは構築入力から、単IPは構築した資源の割当IPから、PEは構築したPE NICの全IPから
動的に設定する。単IP・PE IPは/32とし、同じサブネットの他資源へ無条件に広げない。
公開クライアントの初期IPv4範囲は`0.0.0.0/0`固定。サービス・タグはAzureが管理する範囲であり、
そのサービスへの認証・認可を意味しない。同じタグを使う複数の用途は説明で区別する。

企業のIP制限、独自DNS、ハブ＆スポーク、管理VMへのSSH接続元は構築後にPortalで手動編集する。
公開入力の`network.clientAddressPrefixes`、`network.certificatePublicProbeEnabled`、
`network.operatorAddressPrefixes`、`network.operatorProtocol`、`network.operatorPorts`、`network.dnsServers`、`network.firewall.dnsServers`、
`network.ntpAddressPrefixes`、`network.ntpFqdns`、`network.managementAdditionalSecurityRules`、
`network.apimKeyVaultAccessEnabled`と`gateway.grpc`は廃止し、空値でも入力検査で拒否する。
NSGの初期集合を変える新しいスイッチを追加しない。

| Bicep | 定義区間 |
| --- | --- |
| [network.bicep](../../infra/modules/network.bicep) | 5 NSG・サブネット関連付け・静的基盤ルール |
| [network-bindings.bicep](../../infra/modules/network-bindings.bicep) | AppGW／APIM／管理VM割当IP・Foundry／Key Vault全PE IP・終端拒否 |
| [portal-network.bicep](../../infra/modules/portal-network.bicep)と[bindings](../../infra/modules/portal-network-bindings.bicep) | ACA VIP・ACR全PE IP |
| [credential-broker-network.bicep](../../infra/modules/credential-broker-network.bicep) | 共有SQL全PE IP・APIM→ACA共有VIP |

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
| 120 | `allow-apim-http-egress` | AppGWサブネットCIDR | APIM内部VIP /32 | TCP／80 | `N-AGW-OUT-01` |
| 121 | `allow-aca-http-egress` | AppGWサブネットCIDR | ACA共有VIP /32 | TCP／80 | `N-AGW-OUT-03` |
| 220 | `allow-key-vault-https-egress` | AppGWサブネットCIDR | Key Vault PE全IP /32 | TCP／443 | `N-AGW-OUT-04` |

## APIM（apim）

### Inbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 120 | `allow-appgw-http-ingress` | AppGWサブネットCIDR | APIMサブネットCIDR | TCP／80 | `N-APIM-IN-01` |
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
| 220 | `allow-aca-https-egress` | APIMサブネットCIDR | ACA共有VIP /32 | TCP／443 | `N-APIM-OUT-02` |
| 221 | `allow-key-vault-https-egress` | APIMサブネットCIDR | Key Vault PE全IP /32 | TCP／443 | `N-APIM-OUT-03` |
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

NTPの確認では、対象FirewallとAPIM送信元CIDRからのUDP123を全Actionで検索し、許可だけでなく拒否も確認します。`allow-apim-ntp`に一致するAllowは、その通信がFirewallのAPIM用UDP123規則で許可されたことを示します。NTP応答・時計同期の成功は、このログだけでは確認できません。既存環境へまとめて反映する際は、資材・Azureの読戻し・実時計同期をそれぞれ区別して確認します。

## Private Endpoint（pep）

### Inbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 120 | `allow-apim-foundry-https-ingress` | APIMサブネットCIDR | Foundry PE全IP /32 | TCP／443 | `B-PE-05` |
| 220 | `allow-appgw-key-vault-https-ingress` | AppGWサブネットCIDR | Key Vault PE全IP /32 | TCP／443 | `B-PE-01` |
| 221 | `allow-apim-key-vault-https-ingress` | APIMサブネットCIDR | Key Vault PE全IP /32 | TCP／443 | `B-PE-02` |
| 222 | `allow-aca-key-vault-https-ingress` | ACAサブネットCIDR | Key Vault PE全IP /32 | TCP／443 | `B-PE-03` |
| 223 | `allow-mgmt-vm-key-vault-https-ingress` | 管理VM private IP /32 | Key Vault PE全IP /32 | TCP／443 | `B-PE-04` |
| 224 | `allow-mgmt-vm-foundry-https-ingress` | 管理VM private IP /32 | Foundry PE全IP /32 | TCP／443 | `B-PE-06` |
| 225 | `allow-mgmt-vm-sql-1433-ingress` | 管理VM private IP /32 | 共有SQL PE全IP /32 | TCP／1433 | `N-PE-IN-MGMT-SQL` |
| 226 | `allow-aca-sql-1433-ingress` | ACAサブネットCIDR | 共有SQL PE全IP /32 | TCP／1433 | `B-PE-07` |
| 227 | `allow-aca-acr-https-ingress` | ACAサブネットCIDR | ACR PE全IP /32 | TCP／443 | `B-PE-08` |
| 228 | `allow-mgmt-vm-acr-https-ingress` | 管理VM private IP /32 | ACR PE全IP /32 | TCP／443 | `B-PE-09` |
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
| 220 | `allow-key-vault-https-egress` | 管理VM private IP /32 | Key Vault PE全IP /32 | TCP／443 | `B-MGMT-02` |
| 221 | `allow-acr-https-egress` | 管理VM private IP /32 | ACR PE全IP /32 | TCP／443 | `B-MGMT-03` |
| 222 | `allow-apim-https-egress` | 管理VM private IP /32 | APIMサブネットCIDR | TCP／443 | `B-MGMT-18` |
| 223 | `allow-foundry-https-egress` | 管理VM private IP /32 | Foundry PE全IP /32 | TCP／443 | `B-MGMT-21` |
| 224 | `allow-sql-1433-egress` | 管理VM private IP /32 | 共有SQL PE全IP /32 | TCP／1433 | `N-MGMT-OUT-SQL` |
| 225 | `allow-aca-http-egress` | 管理VM private IP /32 | ACA共有VIP /32 | TCP／80 | `N-MGMT-OUT-ACA-HTTP` |
| 226 | `allow-aca-https-egress` | 管理VM private IP /32 | ACA共有VIP /32 | TCP／443 | `N-MGMT-OUT-ACA-HTTPS` |
| 320 | `allow-azure-resource-manager-egress` | 管理VMサブネットCIDR | `AzureResourceManager`タグ | TCP／443 | `B-MGMT-06` |
| 321 | `allow-entra-egress` | 管理VMサブネットCIDR | `AzureActiveDirectory`タグ | TCP／443 | `B-MGMT-07` |
| 322 | `allow-monitor-egress` | 管理VMサブネットCIDR | `AzureMonitor`タグ | TCP／443 | `B-MGMT-08` |
| 323 | `allow-storage-egress` | 管理VMサブネットCIDR | `Storage`タグ | TCP／443 | `B-MGMT-09` |
| 324 | `allow-internet-egress` | 管理VMサブネットCIDR | `Internet`タグ | TCP／80, 443 | `B-MGMT-10` |
| 325 | `allow-time-sync-egress` | 管理VMサブネットCIDR | `Internet`タグ | UDP／123 | `B-MGMT-11` |
| 4096 | `deny-any-egress` | Any | Any | Any／Any | `B-MGMT-20` |

管理VMのNTPはFirewallの`allow-mgmt-vm-ntp`で管理サブネットから宛先`*`へのUDP123を許可し、NSGの325でInternet宛に制限します。VNet標準DNSとFirewallの独立した回答差による遮断を避ける設計です。guestへ設定する標準外部NTPは`ntp.ubuntu.com`ですが、既存PHC・OS側pool等も保持します。変更後は他のInternet NTP sourceも到達できるため、実際の選択元・到達性・時刻精度を確認します。Firewall Allowやdaemon稼働だけで外部同期の成功とは扱いません。資材・Azure読戻し・同期結果は別に記録します。

### ACA Dev API向けFirewall許可

管理VMのACAライブ保守通信は、NSGのInternet宛TCP443許可に加え、Firewallの
`rcg-egress`（400）／`allow-vm-egress`（210）にある`allow-mgmt-vm-aca-dev-https`で許可します。
送信元は構築した管理サブネットのCIDR、宛先はACAと同じ構築リージョンから生成する
単一の`<region>.azurecontainerapps.dev`、プロトコルはHTTPS443です。初期常設とし、
作成フラグや追加FQDNの公開入力は設けません。TLS inspectionは無効です。

この宛先はリージョン共用のDev gatewayです。Firewallは資源パスやテナントを限定しないため、
構築したApp・環境・Jobへの操作は管理VMのMI／RBACと保守clientのinventoryで制限します。
[公式CLI](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/containerapp/custom.py)が扱う
metadata返却先・replicaの`logStreamEndpoint`を照合し、TLS、ライブログ本文、execを個別に検証します。
規則の作成・読戻しだけで、各保守操作の成功とは判定しません。

## ACA（aca）

APIM→ACAのHTTPS443、ACA→SQL PEのTCP1433、ACA→Key Vault PEのHTTPS443は、
初期構成ではVNet内の直接経路を使います。ACA／APIMの既定の`0.0.0.0/0`経路より、
VNet・Private Endpointへの具体的な経路が優先されるため、Firewall規則には登録しません。
通信の許可は送信元と宛先のNSGに記録します。Firewallには現在の経路で通過する通信だけを登録し、
将来の経路変更用の許可は含めません。

### Inbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 120 | `allow-appgw-http-ingress` | AppGWサブネットCIDR | ACAサブネットCIDR | TCP／80, 31080 | `B-ACA-03` |
| 220 | `allow-apim-https-ingress` | APIMサブネットCIDR | ACAサブネットCIDR | TCP／443, 31443 | `B-ACA-04` |
| 221 | `allow-mgmt-vm-http-ingress` | 管理VM private IP /32 | ACAサブネットCIDR | TCP／80, 31080 | `N-ACA-IN-MGMT-HTTP` |
| 222 | `allow-mgmt-vm-https-ingress` | 管理VM private IP /32 | ACAサブネットCIDR | TCP／443, 31443 | `N-ACA-IN-MGMT-HTTPS` |
| 320 | `allow-azure-load-balancer-ingress` | `AzureLoadBalancer`タグ | ACAサブネットCIDR | TCP／30000-32767 | `B-ACA-05` |

### Outbound

| 優先度 | 規則名 | 送信元 | 宛先 | プロトコル／宛先ポート | 設計ID |
| --- | --- | --- | --- | --- | --- |
| 120 | `allow-appgw-https-egress` | ACAサブネットCIDR | AppGW公開IP /32 | TCP／443 | `B-ACA-11` |
| 220 | `allow-key-vault-https-egress` | ACAサブネットCIDR | Key Vault PE全IP /32 | TCP／443 | `B-ACA-06` |
| 221 | `allow-sql-1433-egress` | ACAサブネットCIDR | 共有SQL PE全IP /32 | TCP／1433 | `B-ACA-07` |
| 222 | `allow-acr-https-egress` | ACAサブネットCIDR | ACR PE全IP /32 | TCP／443 | `B-ACA-08` |
| 223 | `allow-arm-https-egress` | ACAサブネットCIDR | `AzureResourceManager`タグ | TCP／443 | `B-ACA-13` |
| 320 | `allow-aca-internal-egress` | ACAサブネットCIDR | ACAサブネットCIDR | Any／Any | `B-ACA-02` |
| 321 | `allow-entra-egress` | ACAサブネットCIDR | `AzureActiveDirectory`タグ | TCP／443 | `B-ACA-12` |
| 322 | `allow-monitor-egress` | ACAサブネットCIDR | `AzureMonitor`タグ | TCP／443 | `B-ACA-14` |
| 323 | `allow-mcr-egress` | ACAサブネットCIDR | `MicrosoftContainerRegistry`タグ | TCP／443 | `B-ACA-15` |
| 324 | `allow-afd-first-party-egress` | ACAサブネットCIDR | `AzureFrontDoor.FirstParty`タグ | TCP／443 | `B-ACA-16` |

## 既定規則と末尾拒否の扱い

5 NSGそれぞれでAzure既定のInbound 65000/65001/65500、Outbound 65000/65001/65500を維持する。
NSGはstatefulなので、受信で許可した接続の応答のための逆方向Allowは作成しない。
[既定規則と接続状態](https://learn.microsoft.com/azure/virtual-network/network-security-groups-overview)

AppGWとAPIMのAzureLoadBalancer受信は、送信元タグ以外をAnyに統一して明示する。
AppGW／APIM／PE／管理VMの受信末尾にDenyを置き、ACAの受信・送信にはカスタムAny末尾Denyを置かない。
APIM／管理VMの送信末尾にはDenyを置く。AppGWとPEの送信末尾には置かない。

標準AppGW v2はInternet向け送信包括許可を必要とするため、既定Outbound許可を維持する。
AppGWの3件の明示送信Allowは通信設計の記録でもあり、これらだけで送信範囲を限定したとは解釈しない。
Network Isolationの例外をSKU名だけで適用しない。
[AppGW NSG要件](https://learn.microsoft.com/azure/application-gateway/configuration-infrastructure#network-security-groups)・
[Network Isolation](https://learn.microsoft.com/azure/application-gateway/application-gateway-private-deployment)

ACAにはInternet／Storageの追加包括Allowを初期作成しない。必要な基盤通信の完全な限定を
検証する前に末尾Denyを追加しない。アプリ／Job・image取得・scale・更新・復旧の運用成立は別途確認する。
[ACA NSG要件](https://learn.microsoft.com/azure/container-apps/firewall-integration#nsg-allow-rules)

PEはサービス側から新規接続を開始しない。PEサブネットのNSGポリシーは
`NetworkSecurityGroupEnabled`とし、受信Allowと末尾Denyで制御する。同サブネットへ別NICを置く場合は
そのNICの送信を別途設計する。
[PEポリシー](https://learn.microsoft.com/azure/private-link/disable-private-endpoint-network-policy)

標準DNSはVNet DHCP・Firewall上流を空配列へ固定する。Azure提供DNS、VM IMDS、Agent host channel、
DHCP、host time等を通常NSGのAllowへ置き換えない。[非NSG通信と運用条件](../reference/platform-communications.md)を参照する。

## 新規構築と既存環境の移行

新規構築は同じ固定Bicepと[正式構築手順](../setup/06-deploy.md)を使う。
同じNSGの子ルールへの書込は、`batchSize(1)`と`dependsOn`で直列化する。
モジュールは`network → networkBindings → portalNetwork → portal → portalNetworkBindings → credentialNetwork`の
順で実行し、各モジュール内でも同じNSGを更新する子ルールに依存関係を置く。
既存NSGの親は再PUTせず、タグは専用Tags API、ルールは子リソースで更新する。
この順序と固定ルールの全条件をコンパイル済み資材で確認し、新規構築と再適用で同じ定義を使う。

既存環境も最終状態は同じ資材に収束させる。今回の関連改修ではPortal HTTP80・gRPC撤去・標準DNS固定を
含むため、旧HTTPS後段へ最終Coreを先に適用しない。

1. Windows側Azure CLIで認証し、入力・Core state・所有RG／VNet／NSG／内部資源・実ルールを読み取る。
   WSLはローカル試験とBicepコンパイルに使用し、Entra認証にLinux側Azure CLIを使わない。
2. [Portal HTTP80移行](platform-settings.md#portal後段をhttp80へ移行する)を先行する。
   Firewall80追加時は443を保持し、対応image／revision・HTTP readinessを確認してAppGW後段とprobeを切り替える。
3. `fixed_nsg.prepare_fixed_nsg_rules`は変更前に全NSGを監査し、完全一致した所有済み旧ルールだけを
   一時優先度へ移す。旧Allowは1000番台、旧末尾Denyは4080とし、旧順序を維持する。
   未知・手動変更・所有不明のルールがあれば変更前に停止する。各変更をETagと保護記録で限定する。
4. [NSG限定資材](../../infra/modules/nsg-fixed-upgrade.bicep)を単独what-ifで検証し、固定72子と
   新ACA NSGだけをIncremental配備する。既存NSGの親、UDR、VNet親、Firewall本体、Public IPは書かない。
   その後、ACA subnetの直前ETagを使った限定PUTでNSG関連付けだけを追加する。
   `complete_fixed_nsg_rules`が5関連付け・全72条件と既定規則を照合した後にだけ、完全一致する旧子を回収する。
   Incremental配備で省いた旧子が削除されたとは判断しない。
5. APIMの通常L7保護を常設し、所有参照を照合した旧gRPC専用domainとAppGW L4設備を撤去する。
   APIM標準名でのHTTPS保守を維持し、Server.Http2だけfalseへ固定する。他customPropertiesと他用途domainを保持する。
6. 全ルール・割当IP・関連付け・非対象UDR／Firewall設定を読み戻し、新規接続で通常API・Portal・保守を確認する。
   過去のPASSや既存接続の継続だけで今回の移行成功としない。

工程は1トランザクションではなく、無停止を保証しない。応答不明・競合・未完了時は再送せず実状態を確認する。
証跡は`.local/<environment>/`の保護領域へ保存する。作業ごとにUUIDと連番で記録し、再実行で初回snapshotを上書きしない。
手動の企業向け追加ルールを、資材所有の旧規則として自動削除しない。
今回のFIXで追加UDR・Peering改修は要求しない。Firewall Policy全体の棚卸しと未確定の運用許可は別工程として残す。

### NSG限定配備の成立条件

`fixed_nsg_upgrade_parameters`は保護した構築設定と実資源からCIDR、割当IP、全PE NIC IP、
所有する既存NSG名を読み取る。新ACA parentのtagsは再構築と同じ構築tagsと固定3タグのunionとする。
実VNetのDNSとFirewall上流DNSがAzure提供の空設定であり、Firewall DNS proxyが有効であることを
NSG変更前に要求する。手動custom DNSがあればこの入口で切り替えず、規則の更新前に停止する。
公開params／configは増やさず、72規則を常に生成する。
`verify_fixed_nsg_upgrade_what_if`は72子の全tupleを照合し、展開診断、対象子のIgnore、
不足、削除、既存parentやNSG以外への書込を拒否する。what-ifのSucceededだけで判断しない。

`associate_fixed_aca_nsg`は72子が揃った後に、ACA subnetの既知writable設定を保持して
NSG参照だけを変更する。現routeTable、delegations、標準送信設定等を保持し、API仕様の
readOnly以外で未対応の項目があれば値を省略せず停止する。現在の関連付けが別NSGなら停止する。
変更前記録・直前ETag・If-Match・一致readbackを必要とし、応答不明や未完了時はPUTを再送しない。

単独ARM配備でも各子へ個別のIf-Matchを指定できないため、直前のNSG現物照合と保護した
計画、配備後の全tuple確認を行う。作業中の別端末による編集を原子的に防ぐ仕組みとは扱わない。
通常のnative hook／direct Coreにも既存規則の準備・完了確認はあるが、Core全体のwhat-ifで
参照未解決のmoduleが省略された場合、このNSG限定検証を代替しない。
