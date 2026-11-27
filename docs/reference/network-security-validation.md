# ネットワーク設計の検証と実証結果の管理

[通信要件](network-communication-requirements.md) · [NSG設計](network-security-design.md) · [Firewall設計](firewall-policy-design.md) · [運用テスト](../testing/operations-tests.md)

本書は、通信要件、構築資材、設定の読戻し、実操作の関係と、結果の管理方法を定める。公開する合成試験と環境別の実証原本を分け、設定一致だけで通信全件の実証済みとは判定しない。

## 確認段階

| 段階 | 判定する内容 | この段階だけでは証明しない内容 |
| --- | --- | --- |
| 設計判断 | 通信要件、NSG/FWへの対応、採否、例外、変更入力の範囲 | 資材への実装と実環境動作 |
| 資材実装 | 固定集合、構築IPのbinding、初期構築工程、限定更新工程 | 実際の配備成功とパケット到達 |
| 合成検証 | 架空入力でのBicep展開、tuple・経路・生成・境界・競合処理の一致 | Azure側の動作、実DNS回答、サービス内部の全通信 |
| Azure限定反映・読戻し | 所有する対象の設定一致、既存設定保持、処理完了 | NSG・Firewall規則ごとの個別パケット実証と管理VMの現在の時刻精度 |
| 実操作 | 新規接続での業務・保守操作、認可、結果取得、失敗時の復旧 | 将来の全scale/基盤更新/障害復旧と、任意の企業接続 |
| 新規再構築E2E | 新しい環境の初期工程から同じ機能が成立すること | 既存環境の限定反映や合成試験だけではこの段階を完了にしない |

NSGの既定規則、サービス・タグ、Azure基盤例外、provider内部処理の留保は[NSG設計](network-security-design.md)と[非NSG通信](platform-communications.md)を参照する。不可視の内部wireへ推測したIP/portを追加して「網羅済み」にしない。

## 合成検証の結果

公開結果には、実環境から独立した合成入力を使用する。以下はネットワーク設計の基礎を検査する再実行対象である。

| 試験 | 確認する内容 |
| --- | --- |
| [test_nsg_rule_priorities.py](../../tests/test_nsg_rule_priorities.py) | NSG正本の定義の展開一致、ACAの終端Deny、複数PE IP、priority、Firewallの基盤用途分割、直接経路向けFW規則の除去、ACA Dev宛先の固定生成 |
| [test_parameter_ip_ownership.py](../../tests/test_parameter_ip_ownership.py) | 構築サブネット・IPの所有境界、5 NSGとFirewallサブネットの非関連付け |
| [test_dns_nsg.py](../../tests/test_dns_nsg.py) | 廃止DNS用規則の対象限定、未知の手動規則保持、競合・応答不明時の扱い |
| [test_grpc_fixed_infrastructure.py](../../tests/test_grpc_fixed_infrastructure.py) | gRPC専用設備の撤去、HTTPS保守保持、AppGW/API入口の構成 |
| [test_credential_broker_initial_deployment.py](../../tests/test_credential_broker_initial_deployment.py) | Broker初期build/配備、source bundle、所有資源、再試行とcleanupの境界 |
| [test_audit_storage_network.py](../../tests/test_audit_storage_network.py) | 監査Blob PEの全IP、DNS zone/group/linkとレコード照合、構築順序 |
| [test_portal_gateway.py](../../tests/test_portal_gateway.py) | APIMとPortalのHTTPSプローブと検査用ルート照合 |

旧外部NTP構成での上記8スイートの再実行結果は **102試験PASS、失敗0、エラー0、スキップ0**。当時の独立した文書照合でもNSG76規則のtupleに欠落・重複はなく、Firewallは6 RCG・11 collection・36規則（Network17、Application19）の名前集合が合成展開と一致した。全FW規則はAllow、全Application規則は`terminateTLS: false`であった。この過去記録は旧catalog v2の条件として保持し、管理VMのPHC化後のcatalog v4／75 NSG（Allow67、Deny6）／35 FW（Network14、Application21）や新しい同期確認のPASSへ転用しない。今回のAzure反映・実VM同期は未実施である。

試験はWSLのローカルBicep/Pythonを使用し、Azureログイン・CLIによる実環境操作を行わない。Azureへの人間Entra認証を要する操作はWindows側で行う。この結果は公開資材・合成入力の確認であり、実環境の疎通・認可結果は下記の保護実施票で別に管理する。

### 管理VMのPHC化後の合成検証

以下の9スイートの最終関連試験は、**112試験PASS、失敗0、エラー0、スキップ0**（201.580秒）だった。Bicepの実展開、LF／CRLFのguest payload、時刻工程の実コードをfixtureとサービスmockで実行し、設定の再適用・未知設定の拒否・同期確認・失敗時復旧・別編集と保留transactionの保全を確認した。

| 対象 | 試験 |
| --- | --- |
| PHC設定・Core前の先行証跡 | [Linux時刻設定](../../tests/test_management_linux.py)、[管理VM時刻gate](../../tests/test_management_host_time.py) |
| NSG75／FW35とAPIM UDP123保全 | [Firewall](../../tests/test_firewall_diagnostics.py)、[IP所有境界](../../tests/test_parameter_ip_ownership.py)、[NSG正本](../../tests/test_nsg_rule_priorities.py) |
| 既存NTP規則の限定撤去と削除前gate | [専用運用試験](../../tests/test_management_time_network.py) |
| guest埋込とCore配備の依存関係 | [guest payload](../../tests/test_guest_deployment_payloads.py)、[配備順序](../../tests/test_nsg_deployment_order.py) |

合成VM IDの記述を独立生成値へ調整した後、移行／限定配備の2スイート39試験も再確認し、全件成功した（11.706秒、失敗・エラー・スキップ0）。別実行の件数を112へ加算しない。当時の文書のNSG2表は正本75件の名称と優先度に一致し、FW表は35件に一致した。今回のAzure反映、実VMでのPHC同期・時刻精度、再構築E2Eは未実施である。

関連する保守機能の実装・合成検証は次のとおり。以下の一覧に載せることと、今回の文書更新で全試験を再実行することは別である。

| 機能 | 公開実装・試験根拠 |
| --- | --- |
| ACAのFirewall経路 | [Core経路の保全試験](../../tests/test_network_preservation.py)。Firewallを迂回する公開宛経路の拒否と、所定の経路での再適用を確認する。 |
| 固定NSGの構築と再開 | [配備前後の読戻し試験](../../tests/test_fixed_nsg_validation.py)、[配備順序](../../tests/test_nsg_deployment_order.py) |
| Broker常設 | [Broker配備資材](../../apps/api-gateway/deploy/main.bicep)、[maintenance upgrade](../../tests/test_maintenance_upgrade.py)の`test_broker_resources_are_always_selected_independently_of_business_profiles` |
| L7入口保護 | [AppGW資材](../../infra/modules/ingress/application-gateway.bicep)、[APIM共通policy](../../apps/api-gateway/policies/service.xml)、[trusted ingress](../../tests/test_trusted_ingress.py) |
| 本文監査の専用経路 | [追加NSG正本](../../infra/modules/network/nsg/audit-evidence-rules.json)、[本文監査のネットワーク資材](../../infra/modules/audit-evidence/audit-evidence-network.bicep)、[資材試験](../../tests/test_audit_evidence_infra.py) |
| 監査Blob閲覧 | [本人へ付与する保守権限](../operations/management-vm-access.md)、[maintenance upgrade](../../tests/test_maintenance_upgrade.py) |
| ACA/保存ログの認可 | [maintenance roles](../../infra/modules/management/management-maintenance-roles.bicep)、[RBAC試験](../../tests/test_management_maintenance_rbac.py) |
| 監査Blob閲覧 | [保守時の手動付与](../operations.md#保守の付与回収)、[保守資材の回帰試験](../../tests/test_maintenance_upgrade.py) |
| ACA/保存ログの認可 | [ACAカスタムロール定義](../../infra/modules/management/management-maintenance-roles.bicep)、[RBAC試験](../../tests/test_management_maintenance_rbac.py)、[本人クライアント試験](../../tests/test_management_client.py) |
| 保守clientと導入 | [management_client.py](../../scripts/management_client.py)、[client試験](../../tests/test_management_client.py)、[導入試験](../../tests/test_management_client_install.py) |
| SQL直接保守 | [本人への付与・回収](../operations/management-sql-access.md)、[SQL認可の資材試験](../../tests/test_sql_management_grants_infra.py)、[SQLクライアント試験](../../tests/test_management_sql_client.py) |
| Serial初期準備 | [初期module](../../infra/modules/management/management-serial-initialization.bicep)、[初期処理試験](../../tests/test_serial_initialization.py)、[guest試験](../../tests/test_management_serial_guest.py) |
| 管理VMのAzureホスト時刻 | [guest監査設定](../../infra/guest/configure-audit.sh)、[監査module](../../infra/modules/management/management-audit.bicep)、[Linux時刻試験](../../tests/test_management_linux.py)。PHC device／`clock_name`、唯一source、既知旧設定の移行、未知設定の拒否、同期確認失敗時の復旧を合成条件で検証する |
| Core前の管理VM時刻gate | [読取gate](../../scripts/deployment/helpers/management_host_time.py)、[gate試験](../../tests/test_management_host_time.py)。既存VMの所有・現行script全文一致・RunCommand成功出力、VM作成／実行開始／終了のUTC時刻、旧VMや旧結果の拒否、厳密な整数HTTP404の新規構築判定を検証する。NSG限定移行の旧管理NTP削除にも適用する。先行適用証跡を現在精度の保証にしない |

## 実環境の実証結果の管理

[AGENTS.md](../../AGENTS.md)は、実環境の構成・配備・利用の実測記録、ID、実IP、ホスト名、秘密、正確な所在を公開領域へ持ち込むことを禁止している。実証原本は、チェックアウト直下の`.local/<environment>/evidence/`で所有者だけが参照できる保護実施票として管理する。公開docsには本書の一般化した検証条件・判定方法と、独立した合成試験の結果を記載する。

保護実施票は、実証対象の通信ID、対応NSG/FWレコード、使用した資材版、期待する送受信tupleと経路、読戻し、実操作、認可、結果、制約、後始末を結び付ける。実環境固有の原本・参照先を公開docsへ転記しない。

| 保護実施票の欄 | 記録する内容 |
| --- | --- |
| 対象・版 | 構築入力とstate、資材commit、対象資源の所有と世代、操作対象 |
| 要件対応 | 本書群のFLOW ID、NSGのdesignRecordId/規則名、FWのRCG/collection/規則名 |
| 反映 | 実施した限定変更、変更前の保存、処理完了、変更後の全対象tuple読戻し |
| 経路・DNS | 実効経路、往復経路、名前解決とPE全IP、FW通過/直接/基盤例外の区別 |
| 実操作・認可 | 使用するMIとscope、client/API版、接続開始から有限の結果取得まで |
| 結果と制約 | 成功/失敗/初期非採用/未実施、観測できた範囲、観測できない内部状態 |
| 終了 | 一時command、token、検証用秘密、生成入力、変更した公開状態の後始末と確認 |

完了判定は最新の対象実施票を使う。過去の失敗原本や中断receiptを成功へ書き換えず、後続の独立確認で何が解決したかを記録する。応答不明のSQL更新、Job起動、パスワード初期化、秘密削除は、未実行と推測して再送しない。

## 実環境の受入確認

以下は受入条件であり、本文監査を含む現行構成のAzure配備、設定読戻し、実通信の受入は未実施である。本文監査の追加規則は標準のテスト・本番設定で有効になる。

| 確認 | 通信・設定との対応 | 合格条件と限界 |
| --- | --- | --- |
| NSG全件読戻し | 基本構成と証明書用ACAの規則、本文監査有効時の追加規則 | 名前・方向・priority・protocol・両port・全address集合・Allow/Denyが正本に一致する。各ACAの専用サブネット・NSG関連付け、両方向の終端Deny、Azure既定規則、PEのNSGポリシー、管理VM／APIMの時刻通信を確認する。 |
| FW全件読戻し | 基本は6 RCG・10 collection・35規則。本文監査有効時は7 RCG・12 collection・37規則 | priority、action、種別、全source/destination、protocol/port、TLS inspectionが資材と一致する。`rcg-platform`の12規則、本文監査用`rcg-audit-evidence`の2規則、APIMのNTP許可と管理VMの旧NTP規則の撤去を確認する。 |
| 公開入口 | CLIENT、APPGW-APIM、APPGW-ACA | 公開TLS、HTTPリダイレクト、APIM正常API、Portalのhealthと画面、L7入口保護を確認。両バックエンドのHTTPSプローブとFirewallのTLS検査・非対象経路の拒否も確認。healthだけを全APIの認可確認にしない |
| PortalとGateway | ACA-APIM、ACA-SQL | Portalの試用は公開Gatewayを経由する。Portal用実行IDのAPIM管理API呼出しが拒否されることも確認 |
| Broker分離 | APIM-BROKER、ACA-KV、ACA-SQL、ACA-ACR、ACA-ARM、ACA-ENTRA | APIMからBrokerへの許可、Portal用ACA・AppGW・管理VMからの拒否、Brokerの起動・SQL・CM・OBO・監査、PortalとBrokerが別々のACA環境を使うことを確認する。 |
| 本文監査の分離 | APIMから専用中継、専用ACAからEvent Hubs・本文Blob・チェックポイントBlob | 中継へのAPIM以外からの接続と未許可IDを拒否し、保存ワーカーに入口がないことを確認する。Private DNSと実効NSG、各実行IDの権限、JSON／SSEの受領確認、Event Hubs障害時の応答、Blobへの保存を合成データで確認する。 [本文監査の受入条件](request-body-audit.md) |
| PE接続 | APIM/ACA/管理VMとFoundry/KV/SQL/ACR/監査Blob、本文監査用の3 PE | DNS回答とPE全IP、往復制御、サービスTLSと認可を照合する。本文監査が有効な場合はEvent Hubs・本文Blob・チェックポイントBlobの三つを個別に確認する。 |
| Vault分離と証明書 | APPGW-KV、APIM-KV、ACA-KV、MGMT-KV | 2つのPrivate EndpointとDNS回答、Vaultのネットワーク設定、対象オブジェクトの実効RBACを照合。公開証明書の初回発行・自動更新・実配信、APIMのNamed Value解決、必要のない接続とsecret読取の拒否を確認 |
| ACAの現構成依存 | イメージ取得、App/Job起動、標準DNS、管理ID、監視、リビジョン切替、再起動、スケール | 構築ACRと公開MCRの用途を区別する。Portal、Broker、本文監査それぞれのPE接続と起動・更新・復旧を確認する。 |
| 監査Blob・保存ログ | MGMT-AUDIT-JAPAN/FOUNDRY、MGMT-MONITOR | 担当者本人の作業権限で構築コンテナーとWorkspaceを読み、付与前・回収後は拒否される。監査Blobへの書込み・削除権限は付けない |
| ACA App/環境のログ・App exec | MGMT-ARM、MGMT-ACA-DEV | 対象App/revision/replica/containerとtoken scopeを確認し、ログ本文/有限execの結果を取得。環境tokenをログ専用の保証として扱わない |
| ACA Jobログ | ACA-SQL等のJob処理とMGMT-ACA-DEV | 対象Jobのtoken認可、保存ログ、実行が存続する場合のライブログを区別。schema Jobは配備更新工程の正常実行に合わせて確認 |
| SQL直接保守 | MGMT-SQL | 対象DBだけで一時付与した本人のユーザーと最小権限で接続し、ODBC 18のTLSと証明書検証を確認する。別DBと回収後の新規接続は拒否される |
| 本人のVM接続 | 管理ネットワーク、VMのEntra SSH、一時RBACとLinuxグループ | 付与前・回収後の新規SSHを拒否し、付与期間中だけ本人がsudoなしで接続し資材を使用できる。既存セッション終了・ユーザー鍵の扱い・証明書用IDの分離を確認する |
| VM基盤・監査 | 非NSGのIMDS、DHCP、Agent、AMA、Disk、Boot | 実MI/版/実効設定/転送と保存を確認。managed内部保存を顧客NIC通信へ推測対応付けしない |
| Serial Console | 操作端末/Compute制御とguest serial channel | 認可、managed Boot Diagnostics、guest初期認証、実ログイン、SSH鍵維持を確認。VM NSGのSSH受信許可を追加しない |
| APIM時刻 | APIM-NTP | UDP123のAllow/拒否、診断エラー、APIM自身の現在時刻応答を組み合わせる。内部daemon/NTP応答packetは公開APIで不可視の場合があり、観測範囲を明記 |
| 管理VM時刻 | MGMT-HOST-TIME、Azure VMBus／PHCとChrony | `/dev/ptp_hyperv`のcharacter deviceと`clock_name=hyperv`、唯一sourceのPHC／`AZUR`選択、`Normal`同期、現在offset／精度を確認。時間を限定したwaitsyncと先行RunCommandの成功証跡だけで、現在精度や保守復帰後の同期まで自動保証しない。非IP連携なのでFW UDP123ログを同期証拠にしない |
| 管理VM時刻の既存移行 | 現行guest moduleの単独先行適用 → 旧管理NSG325の限定削除／所有FW collection更新 → 全対象読戻し | Core前にVM所有、現行script全文一致、RunCommandのprovisioning／executionがSucceeded、exitCodeが整数0、出力がconfigured／timeSource azure-host-phc／timeSynchronized trueと確認できること。旧結果・未適用・失敗はCore変更前に停止。未知の企業時刻設定・規則を回収しない |
| 限定反映の終了 | 全変更対象 | 未所有の手動設定を保持し、公開範囲を復元、一時command/検証用秘密の削除・active不存在を確認。Key Vaultのsoft-deleteとpurgeを区別 |

各FWレコードの設定一致とFW全体の実操作成功を、全Application Ruleのhit済みへ読み替えない。Network Ruleが先に一致する重複、CA互換先、未使用の基盤機能の許可先には、個別hitを完了条件にしない場合がある。実効許可範囲とその理由は[Firewall設計](firewall-policy-design.md)に残す。

## 初期不採用と検証の限界

| 判断 | 初期範囲での扱い | 理由・後続条件 |
| --- | --- | --- |
| Job対話exec | 初期非採用 | JobのライフサイクルとAPI/clientの制約をApp execと区別。ログ取得とJobの通常処理は維持 |
| Serial NMI/reset/SysRq | 初期非採用 | 対話ログインと診断取得を保守範囲にし、破壊的な障害注入を実証のために追加しない |
| ACAのStorage通信 | 利用中のプライベート接続を許可 | 追加のStorage依存は宛先・用途を整理し、末尾拒否より前の明示許可と実検証を伴う |
| カスタムDNS用の作成switch | 廃止 | 初期は標準DNS固定。企業DNS移行は問い合わせ元、DNS IP、53/TCP/UDP、zone/link、FW自身のDNSと往復経路を手動で整合 |
| gRPC専用設備 | 要件廃止 | HTTPS保守と通常HTTP/HTTPSの入口保護を残す。8443や専用転送を再作成しない |
| fresh再構築E2E | 限定パッチ・合成検証とは別 | 初期工程への組込みと、実際に新しい全環境を構築して全機能を確認することを分ける |

採用した保守判断の集計と、[OPS-01〜14](../testing/operations-tests.md)の業務運用シナリオの集計は別である。上位の網羅確認と個々のclient操作を二重計上せず、SQL直接保守など追加採用した機能もそれぞれの実施票で追跡する。

## 新規構築資材の防御レビュー

ローカルの構成、認証、権限、ログの流れを確認した。実環境では新規配備後に、テスト専用の本人・購読・入力で以下を受け入れる。

| 優先度・到達点 | ソースで確認した境界と残る影響 | 対応と新規構築後の確認 |
| --- | --- | --- |
| 高・管理VMから共用資源 | 旧資材は共有KV Officer、DBの`db_owner`、ACAのexecと環境Dev API token取得をVMのIDに付けていた | 新規資材は対話保守の常設割当を廃止し、本人へ作業単位で付与・回収する。無人の証明書処理は名前を固定した対象に絞る。初回発行、自動更新、他の秘密へのアクセス拒否、保守権限の回収後の拒否を確認する。 [管理VMの一時権限](../operations/management-vm-access.md) |
| 高・公開入口の迂回 | AppGWが再暗号化し、Firewall経由のHTTPSがAPIM／ACAの許可元になる。APIMにもAppGWの信頼済み入口確認がある。別経路からの到達や信頼情報の漏えいで境界が弱くなる | 有効なNSG・ルート・SNIを読戻し、外部入口、バックエンドの直接接続、許可されていないサブネットからの到達を確認する。AppGWとAPIMの監査を要求IDで照合する |
| 高・TLS検査とログ | 検査したURLはFirewallのApplication Rule／Threat Intelログの`TargetUrl`に含まれる可能性があり、OAuthの戻り先には一回限りの値が含まれ得る | URLを記録するカテゴリには取り込み時の削除を適用し、加工前のStorage直送を分ける。合成マーカーのある要求でWorkspace・Storageの双方を確認する。 [Application Ruleログの項目](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/azfwapplicationrule) · [Threat Intelログの項目](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/azfwthreatintel) |
| 高・Portalと接続所有者 | Portalのプロセスは、同じアプリに割り当てた認証SQL用IDとトークン暗号鍵用IDを使う。侵害時は両方に影響し得る。Entraは同じPortalクライアントで同意済みの別用途にも更新トークンを交換できる | 固定鍵版、期限、認証世代、SQLセッションを照合する。別本人、失効、更新競合、複数レプリカでの停止、インシデント時のセッション失効と鍵更新を受け入れる。 [PortalのID割当](../../apps/portal/deploy/application.bicep) · [Entra追加認証と残る影響](../setup/09-egakumi-portal.md#一度の登録依頼に含める設定) |
| 高・公開Portalへの権限集中 | Portalから購読管理を撤去し、APIM購読は基盤管理者が管理する。Portalの実行IDは認証・試用のSQLとKey Vaultを使用する | [PortalとAPIMの管理境界](#portalとapimの管理境界)を確認する |
| 高・共有Key Vaultの読取範囲 | 公開TLSのPFXとACME状態を専用Vaultに分離した。新規資材はAppGWに対象PFXの読取りを付与する。APIMは指定のNamed Valueを参照し、読取のRBACは業務用Vault全体に及ぶ。業務用Vaultは複数処理で共有し、APIM管理プレーンのための例外を持つ | 必要な参照に加え、AppGWによる他のsecretの読取と、APIMや業務用ACAのIDによるTLS Vaultへの未許可操作が拒否されることを確認する。既存環境の広域割当、旧timer、旧証明書とACME状態は実際の依存を確認してから個別に取り扱う。 [Vaultの設定](../../infra/modules/services/services.bicep) · [RBACの設定](../../infra/modules/services/service-bindings.bicep) |
| 高・PortalからBrokerへの横移動 | Brokerを専用の内部ACA環境とサブネットへ分離した。アプリの業務接続をAPIMに限定し、Broker側でも共有鍵・本人・権限を照合する。同じVNetのPEとAzure管理・監視サービスは用途別に利用する | Portal用ACAからBrokerへの直接到達を拒否し、公開Gateway経由の正規処理が成立することを確認する。APIM由来の異常な要求と、Broker実行IDによる対象外の秘密・DB・ARM操作も拒否されることを確認する。 [Broker環境の分離](#credential-broker環境の分離) |
| 中・接続先、長時間通信と障害 | 検査用中間CAとDNSが経路の信頼点になる。CA失効、証明書名の違い、長い接続の切断はGateway全体へ波及し得る | CAアクセスを専用IDと対象secretへ絞り、切替と期限を運用で確認する。正しい経路と証明書不一致、JSON、SSE、WebSocket、MCPを試験し、タイムアウトとIDPSを監査で識別する |

管理VMの権限見直しは資材上で行い、Azure受入で付与と回収を確認する。上記の実接続とログ検証を、資材の構文・生成・合成入力の結果と区別して記録する。

### 初回公開前に確認する実効境界

| 対象 | 資材上の境界 | Azureで確認する内容 |
| --- | --- | --- |
| Portal用環境の内部通信 | PortalはApp GatewayからHTTPSで受信し、試用要求を公開Gatewayへ送る | APIMからPortalのACA ingressへの直接経路が拒否されることを確認する |

実効権限、到達性、遮断結果は新規構築後の保護実施票に記録する。依存パッケージの公表済み指摘は[Portalの依存監査](../testing/portal-authentication-tests.md#本番依存の再監査)を参照する。

### PortalとAPIMの管理境界

Portalの実行IDがログイン・試用に必要なSQLとKey Vaultを利用でき、APIM購読の作成、キー取得・再生成、停止、ポリシーの更新が拒否されることを確認する。APIMからPortal内部のACAに直接接続できず、PortalからGatewayへは公開HTTPS経路で到達することを確認する。例外発行のキーはAPIMで停止し、停止後の要求が拒否されることを確認する。[Portalの配備](../setup/12-deploy-portal.md) · [購読キーの運用](../operations/access-and-keys.md)

### Credential Broker環境の分離

| 確認対象 | 新規環境で受け入れる契約 |
| --- | --- |
| 初回構築とPrivate DNS | Portal用の`cae-<stem>`とBroker用の`cae-<stem>-broker`が別のサブネット・内部VIP・DNS名に対応し、公開ネットワークが無効。Broker本体、SQL初期化Job、定期照合Jobは専用環境に所属する |
| 正規経路と拒否 | APIMから専用Broker FQDNへHTTPS443で到達し、TLS証明書名を照合する。到達しても無効な共有鍵・本人・権限で処理されない。Portal用ACA、AppGW、管理VMおよび外部からの直接アクセスを拒否し、Portalの利用者は公開Gateway経由で本人確認・Graph・CMを利用できる |
| Brokerの基盤依存 | ACR login/data PEからのイメージ取得、業務用Key Vault PE、SQL PE、Entra/JWKS、ARMおよびACAの管理通信を使い、起動・スケール・定期照合を確認する。Firewallログ、NSGの設定、実行ログから、到達性と認可失敗を切り分ける |
| API・資格情報 | 通常のGateway APIとプロファイル照会、購読キーのCM接続・継続利用、Credentialの公開コールバック、本人BearerのGraph OBOを試す。異なる本人、期限切れ、依存先障害、誤ったscopeで停止する。トークンやsecretを監査に残さない |
| 更新・保守・撤去 | Gateway配備とBroker単体更新で、環境IDとFQDN、App／Job、期待imageと正常revisionを照合する。管理VMのinventoryは両環境を含み、ログ・execの権限を当該資源へ限定できる。撤去時は所有する子資源、両環境・サブネット・各Azure管理RGの消失を順に確認する |

既存環境のBroker移設と資源の回収は別の作業である。今回のAzure配備・受入は未実施である。 [Gatewayの正式手順](../setup/11-deploy-gateway.md) · [保守範囲](../operations/management-vm-access.md)

### 公開TLS用Key Vaultの分離

| 確認対象 | 新規環境で受け入れる契約 |
| --- | --- |
| VaultとPE | 公開TLS用と業務用が別IDと別PEに対応し、各FQDNがそれぞれのPEに解決される。公開TLS用の資材は`publicNetworkAccess: Disabled`、`networkAcls.defaultAction: Deny`、`networkAcls.bypass: AzureServices`を指定する。業務用はAPIM管理プレーンのNamed Value用に例外を持つ。Azureでは両Vaultの設定を読み戻し、AppGWの公開TLS証明書参照、公開TLS JobのPE経由の到達、APIMと業務用ACAの業務用Vaultへの到達、対象外の接続・操作の拒否を個別に確認する |
| 証明書の初回発行と更新 | 対象PFX・ACME状態の専用ACA Jobによる操作と、対象PFXのAppGWによる参照を確認する。業務用VaultのPortal認証証明書や通常のsecretと混同しない。公開2ホストの実TLSを証明書と照合する |
| APIMの実効権限 | 標準Gateway証明書を使用。子リソースごとに入口、Broker、購読照会と、使用時の所有者チケットのNamed Valueが設定どおり取得できることを確認する。APIMの読取割当が業務用Vaultを対象とし、公開TLS用Vaultに及ばないことを確認する |
| TLS Vaultの監査 | 公開TLS用Vaultに配備する`core-audit`の`AuditEvent`と送信先を読み戻す。対象オブジェクトに対する正規の読取・証明書更新と、未許可操作の結果を保護証跡で確認する |
| 既存環境からの切替 | 新しい参照・配信・定期Jobの受入後、旧Vaultの証明書・ACME状態と旧RBACの利用先を確認する。既存の資源回収は新規構築の検証に含めない |

[公開証明書の運用](../operations/secrets-and-certificates.md#tls証明書を更新し公開httpsまで確認する) · [APIM管理プレーンの参照仕様](https://learn.microsoft.com/en-us/azure/api-management/breaking-changes/trusted-service-connectivity-retirement-march-2026#scenarios-that-are-not-affected-by-the-breaking-change)

### 基盤用Entraアプリの分離で追加する確認

| 対象 | 資材上の制御と実環境での確認 |
| --- | --- |
| OBOの信頼境界 | BrokerはAPIM内部の共有鍵と検証済み本人を照合する。Entraが元のトークンを検証し、Graphの対象は固定の本人取得とする。同意不足・別人・期限切れ・条件付きアクセス時の停止を確認する |
| 署名と秘密の権限 | Brokerの実行IDによるOBO署名を専用鍵に、秘密の読み取りをBrokerとCredentialの専用secretに限定する。Azureで、有効な版と実証明書の組、対象外の鍵・secretの拒否を確認する。旧構成から更新する場合は過去の割当も棚卸する |
| トークンの監査 | APIMの通常診断と権限付きデバッグ、Firewall、Brokerのログを確認する。元のBearer・署名済みclient assertion・Graphトークン・追加認証claimsが通常監査に保存されず、デバッグの利用者と取得範囲が限定されることを確認する |
| 管理VM | 保守時だけ担当者本人に権限を付与し、終了時に回収する。PIMを利用する場合は、Azure基盤側のライセンスと会社の承認体制を確認する |

### 手動追加したAPIの公開前確認

| 対象 | 確認する内容 |
| --- | --- |
| 公開経路 | 新しいパスで有効なWAFの関連付けとモード、AppGWからAPIMへのHTTPS・信頼済み入口の書換え、許可した操作の到達性を照合する |
| Productと認証 | 許可したProductの購読キーと、公開対象なら本人Bearerで接続する。対象外Product、無効キー、期限切れ・scope不足のBearerを拒否し、監査に認証情報が残らないことを確認する |
| 下流の到達範囲 | 許可したOperationと固定バックエンドの組、APIMのIDを割り当てた対象資源と推論権限のscopeを確認する。応答の監査、SSEの転送、要求回数の上限も合わせて確認する |

具体的な設定は[Claude Messagesの手動追加手順](../operations.md#構築後にclaudeをguiで追加する)を参照する。検証結果は環境別の保護実施票で管理する。
