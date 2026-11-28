# 通信要件とネットワークの設計前提

[基盤仕様](platform.md) · [NSG設計](network-security-design.md) · [Firewall Policy設計](firewall-policy-design.md) · [検証と実証結果の管理](../../testing/network-security-validation.md)

本書は、現在の構築資材が必要とする業務・保守・Azure基盤通信の参照元である。同一の接続を複数のAPIが使う場合は通信を共通化し、APIごとにNSGのポートを追加しない。HTTP、SSE、WebSocket、MCPのAPI契約と公開範囲は、[API定義](../../../apps/api-gateway/apis/)と[API設計](../gateway/api-contracts.md)を参照する。

`FLOW-...`は通信要件のIDであり、Azureのルール名ではない。NSG設計の各レコードとFirewall設計の各レコードから同じIDを参照する。終端Denyと既定規則は通信そのものではなく制御方針であるため、NSG側の`POLICY-...`で記録する。通信IDの行にある「FW」は現在の構築経路でFirewallを通過する区間だけを示す。

## 設計前提と判断

| 判断 | 採用する設計 | 理由と境界 |
| --- | --- | --- |
| 初期ネットワーク | 独立VNetと用途別のサブネット・NSG・ルートテーブル | AppGW、APIM、Private Endpoint、管理VM、Portal用ACA、Broker用ACA、証明書用ACAへNSGを関連付ける。AzureFirewallSubnetには顧客NSGを関連付けない |
| 固定ルール集合 | NSGとFirewallの名称・優先度・用途を資材で固定 | ルール作成の条件は資材にあり、構築した資源のアドレスを設定する |
| 公開入口 | AppGWのHTTP80とHTTPS443 | HTTPはHTTPSへのリダイレクト用。公開IPv4の初期範囲は`0.0.0.0/0`。企業の許可元IP制限は引渡し後に手動で変更する |
| AppGWのバックエンド | APIMとPortalのACA入口へHTTPS443 | AppGWで再暗号化し、Firewall PremiumでTLS検査後に再暗号化する。 |
| APIMからの接続 | Foundry・Key Vault PE、BrokerのACA入口等へHTTPS443 | AppGWのバックエンド設定とは別接続。サービスのHTTPS契約と認証を維持する |
| L7入口保護 | AppGWが`X-Core-L7-Ingress`を上書きし、APIMが検査 | 入口の証明は公開文書・paramsへ記載しない。業務APIの認可には別途Bearerまたは例外発行の購読キーを使う |
| 標準DNS | VNetとFirewallの上流DNSをAzure提供DNSへ固定。Firewall DNS proxyを保持 | DNS変更入力と初期カスタムDNS用NSG規則を設けない。Azure提供DNSのNSG例外を、他のDNSや全宛先53番へ一般化しない |
| 標準時刻 | APIMの外部NTP、管理VMのAzureホストPHC同期 | APIMはNSG328でInternet宛UDP123、FirewallでAPIMサブネットからUDP123を許可。管理VMはChronyでPHC／`AZUR`だけを参照し、非IPのVMBus／PHCにNSG／FWのUDP123／319／320許可を作らない |
| 明示Allowと既定許可 | Portal用・Broker用のACA両方向は必要通信を許可して末尾で拒否 | ACAの許可は同一サブネットの基盤通信、Azure製品基盤と用途別の経路に明示する。AppGWとPEのOutboundはAzure既定規則を使う |
| 共有SQLの接続方式 | Proxy固定、SQL PEへTCP1433で直接接続 | ACAと管理VMからSQL PEへのネットワーク許可をTCP1433に絞る。 [Proxy採用理由](sql-connectivity.md) |
| Firewallの記録範囲 | 実際にFirewallを通る通信だけをルールにする | PEへ直接接続するSQL/Key Vault等に、将来の迂回を想定したFW規則を置かない。通信要件は本書で記録する |
| 引渡し後の企業接続 | ハブ＆スポーク、中央DNS、企業NTP、許可元IPを手動で調整 | 初期構築の固定資材を企業設定の再適用手段としない。再実行は手動変更を戻し得るため、必要になった場合は別途差分と所有範囲を確認する |

設定・経路の実装元は[network.bicep](../../../infra/modules/network/network.bicep)、[network-bindings.bicep](../../../infra/modules/network/network-bindings.bicep)、[Portalの経路binding](../../../infra/modules/network/portal-network-bindings.bicep)である。[ハブ＆スポーク移行手順](../../setup/10-hub-spoke.md)は、初期要件と企業側変更の所有範囲を分けて読む。

公開TLS用Key Vaultと業務用Key Vaultには別々のPrivate Endpointを作り、同じ `privatelink.vaultcore.azure.net` ゾーンへ各Vaultのレコードを登録する。 [証明書用Jobの通信](../../operations/certificate-jobs.md)

## 主な業務経路

```mermaid
flowchart LR
    Client[利用端末] -->|HTTPS443| GW[AppGW]
    GW -->|HTTPS443| FW[Azure Firewall Premium / TLS inspection]
    FW -->|HTTPS443| APIM[Internal APIM]
    FW -->|HTTPS443| Portal[公開Portal / Portal用ACA]
    APIM -->|HTTPS443・直接| Foundry[Foundry PE]
    APIM -->|HTTPS443・Broker内部VIP| Broker[Broker / 専用ACA]
    Admin -->|TCP1433・直接| SQL[SQL PE]
    Profile -->|TCP1433・直接| SQL
    Broker -->|TCP1433・直接| SQL
    Broker -->|HTTPS443・直接| KV[Key Vault PE]
    Portal -->|HTTPS443・公開Gatewayへ| FW
    FW -->|HTTPS443・送信元変換後| GW
```

AppGWは構築APIM VIPとPortal用ACA VIPの`/32`宛UDRでFirewallを経由する。APIMとPortal用ACAからAppGWへの復路もFirewallへ向ける。Brokerの内部VIPにはAPIMが直接接続する。

APIM、管理VM、ACAの外部向け通信は各デフォルトUDRでFirewallを経由する。ただしVNet内・PE宛などの、より具体的なシステム経路が優先される区間と、APIM管理用`ApiManagement → Internet`のUDRは別扱いである。PEサブネットにルートテーブルは関連付けず、`privateEndpointNetworkPolicies: NetworkSecurityGroupEnabled`で対向NSGを評価する。経路とNSGのどちらか一方だけで成立を判定しない。

NSGはstatefulである。許可された接続への応答に逆向きの新規Allowを追加する必要はない。APIM内同期やPortalから公開Gatewayへ送る要求は、新たな接続なので別の通信要件として扱う。

## 公開入口と業務・内部接続

「送信元 → 宛先」は接続を開始する主体を示す。ACAの80/443は環境入口のポートであり、内部のNodePortやコンテナーのtargetPortとは区別する。

| 通信ID | 送信元 → 宛先 | プロトコル・宛先ポート | 用途 | 経路・制御 |
| --- | --- | --- | --- | --- |
| <a id="flow-client-http"></a>`FLOW-CLIENT-HTTP` | 公開クライアント → AppGW | TCP80 / HTTP | HTTPS入口へのリダイレクト | AppGW Inbound NSG。FW非通過 |
| <a id="flow-client-https"></a>`FLOW-CLIENT-HTTPS` | 公開クライアント → AppGW | TCP443 / HTTPS、WSS | API、SSE、WebSocket、MCP、Portal | AppGW Inbound NSGとWAF。バックエンド接続は次の行で区別 |
| <a id="flow-firewall-public-appgw"></a>`FLOW-FIREWALL-PUBLIC-APPGW` | 構築Firewallのpublic IP → AppGW | TCP443 / HTTPS | Portal等の公開Gateway呼出しでFWの送信元変換を経る通信の明示 | AppGW Inbound NSG。クライアントAllowと重なるため、この1規則だけで公開元を限定したとは扱わない |
| <a id="flow-appgw-apim"></a>`FLOW-APPGW-APIM` | AppGW → APIM内部VIP | TCP443 / HTTPS | API転送とAPIM health probe | VIP宛UDRでFWへ送り、標準Gateway FQDNでTLS検査。APIM Inboundの送信元はFirewallサブネット |
| <a id="flow-appgw-aca"></a>`FLOW-APPGW-ACA` | AppGW → Portal用ACAの内部VIP | TCP443 / HTTPS。ACA NSGは443と31443 | Portal転送と`/api/health`のprobe | VIP宛UDRでFWへ送り、Portal FQDNでTLS検査。PortalのACA Inboundの送信元はFirewallサブネット |
| <a id="flow-appgw-kv"></a>`FLOW-APPGW-KV` | AppGW → 公開TLS用Key Vault PE | TCP443 / HTTPS | 公開リスナー証明書のPFX secretを参照 | PE直接。AppGW Outbound、PE Inbound、対象secretの読取 |
| <a id="flow-apim-foundry"></a>`FLOW-APIM-FOUNDRY` | APIM → Foundry PE群 | TCP443 / HTTPS、WSS | モデル、音声、PII、文書解析等の上流API | PE直接。APIM Outbound、PE Inbound。API認可は別条件 |
| <a id="flow-apim-broker"></a>`FLOW-APIM-BROKER` | APIM → 専用ACA内部VIPのCredential Broker | TCP443 / HTTPS。Broker NSGは443と31443 | 本人確認、CM、本人トークンのOBO交換 | VNet直接。APIM Outboundの宛先はBroker VIP、Brokerの業務InboundはAPIMサブネット |
| <a id="flow-apim-kv"></a>`FLOW-APIM-KV` | APIM → 業務用Key Vault PE | TCP443 / HTTPS | Named Valueに対応する入口、Brokerと所有者チケットの秘密を参照 | PE直接。APIM Outbound、PE Inbound、対象secretの読取。APIMのサーバー証明書はAzure管理の標準証明書 |
| <a id="flow-aca-kv"></a>`FLOW-ACA-KV` | ACA App/Job → 業務用Key Vault PE | TCP443 / HTTPS | Portal、Broker等の用途別秘密と署名鍵 | PE直接。ACA Outbound、PE Inbound、実行IDの対象オブジェクト権限 |
| <a id="flow-aca-sql"></a>`FLOW-ACA-SQL` | ACA App/Job → SQL PE群 | TCP1433、SQL接続時のTLS | 本人接続、Portal認証と試用、schema初期化・更新 | PE直接。ACA Outbound、PE Inbound。SQL権限は用途別の実行IDへ割り当てる |
| <a id="flow-aca-acr"></a>`FLOW-ACA-ACR` | ACA → 構築ACR PEの全IP | TCP443 / HTTPS | App/Jobイメージ取得 | PE直接。login/dataのPE全IPを両NSGへ設定 |
| <a id="flow-aca-appgw"></a>`FLOW-ACA-APPGW` | Portal用ACA → 構築AppGW public IP | TCP443 / HTTPS、WSS | Portalサーバーから公開Gateway APIを呼ぶ | FW経由。Portal用ACA Outbound、AppGW Inbound |

AppGWは公開TLSを終端してWAFで検査し、Firewall経由でAPIMとPortalへHTTPS 443で転送する。Firewallはその2経路で復号・検査し、それぞれのバックエンドとTLSを確立する。APIMから上流への通信およびACAの外部接続は、それぞれのルートと認証に従う。 [FirewallのTLS検査設計](firewall-policy-design.md)

証明書用ACAの専用経路は[証明書Job](../../operations/certificate-jobs.md)、監査の送信先は[監査設計](../audit/audit-log-design.md)を参照する。

## 管理VMからの保守接続

| 通信ID | 送信元 → 宛先 | プロトコル・宛先ポート | 用途 | 経路・制御 |
| --- | --- | --- | --- | --- |
| <a id="flow-mgmt-kv"></a>`FLOW-MGMT-KV` | 管理VM → 業務用Key Vault PE | TCP443 / HTTPS | 業務用Vaultの保守と許可されたメタデータの確認 | PE直接。管理VM Outbound、PE Inbound、担当者本人への期限付き権限 |
| <a id="flow-mgmt-foundry"></a>`FLOW-MGMT-FOUNDRY` | 管理VM → Foundry PE群 | TCP443 / HTTPS | Foundryデータ面の保守 | PE直接。管理VM Outbound、PE Inbound。ARM管理は別接続 |
| <a id="flow-mgmt-acr"></a>`FLOW-MGMT-ACR` | 管理VM → ACR PEの全IP | TCP443 / HTTPS | イメージ配備・取得の保守 | PE直接。管理VM Outbound、PE Inbound |
| <a id="flow-mgmt-sql"></a>`FLOW-MGMT-SQL` | 管理VM → SQL PE群 | TCP1433、SQL接続時のTLS | 構築Gateway/PortalのDB直接保守 | PE直接。管理VM Outbound、PE Inbound。担当者本人のEntra認証、作業期間中の対象DB内の権限、ODBC clientが必要 |
| <a id="flow-mgmt-apim"></a>`FLOW-MGMT-APIM` | 管理VM → APIMサブネット | TCP443 / HTTPS | private Gateway側の保守確認 | VNet直接。管理VM Outbound、APIM Inbound。APIM管理用ARMとは別 |
| <a id="flow-mgmt-aca-https"></a>`FLOW-MGMT-ACA-HTTPS` | 管理VM → Portal用ACAの内部VIP | TCP443 / HTTPS。Portal用ACA NSGは443と31443 | Portalのprivate HTTPS入口確認 | VNet直接。Brokerの保守ログ・execは対象環境に許可されたDev APIを使う |
| <a id="flow-mgmt-audit-japan"></a>`FLOW-MGMT-AUDIT-JAPAN` | 管理VM → 基盤監査Storage（`collectJapan`）のBlob PE群 | TCP443 / HTTPS | APIM、Firewall、WAFなどの基盤監査Blobの閲覧 | PE直接。管理VM Outbound227、PE Inbound229。構築コンテナー単位の読取権限 |
| <a id="flow-mgmt-audit-foundry"></a>`FLOW-MGMT-AUDIT-FOUNDRY` | 管理VM → collectFoundry監査StorageのBlob PE群 | TCP443 / HTTPS | Foundry側監査Blobの閲覧 | PE直接。管理VM Outbound228、PE Inbound230。Foundry APIへの接続とは別 |
| <a id="flow-mgmt-aca-dev"></a>`FLOW-MGMT-ACA-DEV` | 管理VM → 構築リージョンのACA Dev gateway | TCP443 / HTTPS、WSS | App/環境/Jobのライブログ、App exec | FW経由。NSGのInternet443とFWの単一リージョン宛FQDN規則。本人のARM token、対象リソースのRBAC、対象実行の存続も必要 |

保守操作の権限とclientは[管理VMの保守操作](../../operations/management-client.md#管理vmから保守データを取得する)を参照する。NSGのTCP1433/443許可だけで、SQL保守、Blob閲覧、ACA execが認可されるわけではない。手動Jobのログ確認のためだけにDDLや初期データ更新を再実行しない。

## Azure製品の仕様上の通信

ここではサービス・タグを宛先分類に使う。タグは構築資源1件への限定ではなくAzureが管理する範囲であり、NSGのAllowと後段のFQDN規則・サービス認可を区別する。

| 通信ID | 送信元 → 宛先 | プロトコル・宛先ポート | 用途・境界 |
| --- | --- | --- | --- |
| <a id="flow-appgw-gateway-manager"></a>`FLOW-APPGW-GATEWAY-MANAGER` | GatewayManager → AppGW | TCP65200–65535 | 採用AppGW v2の基盤管理Inbound。業務リスナーと別。FW非通過 |
| <a id="flow-appgw-load-balancer"></a>`FLOW-APPGW-LOAD-BALANCER` | AzureLoadBalancer → AppGW | NSGは全プロトコル・全ポート | 基盤probe用の常設Allow。タグ内を広く許可する採用判断であり、全ポートで業務通信が発生するという意味ではない |
| <a id="flow-apim-management"></a>`FLOW-APIM-MANAGEMENT` | ApiManagement → APIM | TCP3443 | classic APIMの管理通信。管理専用UDRと復路も確認 |
| <a id="flow-apim-load-balancer"></a>`FLOW-APIM-LOAD-BALANCER` | AzureLoadBalancer → APIM | NSGは全プロトコル・全ポート | 公式probe要件のTCP6390を包含。AppGWとAnyへ統一した判断であり、最小ポート許可と同義ではない |
| <a id="flow-apim-counter-sync"></a>`FLOW-APIM-COUNTER-SYNC` | APIMインスタンス間 | UDP4290 | rate-limit counter同期。APIM両方向NSG。FW非通過 |
| <a id="flow-apim-internal-cache"></a>`FLOW-APIM-INTERNAL-CACHE` | APIMインスタンス間 | TCP6381–6383 | 内部cache依存。APIM両方向NSG。FW非通過 |
| <a id="flow-apim-storage"></a>`FLOW-APIM-STORAGE` | APIM → Storageタグ | TCP443 / HTTPS | APIM自身の基盤Storage。FW経由。ACAアプリのStorage依存とは別 |
| <a id="flow-apim-sql-platform"></a>`FLOW-APIM-SQL-PLATFORM` | APIM → Sqlタグ | TCP1433 | APIM自身の基盤SQL。FW経由。構築業務SQL PEとは別 |
| <a id="flow-apim-kv-platform"></a>`FLOW-APIM-KV-PLATFORM` | APIM → AzureKeyVaultタグ | TCP443 / HTTPS | Azure基盤の依存と、構築資材外のVaultを追加バックエンドに使う場合の経路。FW経由。対象Vaultで別途ネットワークとRBACを認可する |
| <a id="flow-apim-monitor"></a>`FLOW-APIM-MONITOR` | APIM → AzureMonitorタグ | TCP1886、443 | 監視・診断。FW経由 |
| <a id="flow-apim-event-hubs"></a>`FLOW-APIM-EVENT-HUBS` | APIM → EventHubタグ | TCP443、5671、5672 | Event Hubs依存。FW経由。実イベント送信は採用logger設定にも依存 |
| <a id="flow-apim-entra"></a>`FLOW-APIM-ENTRA` | APIM → AzureActiveDirectoryタグ | TCP443 / HTTPS | Entra認証・ID基盤。FW経由 |
| <a id="flow-apim-credential-manager"></a>`FLOW-APIM-CREDENTIAL-MANAGER` | APIM → AzureConnectorsタグ | TCP443 / HTTPS | CMの接続基盤。FW経由。利用する本人OAuth providerは別に整理 |
| <a id="flow-apim-internet"></a>`FLOW-APIM-INTERNET` | APIM → Internetタグ | TCP80、443 | APIM NSG側の外向き許可枠。FWは下表の具体的なFQDN用途で制限。全Internet宛がFWでAllowになるわけではない |
| <a id="flow-apim-ntp"></a>`FLOW-APIM-NTP` | APIM → 外部時刻源 | UDP123 | FW経由。NSGはInternet、FW宛先は`*`。DNS回答差で時刻同期を遮断しない。内部daemonの可視性とAPIMの時刻応答を区別 |
| <a id="flow-aca-load-balancer"></a>`FLOW-ACA-LOAD-BALANCER` | AzureLoadBalancer → ACAサブネット | TCP30000–32767 | ACA基盤probe。FW非通過。AppGW/APIMのAny判断をACAへ適用しない |
| <a id="flow-aca-internal"></a>`FLOW-ACA-INTERNAL` | ACAサブネット内 → 同サブネット | 全プロトコル・全ポート | managed基盤の内部通信。同じACAサブネット内の両方向を明示許可する。 |
| <a id="flow-aca-arm"></a>`FLOW-ACA-ARM` | Broker用ACA → ARM | TCP443 / HTTPS | Credential Managerの操作と照合。FW経由 |
| <a id="flow-aca-entra"></a>`FLOW-ACA-ENTRA` | ACA → Entra ID基盤 | TCP443 / HTTPS | Portalの認可コード交換、BrokerのOBOによるGraphアクセストークン取得。既存の`AzureActiveDirectory`サービス タグにEntraサインインとMicrosoft Graphが含まれる。 [Azureサービス タグ](https://learn.microsoft.com/en-us/azure/virtual-network/service-tags-overview) |
| <a id="flow-aca-monitor"></a>`FLOW-ACA-MONITOR` | ACA → AzureMonitorタグ | TCP443 / HTTPS | 監視・ログ送信。FW経由 |
| <a id="flow-aca-mcr"></a>`FLOW-ACA-MCR` | ACA → Microsoft Container Registry | TCP443 / HTTPS | Microsoft提供イメージ取得。FW経由。構築ACR PEとは別 |
| <a id="flow-aca-platform-packages"></a>`FLOW-ACA-PLATFORM-PACKAGES` | ACA → AzureFrontDoor.FirstPartyと基盤パッケージ配布先 | TCP443 / HTTPS | CDN経由のパッケージも含め、NSGから構築Firewallへ到達させる。Firewallの許可済みFQDNに限定する |
| <a id="flow-aca-managed-identity-upstream"></a>`FLOW-ACA-MANAGED-IDENTITY-UPSTREAM` | ACA基盤 → 管理IDの上流サービス | TCP443 / HTTPS | managed基盤側の依存先。FWの承認FQDNで扱う。AppからIDENTITY_ENDPOINTへのローカルtoken取得とは別 |
| <a id="flow-mgmt-arm"></a>`FLOW-MGMT-ARM` | 管理VM → AzureResourceManagerタグ | TCP443 / HTTPS | VM/ACA等の管理、token発行API、Azure DNSによる証明書更新。FW経由 |
| <a id="flow-mgmt-entra"></a>`FLOW-MGMT-ENTRA` | 管理VM → AzureActiveDirectoryタグ | TCP443 / HTTPS | Entra SSHと担当者本人のAzure CLI認証。FW経由 |
| <a id="flow-mgmt-monitor"></a>`FLOW-MGMT-MONITOR` | 管理VM → AzureMonitorタグ | TCP443 / HTTPS | AMA送信・保存ログ照会。FW経由。監査Blob PE閲覧とは別 |
| <a id="flow-mgmt-agent-storage"></a>`FLOW-MGMT-AGENT-STORAGE` | 管理VM → Storageタグ | TCP443 / HTTPS | Agent/拡張の直接Storage接続等。FW経由。接続済みManaged Disk I/Oやmanaged Boot Diagnostics内部保存をこの接続へ置き換えない |
| <a id="flow-mgmt-internet"></a>`FLOW-MGMT-INTERNET` | 管理VM → Internetタグ | TCP80、443 | NSG側の外向き許可枠。FWはOS更新、CA、build、ACA Dev等へ用途を分ける |

## Firewallを通る外部HTTP/HTTPSの用途分割

`AzureActiveDirectory`タグはサインインとMicrosoft Graphを含む。ACAパッケージのCDN宛はNSGの公開TCP443枠とFirewallのFQDN規則で制御する。[サービス・タグの仕様](https://learn.microsoft.com/en-us/azure/virtual-network/service-tags-overview) · [ACAのFirewall要件](https://learn.microsoft.com/en-us/azure/container-apps/use-azure-firewall)

下表の宛先群は、[Firewallの規則と通信対応](firewall-policy-design.md#ルールと通信要件の対応)でFQDN・protocol・port・collectionへ対応付ける。Network Ruleに先に一致する接続もあるため、各Application Ruleが必ずhitするという前提を置かない。

| 通信ID | 送信元 | 用途・宛先選択 | NSG側の許可枠 |
| --- | --- | --- | --- |
| <a id="flow-apim-certificate-validation"></a>`FLOW-APIM-CERTIFICATE-VALIDATION` | APIM | Azure基盤の証明書確認互換として維持する7宛先のOCSP/CRL等。7宛先すべてへの実通信発生を意味しない | APIM Internet TCP80/443 |
| <a id="flow-apim-windows-update"></a>`FLOW-APIM-WINDOWS-UPDATE` | APIM | Windows Update依存 | APIM Internet TCP80/443 |
| <a id="flow-apim-microsoft-redirect"></a>`FLOW-APIM-MICROSOFT-REDIRECT` | APIM | Microsoftの基盤redirect依存 | APIM Internet TCP80/443 |
| <a id="flow-apim-platform-diagnostics"></a>`FLOW-APIM-PLATFORM-DIAGNOSTICS` | APIM | Azure基盤診断先 | APIM Internet TCP443 |
| <a id="flow-apim-windows-defender"></a>`FLOW-APIM-WINDOWS-DEFENDER` | APIM | Defender基盤更新・配布先 | APIM Internet TCP443 |
| <a id="flow-apim-portal-diagnostics"></a>`FLOW-APIM-PORTAL-DIAGNOSTICS` | APIM | Microsoft管理Portalの診断先 | APIM Internet TCP443 |
| <a id="flow-apim-approved-services"></a>`FLOW-APIM-APPROVED-SERVICES` | APIM | 検証済みAPI backend、本人OAuth identity endpoint等から生成した承認FQDN | APIM Internet TCP443。資源/業務設定から宛先を生成し、任意FW追加入力は使わない |
| <a id="flow-mgmt-certificate-validation"></a>`FLOW-MGMT-CERTIFICATE-VALIDATION` | 管理VM | 公開CAの7宛先へのOCSP/CRL等の証明書検証。ACME発行先は別行で管理 | 管理VM Internet TCP80/443 |
| <a id="flow-mgmt-linux-packages"></a>`FLOW-MGMT-LINUX-PACKAGES` | 管理VM | Ubuntu等のOS package取得 | 管理VM Internet TCP80/443 |
| <a id="flow-mgmt-app-build"></a>`FLOW-MGMT-APP-BUILD` | 管理VM | GitHub、Node/npm、DockerのApp build依存 | 管理VM Internet TCP443 |
| <a id="flow-mgmt-python-build"></a>`FLOW-MGMT-PYTHON-BUILD` | 管理VM | Python package、ODBC、Brokerイメージbuildの配布先 | 管理VM Internet TCP443 |
| <a id="flow-mgmt-approved-services"></a>`FLOW-MGMT-APPROVED-SERVICES` | 管理VM | 構築公開Hostと資材に定義した保守・配布先のFQDN | 管理VM Internet TCP443 |

FWの許可先が一定でも、各通信が常に発生するという意味ではない。CA検証、OS更新、build、Event Hubs logger、外部backend等は該当処理を使用した際に発生する。固定集合に将来の業務通信を推測して追加せず、用途が増えるときは接続先・実経路・認可を別途確認する。

## 通常NSGルールへ置き換えない通信

以下は操作端末・サービス側の制御通信の要件である。構築のAzure CLI認証は操作端末で行い、管理VMで保守クライアントを使う担当者はゲスト内のAzure CLIにも本人でサインインする。SSHの到達経路と作業中の許可は[管理VMへの接続手順](../../operations/management-vm-access.md)に従う。

| 主体 → 宛先 | プロトコル・用途 | 構築VNet側の制御との関係 |
| --- | --- | --- |
| Windows操作端末/ブラウザ → Entra | HTTPS443、CLI/Portalの認証・同意 | 端末/企業NW側の制御。App/VM自身のMI取得とは別 |
| Windows操作端末 → ARM/Graph | HTTPS443、構築・読戻し・RBAC・Entra登録/所属の所定操作 | 管理VMのARM/Entra Allowを端末通信の許可と解釈しない |
| 操作端末 → Serial Console/診断取得先 | HTTPS/WSS443、serial対話・診断表示/返却URIからの取得 | 端末の認可と接続先を確認。VMへのInbound SSH22を追加しない |
| Azure診断基盤 → 構築監査Storage/Workspace | 診断設定によるログ・監査の保存 | provider送信と、VMからのBlob/保存ログ読取を区別 |
| CM/外部OAuth基盤/利用ブラウザ → 所定の認可・callback先 | HTTPS443、本人同意と接続の更新 | 正式API/CM契約に従う。自VNetを通る区間だけを対応NSG/FWへ紐付ける |

詳細な成立条件と公式根拠は[NSG規則にしない基盤通信](platform-communications.md)を参照する。これらも通信設計の対象であるが、架空のAllow名やポートを割り当てない。

| 記録 | 主体・処理 | 通信方式 | NSG/FWとの関係 |
| --- | --- | --- | --- |
| Azure提供DNS | AppGW/APIM/ACA/管理VM等のresolver | UDP/TCP53、Azure提供DNS | 通常NSG/UDRの基盤例外。Private DNS zone/link/PE zone groupと回答を確認。カスタムDNSに同じ例外はない |
| VM IMDS | 管理VMのAzure Monitor Agentなど → IMDS | Metadata付きHTTP、proxy/redirect回避 | 通常Denyと専用AzurePlatformIMDS制御を区別。token取得後のVault/ARM等は別のFLOW |
| ACA管理ID取得 | App/JobのSDK → 提供IDENTITY_ENDPOINT | 提供URLへの内部HTTP GET | IP/portをparamsやNSGへ固定しない。用途別MIと後続サービス認可を確認 |
| DHCP | 管理VM guest ↔ Azure DHCP | client UDP68 / server UDP67 | 基盤例外。初回IP取得と更新を、割当済VM /32への普通のAllowで表さない |
| VM Agent/HostGAPlugin | 管理VM → Azure host/WireServer | TCP80/32526のhostchannel | 通常NSG/UDRの基盤例外。直接Storage等のfallbackは別のFLOW |
| Managed Run Command/拡張 | Azure Compute制御 → VM Agent/guest | ARM制御とAgentの処理 | VMへの通常SSH22 Inbound接続へ置き換えない。スクリプトの外部接続は別途制御 |
| rsyslog → AMA | 同VM内の監査転送 | loopback TCPまたはUnix Domain Socket | remote Syslog Inboundを追加しない。実Agent版、生成forwarder、DCRとログ到着を確認 |
| <a id="flow-mgmt-host-time"></a>`FLOW-MGMT-HOST-TIME` | Azure Hyper-V/VMBus/PHC → 管理VMのChrony | 非IPのホスト連携 | Ubuntu24.04で`/dev/ptp_hyperv`と`clock_name=hyperv`を検証し、PHC／`AZUR`を唯一sourceとして選択・`Normal`同期を確認 |
| Managed Disk I/O | guest → 接続済みOS Disk | 仮想block device | guest Storage HTTPS通信ではない。backup/import/exportは別要件 |
| Boot Diagnostics | Azure Compute基盤 → managed診断保存先 | provider内部収集・保存 | 顧客VM NICを通るStorage接続に推測対応付けしない。閲覧取得のARM/Storageは実行主体で区別 |
| Serial Console | Windows操作端末 → Azure Serial Console → guest serial channel | 操作端末側のHTTPS/WSSと非IPのserial channel | 管理VM Inbound NSGでSSH22を開けない。利用者認可、Boot Diagnostics、guestのローカル認証が必要 |
| Private Link/provider処理 | PE応答とサービス内部のHA/backup/保守 | 許可接続へのstateful応答・provider内部処理 | 応答専用Outbound Allowやprovider内部の推測IP/portを追加しない |
| 診断・監査のprovider送信 | APIM/Firewall等 → 診断設定の保存先 | Azure診断基盤の処理 | Storage PEの読取接続とは別。顧客NSGを通るguest接続と一律に扱わない |
| Azure Firewall自身の基盤通信 | Firewallサービス・上流DNS・管理基盤 | managedサービスの依存通信 | AzureFirewallSubnetへ顧客NSGを追加しない。顧客通信の転送規則とAzureの内部管理通信を区別する |

## params/configとアドレスの依存

| 入力・出力 | 変わるもの | 変えないもの |
| --- | --- | --- |
| `network.subnetPrefixes`、VNet address space | 各サブネットの送受信範囲、構築UDR | ルールの存在・名前・priority・protocol・port |
| 構築VM/Firewall/AppGW/APIM/ACAの割当IP | VM/公開IP/VIPの対象。単一IPは`/32` | 企業外部IPの任意入力や公開client許可元入力を再導入しない |
| PEの構築出力 | Foundry、Vault、SQL、ACR、監査Blob PE NICの全IP | 最初のIPだけを採用したり、PEサブネット全体へのAllowへ広げたりしない |
| 構築リージョン・公開Host・Azure cloud | ACA Dev等のリージョン宛先、公開Host、公式cloud endpoint | 宛先を任意FW AllowedFqdnsで追加する入口を設けない |
| 検証済みAPI backend / 本人接続provider設定 | API定義のbackend/operation overrideと、`gateway.authentication.credentialLogin.providerNames`で選択したproviderの`identityEndpoint`から必要FQDNを生成 | Broker本体とRCGを省略しない。credential profileの空/非空をFW規則作成だけのフラグへ使わない |
| 証明書のproduction/staging選択 | 資材が用意したCA接続先の選択 | OCSP/CRLとACME発行の違い、公開TLS/入口保護を維持 |
| Firewall SKU、IDPS、Threat Intelligence等 | 検査機能・動作mode | 固定Allow集合の作成有無とは別。TLS inspectionが自動で有効になるとは扱わない |

構築設定の入力範囲は [環境別params](../../setup/07-params.md) と [標準config](../configuration/config-options.md) を参照する。

## 初回構築後の確認

Coreは固定の通信要件、構築資源のアドレス、標準DNSと保守のネットワーク経路を用意する。作業に必要な権限は担当者本人へ作業期間に合わせて付与・回収する。配備後は設定・実効経路を読み戻し、実際の業務接続と保守操作で到達性・認可を確認する。 [ネットワークの受入確認](../../testing/network-security-validation.md#実環境の受入確認)
