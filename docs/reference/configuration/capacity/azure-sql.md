# Azure SQLの容量

[3環境の初期配備値](../environment-capacity.md)

Broker DBのvCore数・最大データ容量はparamsの `capacity.brokerSql` で指定する。Broker DBのサービス階層・世代・バックアップ・ゾーン冗長化と、Portal認証DBの設定は[サービス別config](../../../../infra/config/capacity/azure-sql.json)で管理する。 [容量の入力項目](../parameters.md#環境ごとに確定する容量)

BrokerとPortal認証は1つの論理サーバーに置く別々の単一DBで、計算容量を個別に割り当てる。Private EndpointのTCP 1433・Proxy接続・Entra認証を維持し、この経路を含めて測定する。 [既存のSQL接続方式](../../architecture/sql-connectivity.md) · [Azure SQLの接続方式](https://learn.microsoft.com/en-us/azure/azure-sql/database/connectivity-architecture)

| DB | `prod` | `stg` | `dev` | 容量を消費する処理 |
| --- | --- | --- | --- | --- |
| Broker | General Purpose provisioned `GP_Gen5_4`（4 vCore）、データ上限32 GiB。ゾーン冗長、Geoバックアップ、PITR 35日 | 同じGeneral Purposeと保護設定で `GP_Gen5_2`。本番負荷の検証時に4 vCoreへ増強 | `GP_Gen5_2`、32 GiB | CMの事前認可、使用時のセッション・接続状態更新、ログイン、失効、毎時の照合・回収 |
| Portal認証 | `GP_Gen5_2`（2 vCore）、32 GiB、Geoバックアップ、PITR 14日。非ゾーン冗長 | 同左 | Basic 5 DTU・2 GiB・Geoバックアップ・PITR 7日。Private Endpoint、Entra専用認証、配備、復元を確認 | サインイン、セッション確認、API試用用トークンの更新 |

- **Broker SQL**。外部の個人CM通信100／200 RPSから、SQLを使う内部処理200／400回/秒、更新トランザクション100／200回/秒を試験する。接続取得回数と、実際にSQLへログインした回数を別に計測する。同一セッションへの並行要求、期限更新、失効、照合Jobが重なるケースも含める。4 vCoreの公式上限はデータI/O最大1,280 IOPS、ログ書き込み18 MiB/s、同時ワーカー・ログイン各400。2 vCoreは640 IOPS、9 MiB/s、各200。HTTPの要求数から直接IOPSへの換算は行わず、計測値で容量を決める。 [BrokerのSQL処理](../../../../apps/credential-broker/credential_broker/store.py) · [Azure SQLのvCore上限](https://learn.microsoft.com/en-us/azure/azure-sql/database/resource-limits-vcore-single-databases#general-purpose-standard-series-gen5-part-1-of-3)
- **Portal認証SQL**。現行の接続プールはプロセスあたり最大5。1プロセスのアプリが最大2レプリカなら通常のアプリ接続枠は10で、スキーマJobとリビジョン切替分は別に数える。ログインの集中、同時更新、手動試用、時点復元で容量を確認する。Basicは同時ワーカー・ログイン各30で、復元の性能・機能要件に合わなければGeneral Purposeを使う。 [Portalの接続プール](../../../../apps/portal/lib/auth-database.ts) · [DTUの上限](https://learn.microsoft.com/en-us/azure/azure-sql/database/resource-limits-dtu-single-databases#basic-service-tier)
- **増強条件**。通常ピークでDB CPU、データI/O、ログ書き込み、ワーカーの継続使用率が各70%以内で、接続取得・クエリ・ロック待ちが応答目標内に収まることを初期の判定基準とする。上回る場合は接続プール、索引、ロック競合を確認し、Brokerは6／8 vCoreを再試験の候補とする。データ・索引・一時領域の増加は別途追跡し、DB上限の70%で増量計画、85%で対応する。 [Azure SQLの監視指標](https://learn.microsoft.com/en-us/azure/azure-sql/database/monitoring-metrics-alerts)
