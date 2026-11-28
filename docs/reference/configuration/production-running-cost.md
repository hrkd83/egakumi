# 本番の概算月額

**固定・最低構成の概算は月額約 23,300 USD。** `prod` の公開paramsに記入済みの容量で日本東部を730時間稼働させる場合の公開価格で、2026年11月28日時点の試算。APIMの12ユニットが約78%を占める。実契約の見積もりは、導入先の `capacity`、選択したリージョン、価格プランに合わせる。契約割引、予約割引、税、為替換算は含めていない。 [prodの公開params](../../../params/prod.example.json) · [サービス別の初期配備値](environment-capacity.md) · [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices) · [Azure料金計算ツール](https://azure.microsoft.com/ja-jp/pricing/calculator/)

## 一定の構成で発生する費用

| サービス | 月額の計算 | USD/月 |
| --- | --- | ---: |
| API Management Classic Premium | 1ユニット目3.829 USD/時間、追加11ユニット各1.9145 USD/時間。1インスタンスを12ユニットで稼働。 [APIMの料金](https://azure.microsoft.com/en-us/pricing/details/api-management/) | 18,169 |
| Application Gateway WAF v2 | 固定0.522 USD/時間と、最小20インスタンスの200 Capacity Units × 0.0144 USD/CU時。使用量が最低分を上回る差額は別。 [Application Gatewayの課金](https://learn.microsoft.com/en-us/azure/application-gateway/understanding-pricing) | 2,483 |
| Azure Firewall Premium | 1基 × 1.75 USD/時間。TLS検査・IDPSを使う。処理GBは別。 [Azure Firewallの料金](https://azure.microsoft.com/ja-jp/pricing/details/azure-firewall/) | 1,278 |
| Azure SQL Broker DB | GP Gen5 4 vCoreの0.68802 USD/時間＋ゾーン冗長0.412812 USD/時間。設定32 GBとトランザクションログ分30% × 0.276 USD/GB月。 [SQL単一DBの料金](https://azure.microsoft.com/ja-jp/pricing/details/azure-sql-database/single/) · [ストレージの課金](https://learn.microsoft.com/en-us/azure/azure-sql/database/service-tiers-sql-database-vcore#data-and-log-storage) | 815 |
| Azure SQL Portal認証DB | GP Gen5 2 vCoreの0.34401 USD/時間。設定32 GBとログ分30% × 0.138 USD/GB月。論理サーバーの別料金なし。 [SQL単一DBの料金](https://azure.microsoft.com/ja-jp/pricing/details/azure-sql-database/single/) | 257 |
| 管理VM・OSディスク | Linux D2as v6を0.118 USD/時間、E10 Standard SSD LRS（128 GiB）を9.60 USD/月とする。 [Linux VMの料金](https://azure.microsoft.com/ja-jp/pricing/details/virtual-machines/linux/) · [Managed Diskの料金](https://azure.microsoft.com/ja-jp/pricing/details/managed-disks/) | 96 |
| Container Registry Premium | 1個を1.6666 USD/日。追加ストレージとレプリカは別。 [ACRの料金](https://azure.microsoft.com/ja-jp/pricing/details/container-registry/) | 51 |
| Private Endpoint・IP・DNS・ACAの管理ネットワーク | Private Endpoint 9個 × 0.01 USD/時、Standard IPv4 6個 × 0.005 USD/時、ACA管理のStandard LB 3基 × 0.025 USD/時を目安とする。Private DNS 10ゾーン × 0.50 USD/月。 [Private Link](https://azure.microsoft.com/ja-jp/pricing/details/private-link/) · [IPアドレス](https://azure.microsoft.com/ja-jp/pricing/details/ip-addresses/) · [Load Balancer](https://azure.microsoft.com/ja-jp/pricing/details/load-balancer/) · [DNS](https://azure.microsoft.com/ja-jp/pricing/details/dns/) · [ACAの管理リソース](https://learn.microsoft.com/en-us/azure/container-apps/custom-virtual-networks#managed-resources) | 約147 |
| Azure Monitorのログ検索アラート | ワークスペース対象の5分間隔、6件 × 1.50 USD/月。 [Azure Monitorの料金](https://azure.microsoft.com/ja-jp/pricing/details/monitor/) | 9 |
| **小計** | 端数は合計後に丸める | **約23,300** |

Private EndpointはFoundry、Key Vault 3用途、監査Blob 2用途、証明書状態Blob、ACR、SQL。Standard IPv4はApp Gateway、Firewall、APIMの管理面、ACAの管理ネットワーク3環境分を計上。Private DNSはFoundryの3ゾーン、Key Vault、Blob、ACR、SQL、APIM、Portal用ACA、Broker用ACA。メトリックアラートは最初の10時系列/月が無料で、超過分は0.10 USD/時系列・月。App Gateway等のディメンション展開と、同じサブスクリプションの無料枠消費で変動する。事前準備の公開DNSゾーン、社内のハブ回線、Firewall中間CA用の持ち込みVault、Entraライセンスは共用費用として導入先で計上する。 [Azure Monitorの料金](https://azure.microsoft.com/ja-jp/pricing/details/monitor/)

## 最小レプリカ分と利用量に応じる費用

- **Container Appsの常駐アプリ**。Portal 1レプリカとBroker 5レプリカを保持する前提で、5.5 vCPU・11 GiBの合計を730時間換算すると、約130 USD（全時間idle単価）〜約434 USD（全時間active単価）。無料枠、追加レプリカ、HTTP要求、Job実行分は含めない。リクエストを受けていない時間も、CPUや通信がidleの判定に収まるかで課金が変わる。 [Container Appsの課金](https://learn.microsoft.com/en-us/azure/container-apps/billing) · [Container Appsの料金](https://azure.microsoft.com/ja-jp/pricing/details/container-apps/)
- **Foundry・外部API・監査**。モデル推論、AIサービス、外部API、Key Vaultの操作数、Monitorのログ取り込み・90日保持・Export、約7年WORMのBlob容量と書き込み・検索・復元は利用量に応じて見積もる。ProvisionedモデルのPTUは今回の初期配備に含めていないので、予約する場合は別途、予約枠を計上する。 [Foundryの料金](https://azure.microsoft.com/ja-jp/pricing/details/microsoft-foundry/) · [Azure Monitorの料金](https://azure.microsoft.com/ja-jp/pricing/details/monitor/) · [Blobの料金](https://azure.microsoft.com/ja-jp/pricing/details/storage/blobs/)
- **通信とバックアップ**。Firewall・Private Link・Load Balancerの処理量、リージョン間／外向き通信、DNSクエリ、SQLのPITRバックアップ超過分、ディスク操作料は利用量で計上する。Event Hubs・本文監査中継・検査ワーカーは後続の本文監査設計で別途見積もる。 [Azureの帯域幅料金](https://azure.microsoft.com/ja-jp/pricing/details/bandwidth/) · [SQLバックアップの課金](https://learn.microsoft.com/en-us/azure/azure-sql/database/service-tiers-sql-database-vcore#backup-storage)
