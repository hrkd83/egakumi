# NSGの構成と優先度

[共通確認](common.md) · [基盤設定](platform-settings.md) · [接続元の設定](../setup/04-params.md)

4つのNSGは、業務通信を120から、管理・基盤通信を220から5刻みで定義する。
100–119と200–219の各20番号は、先に評価する規則の追加用に空ける。
末尾のカスタム拒否規則は4096とする。

## 正本と定義の関係

採番の正本は [infra/nsg-rule-priorities.json](../../infra/nsg-rule-priorities.json) である。
Bicepの各モジュールが同じJSONを読み、送信元・宛先・ポート・プロトコル・Allow/Denyは各規則で定義する。

| 定義 | 担当する規則 |
| --- | --- |
| [network.bicep](../../infra/modules/network.bicep) | 静的な入口・Azure基盤依存 |
| [network-bindings.bicep](../../infra/modules/network-bindings.bicep) | 作成後のIPを使う通信・管理者接続・最後の拒否 |
| [portal-network.bicep](../../infra/modules/portal-network.bicep) | 管理VMとContainer AppsからACRへの通信 |
| [credential-broker-network.bicep](../../infra/modules/credential-broker-network.bicep) | APIMから認証窓口、Container AppsからSQL・Vaultへの通信 |

同じNSGには複数モジュールが規則を追加するため、1ファイルだけで番号を決めない。
InboundとOutboundは別に採番できるが、同じ方向では重複できない。
120・125…と220・225…の間にも追加余地を残す。
この区分と余白は資材の整理方針であり、Azureが用途別に予約した番号ではない。
Azureのカスタム規則は100～4096で、低い番号から最初に一致した規則が適用される。
[MicrosoftのNSG評価仕様](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview#security-rules)

## 現行の規則

条件付き規則を含む定義の一覧である。管理者の接続元や各機能の設定により、規則の有無・Allow/Denyを決める。

### AppGW（agw）

| 方向 | priority | 規則名 | 用途 |
| --- | --- | --- | --- |
| Inbound | 120 | `allow-client-web` | 利用者のHTTP/HTTPS |
| Inbound | 125 | `allow-client-grpc` | 利用者のgRPC |
| Inbound | 220 | `allow-certificate-observer` | 証明書の公開到達確認 |
| Inbound | 225 | `allow-gateway-manager` | GatewayManager制御 |
| Inbound | 230 | `allow-load-balancer` | Azure LBのヘルス監視 |
| Inbound | 4096 | `deny-other-inbound` | その他の受信を拒否 |
| Outbound | 120 | `allow-apim-http` | AppGWからAPIMへHTTP |
| Outbound | 125 | `allow-apim-grpc` | AppGWからAPIMへgRPC |
| Outbound | 220 | `allow-key-vault` | AppGWからVault PE |

### APIM（apim）

| 方向 | priority | 規則名 | 用途 |
| --- | --- | --- | --- |
| Inbound | 120 | `allow-agw-api` | AppGWから業務API |
| Inbound | 125 | `allow-agw-grpc` | AppGWからgRPC |
| Inbound | 220 | `allow-apim-management` | APIMの管理制御 |
| Inbound | 225 | `allow-load-balancer` | Azure LBのヘルス監視 |
| Inbound | 230 | `allow-counter-sync-inbound` | APIM内のカウンター同期 |
| Inbound | 235 | `allow-management-grpc` | 管理VMからgRPC確認 |
| Inbound | 4096 | `deny-other-inbound` | その他の受信を拒否 |
| Outbound | 120 | `allow-private-backends` | APIMからPrivate Endpoint |
| Outbound | 125 | `allow-credential-broker` | APIMから認証窓口 |
| Outbound | 220 | `allow-key-vault-pe` | APIMからVault PE |
| Outbound | 225 | `allow-storage` | APIM基盤のStorage依存 |
| Outbound | 230 | `allow-sql-platform` | APIM基盤のSQL依存 |
| Outbound | 235 | `allow-key-vault-platform` | APIM基盤のVault依存 |
| Outbound | 240 | `allow-monitor` | 監視ログ・メトリクス |
| Outbound | 245 | `allow-monitor-event-hubs` | 監視・監査のEvent Hubs |
| Outbound | 250 | `allow-entra` | Entraへの通信 |
| Outbound | 255 | `allow-credential-manager` | Credential Manager依存 |
| Outbound | 260 | `allow-web-dependencies` | 許可済みWeb依存先 |
| Outbound | 265 | `allow-time-sync` | 時刻同期 |
| Outbound | 270 | `allow-counter-sync-outbound` | APIM内のカウンター同期 |
| Outbound | 4096 | `deny-other-outbound` | その他の送信を拒否 |

### Private Endpoint（pep）

| 方向 | priority | 規則名 | 用途 |
| --- | --- | --- | --- |
| Inbound | 120 | `allow-apim-foundry` | APIMからFoundry PE |
| Inbound | 220 | `allow-key-vault` | AppGW・APIMからVault PE |
| Inbound | 225 | `allow-vm-https` | 管理VMからPEへHTTPS |
| Inbound | 230 | `allow-aca-acr` | Container Apps・管理VMからACR |
| Inbound | 235 | `allow-credential-broker-sql` | Container AppsからSQL PE |
| Inbound | 240 | `allow-credential-broker-vault` | Container AppsからVault PE |
| Inbound | 4096 | `deny-other-inbound` | その他の受信を拒否 |

### 管理VM（management / suffix: vm）

| 方向 | priority | 規則名 | 用途 |
| --- | --- | --- | --- |
| Inbound | 220（接続元指定時だけ） | `allow-operator-management` | 承認した管理者の接続 |
| Inbound | 4096 | `deny-other-inbound` | その他の受信を拒否 |
| Outbound | 220 | `allow-private-services` | 管理VMからPrivate Endpoint |
| Outbound | 225 | `allow-apim-grpc-comparison` | 管理VMからAPIMの比較確認 |
| Outbound | 230 | `allow-runner-acr` | 管理VMからACR |
| Outbound | 235 | `allow-approved-entra` | 管理VMからEntra |
| Outbound | 240 | `allow-approved-management-api` | 管理VMからARM |
| Outbound | 245 | `allow-approved-monitor` | 管理VMからMonitor |
| Outbound | 250 | `allow-approved-agent-storage` | 管理VMのagent用Storage |
| Outbound | 255 | `allow-web-dependencies` | 許可済みWeb依存先 |
| Outbound | 260 | `allow-time-sync` | 時刻同期 |
| Outbound | 4096 | `deny-other-outbound` | その他の送信を拒否 |

## Azure提供DNS

VNetとFirewallの`dnsServers`は空配列に固定し、Azure提供DNSを使用する。
Azure提供DNSへの通信は通常のNSG規則で制限されず、明示的に`AzurePlatformDNS`を対象とする規則で制御する。
この資材はその拒否規則を作らないため、AppGW・APIM・管理VMの送信にDNS用のカスタムAllowは作成しない。
既存のDNS用規則を削除しても、DNS設定、他の送受信規則と優先度は変更しない。
[MicrosoftのAzure提供DNSとNSGの扱い](https://learn.microsoft.com/en-us/azure/virtual-network/what-is-ip-address-168-63-129-16#scope-of-azure-platform-virtual-ip)

Incremental配備は省略した子規則を自動削除しないため、既存環境では対象NSGの所有と規則の内容を照合し、
AppGW・APIM・管理VMの`allow-dns-tcp`と`allow-dns-udp`だけを削除する。
反映後はAzure提供DNSの設定と、新しい接続での名前解決・必要な通信を確認する。

## 管理VMへの接続

`network.operatorAddressPrefixes` が空配列または省略の場合、`allow-operator-management` は作成しない。
常時作成する `deny-other-inbound` の4096で、明示的に許可していない受信通信を拒否する。
接続元を指定した場合だけAllow・220を作る。管理VMから見える送信元IPv4/CIDRを指定し、private範囲も使える。

構築資材を同じ環境へ再適用する場合、接続元の設定を外すと `azd up`・`azd provision` の構築前処理が現行のAllow・220を削除する。
同名規則が独自に変更されている場合や、末尾の受信拒否を確認できない場合は停止する。
Incremental配備は省いた子規則を自動削除しないため、Bicepだけの直接配備ではこの削除処理は実行されない。
[ARMのIncremental配備](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deployment-modes#incremental-mode)
通常保守では初期構築コマンドを使わず、[共通確認](common.md)に従って対象の規則に限定した操作を決める。

Bastionを別途使う場合、管理VMへの送信元は通常AzureBastionSubnetのCIDRである。
管理者PCやBastionのpublic IPをVM側の許可元として指定しない。
この資材はBastion・VPN等の接続経路を作らず、管理VMのNICにpublic IPを付けない。
[接続元とBastionの設定](../setup/04-params.md)、
[MicrosoftのBastion対象VM側NSG要件](https://learn.microsoft.com/en-us/azure/bastion/bastion-nsg#target-vm-subnet)を参照する。

## 既定規則は変更しない

Azureが持つ次の既定規則は、この資材では変更しない。
カスタム規則として再作成しない。

| 方向 | 既定規則 | priority | 動作 |
| --- | --- | --- | --- |
| Inbound | AllowVNetInBound | 65000 | VirtualNetwork内の通信を許可 |
| Inbound | AllowAzureLoadBalancerInBound | 65001 | AzureLoadBalancerからの通信を許可 |
| Inbound | DenyAllInbound | 65500 | その他を拒否 |
| Outbound | AllowVnetOutBound | 65000 | VirtualNetwork内の通信を許可 |
| Outbound | AllowInternetOutBound | 65001 | Internet宛てを許可 |
| Outbound | DenyAllOutBound | 65500 | その他を拒否 |

4096のカスタムDenyは既定規則より先に評価される。そのため、Inboundの最後にDenyを置く
AppGWとAPIMでは、AzureLoadBalancerの明示Allowを管理・基盤の帯に定義する。
GatewayManagerやAPIM管理ポート、基盤向け送信許可も維持する。
[Microsoftの既定NSG規則](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview#default-security-rules)

## AppGWとPrivate Endpointの送信末尾にDenyを置かない理由

AppGWとPEPに送信4096の全拒否は作成しないが、理由は異なる。

| NSG | 判断 | 根拠と影響 |
| --- | --- | --- |
| AppGW | 現資材の標準構成では追加しない | 通常のApplication Gateway v2はInternet宛ての全送信許可を必要とする。バックエンド・Key Vault向けの個別Allowだけでは代替できず、送信全拒否を追加すると基盤通信や規則の適用に問題が生じる。既定65001を維持する。 |
| PEP | PE専用サブネットでは追加防御のために必要ない | Private Endpointはサービス側から新規通信を開始できない。PE宛ての接続を受信Allowと4096で制限する。同じサブネットに別のNICを置く場合は、そのNICの送信を別途評価する。 |

[MicrosoftのAppGW NSG要件](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure#network-security-groups)
は、標準構成の送信Internet許可を必須としている。
Network Isolationによるenhanced network controlを採用したAppGWには例外があるが、
この資材はその機能登録・構成切替を自動化していない。SKU名やpublic IPの有無だけで採用済みとは判断しない。
切り替える場合は既存AppGWの適用条件を別途検討し、4096の追加だけで済ませない。
[MicrosoftのNetwork Isolationと既存v2の共存制約](https://learn.microsoft.com/en-us/azure/application-gateway/application-gateway-private-deployment#coexisting-v2-application-gateways-created-prior-to-enablement-of-enhanced-network-control)

PEPの `privateEndpointNetworkPolicies` は `NetworkSecurityGroupEnabled` で、PEへの受信制限を有効化している。
送信Denyの作成自体が禁止されているという意味ではなく、PEが開始できない通信を拒否する実益がないという判断である。
[MicrosoftのPrivate Endpoint NSG考慮事項](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-overview#nsg-more-considerations)

NSGはstatefulであり、受信で許可した接続の応答のために送信Allowを別途作る必要はない。
[MicrosoftのNSG応答通信の扱い](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview#security-rules)
同じPEPサブネットに別のVM等を置く場合は、そのNICには通常のNSG送信規則が適用されるため、PEだけを理由に送信制御が不要とは判断しない。
[MicrosoftのPrivate Endpointポリシー適用範囲](https://learn.microsoft.com/en-us/azure/private-link/disable-private-endpoint-network-policy)

## 新規構築と通常更新

新規環境では、[構築手順](../setup/06-deploy.md)の `azd up` が正本とBicepの規則を作成する。
構築後のNSG定義の更新は、同じ正本・params・Core stateを使い、[共通確認](common.md)に従って対象と復旧方法を決めた操作で行う。
アプリだけを更新するときは、[個別配備](common.md)を使う。

1. 対象の通信と送信元・宛先・ポートを決め、採番JSONと関係する全モジュールを確認する。
   `managementAdditionalSecurityRules`等の追加規則も含め、同じ方向の番号重複と評価順を確認する。
2. 対応するローカル検査とBicepの生成結果を確認する。
   NSGの変更にWAF・APIMポリシー・UDR・Firewall・NSG関連付け・IP・IDの変更を混ぜない。
3. 同じ環境のparamsとstateで対象の変更を反映し、Azureの完了状態と現在の規則を読む。
   別端末・Azure Portal・他の配備から同時に変更しない。
4. 新しい接続で公開API、Portal、内部APIM、必要な管理経路を確認する。
   NSGはstatefulで、規則変更は原則として新規接続に適用される。既存接続の継続だけで成功にしない。
   [Microsoftの接続状態とNSG変更](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview#security-rules)

複数NSGや関連資源の配備は1トランザクションではなく、無停止も保証しない。
途中停止した場合は、配備の状態・現在の規則・通信結果を確認してから再開する。
実ID・IP・規則全文・実測結果は同じ `.local/<environment>/` に保護保存し、公開資料へ転載しない。

APIMの管理・LB・基盤依存や、Container Appsの内部通信・registry・identity等の必要条件も確認する。
NSGの番号を揃えても、UDRやFirewallの許可、認証・APIの認可を代替しない。
[APIMの必要ポート](https://learn.microsoft.com/en-us/azure/api-management/virtual-network-reference#required-ports)、
[Container AppsのNSG要件](https://learn.microsoft.com/en-us/azure/container-apps/firewall-integration#nsg-allow-rules)を参照する。
