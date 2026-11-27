# 共有Azure SQLでProxyを採用する理由

共有Azure SQLの接続ポリシーは、`Proxy`へ明示固定する。SQL Private Endpointへの通信をTCP1433に限定し、Portalの既存の認証・接続制御を維持するためである。Redirectによる性能改善幅が未計測の段階では、接続層の移行と保守範囲の拡大を採用しない。

対象はPortal、Credential Broker、SQL初期化・照合Job、管理VMが利用する共有SQLサーバーである。PortalだけをProxyにする混在方針ではない。APIM製品自身のSqlサービスタグへの基盤通信は、[別の通信要件](network-communication-requirements.md#flow-apim-sql-platform)として扱う。

## ネットワーク上の採用理由

Azure SQLのProxyは、接続中のSQL通信をAzure SQL Gateway経由で転送する方式である。現在の構築では、クライアントからSQL Private EndpointへVNet内で直接接続し、Azure Firewallを経由しない。Azure SQL Gatewayと、この基盤が配置するAzure Firewallは別の設備である。

| 接続方式 | Private Endpointで必要なポート | この基盤での判断 |
| --- | --- | --- |
| Proxy（採用） | TCP1433 | ACA・管理VMの送信とSQL PEの受信を1433に揃え、宛先を構築SQL PEの全IPに限定する。現行の4つのSQL向けNSG規則と保守clientのProxy照合を維持する。 |
| Redirect（見送り） | TCP1433–65535 | MicrosoftのPrivate Endpoint仕様に従い、クライアント側の送信とPE側の受信許可を広げる必要がある。送信元とSQL PEへの宛先制限は維持できるが、driver・NSG・構築・保守の整合した変更と実通信確認が必要になる。 |

Private EndpointのRedirect要件は、公開endpointで使うTCP1433とTCP11000–11999の要件とは異なる。[MicrosoftのPrivate Endpoint仕様](https://learn.microsoft.com/en-us/azure/azure-sql/database/private-endpoint-overview?view=azuresql#use-redirect-connection-policy-with-private-endpoints)を基準にする。Proxyの選択はPrivate Endpoint、SQL接続時のTLS検証、EntraのManaged Identity認証と両立する。

実装の正本は[共有SQLのconnection policy](../../infra/modules/services/shared-sql.bicep)、[SQL向けNSG](../../infra/modules/network/credential-broker-network.bicep)、[管理clientの前提照合](../../scripts/deployment/helpers/management_client_assets.py)である。通信の対応は[ACAからSQL](network-communication-requirements.md#flow-aca-sql)と[管理VMからSQL](network-communication-requirements.md#flow-mgmt-sql)を参照する。

## Portalが同じ物理接続を保持する理由

Portalの購読同期は、SQLの台帳を正本としてAPIMの購読を更新・再作成・削除する。[同期repository](../../apps/portal/lib/subscription-sync/repository.ts)は`sp_getapplock`で購読ごとの排他ロックを取得し、[同期service](../../apps/portal/lib/subscription-sync/service.ts)がAPIMへのHTTP処理を終えるまで保持する。登録済み購読では[通常のキー更新・削除](../../apps/portal/lib/api-keys/repository.ts)と同じロック名を使うため、別のリクエストやPortalの別レプリカとの競合もSQL側で直列化できる。APIM側の別の変更はETagと再照合で検出する。

同期のロックは`@LockOwner='Session'`であり、所有者はSQLのセッション、すなわちその物理接続である。処理順序は次のとおり。

1. Kyselyの`database.connection().execute(...)`で、プールから1本の接続を確保する。
2. その接続でSession所有の排他ロックを取得する。
3. SQL台帳とAPIMの最新状態を読み、確認画面で承認された内容と再照合する。
4. 開始監査をSQLへ自動コミットで保存してから、APIMへの更新・削除などのHTTP要求を送る。
5. APIMを再取得して結果を確認し、成功・保留・失敗の監査を保存する。
6. 同じ接続で`sp_releaseapplock`を実行し、排他区間を終える。

同じJavaScriptオブジェクトや同じ接続設定を使うだけでは、この条件を満たさない。各queryがプールから別の接続を借りる方式では、別セッションの処理やロック解放を、ロックを取得したセッションの処理として扱えない。取得済み接続を途中でプールへ返すと、そのセッションにロックを残したまま他の処理へ貸し出すおそれもある。保持するのは1件の同期操作の排他区間であり、画面を開いている間や利用者のログイン期間全体ではない。

Session所有を選ぶのは、外部HTTPが失敗・中断しても開始監査を残すためである。同期中のSQL書込みは自動コミットにし、APIMへの通信を長いDBトランザクションには包まない。トランザクションで接続固定を代用すると、HTTP失敗時のロールバックで開始監査まで消すなど、現在の意味を変え得る。また、SQLトランザクションはAPIMの変更を一緒にロールバックできない。[SQL applockの所有・解放仕様](https://learn.microsoft.com/en-us/sql/relational-databases/system-stored-procedures/sp-getapplock-transact-sql?view=azuresqldb-current)と、この監査・排他の契約を維持する必要がある。

## 調査したライブラリと移行負担

Redirectと接続保持は別の要件である。Redirect自体がSessionロックを禁止するわけではない。問題は、Redirect対応の接続方式を現行Portalへ組み込む際に、認証と上記の接続保持を維持できるかである。

| 調査対象 | 利用できる機能 | Portalへの適用で残った課題 |
| --- | --- | --- |
| 現行Kysely 0.29.7＋Tedious 19.2.1 | KyselyのMSSQL driverはTediousのConnection・Request・型・transaction APIとTarnのプールを使う。`connection().execute(...)`で接続を確保し、現行のSessionロックと自動コミットを両立できる。 | 標準MSSQL driverはODBCラッパーの単純な依存差替えに対応しない。Tediousのrouting再接続処理だけでは、このPortalのPE Redirect成立を確認したことにならない。 |
| Microsoft ODBC Driver 18 | MicrosoftがPE Redirect対応を明記している。標準`Authentication=ActiveDirectoryMsi`でMI認証を選べる。 | 標準機能の存在と、PortalのACA上での認証・Kysely連携・接続保持の成立は別であり、後者には組込みと受入検証が必要である。 |
| msnodesqlv8 6.2.2などのNode ODBCラッパー | ODBCへ接続文字列を渡し、ODBC自身の標準MI認証を使う候補がある。 | 調査した公開APIでは、PortalがAzure SDKで取得したaccess tokenを接続前のODBC属性へ渡す入口を確認できなかった。driverにMI認証を任せる案なら独自token注入は不要だが、開発用Azure CLI認証と接続寿命を含む変更・検証は残る。 |
| kysely-typeorm 0.3.0＋TypeORM 0.3.28のSQL Server QueryRunner | MSSQLを扱う公開アダプターと、ODBC経路を選ぶ候補が存在する。 | この版の`connect()`は物理接続を取得しない。トランザクション外のqueryはpoolからRequestを生成するため、同じQueryRunnerを保持しても、ロック取得からHTTP処理・解放まで同じ物理接続を確保する契約をそのまま満たせない。 |

現行の[SQL接続設定](../../apps/portal/lib/auth-database.ts)は、新規接続時にAzure SDKでSQL用トークンを取得してTediousへ渡し、TLSの論理サーバー名を検証する。これはPortalの利用者ログインの認証とは別である。ODBCへ移行する場合は、同一接続の確保・解放、切断やtimeout時の扱い、poolの再利用、型・UTC・精度・影響行数・transaction、Portal本体と関連Jobをまとめて確認する必要がある。接続層の追加実装や独自native bindingを採用すれば、その保守も継続して担うことになる。

したがって、「Redirect対応ライブラリがない」「独自C++改修が必須」という理由でProxyを採用したわけではない。MicrosoftのPE仕様では対応driver一覧以外の接続はProxy経由となるため、共有policyをRedirectへ変えて既存Portalを残す案も、Portal自身のRedirectによる改善を保証しない。標準機能と公開アダプターの候補はあるが、現行Portalの契約を保った移行の効果と費用を確認できていないため、共有policyと接続層を維持する判断とした。

## 性能上のトレードオフと再評価条件

Microsoftは、SQL Gatewayを介する通信を減らして遅延とスループットを改善するためにRedirectを推奨している。Proxy維持では、その改善機会をPortalだけでなくBroker・Job・管理clientでも見送る。この選択はProxyの性能優位を実測した結果ではない。今回の比較ではPortalのACA上でのODBC認証・PE Redirect・性能差を実証しておらず、試作の実行環境での失敗もdriverの非対応を示す根拠にはしない。

将来Redirectを再評価する場合は、SQL接続の遅延・スループットが要件上の制約となることを測り、次の条件を揃える。

- Sessionロックの取得・HTTP処理・監査・解放まで、同じ物理接続を保持できる。切断・timeout・解放失敗時の接続処分とpool再利用も確認する。
- ACAのMI、Private Endpoint、TLS検証、開発用認証、Portal・Broker・Job・管理clientの必要な回帰を確認する。
- 同じ条件のProxyとRedirectを比較し、接続層・依存・運用の保守負担に見合う改善を得られる。

再評価時は、新しい接続層をまずProxyで受入し、その後にNSGを準備してSQL policyを切り替える。policyの読戻しだけで合格とせず、新規物理接続の実宛先・ポート・PE限定経路と性能を確認する。現在の採用方針はProxyであり、この条件はRedirect移行の実行計画ではない。

## 仕様とソースの参照

- [Azure SQLの接続アーキテクチャとProxy／Redirect](https://learn.microsoft.com/en-us/azure/azure-sql/database/connectivity-architecture?view=azuresql#connection-policy)
- [Private EndpointのRedirect要件と対応driver](https://learn.microsoft.com/en-us/azure/azure-sql/database/private-endpoint-overview?view=azuresql#use-redirect-connection-policy-with-private-endpoints)
- [ODBCのMicrosoft Entra認証とMI・access token属性](https://learn.microsoft.com/en-us/sql/connect/odbc/using-azure-active-directory?view=sql-server-ver17)
- [Kysely 0.29.7のMSSQL driver](https://github.com/kysely-org/kysely/blob/v0.29.7/src/dialect/mssql/mssql-driver.ts)
- [Kysely 0.29.7の接続固定API](https://github.com/kysely-org/kysely/blob/v0.29.7/src/kysely.ts#L844)
- [kysely-typeorm 0.3.0の接続取得](https://github.com/kysely-org/kysely-typeorm/blob/v0.3.0/src/driver.mts#L13)
- [TypeORM 0.3.28のSQL Server QueryRunner](https://github.com/typeorm/typeorm/blob/0.3.28/src/driver/sqlserver/SqlServerQueryRunner.ts)
- [kysely-typeorm 0.3.0の公開配布](https://registry.npmjs.org/kysely-typeorm/0.3.0)、[msnodesqlv8 6.2.2の公開配布](https://registry.npmjs.org/msnodesqlv8/6.2.2)
