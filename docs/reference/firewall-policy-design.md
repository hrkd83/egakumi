# Azure Firewall Policyの通信設計

[通信要件](network-communication-requirements.md) · [NSG設計](network-security-design.md) · [検証・実証結果の管理](network-security-validation.md) · [基盤通信の例外](platform-communications.md)

本書は、初期構築のAzure Firewallを実際に通過する通信と、構築資材の許可規則を対応付ける。NSGで許可するVNet内通信のすべてがFirewallを通る設計ではない。送信元、宛先、プロトコル、ポートだけでなく、[通信要件](network-communication-requirements.md)の経路と併せて読む。

初期集合は **6 Rule Collection Group（RCG）・11 collection・35 Allow規則（Network16、Application19）**。RCG、collection、規則の作成有無を公開params／configで切り替える入力は設けない。構築サブネットCIDR、割当IP、公開Host、リージョンと、検証済みの業務接続設定だけを該当するアドレスへ差し込む。資材・合成検証・実環境への適用・実通信の確認は別の段階として扱う。

## 設計前提と評価順序

- 初期構築は独立VNet、Azure提供DNSを使用する。VNetのDNSサーバーとFirewallの上流DNSは空配列に固定し、FirewallのDNS proxyは有効にする。VNetのclientが自動的にFirewall DNS proxyを問い合わせる設定にはしない。
- AppGW→APIM内部VIP、AppGW→ACA共有VIPには、構築後に判明する各VIP `/32` を対象とするUDRを設定する。AppGWのInternet向けdefault route全体をFirewallへ変更しない。
- APIM、管理VM、ACAのInternet向け送信は既存のUDRでFirewallへ送る。ACA→AppGWサブネットのreturn routeもFirewallへ送る。
- APIM→ACA、ACA→SQL／Key Vault PE、管理VM→PEなど、同一VNet内のより具体的な直接経路にはFirewall規則を置かない。PE側のNSG評価は[NSG設計](network-security-design.md)で記録する。
- 全Application Ruleは `terminateTLS: false`。Network RuleはTCP／UDPを許可し、TLS inspectionを行う規則ではない。TLSが暗号化した本文をIDPSが常に検査できるという前提を置かない。
- 独自のAny宛Internet Network Ruleや、独自の末尾Deny collectionは追加しない。許可されない通信にはFirewallの暗黙Denyが適用される。

Firewallは規則種別を含む評価順序を持ち、Network RuleがApplication Ruleに先行する。小さいRCG／collection優先度は同種の評価順を整理するものであり、NSGの優先度とは独立している。各規則にNSGのような個別priorityはない。同じ通信がサービスタグのNetwork RuleとFQDNのApplication Ruleの両方に一致する場合、後段のFQDN規則へ必ずhitするとは記録しない。[公式の規則処理](https://learn.microsoft.com/en-us/azure/firewall/rule-processing)

NSGの `allow-internet-egress` がTCP80／443を許可していても、Firewallの任意Internet許可を意味しない。NSGとFirewallの両方の許可、経路、名前解決、TLS、認可が成立条件となる。サービス・タグはAzureが管理する宛先集合であり、特定の自資源や利用者だけを許可する認証境界ではない。

## 資材の所有範囲

| RCG | 優先度 | collection（優先度） | 規則数 | 正本 |
| --- | ---: | --- | ---: | --- |
| `rcg-ingress` | 200 | `allow-agw-to-apim`（200） | 1 | [network-bindings.bicep](../../infra/modules/network-bindings.bicep) |
| `rcg-platform` | 300 | `allow-apim-platform`（200）、`allow-vm-platform`（210） | 12 | [network.bicep](../../infra/modules/network.bicep) |
| `rcg-portal` | 350 | `portal-ingress`（100）、`portal-azure-monitor`（150）、`portal-platform-and-probe`（200）、`portal-runner`（300） | 7 | [portal-network.bicep](../../infra/modules/portal-network.bicep) |
| `rcg-credential-broker` | 360 | `credential-broker-azure`（200） | 3 | [credential-broker-network.bicep](../../infra/modules/credential-broker-network.bicep) |
| `rcg-portal-gateway` | 370 | `portal-gateway`（100） | 1 | [gateway-egress.bicep](../../apps/portal/deploy/gateway-egress.bicep) |
| `rcg-egress` | 400 | `allow-apim-egress`（200）、`allow-vm-egress`（210） | 11 | [network.bicep](../../infra/modules/network.bicep) |

同じFirewall Policyへの配備は先行処理の完了後に進め、別アプリのRCGを置き換えない。Portal Gateway用RCGは370とし、Broker用360との同順位を解消する。再配備時に個々の規則だけでなく、所有collectionのpriority、action、規則集合、送信元、宛先、プロトコルを照合する。

## ルールと通信要件の対応

以下は実装された全規則。`N` はNetwork Rule、`A` はApplication Ruleで、actionは全件Allow。Application RuleのTLS inspectionは全件無効である。送信元は構築された各サブネットのCIDRとし、管理VMの個別 `/32` 許可は対向のNSGで扱う。表のNSG名は規則名であり、Azureの実資源名ではない。

### rcg-ingress / allow-agw-to-apim

| 規則名 | 種別・通信 | 送信元 → 宛先 | 通信ID・用途 | 対応NSG・採用理由 |
| --- | --- | --- | --- | --- |
| `allow-appgw-apim-http` | N・TCP80 | AppGW → 構築APIM内部VIP単IP | [`FLOW-APPGW-APIM`](network-communication-requirements.md#flow-appgw-apim)。通常API、SSE、WebSocket等の入口とbackend probe | AppGW `allow-apim-http-egress`、APIM `allow-appgw-http-ingress`。AppGWでTLSを終端し、内部HTTP80をFW経由で中継する既FIXの経路を維持 |

宛先はAPIMの構築出力から取得する。AppGW→APIMの暗号化しない内部区間と、外部利用者→AppGWのHTTPS区間を区別する。gRPC／TCP8443の許可は含めない。

### rcg-platform / allow-apim-platform

| 規則名 | 種別・通信 | 宛先 | 通信ID・用途 | 対応APIM NSG・採用理由 |
| --- | --- | --- | --- | --- |
| `allow-apim-storage` | N・TCP443 | `Storage`タグ | [`FLOW-APIM-STORAGE`](network-communication-requirements.md#flow-apim-storage)。APIM基盤のStorage依存 | `allow-storage-egress`。自前の業務Storageだけに限定しないAzure基盤依存 |
| `allow-apim-sql` | N・TCP1433 | `Sql`タグ | [`FLOW-APIM-SQL-PLATFORM`](network-communication-requirements.md#flow-apim-sql-platform)。APIM基盤のSQL依存 | `allow-sql-egress`。Portal／Brokerの構築業務DBへのPE接続とは別 |
| `allow-apim-key-vault` | N・TCP443 | `AzureKeyVault`タグ | [`FLOW-APIM-KV-PLATFORM`](network-communication-requirements.md#flow-apim-kv-platform)。APIMのAzure基盤／Key Vaultサービス依存 | `allow-key-vault-egress`。構築Key Vault PE `/32` の規則と混同しない |
| `allow-apim-monitor` | N・TCP1886、443 | `AzureMonitor`タグ | [`FLOW-APIM-MONITOR`](network-communication-requirements.md#flow-apim-monitor)。APIMの監視・診断 | `allow-monitor-egress`。Monitor本体の仕様上の到達性 |
| `allow-apim-event-hubs` | N・TCP443、5671、5672 | `EventHub`タグ | [`FLOW-APIM-EVENT-HUBS`](network-communication-requirements.md#flow-apim-event-hubs)。Event Hubsへ転送する監視機能 | `allow-event-hubs-egress`。機能利用の有無で規則作成を切り替えず、初期到達性を常設 |
| `allow-apim-entra` | N・TCP443 | `AzureActiveDirectory`タグ | [`FLOW-APIM-ENTRA`](network-communication-requirements.md#flow-apim-entra)。Entra認証・トークン等のAzure依存 | `allow-entra-egress`。認証処理の宛先をAzureのタグで管理 |
| `allow-apim-credential-manager` | N・TCP443 | `AzureConnectors`タグ | [`FLOW-APIM-CREDENTIAL-MANAGER`](network-communication-requirements.md#flow-apim-credential-manager)。Credential Managerの依存 | `allow-credential-manager-egress`。Brokerの管理ID取得用ローカルendpointとは別 |
| `allow-apim-ntp` | N・UDP123 | `*` | [`FLOW-APIM-NTP`](network-communication-requirements.md#flow-apim-ntp)。APIM管理基盤の時刻同期 | `allow-time-sync-egress`。NSGのInternet宛UDP123制限と組み合わせ、独立したDNS回答の差でNTPを遮断しない |

送信元は全件APIMサブネット。宛先タグ、プロトコル、ポートはBicep固定とする。NTPの許可は任意のInternet宛UDP123への到達性を持つ。`time.windows.com`への限定や、インスタンス間counter同期のUDP4290と同一の要件にはしない。[APIMの公式ネットワーク要件](https://learn.microsoft.com/en-us/azure/api-management/virtual-network-reference)

### rcg-platform / allow-vm-platform

| 規則名 | 種別・通信 | 宛先 | 通信ID・用途 | 対応管理VM NSG・採用理由 |
| --- | --- | --- | --- | --- |
| `allow-mgmt-vm-entra` | N・TCP443 | `AzureActiveDirectory`タグ | [`FLOW-MGMT-ENTRA`](network-communication-requirements.md#flow-mgmt-entra)。Entraログインと管理IDを利用した後続認証 | `allow-entra-egress`。VM内IMDSへのHTTP80取得とは別の外向き通信 |
| `allow-mgmt-vm-arm` | N・TCP443 | `AzureResourceManager`タグ | [`FLOW-MGMT-ARM`](network-communication-requirements.md#flow-mgmt-arm)。構築資源の管理API操作、ログ接続token取得 | `allow-azure-resource-manager-egress`。VMを全リソースの保守拠点とするための管理到達性 |
| `allow-mgmt-vm-monitor` | N・TCP443 | `AzureMonitor`タグ | [`FLOW-MGMT-MONITOR`](network-communication-requirements.md#flow-mgmt-monitor)。AMAの監視送信、保存済みログ閲覧 | `allow-monitor-egress`。監視送信と閲覧は用途・認可を分ける |
| `allow-mgmt-vm-agent-storage` | N・TCP443 | `Storage`タグ | [`FLOW-MGMT-AGENT-STORAGE`](network-communication-requirements.md#flow-mgmt-agent-storage)。Agent配布・状態等の直接Storage依存、診断取得 | `allow-storage-egress`。構築監査Blob PEへの閲覧許可とは別 |

送信元は全件管理VMサブネット。管理VMの時刻同期はAzureホストのPHCをChronyで参照する非IPの連携であり、このcollectionに外部NTP規則を置かない。既存VMには現行guest監査moduleを単独適用してPHC同期を成立させた後、所有する`allow-vm-platform`から旧`allow-mgmt-vm-ntp`を撤去して読み戻す。通常CoreのFirewall更新はguest監査より先なので、先行適用の成功証跡を確認できない既存VMはCore変更前に停止する。APIMの`allow-apim-ntp`は維持し、未知の企業ルールを自動削除しない。WireServer、DHCP、IMDS、Azure提供DNSとホスト時刻の基盤連携は[別の通信記録](platform-communications.md)で扱う。

### rcg-portal

| collection（優先度） | 規則名 | 種別・通信 | 送信元 → 宛先 | 通信ID・用途 | 対応NSG・採用理由 |
| --- | --- | --- | --- | --- | --- |
| `portal-ingress`（100） | `allow-appgw-aca-http` | N・TCP80 | AppGW → ACAサブネット | [`FLOW-APPGW-ACA`](network-communication-requirements.md#flow-appgw-aca)。PortalのHTTP backendとprobe | AppGW `allow-aca-http-egress`、ACA `allow-appgw-http-ingress`。経路はACA共有VIP `/32` UDR、FW宛先は既存のACA CIDRを維持 |
| `portal-azure-monitor`（150） | `allow-aca-monitor` | N・TCP443 | ACA → `AzureMonitor`タグ | [`FLOW-ACA-MONITOR`](network-communication-requirements.md#flow-aca-monitor)。ACAの監視依存 | ACA `allow-monitor-egress`。他のAzureタグ用途と名前を分離 |
| `portal-azure-monitor`（150） | `allow-aca-entra` | N・TCP443 | ACA → `AzureActiveDirectory`タグ | [`FLOW-ACA-ENTRA`](network-communication-requirements.md#flow-aca-entra)。ACAの認証基盤依存 | ACA `allow-entra-egress`。Monitor許可と同じportでも別規則にする |
| `portal-platform-and-probe`（200） | `allow-aca-mcr` | A・HTTPS443 | ACA → `MCR`宛先群 | [`FLOW-ACA-MCR`](network-communication-requirements.md#flow-aca-mcr)。Microsoft Container Registryのimage取得 | ACA `allow-mcr-egress`。構築したACR PEへの接続と区別 |
| `portal-platform-and-probe`（200） | `allow-aca-platform-packages` | A・HTTPS443 | ACA → `ACA-PACKAGES`宛先群 | [`FLOW-ACA-PLATFORM-PACKAGES`](network-communication-requirements.md#flow-aca-platform-packages)。ACA基盤のpackage依存 | ACA `allow-afd-first-party-egress`。必要FQDNを明示し、任意Internet許可にしない |
| `portal-platform-and-probe`（200） | `allow-aca-managed-identity` | A・HTTPS443 | ACA → `ACA-IDENTITY`宛先群 | [`FLOW-ACA-MANAGED-IDENTITY-UPSTREAM`](network-communication-requirements.md#flow-aca-managed-identity-upstream)。ACA基盤のidentity依存 | ACAのARM／Entra等の仕様上の許可と既定送信。SDK→`IDENTITY_ENDPOINT`ローカル取得そのものをFW通信へ読み替えない |
| `portal-runner`（300） | `allow-mgmt-vm-github-docker-node-build` | A・HTTPS443 | 管理VM → `APP-BUILD`宛先群 | [`FLOW-MGMT-APP-BUILD`](network-communication-requirements.md#flow-mgmt-app-build)。Git取得、Docker／Node／npmによる配備・build | 管理VM `allow-internet-egress`。必要配布先の共用CDN範囲を含め、TLS inspectionなしで既存配備到達性を維持 |

`*.r2.cloudflarestorage.com`等のwildcardは特定自資源だけを許可する宛先ではない。配布元の共用CDNに必要な到達性として明記し、構築サブネット送信元とNSG、実行者の権限で用途を制限する。`api.ipify.org`向けの疎通probe許可は初期集合へ含めない。

### rcg-credential-broker / credential-broker-azure

| 規則名 | 種別・通信 | 送信元 → 宛先 | 通信ID・用途 | 対応NSG・採用理由 |
| --- | --- | --- | --- | --- |
| `allow-aca-entra-login` | A・HTTPS443 | ACA → `login.microsoftonline.com` | [`FLOW-ACA-ENTRA`](network-communication-requirements.md#flow-aca-entra)。Brokerを含むACAのEntra接続 | ACA `allow-entra-egress`。上記タグのNetwork Ruleと範囲が重なる場合もFQDN用途を明記し、当規則hitを保証しない |
| `allow-aca-arm` | A・HTTPS443 | ACA → `management.azure.com` | [`FLOW-ACA-ARM`](network-communication-requirements.md#flow-aca-arm)。BrokerのCredential Manager等の管理API利用 | ACA `allow-arm-https-egress`。Entraと同じportでも管理API用途を別記録 |
| `allow-mgmt-vm-python-build` | A・HTTPS443 | 管理VM → `PYTHON-BUILD`宛先群 | [`FLOW-MGMT-PYTHON-BUILD`](network-communication-requirements.md#flow-mgmt-python-build)。BrokerのPython依存・build | 管理VM `allow-internet-egress`。Node／Docker側の配布先と用途を分離 |

Brokerは初期構築の常設構成要素とし、`credentialProfiles`が空であることを理由にこのRCGを不作成にはしない。Gatewayの内部 `deployApplication` はDB初期化→アプリ配備という工程の段階を表すもので、公開のBroker／FW作成スイッチではない。[Gateway配備入口](../../apps/gateway/deploy/main.bicep)

### rcg-portal-gateway / portal-gateway

| 規則名 | 種別・通信 | 送信元 → 宛先 | 通信ID・用途 | 対応NSG・採用理由 |
| --- | --- | --- | --- | --- |
| `allow-aca-appgw-https` | A・HTTPS443 | ACA → 構築Gatewayの公開Host | [`FLOW-ACA-APPGW`](network-communication-requirements.md#flow-aca-appgw)。Portalのサーバー処理による公開Gateway API呼出し | ACA `allow-appgw-https-egress`、AppGW `allow-firewall-public-https-ingress`。外向きFW経由・公開IPへのSNATを含む公開入口経路 |

宛先は同じ構築のGateway originからhostnameだけを取得する。任意の利用者指定Hostを許可する入力ではない。AppGWのbackend用HTTP80と、Portalから公開Gatewayへ接続するHTTPS443は別の区間である。

### rcg-egress / allow-apim-egress

| 規則名 | 種別・通信 | 宛先 | 通信ID・用途 | 対応APIM NSG・採用理由 |
| --- | --- | --- | --- | --- |
| `allow-apim-certificate-validation` | A・HTTP80、HTTPS443 | `CERT-VALIDATION`（7宛先） | [`FLOW-APIM-CERTIFICATE-VALIDATION`](network-communication-requirements.md#flow-apim-certificate-validation)。証明書chain／失効・PKI確認 | `allow-internet-egress`。証明書購入APIとは別の基盤互換到達性を保持 |
| `allow-apim-windows-update` | A・HTTP80、HTTPS443 | `APIM-WINDOWS-UPDATE` | [`FLOW-APIM-WINDOWS-UPDATE`](network-communication-requirements.md#flow-apim-windows-update)。APIM管理基盤のWindows配布依存 | `allow-internet-egress`。管理VMのOSがWindowsという意味ではない |
| `allow-apim-microsoft-redirect` | A・HTTP80、HTTPS443 | `go.microsoft.com` | [`FLOW-APIM-MICROSOFT-REDIRECT`](network-communication-requirements.md#flow-apim-microsoft-redirect)。Microsoft FWLink経由の基盤配布 | `allow-internet-egress`。共通redirect先をWindows updateのwildcard群と分離 |
| `allow-apim-platform-diagnostics` | A・HTTPS443 | `APIM-PLATFORM-DIAGNOSTICS` | [`FLOW-APIM-PLATFORM-DIAGNOSTICS`](network-communication-requirements.md#flow-apim-platform-diagnostics)。APIM基盤診断・profiler | `allow-internet-egress`。Defender、Portal診断を別規則へ分離 |
| `allow-apim-windows-defender` | A・HTTPS443 | `wdcp.microsoft.com`、`wdcpalt.microsoft.com` | [`FLOW-APIM-WINDOWS-DEFENDER`](network-communication-requirements.md#flow-apim-windows-defender)。APIM管理基盤のDefender依存 | `allow-internet-egress`。OS管理基盤の宛先を用途別に記録 |
| `allow-apim-portal-diagnostics` | A・HTTPS443 | `dc.services.visualstudio.com` | [`FLOW-APIM-PORTAL-DIAGNOSTICS`](network-communication-requirements.md#flow-apim-portal-diagnostics)。既存Portal診断の互換到達性 | `allow-internet-egress`。APIM本体からの必須利用までは保証せず既存許可を保持 |
| `allow-apim-approved-services` | A・HTTPS443 | `oneocsp.microsoft.com`＋生成した業務FQDN | [`FLOW-APIM-APPROVED-SERVICES`](network-communication-requirements.md#flow-apim-approved-services)。登録済み外部backend、Operation override、本人情報取得先 | `allow-internet-egress`。ルールを常設し、APIMの業務設定から必要宛先を生成 |

送信元は全件APIMサブネット。`oneocsp.microsoft.com`の常設基底は同送信元・HTTPS443・TLS inspection無効の証明書確認規則と重なり、外部backendが空のときに許可範囲を広げない。

業務宛先は[firewall_destinations.py](../../scripts/deployment/helpers/firewall_destinations.py)が検証済みAPI／Operationから取得する。`backendService: external` のHTTPS／WSS443 FQDNを対象とし、IP literal、任意port、資格情報付きURLを許可しない。本人接続機能では選択providerの `identityEndpoint` とARM互換到達性を生成する。公開の自由なFW宛先配列は使わない。API配備による宛先更新は、所属するcollectionや他規則を保持して読戻し・競合検知の対象とする。

### rcg-egress / allow-vm-egress

| 規則名 | 種別・通信 | 宛先 | 通信ID・用途 | 対応管理VM NSG・採用理由 |
| --- | --- | --- | --- | --- |
| `allow-mgmt-vm-certificate-validation` | A・HTTP80、HTTPS443 | `CERT-VALIDATION`（7宛先） | [`FLOW-MGMT-CERTIFICATE-VALIDATION`](network-communication-requirements.md#flow-mgmt-certificate-validation)。保守処理の証明書／PKI確認 | `allow-internet-egress`。既存の互換到達性を維持し、全7宛先の必須利用とは区別 |
| `allow-mgmt-vm-linux-packages` | A・HTTP80、HTTPS443 | `LINUX-PACKAGES` | [`FLOW-MGMT-LINUX-PACKAGES`](network-communication-requirements.md#flow-mgmt-linux-packages)。Ubuntu、PowerShell、Azure CLI等の準備 | `allow-internet-egress`。初期構築から保守clientを再現する配布依存 |
| `allow-mgmt-vm-aca-dev-https` | A・HTTPS443 | 構築リージョンの `<region>.azurecontainerapps.dev` | [`FLOW-MGMT-ACA-DEV`](network-communication-requirements.md#flow-mgmt-aca-dev)。ACA App／Jobのログ、exec等のDev gateway接続 | `allow-internet-egress`。単一リージョンの共用gatewayへ接続し、対象App／Job操作は別途MI／RBACで制限 |
| `allow-mgmt-vm-approved-services` | A・HTTPS443 | `MGMT-SERVICES` | [`FLOW-MGMT-APPROVED-SERVICES`](network-communication-requirements.md#flow-mgmt-approved-services)。ACME発行、配布、ARM、構築した公開Hostの保守確認 | `allow-internet-egress`、該当するARMタグ許可。自由な宛先追加入力を廃止し、共通配布先と構築Hostから生成 |

送信元は全件管理VMサブネット。ACA Dev gatewayはリージョン共用であり、当Firewall規則だけで接続対象のApp／Jobを限定しない。ログ閲覧、exec、環境token、Jobログ等のMI／RBACとclient/APIの条件は[通信要件](network-communication-requirements.md)で別に管理する。

## FQDN宛先群の定義

以下は公式共通endpoint、製品配布先、構築情報から生成する値である。実環境のHostやURLを例として転記しない。wildcardはその配下の共用範囲を含む。

| 宛先群 | 実装されたFQDN |
| --- | --- |
| `CERT-VALIDATION` | `mscrl.microsoft.com`、`crl.microsoft.com`、`oneocsp.microsoft.com`、`cacerts.digicert.com`、`crl3.digicert.com`、`csp.digicert.com`、`issuer.pki.azure.com` |
| `APIM-WINDOWS-UPDATE` | `*.update.microsoft.com`、`*.ctldl.windowsupdate.com`、`ctldl.windowsupdate.com`、`download.windowsupdate.com`、`*.windowsupdate.com`、`*.delivery.mp.microsoft.com` |
| `APIM-PLATFORM-DIAGNOSTICS` | `azurewatsonanalysis-prod.<Azure環境のStorage suffix>`、`*.data.microsoft.com`、`azureprofiler.trafficmanager.net`、`shavamanifestazurecdnprod1.azureedge.net`、`shavamanifestcdnprod1.azureedge.net` |
| `LINUX-PACKAGES` | `azure.archive.ubuntu.com`、`archive.ubuntu.com`、`security.ubuntu.com`、`packages.microsoft.com` |
| `MCR` | `mcr.microsoft.com`、`*.data.mcr.microsoft.com` |
| `ACA-PACKAGES` | `packages.aks.azure.com`、`acs-mirror.azureedge.net` |
| `ACA-IDENTITY` | `*.identity.azure.net`、Azure環境のEntra login host、`*.<同Entra login host>`、`*.login.microsoft.com`、`login.microsoft.com` |
| `APP-BUILD` | `github.com`、`*.github.com`、`*.githubusercontent.com`、`registry.npmjs.org`、`nodejs.org`、`auth.docker.io`、`registry-1.docker.io`、`production.cloudflare.docker.com`、`production.cloudfront.docker.com`、`*.r2.cloudflarestorage.com` |
| `PYTHON-BUILD` | `pypi.org`、`files.pythonhosted.org`、`packages.microsoft.com`、`deb.debian.org`、`security.debian.org` |
| `MGMT-SERVICES` | 選択ACME directoryの公式Host、`management.azure.com`、`cdn.oneget.org`、`cdn.powershellgallery.com`、`*.powershellgallery.com`、`go.microsoft.com`、`aka.ms`、構築Gateway公開Host、構築Portal公開Host |

ACME directoryは業務の証明書発行設定と共有する。productionでは `acme-v02.api.letsencrypt.org`、stagingでは `acme-staging-v02.api.letsencrypt.org`を用いる。7件の証明書確認FQDNはLet's Encryptの証明書発行APIと同じ用途ではなく、APIM／保守側の基盤証明書確認を含む互換到達性として固定で維持する。将来DigiCertから証明書を取得する場合だけの規則とはしない。

## params／configと固定部分

| 入力・出力 | 規則に影響する範囲 | 固定する範囲 |
| --- | --- | --- |
| `network.subnetPrefixes` | AppGW／APIM／管理VM／ACAの送信元、ACA宛先CIDR | RCG／collection／規則の存在、プロトコル・ポート |
| APIM構築出力 | `allow-appgw-apim-http`の宛先単IPと対応UDR `/32` | TCP80、Allow、規則名 |
| 構築Gateway／Portal公開Host | Portal→公開Gateway、管理VM保守確認の宛先 | HTTPS443、Allow、常設規則 |
| 構築リージョン | 管理VM ACA Dev gatewayの単一FQDN | HTTPS443、TLS inspection無効、常設規則 |
| 証明書ACME directory | 管理VMのproduction／staging公式CA Host | 証明書確認7宛先、CA許可規則の存在 |
| 検証済みAPI／Operation／本人接続設定 | APIM approved-servicesのFQDN集合 | 単一常設規則、HTTPS443、FQDN検証、他規則の保持 |
| Azure環境定義 | Storage suffix、Entra login host | 共通宛先の用途と規則集合 |
| Firewall SKU／IDPS／Threat Intel／SNAT設定 | Firewallの検査・変換方式 | 上記35のAllow集合。作成有無のフラグへ転用しない |

公開入力の `network.firewall.apimAllowedFqdns`、`network.firewall.managementAllowedFqdns`、`network.firewall.dnsServers`等の旧自由入力は廃止し、空でも検査で拒否する。初期の標準DNS、APIM NTP、管理VMのホスト時刻同期、Broker常設、gRPC撤去に関する作成有無の入力を再導入しない。

StandardにはIDPS自体を送信せず、Premiumでのみ構成する。標準のtest構成はIDPSなし、production構成は採用したPremium IDPSを使用する。IDPS private ranges、SNAT private ranges、bypass／signature設定の役割を混同しない。TLS inspection無効のまま、IDPSがHTTPS本文まで見えるという検証結果は作らない。

## 判断記録

| 判断ID | 判断・採用理由 | 影響範囲 |
| --- | --- | --- |
| `FW-D01` | Firewallを通過する通信だけを登録する。将来の経路変更用やNSG通信の記録目的だけではFW許可を作らない | APIM→ACA、ACA→SQL／KV PEの旧内部FW規則を撤去。必要NSGと直接経路は保持 |
| `FW-D02` | 全RCG／collection／規則は初期常設にする | Broker profiles、任意機能の利用有無で初期集合を変えない。内部配備段階と公開作成フラグを区別 |
| `FW-D03` | Portal GatewayのRCGを370にする | Broker360との同順位を解消し、350→360→370→400の順を明示。許可範囲は維持 |
| `FW-D04` | AppGW→APIM／ACAはHTTP80をFW経由で許可 | 外部HTTPSはAppGW終端。通常API・Portal・probeの経路を維持し、gRPC許可を撤去 |
| `FW-D05` | APIM NTPはAPIMサブネット・UDP123・宛先`*`を維持し、管理VMはホストPHCを使う | APIMはDNS回答差を回避するNSG328と組み合わせる。管理VMのPHC／VMBusは非IPなので旧FW NTPを撤去し、UDP123／319／320を追加しない。guest同期の先行成立と現在状態の確認は分ける |
| `FW-D06` | 同じHTTPSでもEntra／ARM／Monitor／build等の用途を規則名で分離する | 同じタグやNetwork RuleとApplication Ruleの重複を明記し、特定の後段規則hitは保証しない |
| `FW-D07` | 証明書確認7宛先を維持する | 基盤互換到達性を失わない。全宛先の毎回利用、将来の証明書購入方式とは区別 |
| `FW-D08` | 管理VM ACA Dev gatewayを単一構築リージョンに常設許可する | 管理VMからのログ・exec等の保守経路を成立させる。対象操作はMI／RBACで認可 |
| `FW-D09` | 任意Internet Network Rule、自由なFW宛先配列、ipify probeを作らない | 必要な固定FQDN・生成済み業務宛先と暗黙Denyで管理 |
| `FW-D10` | 全Application RuleでTLS inspectionを無効とする | 既存SDK、証明書、配布、ACA Dev APIのTLS契約を維持。本文検査の有無をIDPSの有無と分離 |

### 規則を作成しない直接通信

| 通信ID | 通信 | Firewall規則を作成しない理由 |
| --- | --- | --- |
| [`FLOW-APIM-ACA`](network-communication-requirements.md#flow-apim-aca) | APIM → ACA Broker、HTTPS443 | 同一VNetの直接経路。APIM送信／ACA受信NSGで許可 |
| [`FLOW-ACA-SQL`](network-communication-requirements.md#flow-aca-sql) | ACA App／Job → SQL PE、TCP1433 | PEの直接到達。ACA送信／PE受信NSGとSQL認可で制御 |
| [`FLOW-ACA-KV`](network-communication-requirements.md#flow-aca-kv) | ACA → Key Vault PE、HTTPS443 | PEの直接到達。ACA送信／PE受信NSGとVault認可で制御 |
| [`FLOW-ACA-ACR`](network-communication-requirements.md#flow-aca-acr) | ACA → 構築ACR PE、HTTPS443 | PEの直接到達。MCR外部取得とは異なる経路 |
| [`FLOW-APIM-FOUNDRY`](network-communication-requirements.md#flow-apim-foundry)、[`FLOW-APIM-KV`](network-communication-requirements.md#flow-apim-kv)、[`FLOW-APPGW-KV`](network-communication-requirements.md#flow-appgw-kv) | APIM／AppGW → 構築Foundry／Vault PE | PEの直接到達。サービス・タグの基盤許可と自PE接続を区別 |
| [`FLOW-MGMT-KV`](network-communication-requirements.md#flow-mgmt-kv)、[`FLOW-MGMT-FOUNDRY`](network-communication-requirements.md#flow-mgmt-foundry)、[`FLOW-MGMT-ACR`](network-communication-requirements.md#flow-mgmt-acr)、[`FLOW-MGMT-SQL`](network-communication-requirements.md#flow-mgmt-sql) | 管理VM → 各構築PE | VNet内保守。送受信NSGと用途別MI／RBAC・DB権限で制御 |
| [`FLOW-MGMT-AUDIT-JAPAN`](network-communication-requirements.md#flow-mgmt-audit-japan)、[`FLOW-MGMT-AUDIT-FOUNDRY`](network-communication-requirements.md#flow-mgmt-audit-foundry) | 管理VM → 構築監査Blob PE | 監査閲覧の直接到達。外部Storageタグ依存とは別 |
| [`FLOW-MGMT-APIM`](network-communication-requirements.md#flow-mgmt-apim)、[`FLOW-MGMT-ACA-HTTP`](network-communication-requirements.md#flow-mgmt-aca-http)、[`FLOW-MGMT-ACA-HTTPS`](network-communication-requirements.md#flow-mgmt-aca-https) | 管理VM → APIM／ACA内部endpoint | VNet内保守の直接経路。外部Dev gateway接続とは別 |

将来ハブ＆スポークへ手動変更するときは、実経路がFirewall通過へ変わる通信を特定してから、企業のDNS・UDR・SNAT・NSG・FWを一緒に見直す。本書の直接通信表を、将来用の未使用FW許可として初期投入しない。構築後に資材を再実行しない契約の手動変更は、初期資材の再適用で維持されるとは保証しない。

## 検証条件・結果の扱い

### 公開可能な合成検証

独立した架空の構築入力で、以下のassertionを確認する。実ID・実Host・実CIDRや実ログをfixtureへ転記しない。

旧外部NTP構成での文書照合では、5つのBicep入口をコンパイルして合成入力で展開し、6 RCG・11 collection・36規則（Network 17、Application 19）の名称集合と当時の36レコードが一致した。actionはすべてAllow、Application 19規則のTLS inspectionはすべて無効であることを確認した。当時の全通信IDは通信マスターのアンカーへ対応していた。この記録は旧条件の公開資材の合成照合として保持し、管理VMのPHC化後の35規則（Network16、Application19）の検証結果へ転用しない。今回のAzure反映・実VM同期は未実施である。

| 検証対象 | 判定条件 | 検証資材 |
| --- | --- | --- |
| 固定集合・NSGとの対応 | Bicep生成後の名称、規則種別、action、source、destination、protocol／port、priorityを定義と照合 | [test_nsg_rule_priorities.py](../../tests/test_nsg_rule_priorities.py)、[test_parameter_ip_ownership.py](../../tests/test_parameter_ip_ownership.py) |
| 時刻とDNS | APIMのFW UDP123宛先`*`・NSG328・Internet制限、管理VM用UDP123規則の不存在、VNet／FW上流DNS固定を照合。管理VMのPHC選択・同期はguestで別判定 | [test_firewall_diagnostics.py](../../tests/test_firewall_diagnostics.py)、[test_parameter_ip_ownership.py](../../tests/test_parameter_ip_ownership.py)、[test_management_linux.py](../../tests/test_management_linux.py) |
| ACA Azure依存とDev接続 | Monitor／Entra、Entra login／ARMの分離、ipify除外、単一リージョンDev FQDNを照合 | [test_nsg_rule_priorities.py](../../tests/test_nsg_rule_priorities.py) |
| 外部業務宛先の更新 | approved-servicesの生成・name移行・他collection保持・反復時同一結果・競合拒否・読戻し条件を照合 | [test_publish_apis.py](../../tests/test_publish_apis.py) |
| 診断記録 | Network／Application／Threat Intelのresource-specificログ、PremiumだけのIDPSログ、保存先と配備順を照合 | [test_firewall_diagnostics.py](../../tests/test_firewall_diagnostics.py) |

合成検証のPASSは、実環境で全宛先を利用したという結果ではない。共通配布先やAzure基盤の互換許可を保持する規則は、全FQDNへの毎回の接続成功を要件にしていない。検証の結果区分と初期不採用は[共通検証](network-security-validation.md)を参照する。

### Azure適用・実証の判定

Azure反映は対象RCG／collection／規則の読戻し一致で判定する。通信の実証では、公開API・Portal応答、AppGW backend health、アプリ／Jobの起動とimage取得、管理VMからのDev gateway接続、管理操作・保存ログ・時刻同期等を、それぞれ対応するFLOWへ紐付ける。疎通だけでRBACやDB権限の適切さを代用せず、各操作の認可結果も確認する。

FirewallログにAllow／Denyが記録される場合は、実行主体、protocol／port、宛先、経路、処理時刻を保護証跡内で突き合わせる。同じ通信が複数規則に一致することと、実際に記録された規則を分ける。APIMの非公開NTP daemon状態やAzure管理基盤の全内部wireまで見えた結果にはしない。時刻の成立確認とpacketの全量取得を同一条件にしない。

適用済み・実証済み・非採用・未実施は保護された環境別台帳で管理する。実環境の構成、稼働、時刻、URL、ID、相関情報、exactな証跡所在を本書へ転載しない。新規全環境再構築のE2Eと、既存環境への所有確認付き限定適用は別の検証である。初期資材が同じ規則・経路・DNS・保守権限を生成することは合成検証で確認し、fresh再構築を実行したという結果は作らない。

## 公式根拠と実装参照

- [Azure Firewallの規則処理](https://learn.microsoft.com/en-us/azure/firewall/rule-processing)、[Application Rule](https://learn.microsoft.com/en-us/azure/firewall/features#application-fqdn-filtering-rules)、[FQDNとDNS proxy](https://learn.microsoft.com/en-us/azure/firewall/dns-settings)。規則の種別・優先度とDNSの役割。
- [APIM VNetの送受信要件](https://learn.microsoft.com/en-us/azure/api-management/virtual-network-reference)。Storage／SQL／Key Vault／Monitor／Entra／NTP等の基盤依存。
- [ACAとFirewallの統合](https://learn.microsoft.com/en-us/azure/container-apps/use-azure-firewall)、[ACAの送信要件](https://learn.microsoft.com/en-us/azure/container-apps/firewall-integration#outbound)、[管理IDのREST仕様](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity#rest-endpoint-reference)。外向き依存とローカル取得の区別。
- [固定commitのAzure CLI ACA接続実装](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/containerapp/custom.py)。リージョンDev gatewayと接続clientの根拠。
- [Azure Firewall PremiumのTLS inspection](https://learn.microsoft.com/en-us/azure/firewall/premium-features#tls-inspection)、[IDPS](https://learn.microsoft.com/en-us/azure/firewall/premium-features#idps)。TLS終端・復号とIDPSの関係。
- [AzureのUDRと経路選択](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-networks-udr-overview)、[Private EndpointのNSG／route table条件](https://learn.microsoft.com/en-us/azure/private-link/disable-private-endpoint-network-policy)。宛先に一致する規則だけではFW経由にならないことの根拠。
- [ACA公開Issueの補足](https://github.com/microsoft/azure-container-apps/issues/893#issuecomment-1711892233)。ACA構成に応じた依存の再現条件を確認する参考であり、全必要宛先や顧客NSG評価区間を保証する公式仕様の代わりにしない。

公式仕様は成立条件を判断する根拠であり、導入したSKU・地域・版・資材に対する実証の代替ではない。後続のAzure仕様変更や業務接続追加で要件が変わる場合は、通信マスター、NSG、実経路、Firewall、検証条件を一緒に更新する。
