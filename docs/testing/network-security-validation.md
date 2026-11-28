# ネットワーク設計の検証と実証結果の管理

[通信要件](../reference/architecture/network-communication-requirements.md) · [NSG設計](../reference/architecture/network-security-design.md) · [Firewall設計](../reference/architecture/firewall-policy-design.md) · [運用テスト](operations-tests.md)

本書は、通信要件、構築資材、設定の読戻し、実操作の関係と、結果の管理方法を定める。公開する合成試験と環境別の実証原本を分け、設定一致だけで通信全件の実証済みとは判定しない。

## 確認段階

| 段階 | 確認する内容 |
| --- | --- |
| 設計と資材 | 通信要件、Bicepの所有範囲、固定規則、構築アドレスの差し込み、配備順序 |
| ローカルの合成試験 | 架空の入力を使ったBicep展開、生成する規則・経路、失敗と再開の境界 |
| Azureの設定確認 | 配備完了、NSG・Firewallの全規則、実効経路とDNS、IDと権限 |
| 新規構築の受入 | 初回手順に沿った配備、アプリの業務操作と保守操作、認可、未許可経路の拒否、失敗時の復旧 |

Azureの設定確認では実行結果と資源の設定を読み戻す。通信の受入では、期待する新規接続を実際に開始して、結果と拒否を確かめる。Azureの基盤内処理は、公開仕様と取得できる観測項目を基準にする。 [NSG設計](../reference/architecture/network-security-design.md) · [基盤通信](../reference/architecture/platform-communications.md)

## ローカルの合成試験

| 対象 | 主な試験 |
| --- | --- |
| 固定規則とアドレス | [NSGの優先度とtuple](../../tests/network/test_nsg_rule_priorities.py) · [IPの所有境界](../../tests/network/test_parameter_ip_ownership.py) · [配備前後の読戻し](../../tests/network/test_fixed_nsg_validation.py) |
| HTTPSと実効経路 | [HTTPS入口](../../tests/network/test_gateway_https_infrastructure.py) · [入口の検証](../../tests/network/test_trusted_ingress.py) · [Firewall経路](../../tests/network/test_network_preservation.py) · [PortalのHTTPSプローブ](../../tests/portal/test_portal_gateway.py) |
| 監査と証明書 | [監査Blobのネットワーク](../../tests/audit/test_audit_storage_network.py) · [Firewall診断](../../tests/audit/test_firewall_diagnostics.py) · [証明書Job](../../tests/certificates/test_certificate_jobs.py) |
| Broker初回配備 | [イメージ・配備・再試行](../../tests/deployment/test_credential_broker_initial_deployment.py) |
| 管理VMからの保守 | [保守RBAC](../../tests/management/test_management_maintenance_rbac.py) · [SQLの付与と回収](../../tests/management/test_sql_management_grants_infra.py) · [ホストPHC同期](../../tests/management/test_management_linux.py) · [Serial初期準備](../../tests/deployment/test_serial_initialization.py) |

## 実環境の実証結果の管理

[AGENTS.md](../../AGENTS.md)は、実環境の構成・配備・利用の実測記録、ID、実IP、ホスト名、秘密、正確な所在を公開領域へ持ち込むことを禁止している。実証原本は、チェックアウト直下の`.local/<environment>/evidence/`で所有者だけが参照できる保護実施票として管理する。公開docsには本書の一般化した検証条件・判定方法と、独立した合成試験の結果を記載する。

保護実施票は、実証対象の通信ID、対応NSG/FWレコード、使用した資材版、期待する送受信tupleと経路、読戻し、実操作、認可、結果、制約、後始末を結び付ける。実環境固有の原本・参照先を公開docsへ転記しない。

| 保護実施票の欄 | 記録する内容 |
| --- | --- |
| 対象・版 | 構築入力とstate、資材commit、対象資源の所有と世代、操作対象 |
| 要件対応 | 本書群のFLOW ID、NSGのdesignRecordId/規則名、FWのRCG/collection/規則名 |
| 反映 | 配備した資材、処理完了、対象規則と関連付けの読戻し |
| 経路・DNS | 実効経路、往復経路、名前解決とPE全IP、FW通過/直接/基盤例外の区別 |
| 実操作・認可 | 使用するMIとscope、client/API版、接続開始から有限の結果取得まで |
| 結果と制約 | 成功/失敗/初期非採用/未実施、観測できた範囲、観測できない内部状態 |
| 終了 | 一時command、token、検証用秘密、生成入力、変更した公開状態の後始末と確認 |

完了判定は最新の対象実施票を使う。過去の失敗原本や中断receiptを成功へ書き換えず、後続の独立確認で何が解決したかを記録する。応答不明のSQL更新、Job起動、パスワード初期化、秘密削除は、未実行と推測して再送しない。

## 実環境の受入確認

標準のテスト・本番構成を確認する。配備した機能の設定と通信を、以下の観点で確認する。

| 確認 | 通信・設定との対応 | 合格条件と限界 |
| --- | --- | --- |
| NSG全件読戻し | 基本構成と証明書用ACAの規則 | 名前・方向・priority・protocol・両port・全address集合・Allow/Denyが正本に一致する。各ACAの専用サブネット・NSG関連付け、両方向の終端Deny、Azure既定規則、PEのNSGポリシー、管理VM／APIMの時刻通信を確認する。 |
| FW全件読戻し | 初期構成、証明書JobのRCGとcollection | 優先度、action、種別、送信元・宛先、プロトコル・ポート、TLS検査対象が資材と一致する。APIMのUDP123許可とPremiumのIDPSモードも照合する。 |
| 公開入口 | CLIENT、APPGW-APIM、APPGW-ACA | 公開TLS、HTTPリダイレクト、APIM正常API、Portalのhealthと画面、L7入口保護を確認。両バックエンドのHTTPSプローブとFirewallのTLS検査・非対象経路の拒否も確認。healthだけを全APIの認可確認にしない |
| PortalとGateway | ACA-APIM、ACA-SQL | Portalの試用は公開Gatewayを経由する。Portal用実行IDのAPIM管理API呼出しが拒否されることも確認 |
| Broker分離 | APIM-BROKER、ACA-KV、ACA-SQL、ACA-ACR、ACA-ARM、ACA-ENTRA | APIMからBrokerへの許可、Portal用ACA・AppGW・管理VMからの拒否、Brokerの起動・SQL・CM・OBO・監査、PortalとBrokerが別々のACA環境を使うことを確認する。 |
| PE接続 | APIM/ACA/管理VMとFoundry/KV/SQL/ACR/監査Blob | DNS回答とPE全IP、往復制御、サービスTLSと認可を照合する。 |
| Vault分離と証明書 | APPGW-KV、APIM-KV、ACA-KV、MGMT-KV | 2つのPrivate EndpointとDNS回答、Vaultのネットワーク設定、対象オブジェクトの実効RBACを照合。公開証明書の初回発行・自動更新・実配信、APIMのNamed Value解決、必要のない接続とsecret読取の拒否を確認 |
| ACAの現構成依存 | イメージ取得、App/Job起動、標準DNS、管理ID、監視、リビジョン切替、再起動、スケール | 構築ACRと公開MCRの用途を区別する。Portal、Broker、証明書JobそれぞれのPE接続と起動・更新・復旧を確認する。 |
| 監査Blob・保存ログ | MGMT-AUDIT-PLATFORM/FOUNDRY、MGMT-MONITOR | 担当者本人の作業権限で構築コンテナーとWorkspaceを読み、付与前・回収後は拒否される。監査Blobへの書込み・削除権限は付けない |
| ACA App/環境のログ・App exec | MGMT-ARM、MGMT-ACA-DEV | 対象App/revision/replica/containerとtoken scopeを確認し、ログ本文/有限execの結果を取得。環境tokenをログ専用の保証として扱わない |
| ACA Jobログ | ACA-SQL等のJob処理とMGMT-ACA-DEV | 対象Jobのtoken認可、保存ログ、実行が存続する場合のライブログを区別。schema Jobは配備更新工程の正常実行に合わせて確認 |
| SQL直接保守 | MGMT-SQL | 対象DBだけで一時付与した本人のユーザーと最小権限で接続し、ODBC 18のTLSと証明書検証を確認する。別DBと回収後の新規接続は拒否される |
| 本人のVM接続 | 管理ネットワーク、VMのEntra SSH、一時RBACとLinuxグループ | 付与前・回収後の新規SSHを拒否し、付与期間中だけ本人がsudoなしで接続し資材を使用できる。既存セッション終了・ユーザー鍵の扱い・証明書用IDの分離を確認する |
| VM基盤・監査 | 非NSGのIMDS、DHCP、Agent、AMA、Disk、Boot | 実MI/版/実効設定/転送と保存を確認。managed内部保存を顧客NIC通信へ推測対応付けしない |
| Serial Console | 操作端末/Compute制御とguest serial channel | 認可、managed Boot Diagnostics、guest初期認証、実ログイン、SSH鍵維持を確認。VM NSGのSSH受信許可を追加しない |
| APIM時刻 | APIM-NTP | UDP123のAllow/拒否、診断エラー、APIM自身の現在時刻応答を組み合わせる。内部daemon/NTP応答packetは公開APIで不可視の場合があり、観測範囲を明記 |
| 管理VM時刻 | MGMT-HOST-TIME、Azure VMBus／PHCとChrony | `/dev/ptp_hyperv`のcharacter deviceと`clock_name=hyperv`、唯一sourceのPHC／`AZUR`選択、`Normal`同期、現在offset／精度を確認。保守復帰後に再確認する場合も実VMのChronyを照合する |
| 受入操作の終了 | 使用した検証用ID、秘密、一時権限とコマンド | 一時権限を回収し、検証用の一時データや変更を記録する。 |

各FWレコードの設定一致とFW全体の実操作成功を、全Application Ruleのhit済みへ読み替えない。Network Ruleが先に一致する重複、CA互換先、未使用の基盤機能の許可先には、個別hitを完了条件にしない場合がある。実効許可範囲とその理由は[Firewall設計](../reference/architecture/firewall-policy-design.md)に残す。

## 新規構築資材の防御レビュー

新規配備後はテスト用の本人、購読と合成入力を使い、資材が設ける信頼境界と実効権限を確認する。

| 優先度・到達点 | ソースで確認した境界と残る影響 | 対応と新規構築後の確認 |
| --- | --- | --- |
| 高・管理VMから共用資源 | 担当者本人への一時RBAC・DB権限と、無人の証明書Jobの実行IDを用途別にする。 | 付与前・回収後に保守操作を拒否し、付与中に対象だけを操作できることを確かめる。証明書Jobは初回発行・自動更新と対象外secretの読取拒否を確認する。 [管理VMの一時権限](../operations/management-vm-access.md) |
| 高・公開入口の迂回 | AppGWが再暗号化し、Firewall経由のHTTPSがAPIM／ACAの許可元になる。APIMにもAppGWの信頼済み入口確認がある。別経路からの到達や信頼情報の漏えいで境界が弱くなる | 有効なNSG・ルート・SNIを読戻し、外部入口、バックエンドの直接接続、許可されていないサブネットからの到達を確認する。AppGWとAPIMの監査を要求IDで照合する |
| 高・TLS検査とログ | 検査したURLはFirewallのApplication Rule／Threat Intelログの`TargetUrl`に含まれる可能性があり、OAuthの戻り先には一回限りの値が含まれ得る | URLを記録するカテゴリには取り込み時の削除を適用し、加工前のStorage直送を分ける。合成マーカーのある要求でWorkspace・Storageの双方を確認する。 [Application Ruleログの項目](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/azfwapplicationrule) · [Threat Intelログの項目](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/azfwthreatintel) |
| 高・Portalと接続所有者 | Portalのプロセスは、同じアプリに割り当てた認証SQL用IDとトークン暗号鍵用IDを使う。侵害時は両方に影響し得る。Entraは同じPortalクライアントで同意済みの別用途にも更新トークンを交換できる | 固定鍵版、期限、認証世代、SQLセッションを照合する。別本人、失効、更新競合、複数レプリカでの停止、インシデント時のセッション失効と鍵更新を受け入れる。 [PortalのID割当](../../apps/portal/deploy/application.bicep) · [Entra追加認証と残る影響](../setup/09-egakumi-portal.md#一度の登録依頼に含める設定) |
| 高・公開Portalへの権限集中 | APIM購読は基盤管理者がAzureポータルで管理する。Portalの実行IDは認証・試用のSQLとKey Vaultを使用する | [PortalとAPIMの管理境界](#portalとapimの管理境界)を確認する |
| 高・共有Key Vaultの読取範囲 | 公開TLSのPFXとACME状態は専用Vaultへ保存する。AppGWの読取りを対象PFXに絞る。APIMは指定のNamed Valueを参照し、読取のRBACは業務用Vault全体に及ぶ。業務用Vaultは複数処理で共有し、APIM管理プレーンのための例外を持つ | 必要な参照に加え、AppGWによる他のsecretの読取と、APIMや業務用ACAのIDによるTLS Vaultへの未許可操作が拒否されることを確認する。[Vaultの設定](../../infra/modules/services/services.bicep) · [RBACの設定](../../infra/modules/services/service-bindings.bicep) |
| 高・PortalからBrokerへの横移動 | Brokerは専用の内部ACA環境とサブネットに配備する。アプリの業務接続をAPIMに限定し、Broker側でも共有鍵・本人・権限を照合する。同じVNetのPEとAzure管理・監視サービスは用途別に利用する | Portal用ACAからBrokerへの直接到達を拒否し、公開Gateway経由の正規処理が成立することを確認する。APIM由来の異常な要求と、Broker実行IDによる対象外の秘密・DB・ARM操作も拒否されることを確認する。 [Broker環境の分離](#credential-broker環境の分離) |
| 中・接続先、長時間通信と障害 | 検査用中間CAとDNSが経路の信頼点になる。CA失効、証明書名の違い、長い接続の切断はGateway全体へ波及し得る | CAアクセスを専用IDと対象secretへ絞り、切替と期限を運用で確認する。正しい経路と証明書不一致、JSON、SSE、WebSocket、MCPを試験し、タイムアウトとIDPSを監査で識別する |

### PortalとAPIMの管理境界

Portalの実行IDがログイン・試用に必要なSQLとKey Vaultを利用でき、APIM購読の作成、キー取得・再生成、停止、ポリシーの更新が拒否されることを確認する。APIMからPortal内部のACAに直接接続できず、PortalからGatewayへは公開HTTPS経路で到達することを確認する。例外発行のキーはAPIMで停止し、停止後の要求が拒否されることを確認する。[Portalの配備](../setup/12-deploy-portal.md) · [購読キーの運用](../operations/access-and-keys.md)

### Credential Broker環境の分離

| 確認対象 | 新規環境で受け入れる契約 |
| --- | --- |
| 初回構築とPrivate DNS | Portal用の`cae-<stem>`とBroker用の`cae-<stem>-broker`が別のサブネット・内部VIP・DNS名に対応し、公開ネットワークが無効。Broker本体、SQL初期化Job、定期照合Jobは専用環境に所属する |
| 正規経路と拒否 | APIMから専用Broker FQDNへHTTPS443で到達し、TLS証明書名を照合する。到達しても無効な共有鍵・本人・権限で処理されない。Portal用ACA、AppGW、管理VMおよび外部からの直接アクセスを拒否し、Portalは公開Gateway経由のBearer試用でGraph OBOを利用する。購読キー向けCMの本人接続は外部クライアントで確認する |
| Brokerの基盤依存 | ACR login/data PEからのイメージ取得、業務用Key Vault PE、SQL PE、Entra/JWKS、ARMおよびACAの管理通信を使い、起動・スケール・定期照合を確認する。Firewallログ、NSGの設定、実行ログから、到達性と認可失敗を切り分ける |
| API・資格情報 | 通常のGateway APIとプロファイル照会、購読キーのCM接続・継続利用、Credentialの公開コールバック、本人BearerのGraph OBOを試す。異なる本人、期限切れ、依存先障害、誤ったscopeで停止する。トークンやsecretを監査に残さない |
| 更新・保守・撤去 | Gateway配備とBroker単体更新で、環境IDとFQDN、App／Job、期待imageと正常revisionを照合する。管理VMのinventoryは両環境を含み、ログ・execの権限を当該資源へ限定できる。撤去時は所有する子資源、両環境・サブネット・各Azure管理RGの消失を順に確認する |

[Gateway配備](../setup/11-deploy-gateway.md) · [保守範囲](../operations/management-vm-access.md)

### 公開TLS用Key Vaultの分離

| 確認対象 | 新規環境で受け入れる契約 |
| --- | --- |
| VaultとPE | 公開TLS用と業務用が別IDと別PEに対応し、各FQDNがそれぞれのPEに解決される。公開TLS用の資材は`publicNetworkAccess: Disabled`、`networkAcls.defaultAction: Deny`、`networkAcls.bypass: AzureServices`を指定する。業務用はAPIM管理プレーンのNamed Value用に例外を持つ。Azureでは両Vaultの設定を読み戻し、AppGWの公開TLS証明書参照、公開TLS JobのPE経由の到達、APIMと業務用ACAの業務用Vaultへの到達、対象外の接続・操作の拒否を個別に確認する |
| 証明書の初回発行と更新 | 対象PFX・ACME状態の専用ACA Jobによる操作と、対象PFXのAppGWによる参照を確認する。業務用VaultのPortal認証証明書や通常のsecretと混同しない。公開2ホストの実TLSを証明書と照合する |
| APIMの実効権限 | 標準Gateway証明書を使用。入口、Broker、所有者チケットのNamed Valueが設定どおり取得できることを確認する。APIMの読取割当が業務用Vaultを対象とし、公開TLS用Vaultに及ばないことを確認する |
| TLS Vaultの監査 | 公開TLS用Vaultに配備する`core-audit`の`AuditEvent`と送信先を読み戻す。対象オブジェクトに対する正規の読取・証明書更新と、未許可操作の結果を保護証跡で確認する |

[公開証明書の運用](../operations/secrets-and-certificates.md#tls証明書を更新し公開httpsまで確認する) · [APIM管理プレーンの参照仕様](https://learn.microsoft.com/en-us/azure/api-management/breaking-changes/trusted-service-connectivity-retirement-march-2026#scenarios-that-are-not-affected-by-the-breaking-change)

### 基盤用Entraアプリの分離で追加する確認

| 対象 | 資材上の制御と実環境での確認 |
| --- | --- |
| OBOの信頼境界 | BrokerはAPIM内部の共有鍵と検証済み本人を照合する。Entraが元のトークンを検証し、Graphの対象は固定の本人取得とする。同意不足・別人・期限切れ・条件付きアクセス時の停止を確認する |
| 署名と秘密の権限 | Brokerの実行IDによるOBO署名を専用鍵に、秘密の読み取りをBrokerとCredentialの専用secretに限定する。Azureで、有効な版と実証明書の組、対象外の鍵・secretの拒否を確認する。|
| トークンの監査 | APIMの通常診断と権限付きデバッグ、Firewall、Brokerのログを確認する。元のBearer・署名済みclient assertion・Graphトークン・追加認証claimsが通常監査に保存されず、デバッグの利用者と取得範囲が限定されることを確認する |
| 管理VM | 保守時だけ担当者本人に権限を付与し、終了時に回収する。PIMを利用する場合は、Azure基盤側のライセンスと会社の承認体制を確認する |

### 手動追加したAPIの公開前確認

| 対象 | 確認する内容 |
| --- | --- |
| 公開経路 | 新しいパスで有効なWAFの関連付けとモード、AppGWからAPIMへのHTTPS・信頼済み入口の書換え、許可した操作の到達性を照合する |
| Productと認証 | 許可したProductの購読キーと、公開対象なら本人Bearerで接続する。対象外Product、無効キー、期限切れ・scope不足のBearerを拒否し、監査に認証情報が残らないことを確認する |
| 下流の到達範囲 | 許可したOperationと固定バックエンドの組、APIMのIDを割り当てた対象資源と推論権限のscopeを確認する。応答の監査、SSEの転送、要求回数の上限も合わせて確認する |

具体的な設定は[Claude Messagesの手動追加手順](../operations/claude-messages.md#構築後にclaudeをguiで追加する)を参照する。検証結果は環境別の保護実施票で管理する。
