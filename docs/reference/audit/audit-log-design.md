# 監査ログの設計

空のAzure環境から構築する資材の設計。ログの確認とAzure受入は[監査ログの確認](request-body-audit.md)を参照する。

## 標準構築の構成

App Gateway・Azure Firewall・APIM・Foundryの標準診断を有効にする。Log Analyticsを共通の検索先とし、対象の監査ログを取り込み時からStorageへも保存する。Foundryエージェントの本文は、既存のFoundryプロジェクトに接続するワークスペースベースのApplication Insightsで採取する。

標準構築で使うのは、Azure Monitorの診断設定、既存のApplication InsightsとLog Analytics、Data Export、監査Blob。APIMは標準のAzure Monitorロガーと既存の認証主体・判定のトレースを使う。モデルAPIはAPIMからFoundryへ直接ルーティングする。本文の保存が必須なAPIは、標準機能で要件を満たせることを公開条件とする。

| 記録元 | 収集する分類と監査項目 | 本番の長期保管 |
| --- | --- | --- |
| App Gateway | `ApplicationGatewayAccessLog` と `ApplicationGatewayFirewallLog`。接続元、HTTPメソッドとパス、応答状態、接続先と時間、WAFのルールと判定 | `AGWAccessLogs` と `AGWFirewallLogs` をData Export。ワークスペースの取り込み変換でURLクエリとWAFの生の一致値を除去してから保存する |
| Azure Firewall | `AZFWNetworkRule`、`AZFWApplicationRule`、`AZFWIdpsSignature`、`AZFWThreatIntel`。許可・拒否、適用ルール、通信元・宛先、FQDN、TLS検査フラグ、IDPS署名と判定。DNSプロキシの `AZFWDnsQuery` と内部FQDN解決失敗の `AZFWFqdnResolveFailure` も対象 | Network RuleとIDPSは診断設定から並行保存。Application RuleとThreat Intelligenceは `TargetUrl` 除去後にData Export。DNS診断は90日検索用。DNATは使用時に追加 |
| APIM | `GatewayLogs`、`GatewayLlmLogs`、`WebSocketConnectionLogs`。要求ID、認証主体・方式、API・操作、認可／制限の結果、接続先、HTTP結果、時間、モデル使用量、WebSocketの接続と終了 | `ApiManagementGatewayLogs` と `ApiManagementGatewayLlmLog` をData Export。本文を採らず、URLのクエリや資格情報を除外。WebSocket接続ログは診断設定から並行保存し、宛先・エラー欄への資格情報混入を受入で確認 |
| Foundryアカウント | `Audit`、`RequestResponse`、`AzureOpenAIRequestUsage`。管理・アクセスの監査、推論の結果と時間、使用量。モデルAPIの生のプロンプトと応答は本文監査の対象として保証しない | `AzureDiagnostics` からのData Exportは非対応。アカウントの診断設定から同リージョンのBlobへ並行保存 |
| Foundryプロジェクト | プロジェクトの `Audit`。エージェントのプロンプト・モデル入出力・関連スパンはApplication Insights経由。プロジェクトの診断カテゴリ `Trace` は利用可否と内容を確認し、運用に必要な場合に検索先へ追加 | プロジェクトの `Audit` は診断設定から並行保存。エージェント本文は `AppGenAIContent`、照合に必要な `AppDependencies`、`AppRequests`、`AppTraces` などはData Exportで保存 |

[App GatewayとWAFのログ](https://learn.microsoft.com/en-us/azure/application-gateway/monitor-application-gateway-reference) · [WAFログテーブル](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/agwfirewalllogs) · [Firewallのログ分類](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-logs/microsoft-network-azurefirewalls-logs) · [APIMの標準ログ](https://learn.microsoft.com/en-us/azure/api-management/monitor-api-management) · [Foundryアカウントの診断](https://learn.microsoft.com/en-us/azure/foundry/how-to/diagnostic-logging) · [Foundryプロジェクトの診断分類](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-logs/microsoft-cognitiveservices-accounts-projects-logs)

### Foundryの本文を取得できる範囲

| 経路 | 標準機能で取得する範囲 |
| --- | --- |
| Foundryでホストするエージェント | Connected resourcesのApplication Insights接続を通して、サーバー側トレースのプロンプトとモデル入出力を採る。初期のモデル配備とは別に、エージェントを使う段階で有効化と着信を確認する |
| Chat Completions・Responses・Claude Messagesなどの直接REST | APIMのメタデータとFoundryの推論診断。プロンプト全文、生成結果全文、SSEの各イベントの本文は標準構築の保証対象外 |
| 埋め込み、画像／音声、Graph、PII、Document、MCP | 各サービスとAPIMが出す診断・要求メタデータ。業務本文を共通で採取する仕組みは設けない |
| WebSocket・WebRTC | 基盤は接続・制御操作の結果を記録する。セッション内の業務テキストや文字起こしは、必要に応じて呼び出し側で記録する |

Application Insights接続とFoundryプロジェクトのマネージドIDへの `Monitoring Metrics Publisher` は現行資材を使う。Application Insightsと同じLog Analyticsに対する診断設定は重ねない。初回構築はモデル配備までなので、エージェントを追加する際の受入で合成プロンプトを送り、`AppGenAIContent` に本文が届き、通常の監視担当者には見えないことを確認する。生成AIの本文テーブルはワークスペースDCRで変換できない。認証情報をエージェントの入力やツール引数に渡さない構成を確認する。自由記述に混入した秘密値の自動除去は標準トレースの保証外なので、除去が必須なエージェントは有効化条件を別途決める。アクセス制御にプレビューを採用できない環境では、本文トレース用の接続先を監視用ワークスペースと分ける。 [Foundryのトレース設定](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-setup) · [AppGenAIContentの仕様](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appgenaicontent) · [本文のアクセス制御](https://learn.microsoft.com/en-us/azure/azure-monitor/app/protect-sensitive-content)

### ログの照合

- App Gatewayは `TransactionId` とAPIM側の `x-appgw-trace-id` で照合する。入口で遮断された要求はApp Gatewayの記録で調べる。
- APIMの要求IDを `x-ms-client-request-id` で下流へ引き継ぐ。Foundry側で使える要求ID、Trace ID、Span IDを調査クエリに含める。エージェントの本文とスパンはTrace ID／Span IDで照合する。バックエンドの診断やエージェントトレースに照合キーが出なければ、未照合として表示する。
- Firewallは通信が通過した区間を、時刻、通信元・宛先、ポート、FQDNで関連付ける。接続再利用やNATで候補が複数なら候補として扱う。APIMからPrivate Endpointへ直接流れる区間は、APIMと接続先の記録で確認する。

[現行の要求IDの照合](audit-identification.md) · [APIMから下流への要求ID](../../../apps/api-gateway/policies/fragments/request/core-api-request-preparation/core-api-request-preparation.xml#L10)

## 構築リソースの診断一覧

配備するすべてのリソースを対象に、下表のいずれかへ分類する。列挙した監査分類を各リソースの診断設定で有効にする。ゲストログとApplication Insightsは固有の収集経路を使う。メトリクスやトラブルシューティング用のログは、日常監視に必要なものだけをLog Analyticsへ送り、7年保管の対象は下表で明示する。

| 配備 | リソースとスコープ | Log Analyticsで取得するもの | 本番の7年保管 |
| --- | --- | --- | --- |
| Core | App Gateway、Firewall、APIMサービス、Foundryアカウントとプロジェクト | 上表。診断設定は各親リソースに付ける。APIM各APIの診断は記録項目を決める既存設定を使う | 上表 |
| Core | サブスクリプション | Activity Logの管理、セキュリティ、ポリシー、構成変更、正常性。独自のログを出さないリソースのARM操作もここで確認 | 診断設定から並行保存 |
| Core | 共有Key Vaultと公開TLS用Key Vault | `AuditEvent`。認証主体、鍵・秘密への操作、結果 | 診断設定から並行保存 |
| Core | ACR | `ContainerRegistryLoginEvents` と `ContainerRegistryRepositoryEvents`。ログイン、イメージのpush・pull・削除 | 診断設定から並行保存 |
| Core | 所有する各NSG | `NetworkSecurityGroupEvent` と `NetworkSecurityGroupRuleCounter`。ルール評価とカウンターの記録 | 診断設定から並行保存 |
| Core | VNet、Public IP | 対象機能が発行する `VMProtectionAlerts` やDDoS通知等を、カテゴリと機能の有効状態を確認して登録。通常の通信判定はFirewall／NSGで確認 | 有効にしたセキュリティ通知を並行保存 |
| Core | 管理VM | 現行のAzure Monitor Agent＋DCRでLinux認証・警告の `Syslog` と性能。対応する更新ログはVMの診断で検索。VM・NIC・ディスクの構成変更はActivity Log | 認証・警告のSyslogをData Export |
| Core | 監査用Storageと証明書の状態保存StorageのBlobサービス | `StorageRead`、`StorageWrite`、`StorageDelete`。アクセス主体、操作、成否。アクセスURLに資格情報がないことを確認 | 監査Storage自身のアクセスログはLog Analyticsで90日。監査Blobへの書き込みを同じ保管先へ記録すると循環するため、`StorageBlobLogs` 全体のData Exportは設定しない。証明書の状態保存Blobは監査Storageへ並行保存 |
| Core | Log Analytics、Application Insights、DCR・アラート | ワークスペースの `Audit` と、接続済みApplication Insightsの `App*`。Data Exportの正常性・失敗・設定変更を監視 | ワークスペースの検索監査はKQL本文を取り込み時に除いてData Export。監査に使うアプリテレメトリーもData Export |
| Core／Gateway／Portal | Portal・Credential Broker・証明書JobのACA環境と配備するアプリ・Job | 環境でコンソールとシステムログを一度だけ収集。リビジョン、Job成否、再起動。IngressのHTTP診断は既存ログで追えない運用上の用途がある場合に追加 | ACAコンソールとシステムログは90日。証明書への操作はKey Vaultと証明書状態Blobの監査で長期保管 |
| Core／Gateway／Portal | Coreの共有SQL論理サーバー、GatewayのBroker DB、PortalのDB | 共有サーバー側でSQL監査を有効にし、`SQLSecurityAuditEvents` で認証と権限・DB主体の変更を収集。二つのDBの記録を確認する。EntraがSQL到達前に拒否した認証は、Entraテナント側のサインインログで確認する。SQL本文を一括採取する既定のバッチ分類は対象から外す | `AzureDiagnostics` に入るため同リージョンのBlobへ標準の並行保存。SQL監査ポリシーから直接Blobへ書く場合は、SQL側の保持日数をWORM期間より長く設定し、保護付き追記を確認する |
| Core／Gateway／Portal | API Centerとカタログ、APIM内部のAPI／ポリシー／Product／Credential Manager、Foundryモデル配備、Key Vault子リソース | 親サービスの診断やActivity Logで操作を確認する。API Centerは対象リソースのカテゴリを確認し、独自の監査分類がない場合はActivity LogとAPIMを使う | 親の監査経路に従う |
| Core／Gateway／Portal | リソースグループ、マネージドID、RBAC、サブネット、UDR、Firewall／WAFポリシー、Private Endpoint、DNSゾーン／レコード／リンク、各構成用子リソース | 変更はActivity Log。通信・データアクセスはApp Gateway、Firewall、NSG、接続先サービスを使う。参照する公開DNSゾーンの変更は管理サブスクリプションのActivity Logで確認する。ACAが管理する内部ロードバランサーはACA環境の診断で追う | サブスクリプションの監査経路に従う |

[Azureのリソース種別ごとのログ一覧](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/logs-index) · [ACR](https://learn.microsoft.com/en-us/azure/container-registry/monitor-container-registry-reference#resource-logs) · [NSG](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-logs/microsoft-network-networksecuritygroups-logs) · [ACA環境](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/microsoft-app_managedenvironments) · [Azure SQL監査](https://learn.microsoft.com/en-us/azure/sql-database/sql-database-auditing-get-started) · [Blob診断の出力先制約](https://learn.microsoft.com/en-us/azure/storage/blobs/monitor-blob-storage#destination-limitations)

## 保持と運用

- **Log Analyticsはテスト・本番とも90日**。対象テーブルはAnalyticsプランとし、テーブル個別の保持が上書きされていないことを読み戻す。本番Blobは現行のGRS・Locked・2,557日と保護付き追記を使う。取り込み時からBlobにも保存し、90日を経過した記録の調査はBlobで行う。テストは配送を検証し、通常の検証先のWORMはロックしない。
- **保管経路は監査分類ごとに一つ**。変換が必要な表とApplication Insights経由の表はData Export。標準診断から並行保管する場合は発生元と同リージョン、Data Exportはワークスペースと同リージョンのStorageを使う。既存の信頼済みサービス例外でAzure Monitorの書き込みを認め、読み取りはPrivate EndpointとRBACで担当者に限定する。
- **Foundry本文の7年保管は現物で受入**。`AppGenAIContent` は既存のエクスポート設定に含まれていない。エージェントを追加するときに合成用のトレースでテーブルを確認し、90日の保持とData Exportを設定してから利用者に公開する。設定の作成結果に加え、保護付き設定のまま合成の入力・出力が `am-*` のBlobに届くことを確認する。標準の継続配送が成立しない場合は、本文の長期保管を未対応と記録し、別の実装判断にする。
- **配送エラーを監視する**。Data Exportは最大12時間再試行し、重複・未達が起こり得る。標準の失敗メトリクスとテーブルの最終着信時刻を確認し、障害時は90日以内にAzure Monitorのクエリから対象期間の記録を抽出してWORMへ補完する。標準構築に常駐保存ワーカーや定期移送Jobは追加しない。

[Log Analyticsの保持期間](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/data-retention-configure) · [診断設定の宛先要件](https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/diagnostic-settings#destinations) · [Data Exportの制約と配送監視](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/logs-data-export) · [Blobの不変ポリシー](https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-policy-configure-container-scope)

## 資材と配備後の確認

1. **Coreの診断設定を完成させる。** [診断設定の中央定義](../../../scripts/deployment/helpers/diagnostics.py)と各リソースのBicepで上表の分類を追加する。Gateway・Portalで後から作る対象は各配備モジュールから設定し、構築の配備段階と診断の所有者を揃える。管理VMのDCRと、Foundryの[Application Insights接続](../../../infra/modules/services/services.bicep#L129)を使う。各配備の受入時に `az monitor diagnostic-settings categories list` と診断設定の読み戻しで、対応分類、送信先、対象外の理由を確認する。Activity Logのみで扱うものにも配備一覧で理由を残す。
2. **既存の保管基盤を使う。** [監視モジュール](../../../infra/modules/monitoring/monitoring.bicep#L49)の保持テーブル・Data Exportと、同リージョンのBlobコンテナー定義を更新する。取り込み変換は[既存の変換定義](../../../infra/modules/monitoring/log-privacy.json)に追加し、[CoreのDCR検証](../../../scripts/deployment/helpers/log_privacy.py)も同時に揃える。新規コンテナーは配備のロック・再実行・撤去判定にも追加する。WAF、APIMのGateway／LLMログ、ワークスペースのクエリ監査は変換後のレコードへ切り替え、未変換のBlob直送を止める。APIMの `core-audit` 設定にはWebSocketの並行保存を残す。既存のAPIM `gateway-request` トレースは `ApiManagementGatewayLogs.TraceRecords` とともに保全する。 [既存のAPIM監査クエリ](../../../scripts/audit/audit-usage.kql#L10)
3. **Gatewayを標準の転送経路に戻す。** Chat CompletionsとResponsesは既存のAPIMマネージドIDとFoundry Private Endpointを使う。Claudeの手動配備ひな型もFoundry直結にする。認証・認可、既存のモデル指定、応答本文とSSEの逐次配信を回帰確認する。DocumentやPIIの公開API契約を作る処理からは監査提出部分だけを外す。 [OpenAIの定義](../../../apps/api-gateway/apis/openai-inference-v1.json) · [APIMの配備テンプレート](../../../apps/api-gateway/api-publication.bicep) · [Claudeの手動追加](../../../apps/api-gateway/policies/portal-examples/claude-messages.md)
4. **空の環境からの受入を行う。** ローカルでは各段階のBicepコンパイル、診断設定・出力先の整合、再実行、API／SSEの回帰を確認する。Azureでは、WAF遮断、Firewallの許可・拒否とTLS検査、APIMの認証・制限、Foundryの推論診断、エージェントの合成プロンプトと応答を発生させる。Log AnalyticsとBlobの実着信、要求の照合、秘密値の除外、権限の拒否、WORMロック、配送欠落の復旧を確認する。
