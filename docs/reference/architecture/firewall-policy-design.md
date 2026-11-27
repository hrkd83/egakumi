# Azure Firewall Policyの通信設計

[通信要件](network-communication-requirements.md) · [NSG設計](network-security-design.md) · [検証・実証結果の管理](../../testing/network-security-validation.md) · [基盤通信の例外](platform-communications.md)

本書は、初期構築のAzure Firewallを実際に通過する通信と、構築資材の許可規則を対応付ける。NSGで許可するVNet内通信のすべてがFirewallを通る設計ではない。送信元、宛先、プロトコル、ポートだけでなく、[通信要件](network-communication-requirements.md)の経路と併せて読む。

基本構成に加え、証明書Jobと本文監査の専用Rule Collection Group（RCG）を使う。構築サブネットCIDR、割当IP、公開Host、リージョンと、検証した業務接続設定を資材の規則へ差し込む。

## 設計前提と評価順序

- 初期構築は独立VNet、Azure提供DNSを使用する。VNetのDNSサーバーとFirewallの上流DNSは空配列に固定し、FirewallのDNS proxyは有効にする。VNetのclientが自動的にFirewall DNS proxyを問い合わせる設定にはしない。
- AppGW→APIM内部VIP、AppGW→ACA共有VIPには、構築後に判明する各VIP `/32` を対象とするUDRを設定する。AppGWのInternet向けdefault route全体をFirewallへ変更しない。
- APIM、管理VM、ACAのInternet向け送信は構築資材のUDRでFirewallへ送る。ACA→AppGWサブネットのreturn routeもFirewallへ送る。
- APIM→ACA、ACA→SQL／Key Vault PE、管理VM→PEなど、同一VNet内のより具体的な直接経路にはFirewall規則を置かない。PE側のNSG評価は[NSG設計](network-security-design.md)で記録する。
- AppGWからAPIMとPortalに向かう2規則でTLSを検査する。表に記載するその他のApplication Ruleは `terminateTLS: false` を設定する。
- 独自のAny宛Internet Network Ruleや、独自の末尾Deny collectionは追加しない。許可されない通信にはFirewallの暗黙Denyが適用される。

Firewallは規則種別を含む評価順序を持ち、Network RuleがApplication Ruleに先行する。小さいRCG／collection優先度は同種の評価順を整理するものであり、NSGの優先度とは独立している。各規則にNSGのような個別priorityはない。同じ通信がサービスタグのNetwork RuleとFQDNのApplication Ruleの両方に一致する場合、後段のFQDN規則へ必ずhitするとは記録しない。[公式の規則処理](https://learn.microsoft.com/en-us/azure/firewall/rule-processing)

NSGの `allow-internet-egress` がTCP80／443を許可していても、Firewallの任意Internet許可を意味しない。NSGとFirewallの両方の許可、経路、名前解決、TLS、認可が成立条件となる。サービス・タグはAzureが管理する宛先集合であり、特定の自資源や利用者だけを許可する認証境界ではない。

## 資材の所有範囲

| RCG | 優先度 | collection（優先度） | 規則数 | 正本 |
| --- | ---: | --- | ---: | --- |
| `rcg-ingress` | 200 | `allow-agw-to-backends`（200） | 2 | [portal-network-bindings.bicep](../../../infra/modules/network/portal-network-bindings.bicep) |
| `rcg-platform` | 300 | `allow-apim-platform`（200）、`allow-vm-platform`（210） | 12 | [network.bicep](../../../infra/modules/network/network.bicep) |
| `rcg-portal` | 350 | `portal-azure-monitor`（150）、`portal-platform-and-probe`（200）、`portal-runner`（300） | 6 | [portal-network.bicep](../../../infra/modules/network/portal-network.bicep) |
| `rcg-credential-broker` | 360 | `credential-broker-azure`（200） | 3 | [credential-broker-network.bicep](../../../infra/modules/network/credential-broker-network.bicep) |
| `rcg-portal-gateway` | 370 | `portal-gateway`（100） | 1 | [gateway-egress.bicep](../../../apps/portal/deploy/gateway-egress.bicep) |
| `rcg-certificate` | 370 | `certificate-controls`（100）、`certificate-runtime`（200） | 3 | [certificate-network.bicep](../../../infra/modules/certificates/certificate-network.bicep) |
| `rcg-audit-evidence` | 375 | `audit-platform-tags`（100）、`audit-platform-hosts`（200） | 2 | [audit-evidence-network.bicep](../../../infra/modules/audit-evidence/audit-evidence-network.bicep) |
| `rcg-egress` | 400 | `allow-apim-egress`（200）、`allow-vm-egress`（210） | 11 | [network.bicep](../../../infra/modules/network/network.bicep) |

同じFirewall Policyへは、各工程が所有するRCGだけを配備する。`rcg-portal-gateway`と`rcg-certificate`はともに370で、対象とする送信元サブネットが異なる。受入では両グループの設定を読み戻し、実際の接続を確認する。

## ルールと通信要件の対応

以下は基本構成の規則。 `N` はNetwork Rule、 `A` はApplication Ruleを示す。AppGWの2規則でTLS検査を有効にする。

### rcg-ingress / allow-agw-to-backends

| 規則名 | 種別・通信 | 送信元 → 宛先 | 通信ID・用途 | 対応NSG・採用理由 |
| --- | --- | --- | --- | --- |
| `allow-appgw-apim-https` | A・HTTPS443、TLS検査 | AppGW → 構築APIMの既定Gateway FQDN | [`FLOW-APPGW-APIM`](network-communication-requirements.md#flow-appgw-apim)。通常API、SSE、WebSocket等とプローブ | AppGW `allow-apim-https-egress`、APIM `allow-firewall-https-ingress` |
| `allow-appgw-portal-https` | A・HTTPS443、TLS検査 | AppGW → Portal ACAのアプリFQDN | [`FLOW-APPGW-ACA`](network-communication-requirements.md#flow-appgw-aca)。Portalとヘルスプローブ | AppGW `allow-aca-https-egress`、ACA `allow-firewall-https-ingress` |

宛先はAPIMとPortalの構築出力から取得し、Private DNSで内部VIPへ解決する。

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
| `allow-mgmt-vm-entra` | N・TCP443 | `AzureActiveDirectory`タグ | [`FLOW-MGMT-ENTRA`](network-communication-requirements.md#flow-mgmt-entra)。Entra SSH、管理VM上の本人認証とMicrosoft Graphでの本人の照合 | `allow-entra-egress`。VM内IMDSへのHTTP80取得とは別の外向き通信 |
| `allow-mgmt-vm-arm` | N・TCP443 | `AzureResourceManager`タグ | [`FLOW-MGMT-ARM`](network-communication-requirements.md#flow-mgmt-arm)。構築資源の管理API操作、ログ接続token取得 | `allow-azure-resource-manager-egress`。VMを全リソースの保守拠点とするための管理到達性 |
| `allow-mgmt-vm-monitor` | N・TCP443 | `AzureMonitor`タグ | [`FLOW-MGMT-MONITOR`](network-communication-requirements.md#flow-mgmt-monitor)。AMAの監視送信、保存済みログ閲覧 | `allow-monitor-egress`。監視送信と閲覧は用途・認可を分ける |
| `allow-mgmt-vm-agent-storage` | N・TCP443 | `Storage`タグ | [`FLOW-MGMT-AGENT-STORAGE`](network-communication-requirements.md#flow-mgmt-agent-storage)。Agent配布・状態等の直接Storage依存、診断取得 | `allow-storage-egress`。構築監査Blob PEへの閲覧許可とは別 |

送信元は全件管理VMサブネット。管理VMはAzureホストのPHCをChronyで参照する。 [管理VMの時刻同期](platform-communications.md#管理vmのazureホスト時刻とapimの外部ntp)

### rcg-portal

| collection（優先度） | 規則名 | 種別・通信 | 送信元 → 宛先 | 通信ID・用途 | 対応NSG・採用理由 |
| --- | --- | --- | --- | --- | --- |
| `portal-azure-monitor`（150） | `allow-aca-monitor` | N・TCP443 | ACA → `AzureMonitor`タグ | [`FLOW-ACA-MONITOR`](network-communication-requirements.md#flow-aca-monitor)。ACAの監視依存 | ACA `allow-monitor-egress`。他のAzureタグ用途と名前を分離 |
| `portal-azure-monitor`（150） | `allow-aca-entra` | N・TCP443 | ACA → `AzureActiveDirectory`タグ | [`FLOW-ACA-ENTRA`](network-communication-requirements.md#flow-aca-entra)。ACAの認証基盤依存 | ACA `allow-entra-egress`。Monitor許可と同じportでも別規則にする |
| `portal-platform-and-probe`（200） | `allow-aca-mcr` | A・HTTPS443 | ACA → `MCR`宛先群 | [`FLOW-ACA-MCR`](network-communication-requirements.md#flow-aca-mcr)。Microsoft Container Registryのimage取得 | ACA `allow-mcr-egress`。構築したACR PEへの接続と区別 |
| `portal-platform-and-probe`（200） | `allow-aca-platform-packages` | A・HTTPS443 | ACA → `ACA-PACKAGES`宛先群 | [`FLOW-ACA-PLATFORM-PACKAGES`](network-communication-requirements.md#flow-aca-platform-packages)。ACA基盤のpackage依存 | ACA `allow-afd-first-party-egress`。必要FQDNを明示し、任意Internet許可にしない |
| `portal-platform-and-probe`（200） | `allow-aca-managed-identity` | A・HTTPS443 | ACA → `ACA-IDENTITY`宛先群 | [`FLOW-ACA-MANAGED-IDENTITY-UPSTREAM`](network-communication-requirements.md#flow-aca-managed-identity-upstream)。ACA基盤のidentity依存 | ACAのARM／Entra等の仕様上の明示許可。SDK→`IDENTITY_ENDPOINT`ローカル取得そのものをFW通信へ読み替えない |
| `portal-runner`（300） | `allow-mgmt-vm-github-docker-node-build` | A・HTTPS443 | 管理VM → `APP-BUILD`宛先群 | [`FLOW-MGMT-APP-BUILD`](network-communication-requirements.md#flow-mgmt-app-build)。Git取得、Docker／Node／npmによる配備・build | 管理VM `allow-internet-egress`。必要配布先の共用CDN範囲を含め、TLS検査を適用せず、ビルドと配備に必要な接続を許可 |

ACAの公開HTTPSでは、NSGの`Internet`タグを固定`0.0.0.0/0` UDRと組み合わせ、構築Firewallが許可するFQDNまたはサービスタグへ宛先を限定する。ルートと拒否の実証は[受入条件](../../testing/network-security-validation.md#実環境の受入確認)を参照する。

`*.r2.cloudflarestorage.com`等のwildcardは特定自資源だけを許可する宛先ではない。配布元の共用CDNに必要な到達性として明記し、構築サブネット送信元とNSG、実行者の権限で用途を制限する。`api.ipify.org`向けの疎通probe許可は初期集合へ含めない。

### rcg-credential-broker / credential-broker-azure

| 規則名 | 種別・通信 | 送信元 → 宛先 | 通信ID・用途 | 対応NSG・採用理由 |
| --- | --- | --- | --- | --- |
| `allow-aca-entra-login` | A・HTTPS443 | Portal用ACA → `login.microsoftonline.com` | [`FLOW-ACA-ENTRA`](network-communication-requirements.md#flow-aca-entra)。Portal用環境のEntra接続。Broker用環境のEntra通信は`AzureActiveDirectory`タグのNetwork Ruleを使う | Portal用ACA `allow-entra-egress` |
| `allow-broker-arm` | A・HTTPS443 | Broker用ACA → `management.azure.com` | [`FLOW-ACA-ARM`](network-communication-requirements.md#flow-aca-arm)。Credential Managerの管理API | Broker用の`allow-arm-https-egress` |
| `allow-mgmt-vm-python-build` | A・HTTPS443 | 管理VM → `PYTHON-BUILD`宛先群 | [`FLOW-MGMT-PYTHON-BUILD`](network-communication-requirements.md#flow-mgmt-python-build)。BrokerのPython依存・build | 管理VM `allow-internet-egress`。Node／Docker側の配布先と用途を分離 |

Brokerは初期構築の常設構成要素とし、`credentialProfiles`が空であることを理由にこのRCGを不作成にはしない。Gatewayの内部 `deployApplication` はDB初期化→アプリ配備という工程の段階を表すもので、公開のBroker／FW作成スイッチではない。[Gateway配備入口](../../../apps/api-gateway/deploy/main.bicep)

### rcg-portal-gateway / portal-gateway

| 規則名 | 種別・通信 | 送信元 → 宛先 | 通信ID・用途 | 対応NSG・採用理由 |
| --- | --- | --- | --- | --- |
| `allow-aca-appgw-https` | A・HTTPS443 | ACA → 構築Gatewayの公開Host | [`FLOW-ACA-APPGW`](network-communication-requirements.md#flow-aca-appgw)。Portalのサーバー処理による公開Gateway API呼出し | ACA `allow-appgw-https-egress`、AppGW `allow-firewall-public-https-ingress`。外向きFW経由・公開IPへのSNATを含む公開入口経路 |

宛先は同じ構築のGateway originからhostnameだけを取得する。任意の利用者指定Hostを許可する入力ではない。AppGWの検査付きbackend用HTTPS443と、Portalから公開Gatewayへ接続するHTTPS443は別の区間である。

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

業務宛先は[firewall_destinations.py](../../../scripts/deployment/helpers/firewall_destinations.py)が検証済みAPI／Operationから取得する。`backendService: external` のHTTPS／WSS443 FQDNを対象とし、IP literal、任意port、資格情報付きURLを許可しない。本人接続機能では選択providerの `identityEndpoint` とARM互換到達性を生成する。公開の自由なFW宛先配列は使わない。API配備による宛先更新は、所属するcollectionや他規則を保持して読戻し・競合検知の対象とする。

### rcg-egress / allow-vm-egress

| 規則名 | 種別・通信 | 宛先 | 通信ID・用途 | 対応管理VM NSG・採用理由 |
| --- | --- | --- | --- | --- |
| `allow-mgmt-vm-certificate-validation` | A・HTTP80、HTTPS443 | `CERT-VALIDATION`（7宛先） | [`FLOW-MGMT-CERTIFICATE-VALIDATION`](network-communication-requirements.md#flow-mgmt-certificate-validation)。保守処理の証明書／PKI確認 | `allow-internet-egress`。共通のPKI検証先への接続を許可 |
| `allow-mgmt-vm-linux-packages` | A・HTTP80、HTTPS443 | `LINUX-PACKAGES` | [`FLOW-MGMT-LINUX-PACKAGES`](network-communication-requirements.md#flow-mgmt-linux-packages)。Ubuntu、PowerShell、Azure CLI等の準備 | `allow-internet-egress`。初期構築から保守clientを再現する配布依存 |
| `allow-mgmt-vm-aca-dev-https` | A・HTTPS443 | 構築リージョンの `<region>.azurecontainerapps.dev` | [`FLOW-MGMT-ACA-DEV`](network-communication-requirements.md#flow-mgmt-aca-dev)。ACA App／Jobのログ、exec等のDev gateway接続 | `allow-internet-egress`。単一リージョンの共用gatewayへ接続し、対象App／Job操作は本人のAzure RBACで制限 |
| `allow-mgmt-vm-approved-services` | A・HTTPS443 | `MGMT-SERVICES` | [`FLOW-MGMT-APPROVED-SERVICES`](network-communication-requirements.md#flow-mgmt-approved-services)。配布、ARM、構築した公開Hostの保守確認 | `allow-internet-egress`、該当するARMタグ許可。共通配布先と構築Hostから生成 |

送信元は全件管理VMサブネット。ACA Dev gatewayはリージョン共用であり、当Firewall規則だけで接続対象のApp／Jobを限定しない。ログ閲覧、exec、環境token、Jobログ等の本人認証とAzure RBACの条件は[通信要件](network-communication-requirements.md)で別に管理する。

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
| `MGMT-SERVICES` | `management.azure.com`、`cdn.oneget.org`、`cdn.powershellgallery.com`、`*.powershellgallery.com`、`go.microsoft.com`、`aka.ms`、構築Gateway公開Host、構築Portal公開Host |

ACME directoryは業務の証明書発行設定と共有する。productionでは `acme-v02.api.letsencrypt.org`、stagingでは `acme-staging-v02.api.letsencrypt.org`を用いる。7件の証明書確認FQDNはLet's Encryptの証明書発行APIと同じ用途ではなく、APIM／保守側の基盤証明書確認を含む互換到達性として固定で維持する。将来DigiCertから証明書を取得する場合だけの規則とはしない。

## params／configと固定部分

| 入力・出力 | 規則に影響する範囲 | 固定する範囲 |
| --- | --- | --- |
| `network.subnetPrefixes` | AppGW／APIM／管理VM／ACAの送信元、ACA宛先CIDR | RCG／collection／規則の存在、プロトコル・ポート |
| `allow-appgw-apim-https` | A・HTTPS443、TLS検査 | AppGW → 構築APIMの既定Gateway FQDN | [`FLOW-APPGW-APIM`](network-communication-requirements.md#flow-appgw-apim)。通常API、SSE、WebSocket等とプローブ | AppGW `allow-apim-https-egress`、APIM `allow-firewall-https-ingress` |
| `allow-appgw-portal-https` | A・HTTPS443、TLS検査 | AppGW → Portal ACAのアプリFQDN | [`FLOW-APPGW-ACA`](network-communication-requirements.md#flow-appgw-aca)。Portalとヘルスプローブ | AppGW `allow-aca-https-egress`、ACA `allow-firewall-https-ingress` |
| 構築Gateway／Portal公開Host | Portal→公開Gateway、管理VM保守確認の宛先 | HTTPS443、Allow、常設規則 |
| 構築リージョン | 管理VM ACA Dev gatewayの単一FQDN | HTTPS443、TLS inspection無効、常設規則 |
| 証明書ACME directory | 証明書Jobのproduction／staging公式CA Host | 証明書確認7宛先、CA許可規則の存在 |
| 検証済みAPI／Operation／本人接続設定 | APIM approved-servicesのFQDN集合 | 単一常設規則、HTTPS443、FQDN検証、他規則の保持 |
| Azure環境定義 | Storage suffix、Entra login host | 共通宛先の用途と規則集合 |
| Firewall SKU／IDPS／Threat Intel／SNAT設定 | Firewallの検査・変換方式 | 機能別のAllow規則 |


Firewallとポリシーは全環境でPremiumを使う。IDPSはDeny（Alert and Deny）で開始し、テスト環境で通常通信と誤検知を確認する。中間CAと専用IDの準備は [セットアップ](../configuration/config-options.md#firewallのtls検査を準備する) を参照する。

構築CLIは、RFC1918と構築VNetのCIDRからAppGWサブネットだけを差し引き、IDPSの内部分類を生成する。このポリシーではAppGWから内部APIM／Portalへの通信が受信方向となり、受信方向専用のシグネチャが適用対象になる。ネットワーク規則のSNAT除外は`IANAPrivateRanges`と構築VNetの範囲を維持する。 [AppGWをFirewallの前段に置く構成のIDPS設計](https://learn.microsoft.com/en-us/azure/architecture/example-scenario/gateway/firewall-application-gateway#application-gateway-in-front-of-azure-firewall)

TLS検査によるURLの記録は、取り込み前の変換を通るLog Analyticsへ送る。アプリケーション規則とThreatIntelの対象URLを保存前に除去する。ネットワーク規則とIDPS署名も診断設定で保存する。[ログの確認手順](../../testing/log-privacy.md)

## 判断記録

| 判断ID | 判断・採用理由 | 影響範囲 |
| --- | --- | --- |
| `FW-D01` | Firewallを通過する通信だけを登録する。将来の経路変更用やNSG通信の記録目的だけではFW許可を作らない | Broker内部VIPとSQL／Key Vault PEへのVNet内の直接通信は、NSGとサービス認可で制御 |
| `FW-D02` | 配備する機能ごとに資材で規則を管理する | Brokerは接続プロファイルの件数にかかわらず配備。本文監査は標準のテスト・本番で有効 |
| `FW-D04` | AppGWからAPIM／ACAへTLS検査付きHTTPS443 | Firewallは検査後に再暗号化し、対向NSGはFirewallの送信元を許可する |
| `FW-D05` | APIM NTPはAPIMサブネット・UDP123・宛先`*`を維持し、管理VMはホストPHCを使う | APIMはDNS回答差を回避するNSG328と組み合わせる。管理VMのPHC／VMBusは仮想ホストからの非IP連携 |
| `FW-D06` | 同じHTTPSでもEntra／ARM／Monitor／build等の用途を規則名で分離する | 同じタグやNetwork RuleとApplication Ruleの重複を明記し、特定の後段規則hitは保証しない |
| `FW-D07` | 証明書確認7宛先を維持する | 基盤互換到達性を失わない。全宛先の毎回利用、将来の証明書購入方式とは区別 |
| `FW-D08` | 管理VM ACA Dev gatewayを単一構築リージョンに常設許可する | 管理VMからのログ・exec等の保守経路を成立させる。対象操作は保守担当者本人への一時RBACで認可 |
| `FW-D09` | 任意Internet Network Rule、自由なFW宛先配列、ipify probeを作らない | 必要な固定FQDN・生成済み業務宛先と暗黙Denyで管理 |
| `FW-D10` | AppGW専用の2規則でTLS検査 | この2経路を復号後に再暗号化し、検査ログはURLを除去して保存する |

### 規則を作成しない直接通信

| 通信ID | 通信 | Firewall規則を作成しない理由 |
| --- | --- | --- |
| [`FLOW-APIM-BROKER`](network-communication-requirements.md#flow-apim-broker) | APIM → Broker専用ACA、HTTPS443 | 専用VIPへVNetで直接接続。APIM送信／Broker受信NSGで許可 |
| [`FLOW-ACA-SQL`](network-communication-requirements.md#flow-aca-sql) | ACA App／Job → SQL PE、TCP1433 | PEの直接到達。ACA送信／PE受信NSGとSQL認可で制御 |
| [`FLOW-ACA-KV`](network-communication-requirements.md#flow-aca-kv) | ACA → Key Vault PE、HTTPS443 | PEの直接到達。ACA送信／PE受信NSGとVault認可で制御 |
| [`FLOW-ACA-ACR`](network-communication-requirements.md#flow-aca-acr) | ACA → 構築ACR PE、HTTPS443 | PEの直接到達。MCR外部取得とは異なる経路 |
| [`FLOW-APIM-FOUNDRY`](network-communication-requirements.md#flow-apim-foundry)、[`FLOW-APIM-KV`](network-communication-requirements.md#flow-apim-kv)、[`FLOW-APPGW-KV`](network-communication-requirements.md#flow-appgw-kv) | APIM／AppGW → 構築Foundry／Vault PE | PEの直接到達。サービス・タグの基盤許可と自PE接続を区別 |
| [`FLOW-MGMT-KV`](network-communication-requirements.md#flow-mgmt-kv)、[`FLOW-MGMT-FOUNDRY`](network-communication-requirements.md#flow-mgmt-foundry)、[`FLOW-MGMT-ACR`](network-communication-requirements.md#flow-mgmt-acr)、[`FLOW-MGMT-SQL`](network-communication-requirements.md#flow-mgmt-sql) | 管理VM → 各構築PE | VNet内保守。送受信NSGと用途別MI／RBAC・DB権限で制御 |
| [`FLOW-MGMT-AUDIT-JAPAN`](network-communication-requirements.md#flow-mgmt-audit-japan)、[`FLOW-MGMT-AUDIT-FOUNDRY`](network-communication-requirements.md#flow-mgmt-audit-foundry) | 管理VM → 構築監査Blob PE | 監査閲覧の直接到達。外部Storageタグ依存とは別 |
| [`FLOW-MGMT-APIM`](network-communication-requirements.md#flow-mgmt-apim)、[`FLOW-MGMT-ACA-HTTPS`](network-communication-requirements.md#flow-mgmt-aca-https) | 管理VM → APIM／ACA内部endpoint | VNet内保守の直接経路。外部Dev gateway接続とは別 |

将来ハブ＆スポークへ手動変更するときは、実経路がFirewall通過へ変わる通信を特定してから、企業のDNS・UDR・SNAT・NSG・FWを一緒に見直す。本書の直接通信表を、将来用の未使用FW許可として初期投入しない。構築後に資材を再実行しない契約の手動変更は、初期資材の再適用で維持されるとは保証しない。

## 検証条件・結果の扱い

Azureの読戻しでRCG、collection、規則の種類、優先度、送受信先、プロトコル・ポート、TLS検査対象を照合する。AppGWのバックエンド正常性、Portalと業務API、App／Jobの起動・イメージ取得、管理VMの保守を新規接続で確認する。実際に一致した規則と許可・拒否の結果は、通信IDと結び付けて保護実施票に記録する。 [ネットワークの受入確認](../../testing/network-security-validation.md#実環境の受入確認)

## 公式根拠と実装参照

- [Azure Firewallの規則処理](https://learn.microsoft.com/en-us/azure/firewall/rule-processing)、[Application Rule](https://learn.microsoft.com/en-us/azure/firewall/features#application-fqdn-filtering-rules)、[FQDNとDNS proxy](https://learn.microsoft.com/en-us/azure/firewall/dns-settings)。規則の種別・優先度とDNSの役割。
- [APIM VNetの送受信要件](https://learn.microsoft.com/en-us/azure/api-management/virtual-network-reference)。Storage／SQL／Key Vault／Monitor／Entra／NTP等の基盤依存。
- [ACAとFirewallの統合](https://learn.microsoft.com/en-us/azure/container-apps/use-azure-firewall)、[ACAの送信要件](https://learn.microsoft.com/en-us/azure/container-apps/firewall-integration#outbound)、[管理IDのREST仕様](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity#rest-endpoint-reference)。外向き依存とローカル取得の区別。
- [固定commitのAzure CLI ACA接続実装](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/containerapp/custom.py)。リージョンDev gatewayと接続clientの根拠。
- [Azure Firewall PremiumのTLS inspection](https://learn.microsoft.com/en-us/azure/firewall/premium-features#tls-inspection)、[IDPS](https://learn.microsoft.com/en-us/azure/firewall/premium-features#idps)。TLS終端・復号とIDPSの関係。
- [AzureのUDRと経路選択](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-networks-udr-overview)、[Private EndpointのNSG／route table条件](https://learn.microsoft.com/en-us/azure/private-link/disable-private-endpoint-network-policy)。宛先に一致する規則だけではFW経由にならないことの根拠。
- [ACA公開Issueの補足](https://github.com/microsoft/azure-container-apps/issues/893#issuecomment-1711892233)。ACA構成に応じた依存の再現条件を確認する参考であり、全必要宛先や顧客NSG評価区間を保証する公式仕様の代わりにしない。

公式仕様は成立条件を判断する根拠であり、導入したSKU・地域・版・資材に対する実証の代替ではない。後続のAzure仕様変更や業務接続追加で要件が変わる場合は、通信マスター、NSG、実経路、Firewall、検証条件を一緒に更新する。
