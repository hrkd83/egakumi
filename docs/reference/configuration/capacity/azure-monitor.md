# Azure Monitorの容量

[3環境の初期配備値](../environment-capacity.md)

設定正本は [サービス別JSON](../../../../infra/config/capacity/azure-monitor.json)。

| サービス | 初期値と増強・切替の判断 |
| --- | --- |
| Log Analytics | 1環境につき1ワークスペース、90日。App Gateway・WAF、Firewall、APIM、Foundry、SQL、ACA、Key Vault等の対象ログは、発生元ごとに `件数 × 平均イベントサイズ` とピークの取り込み量を計測する。日次上限とサービスの取り込み枠は別に管理する。欠損、検索遅延、アラート遅延を確認する。 [Azure Monitorの制限](https://learn.microsoft.com/en-us/azure/azure-monitor/fundamentals/service-limits) |
| Application Insights | Foundryのトレースを既存の連携で収集する。既定の日量100 GB、イベント毎秒32,000件を実設定と照合し、日量80 GBの警報と到達率を確認する。日次上限はApplication Insightsとワークスペースの両方を照合する。通常のAPIM診断はAzure Monitorロガーを直接使い、ログやメトリックから別に計測する。後続のGateway本文の全量保管はWORM経路を使う。 [Application Insightsの上限](https://learn.microsoft.com/en-us/azure/azure-monitor/fundamentals/service-limits#application-insights) · [日次上限](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/daily-cap) |
| 診断・Data Export・アラート | Blobへの直接診断とLog AnalyticsからのData Exportを別々に照合する。同一リージョンのExportは1ワークスペース1ルールを起点とし、公式上限10ルールと照合する。出力失敗、出力件数、Blob着信までの遅延と重複・欠落を調べる。宛先が停止して再試行期限（最大12時間）を超えれば欠落し得る。ログアラートを配備し、テーブルへの着信と権限の反映後に稼働・発報を確認する。Gatewayの5xx率はAPIMの `ApiManagementGatewayLogs` を使う。 [Gatewayログの列定義](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apimanagementgatewaylogs) [Data Exportの制約](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/logs-data-export) · [Azure Monitorの制限](https://learn.microsoft.com/en-us/azure/azure-monitor/fundamentals/service-limits) |
