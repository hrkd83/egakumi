# ネットワーク設計の検証と実証結果の管理

[通信要件](network-communication-requirements.md) · [NSG設計](network-security-design.md) · [Firewall設計](firewall-policy-design.md) · [運用テスト](../testing/operations-tests.md)

本書は、通信要件、構築資材、設定の読戻し、実操作の関係と、結果の管理方法を定める。公開する合成試験と環境別の実証原本を分け、設定一致だけで通信全件の実証済みとは判定しない。

## 確認段階

| 段階 | 判定する内容 | この段階だけでは証明しない内容 |
| --- | --- | --- |
| 設計判断 | 通信要件、NSG/FWへの対応、採否、例外、変更入力の範囲 | 資材への実装と実環境動作 |
| 資材実装 | 固定集合、構築IPのbinding、初期構築工程、限定更新工程 | 実際の配備成功とパケット到達 |
| 合成検証 | 架空入力でのBicep展開、tuple・経路・生成・境界・競合処理の一致 | Azure側の動作、実DNS回答、サービス内部の全通信 |
| Azure限定反映・読戻し | 所有する対象の設定一致、既存設定保持、処理完了 | 全75 NSG規則・全35 FW規則の個別パケット実証と管理VMの現在の時刻精度 |
| 実操作 | 新規接続での業務・保守操作、認可、結果取得、失敗時の復旧 | 将来の全scale/基盤更新/障害復旧と、任意の企業接続 |
| 新規再構築E2E | 新しい環境の初期工程から同じ機能が成立すること | 既存環境の限定反映や合成試験だけではこの段階を完了にしない |

NSGの既定規則、サービス・タグ、Azure基盤例外、provider内部処理の留保は[NSG設計](network-security-design.md)と[非NSG通信](platform-communications.md)を参照する。不可視の内部wireへ推測したIP/portを追加して「網羅済み」にしない。

## 合成検証の結果

公開結果には、実環境から独立した合成入力を使用する。以下はネットワーク設計の基礎を検査する再実行対象である。

| 試験 | 確認する内容 |
| --- | --- |
| [test_nsg_rule_priorities.py](../../tests/test_nsg_rule_priorities.py) | catalog v3の75 NSG定義の展開一致、複数PE IP、priority、Firewallの基盤用途分割、直接経路向けFW規則の除去、ACA Dev宛先の固定生成 |
| [test_parameter_ip_ownership.py](../../tests/test_parameter_ip_ownership.py) | 構築サブネット・IPの所有境界、5 NSGとFirewallサブネットの非関連付け |
| [test_dns_nsg.py](../../tests/test_dns_nsg.py) | 廃止DNS用規則の対象限定、未知の手動規則保持、競合・応答不明時の扱い |
| [test_grpc_fixed_infrastructure.py](../../tests/test_grpc_fixed_infrastructure.py) | gRPC専用設備の撤去、HTTPS保守保持、AppGW/API入口の構成 |
| [test_grpc_fixed_configuration.py](../../tests/test_grpc_fixed_configuration.py) | 旧gRPC設定の拒否と生成設定の境界 |
| [test_credential_broker_initial_deployment.py](../../tests/test_credential_broker_initial_deployment.py) | Broker初期build/配備、source bundle、所有資源、再試行とcleanupの境界 |
| [test_audit_storage_network.py](../../tests/test_audit_storage_network.py) | 監査Blob PEの全IP、DNS zone/group/linkとレコード照合、構築順序 |
| [test_portal_backend_http.py](../../tests/test_portal_backend_http.py) | Portal向けHTTP80切替、限定変更、競合検出、切戻し |

旧外部NTP構成での上記8スイートの再実行結果は **102試験PASS、失敗0、エラー0、スキップ0**。当時の独立した文書照合でもNSG76規則のtupleに欠落・重複はなく、Firewallは6 RCG・11 collection・36規則（Network17、Application19）の名前集合が合成展開と一致した。全FW規則はAllow、全Application規則は`terminateTLS: false`であった。この過去記録は旧catalog v2の条件として保持し、管理VMのPHC化後のcatalog v3／75 NSG（Allow69、Deny6）／35 FW（Network16、Application19）や新しい同期確認のPASSへ転用しない。今回のAzure反映・実VM同期は未実施である。

試験はWSLのローカルBicep/Pythonを使用し、Azureログイン・CLIによる実環境操作を行わない。Azureへの人間Entra認証を要する操作はWindows側で行う。この結果は公開資材・合成入力の確認であり、実環境の疎通・認可結果は下記の保護実施票で別に管理する。

### 管理VMのPHC化後の合成検証

以下の9スイートの最終関連試験は、**112試験PASS、失敗0、エラー0、スキップ0**（201.580秒）だった。Bicepの実展開、LF／CRLFのguest payload、時刻工程の実コードをfixtureとサービスmockで実行し、設定の再適用・未知設定の拒否・同期確認・失敗時復旧・別編集と保留transactionの保全を確認した。

| 対象 | 試験 |
| --- | --- |
| PHC設定・Core前の先行証跡 | [Linux時刻設定](../../tests/test_management_linux.py)、[管理VM時刻gate](../../tests/test_management_host_time.py) |
| NSG75／FW35とAPIM UDP123保全 | [Firewall](../../tests/test_firewall_diagnostics.py)、[IP所有境界](../../tests/test_parameter_ip_ownership.py)、[NSG正本](../../tests/test_nsg_rule_priorities.py) |
| 既存NTP規則の限定撤去と削除前gate | [固定NSG移行](../../tests/test_fixed_nsg_migration.py)、[NSG限定配備](../../tests/test_fixed_nsg_upgrade.py) |
| guest埋込とCore配備の依存関係 | [guest payload](../../tests/test_guest_deployment_payloads.py)、[配備順序](../../tests/test_nsg_deployment_order.py) |

合成VM IDの記述を独立生成値へ調整した後、移行／限定配備の2スイート39試験も再確認し、全件成功した（11.706秒、失敗・エラー・スキップ0）。別実行の件数を112へ加算しない。文書のNSG2表は正本75件の名称と優先度に一致し、FW表は35件に一致した。今回のAzure反映、実VMでのPHC同期・時刻精度、再構築E2Eは未実施である。

関連する保守機能の実装・合成検証は次のとおり。以下の一覧に載せることと、今回の文書更新で全試験を再実行することは別である。

| 機能 | 公開実装・試験根拠 |
| --- | --- |
| 固定NSGの限定移行 | [nsg-fixed-upgrade.bicep](../../infra/modules/nsg-fixed-upgrade.bicep)、[fixed migration](../../tests/test_fixed_nsg_migration.py)、[upgrade](../../tests/test_fixed_nsg_upgrade.py)、[配備順序](../../tests/test_nsg_deployment_order.py) |
| Broker常設 | [Broker配備資材](../../apps/apim/deploy/main.bicep)、[maintenance upgrade](../../tests/test_maintenance_upgrade.py)の`test_broker_resources_are_always_selected_independently_of_business_profiles` |
| L7入口保護 | [AppGW資材](../../infra/modules/application-gateway.bicep)、[APIM共通policy](../../apps/apim/policies/service.xml)、[trusted ingress](../../tests/test_trusted_ingress.py) |
| 監査Blob閲覧 | [audit-reader-bindings](../../infra/modules/audit-reader-bindings.bicep)、[maintenance upgrade](../../tests/test_maintenance_upgrade.py) |
| ACA/保存ログの認可 | [maintenance roles](../../infra/modules/management-maintenance-roles.bicep)、[ACA bindings](../../infra/modules/management-aca-bindings.bicep)、[Workspace bindings](../../infra/modules/management-monitoring-bindings.bicep)、[RBAC試験](../../tests/test_management_maintenance_rbac.py) |
| 保守clientと導入 | [management_client.py](../../scripts/management_client.py)、[client試験](../../tests/test_management_client.py)、[導入試験](../../tests/test_management_client_install.py) |
| SQL直接保守 | [SQL認可の資材試験](../../tests/test_sql_management_grants_infra.py)、[SQL client試験](../../tests/test_management_sql_client.py)、[構築DB discovery試験](../../tests/test_management_sql_discovery.py) |
| Serial初期準備 | [初期module](../../infra/modules/management-serial-initialization.bicep)、[初期処理試験](../../tests/test_serial_initialization.py)、[guest試験](../../tests/test_management_serial_guest.py) |
| 管理VMのAzureホスト時刻 | [guest監査設定](../../infra/guest/configure-audit.sh)、[監査module](../../infra/modules/management-audit.bicep)、[Linux時刻試験](../../tests/test_management_linux.py)。PHC device／`clock_name`、唯一source、既知旧設定の移行、未知設定の拒否、同期確認失敗時の復旧を合成条件で検証する |
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

| 確認 | 通信・設定との対応 | 合格条件と限界 |
| --- | --- | --- |
| NSG全件読戻し | catalog v3の5 NSG・75カスタム規則と既定規則、PE policy | 名前・方向・priority・protocol・両port・全address集合・Allow/Denyが一致。管理VM325の旧UDP123規則が残らずAPIM328が維持される。既定規則を再作成せず、例外区画の既定許可も確認 |
| FW全件読戻し | 6 RCG、11 collection、35規則。`rcg-platform`は12規則 | priority、action、種別、全source/destination、protocol/port、TLS inspectionが資材と一致。管理VM NTP規則が残らずAPIM NTPが維持され、直接経路用の不要規則が混ざらない |
| 公開入口 | CLIENT、APPGW-APIM、APPGW-ACA | 公開TLS、HTTPリダイレクト、APIM正常API、Portalのhealthと画面、L7入口保護を確認。healthだけを全APIの認可確認にしない |
| PE接続 | APIM/ACA/管理VMとFoundry/KV/SQL/ACR/監査Blob | DNS回答とPE全IP、往復制御、サービスTLSと認可を照合。FWログが無いことだけで直接経路成功としない |
| ACAの現構成依存 | イメージ取得、App/Job起動、標準DNS、管理ID、監視 | 構築ACRと公開MCRの用途を区別。現在アプリのStorage非依存を将来の全アプリへ一般化しない |
| 監査Blob・保存ログ | MGMT-AUDIT-JAPAN/FOUNDRY、MGMT-MONITOR | VMのMIで構築コンテナー/Workspaceを読み、取得が完了。監査Blobへの書込み・削除権限は付けない |
| ACA App/環境のログ・App exec | MGMT-ARM、MGMT-ACA-DEV | 対象App/revision/replica/containerとtoken scopeを確認し、ログ本文/有限execの結果を取得。環境tokenをログ専用の保証として扱わない |
| ACA Jobログ | ACA-SQL等のJob処理とMGMT-ACA-DEV | 対象Jobのtoken認可、保存ログ、実行が存続する場合のライブログを区別。schema Jobは配備更新工程の正常実行に合わせて確認 |
| SQL直接保守 | MGMT-SQL | 構築2DBだけでVMのMI・DBユーザー・権限・ODBC18・TLS/証明書検証が成立。DDL/更新の再送可否を結果保存と分ける |
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
| ACA任意Internet/Storage Allowと両方向末尾Deny | 初期不作成 | managed基盤と現在アプリの依存を優先。将来のStorage利用や出口制限は新しい要件・FW経路・実検証を伴う |
| カスタムDNS用の作成switch | 廃止 | 初期は標準DNS固定。企業DNS移行は問い合わせ元、DNS IP、53/TCP/UDP、zone/link、FW自身のDNSと往復経路を手動で整合 |
| gRPC専用設備 | 要件廃止 | HTTPS保守と通常HTTP/HTTPSの入口保護を残す。8443や専用転送を再作成しない |
| fresh再構築E2E | 限定パッチ・合成検証とは別 | 初期工程への組込みと、実際に新しい全環境を構築して全機能を確認することを分ける |

採用した保守判断の集計と、[OPS-01〜14](../testing/operations-tests.md)の業務運用シナリオの集計は別である。上位の網羅確認と個々のclient操作を二重計上せず、SQL直接保守など追加採用した機能もそれぞれの実施票で追跡する。
