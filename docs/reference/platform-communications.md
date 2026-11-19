# NSG規則にしない基盤通信

[NSGの構成](../operations/network-security-groups.md) · [基盤仕様](platform.md) · [保守の共通確認](../operations/common.md)

初期構築は独立VNet、Azure提供DNS、標準NTPを使用する。NSGでは、構築したサブネットCIDRと割当IPを使って業務・保守・Azure基盤への通信を明示する。一方、同じホスト内の処理、Azureの基盤例外、サービス内部の処理は、通常のNSG Allowの名前・優先度・IP／ポートへ置き換えない。本書はその通信要件と成立条件を記録する。

NSGは状態を保持するため、許可された接続への応答だけを理由に逆方向Allowを追加しない。Azure提供DNSやIMDSには専用のサービス・タグによる制御もある。通常の`deny-any-ingress`／`deny-any-egress`で、以下のすべてを遮断できるという設計にはしない。[NSGの評価と基盤サービス](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview)

以下のIDは通信設計の照合用であり、Azureに作成するルール名ではない。仕様・資材の照合と、実環境の稼働確認は分ける。実URL、ヘッダー、トークン、診断SASや実測結果は公開文書へ転記せず、環境の保護された証跡領域で扱う。

## Container Appsサブネット

| 設計ID | 通信要件との対応 | 送信元 → 宛先 | プロトコル／ポート | NSGでの扱い |
| --- | --- | --- | --- | --- |
| `B-ACA-18` | `CA-P08`、`A-MI-001`、`B14`。対象Jobsの取得区間は`B31`／`B32`／`B33`／`B37` | Portal、認証窓口、対象JobのSDK → ACAが提供する`IDENTITY_ENDPOINT` | 内部RESTのHTTP GET。接続先・ポートは提供URLから取得 | 固定IP／ポートのAllowを作らず、名前・優先度も割り当てない |
| `B-ACA-19` | `CA-P10`、`PE-P02`のACA発問い合わせ | ACAのresolver → Azure提供DNS | UDP／TCP 53、Azure共通アドレス`168.63.129.16` | 通常DNS Allowと`AzurePlatformDNS`専用Denyを初期集合へ追加しない |
| `B-ACA-20` | `CA-P15` | ACAのmanaged基盤 → 管理サービス | 個別の完全な宛先・ポート・顧客NSG評価区間は未確定 | 追跡記録を維持し、推測した専用Allow・作成フラグを追加しない |

### ローカル管理IDの取得

ACAは`IDENTITY_ENDPOINT`と`IDENTITY_HEADER`を実行環境へ供給する。[管理IDのREST仕様](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity#rest-endpoint-reference)に従い、Azure Identity SDKで用途別の構築済み管理IDを選ぶ。URL・ヘッダーの実値をparams／configへ追加したり、VM用IMDSの固定IPをACAへ流用したりしない。具体的なloopback IP・固定ポート・全処理のNSG非適用を一律に保証する公開契約としても扱わない。

資材上の対応は、[Portal SQL接続](../../apps/portal/lib/auth-database.ts)、[Portal APIM操作](../../apps/portal/lib/api-keys/apim.ts)、[Broker SQL接続](../../apps/gateway/credential-broker/credential_broker/store.py)、[Broker CM操作](../../apps/gateway/credential-broker/credential_broker/cm.py)の`ManagedIdentityCredential`と、アプリ／Jobの管理ID割当である。`AZURE_CLIENT_ID`等の用途別ID選択は維持する。

トークン要求の対象URIは、ローカル取得時の接続先ではない。取得後のSQL、ARM、Entra、Graph等への要求は、それぞれの接続要件・NSG／Firewall・RBACで扱う。対象機能の設定は後続処理を変え得るが、ローカル管理ID取得用のNSG作成フラグにはしない。

運用確認では、対象アプリとJobが意図した管理IDを使用し、後続サービスの認可を通過することを確認する。実行環境のヘッダーやトークンを表示して確認する方法は使わない。

### Azure提供DNS

[ネットワーク資材](../../infra/modules/network.bicep)はVNet DHCPのDNSを空配列、Firewall Policyの上流DNSを空配列へ固定し、Firewall DNS proxyを維持する。DNSサーバーを変更する公開params／configは設けない。

Azure提供DNSへの問い合わせは、通常のNSG規則では制限されず、`AzurePlatformDNS`を明示対象とする規則で制御できる。[Azure共通IPの仕様](https://learn.microsoft.com/en-us/azure/virtual-network/what-is-ip-address-168-63-129-16#scope-of-azure-platform-virtual-ip)を、カスタムDNSやあらゆる宛先の53番通信へ一般化しない。

Private DNS zone、VNet link、PEのDNS zone groupが正しい回答を作る条件は別に必要である。名前解決成功と、その回答IPへのNSG／Firewall／経路・TLS・認可の成功も別に確認する。[ACAのDNS要件](https://learn.microsoft.com/en-us/azure/container-apps/private-endpoints-with-dns#custom-dns)

### managed基盤の保守

公開済みのMCR、Azure Front Door FirstParty、ARM、Entra、Monitor等の必要通信は、採用済みNSG規則へ対応付ける。未公開または個別条件が未確定の保守処理を、新しい宛先・ポートの推測で補わない。ACAのネットワーク形態・サービス更新・レジストリ依存によって要求が変わり得る。[ACAの送信要件](https://learn.microsoft.com/en-us/azure/container-apps/firewall-integration#outbound)と[Azure Firewallとの統合](https://learn.microsoft.com/en-us/azure/container-apps/use-azure-firewall)を参照する。

初期ACAには受信・送信のカスタムAny末尾Denyを作らず、任意Internet／Storage向けAllowも作らない。既定の存在だけで、Firewall経由の全基盤通信が成功したとは判定しない。レジストリ／Storage依存の検証は未解決事項として保持する。将来末尾Denyを追加する場合は、image取得、起動・scale、アプリ／Job実行、監視、基盤更新と障害復旧を先に検証する。[公式Issueの補足](https://github.com/microsoft/azure-container-apps/issues/893#issuecomment-1711892233)は再現条件の参考であり、必要宛先の公式契約を置き換えない。

## 管理VMサブネット

| 設計ID | 通信要件との対応 | 送信元 → 宛先 | プロトコル／ポート | NSGでの扱い |
| --- | --- | --- | --- | --- |
| `B-MGMT-12` | `R04`、`R17`、`B44`、`REQ-MGMT-CERTIFICATE-OPS-IMDS-001`。AMAの依存は`R39` | 管理VM内のMI利用者 → VM IMDS | HTTP／TCP 80、`169.254.169.254/metadata/identity/oauth2/token` | 通常Allowを作らない。専用`AzurePlatformIMDS`制御とguest条件を区別 |
| `B-MGMT-13` | `R02`。直接配布／状態のfallbackは別要件`R03` | Linux Agent → Azure WireServer／HostGAPlugin host endpoint | TCP 80、32526、`168.63.129.16` | 通常NSG／UDRで制限する区間に置き換えない |
| `B-MGMT-14` | `R01` | DHCP client → Azure DHCP service、および逆方向 | client UDP 68 → server UDP 67、server UDP 67 → client UDP 68 | 両方向を通信記録に残し、通常Allowを作らない |
| `B-MGMT-15` | `R40`、`PE-P02`の管理VM発問い合わせ | guest resolver → Azure提供DNS | UDP／TCP 53、`168.63.129.16` | 通常Allowを作らず、`AzurePlatformDNS`専用Denyも初期不作成 |
| `B-MGMT-16` | `R21` | 同VM内rsyslog → AMA listener | TCP loopback。標準例は`127.0.0.1:28330`。版・構成によってUnix Domain Socket | 同VM内の通信として記録し、remote Syslog用受信Allowを作らない |
| `B-MGMT-17-TIME` | `R20`のhost時刻部分 | Azure仮想ホスト → guest時刻提供機構／PHC／時刻同期daemon | Hyper-V integration channel、VMBus、PHC device access。IP／ポートなし | 外部NTP UDP123やネットワークPTP UDP319／320のAllowに置き換えない |
| `B-MGMT-17-DISK` | `R37`のattached OS Disk部分 | guest OS／アプリ → 接続済みManaged OS Disk | 仮想disk controllerを通るblock I/O。IP／ポートなし | guest Storage HTTPS443やprovider内部IPのAllowに置き換えない |
| `B-MGMT-17-BOOT` | `R37`のmanaged boot diagnostics収集・保存部分 | Azure Compute基盤 → managed diagnostics保存先 | 基盤のconsole output／screenshot収集・保存。顧客NICのIP／ポートとして定義しない | guest Storage443やAgent hostchannelへ推測対応付けしない |

### IMDSと証明書操作

[VM IMDSの仕様](https://learn.microsoft.com/en-us/azure/virtual-machines/instance-metadata-service)は、primary NIC／primary IP、guest DHCP、経路、プロキシ回避等の条件を持つ。通常のAny末尾DenyだけでIMDSを遮断する説明にはしない。専用`AzurePlatformIMDS`タグによる規則、guestのlocal Firewall・route・proxyは別の条件である。[サービス・タグの仕様](https://learn.microsoft.com/en-us/azure/virtual-network/service-tags-overview)

証明書の[guest worker](../../guest/certificate.ps1)は、IMDS要求に`Metadata: true`を付け、自動redirectを禁止してproxyを無効にする。Key Vault用はVMのSystemAssigned ID、Azure DNS更新用は構築時に割り当てた所定のUserAssigned IDを明示する。audienceの指定はトークンの対象サービスを示し、IMDS取得時にそのサービスへHTTPS接続する意味ではない。Key Vault、Azure DNSのARM操作、ACME発行先への通信は別要件である。

[証明書保守helper](../../scripts/operations/certificate.py)の公開certificate metadata取得も、IMDS専用の`ProxyHandler({})`とredirect禁止を使用する。Vault HTTPS用openerは分離し、従来のproxy動作を維持する。秘密／PFXの取得はこのmetadata処理へ追加しない。[合成通信試験](../../tests/test_operations_certificate.py)は、環境proxyを設定してIMDSの直接接続、Vaultのproxy選択、両宛先のredirect拒否をソケット接続前に検証する。実VMのproxy設定や実障害を観測した試験ではない。

運用では、構築管理IDと用途別RBAC、primary NIC／IP、DHCP、guest route／local Firewall、各MI利用処理と後続サービスの認可を確認する。SDKのcache周期や実token更新頻度は実測なしに固定しない。[AMAの管理ID・ネットワーク要件](https://learn.microsoft.com/en-us/azure/azure-monitor/agents/azure-monitor-agent-requirements)

### Agentとホストの管理通信

Linux Agentの状態・構成取得とHostGAPluginのhostchannelは、[Azure共通IP](https://learn.microsoft.com/en-us/azure/virtual-network/what-is-ip-address-168-63-129-16)のTCP80／32526を使用する。これらは通常NSGによる制限とUDRの対象外であり、guest local Firewallでは妨げない。IPが同じでも、DNS53、DHCP、LB probeは別機能である。

[VM資材](../../infra/modules/management.bicep)はVM Agent、Entra login拡張、AMA、DCR関連付けを構成し、[監査設定](../../infra/modules/management-audit.bicep)と[証明書設定](../../infra/modules/certificate.bicep)はManaged Run Commandを利用する。Run Commandを、AzureからVMへの通常のInbound TCP22接続としてNSGへ追加しない。

Agentの配布・状態送信は版や構成によりhostchannelと直接Storage／Webへのfallbackがある。[Linux Agent仕様](https://learn.microsoft.com/en-us/azure/virtual-machines/extensions/agent-linux)、[Managed Run Command](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/run-command-managed)、公式実装の[WireServer](https://github.com/Azure/WALinuxAgent/blob/master/azurelinuxagent/common/protocol/wire.py)／[HostGAPlugin](https://github.com/Azure/WALinuxAgent/blob/master/azurelinuxagent/common/protocol/hostplugin.py)を参照する。実VM搭載版と実channelの確認を残し、公開masterの実装を実wireの証拠にしない。直接Storage／Web、スクリプトが行う外部接続、IMDSは対応する別要件を維持する。

### DHCPと標準DNS

Azure DHCPはNSGの基本サービス例外として扱う。最初のIP取得前から必要になるため、取得後の管理VM `/32`だけで許可する通常NSG規則へ置き換えない。client68／server67の両方向を記録し、初回broadcast、更新時のunicast／rebind、server IDやpeer IPは実測なしに固定しない。[DHCPv4の仕様](https://www.rfc-editor.org/rfc/rfc2131)

[NIC資材](../../infra/modules/management.bicep)のAzure private IP割当方式と、guestがDHCPでネットワーク構成を受け取る条件は区別する。運用確認にはguest DHCPの有効性、lease／更新、primary NIC／IP、経路、local Firewallを含める。DHCPが通る理由を、確立済みNSG接続へのstateful返信だけで説明しない。

管理VMのDNSも、ACAと同じAzure提供DNSの例外を使用する。guest resolver、stub／cache、local Firewall、Private DNSの回答構成と実名前解決は個別に確認する。Firewall自身の上流DNSと、管理VMのDNS問い合わせは同じ通信として扱わない。[Private DNSのVNet link](https://learn.microsoft.com/en-us/azure/dns/private-dns-virtual-network-links)

### rsyslogからAMAへのローカル転送

[公式Syslog収集](https://learn.microsoft.com/en-us/azure/azure-monitor/vm/data-collection-syslog)の標準例はloopback TCP28330を使う。実portはAMAの`config-cache/syslog.port`と生成forwarderで確認する。Unix Domain Socket方式を使う版・構成にはIP／portを割り当てない。両方式が常に同時稼働する説明にも、28330が全版の固定契約である説明にもしない。[Linux rsyslogの確認方法](https://learn.microsoft.com/en-us/azure/azure-monitor/agents/azure-monitor-agent-troubleshoot-linux-vm-rsyslog)と[AMA版履歴](https://learn.microsoft.com/en-us/azure/azure-monitor/agents/azure-monitor-agent-extension-versions)

[guest監査設定](../../guest/configure-audit.sh)は、コマンド本文を含むsudo／cronイベントをAMA転送前に除外し、rsyslog構文検査と稼働確認を行う。AMAのforwarder自体を手製の固定port設定へ置き換えない。DCRの関連付けと収集条件、実Agent版、listener／socket、queue、local Firewallやsocket権限、実ログ到着を確認する。AMAから外部Monitorへの通信は別のNSG送信要件である。

### ホスト時刻と外部NTP

[Azure Linuxの時刻同期](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/time-sync)には、VMICTimeSyncによる起動・保守復帰時の補正と、Chrony等がPHCを使う継続同期がある。VMICTimeSync単独で恒久的な同期を保証する説明にしない。Linuxの[Hyper-V実装](https://github.com/torvalds/linux/blob/master/drivers/hv/hv_util.c)は機構の参考であり、実VMのkernel版一致や同期精度の証明ではない。

[guest監査設定](../../guest/configure-audit.sh)は既存PHC／refclock／OS設定を保持し、既存Chronyまたはtimesyncdへ標準外部NTP `ntp.ubuntu.com`を設定する。外部NTPは`allow-time-sync-egress`のUDP123と対応Firewall規則で扱う。APIM独自のNTP依存も別要件として維持する。

今回、PTPデバイスの新規構成、同期元の強制選択switch、UDP319／320のNSG Allowは追加しない。実driver、PHC device／symlink、選択source、時刻同期・精度・復帰補正を運用で確認する。企業のNTPへ手動変更する場合はguest／NSG／Firewallを揃え、再配備による標準外部NTPの再適用を考慮する。

### Managed OS Disk

[VM資材](../../infra/modules/management.bicep)の接続済みOS Diskの通常I/Oは、仮想block deviceを通る処理である。[Managed Diskの仕様](https://learn.microsoft.com/en-us/azure/virtual-machines/managed-disks-overview)に従い、`diskControllerType`、OS Disk SKU／容量、`encryptionAtHost`、VMサイズ・image互換性の入力を維持する。これらをNSG作成有無の入力へ転用しない。

guest Storage HTTPS443、ARMによる資源操作、Diskのimport／export、SASによるデータ転送、backup／restoreは通常のattached block I/Oとは別である。[Disk import／exportのPrivate Link仕様](https://learn.microsoft.com/en-us/azure/virtual-machines/disks-enable-private-links-for-import-export-portal)をPE全体の適用有無へ一般化しない。[Linux仮想SCSI実装](https://github.com/torvalds/linux/blob/master/drivers/scsi/storvsc_drv.c)も現VMのSCSI／NVMe選択を実測した結果ではない。

運用では実driver／deviceとI/Oを確認する。将来データ転送やbackupを導入する場合は、方式・実行主体・接続先・認可を別途評価する。provider内部の未公開IP／portを通常NSGへ推測追加しない。

### Managed Boot Diagnostics

[VM資材](../../infra/modules/management.bicep)は`bootDiagnostics.enabled: true`とし、`storageUri`を指定しないmanaged方式を使用する。記録対象はCompute基盤によるconsole output／screenshotの収集・保存である。[Boot Diagnostics仕様](https://learn.microsoft.com/en-us/azure/virtual-machines/boot-diagnostics)

診断データの取得・閲覧や対話Serial Consoleは別通信・認可・guest条件である。managed方式ではinstanceViewのBlob URIだけに依存せず、取得用APIの返却URIを扱う。[Compute API定義](https://github.com/Azure/azure-rest-api-specs/blob/main/specification/compute/resource-manager/Microsoft.Compute/Compute/stable/2024-11-01/ComputeRP.json)、[公式CLI実装](https://github.com/Azure/azure-cli/blob/dev/src/azure-cli/azure/cli/command_modules/vm/custom.py)、[Linux Serial Console要件](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/linux/serial-console-linux)

正常な管理VM内から取得を行う場合のARM／Storage等の接続と、外部のWindows操作端末から行う接続は実行主体で分ける。外部端末の要求を管理VM NSGの通信へ対応付けず、guest serial channelをSSH22へ読み替えない。返却URIのscheme／host／port、認可、収集・保存・表示の成功を運用で確認する。managed保存の内部wireをguest Storage443やAgent TCP80／32526へ推測対応付けしない。

## Private Endpointサブネットの境界

`B-PE-15`は`PE-P01`のPE応答とサービス内部処理を区別する記録である。通常のPEはclientの接続先であり、許可された接続への応答はstatefulに扱う。応答専用Outbound Allowと、Foundry、Vault、ACR、SQL内部のbackup／HA／保守用の架空IP・portを追加しない。

この資材はPEサブネットの`privateEndpointNetworkPolicies`を`NetworkSecurityGroupEnabled`にして、client側からPEへの必要通信をNSGで評価する。provider内部処理が顧客PE NSGを通らないことと、PE経由のclient通信を制御できることは別である。[Private Endpointの仕様](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-overview)

## AzureFirewallSubnetのNSG非関連付け

`DESIGN-FIREWALL-SUBNET-NSG-001`として、`AzureFirewallSubnet`には顧客NSGを作成・関連付けしない。[公式FAQ](https://learn.microsoft.com/en-us/azure/firewall/firewall-faq)は、サービス中断を避けるためsubnet-level NSGを無効としている。[公式FAQソース](https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/firewall/firewall-faq.yml)

[ネットワーク資材](../../infra/modules/network.bicep)はFirewallサブネットに構築CIDRを配置し、NSGの関連付けを持たない。独自のNSG Inbound／Outbound規則・名前・優先度・作成switchを追加せず、この状態を再構築時にも維持する。[合成構築試験](../../tests/test_parameter_ip_ownership.py)は、5つの対象サブネットへのNSG関連付けとFirewallサブネットの非関連付けを確認する。

`network.subnetPrefixes.firewall`のCIDRと、Firewall自身のSKU／IDPS／Policyは既存の用途を維持する。利用者通信の制御と要件マッピングはFirewall Policy Network／Application ruleの別棚卸しで行う。Azureが管理する不可視NIC-level NSGを、顧客資材のルールとして採番しない。今回、Firewall管理サブネット、管理Public IP、強制トンネルや新しいUDRを追加する判断は行わない。

## 再構築と運用確認の境界

構築資材は、固定NSG集合、標準DNS／外部NTP、構築管理ID、Agent／AMA／DCR、managed OS Disk、managed Boot Diagnostics、Firewallサブネットの非関連付けを再適用する。企業の手動変更をBicepで再現する新しい入力や、これらの通信のNSG作成フラグは設けない。

合成試験では資材の生成条件と変更範囲を検証する。実環境では、名前解決、管理IDの用途・認可、Agentの版とhostchannel、DHCP、AMAの実local転送と収集、時刻同期と保守復帰、Disk I/O、診断保存・閲覧を確認する。未確定ACA保守・レジストリ／Storage依存は追跡対象を維持し、文書化だけで実通信検証完了としない。
