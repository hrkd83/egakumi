# PortalのSQL接続方式：標準APIの調査と比較

状態：公式仕様、現行Portal、固定版の公開配布ソースを調査済み。標準ODBC構成のLinuxコンテナ確認はビルド環境の失敗で未完了。ACAでのManaged Identity認証、実SQL接続、PE Redirect、性能比較は未実証です。正式Portal・Azure・SQL policyは変更していません。独自native改修の未完成試作は保護領域へ退避し、正式採用していません。

## 調査の結論と現時点の推奨

現時点では、稼働中のProxy構成を維持することを推奨します。理由は「RedirectやManaged Identityに対応する標準ライブラリが存在しない」ためではなく、現在のPortalを標準の接続機構へ置き換えて、既存の接続固定・認証・型・終了処理を維持できる構成をまだ受入していないためです。独自C++追加を必須・推奨とした前提は撤回します。

ODBC18はPE RedirectとManaged Identity認証を標準で扱います。NodeラッパーからODBC自身へトークン取得を任せる候補があります。一方で、現在のKyselyの標準MSSQL DriverはTedious専用であり、ODBCラッパーの単純な依存入替ではPortal全体を移行できません。C++を改修しない候補でも、Kysely連携・開発用認証・接続寿命の設計と回帰が必要です。

これは方式判断のための調査報告です。Proxy維持へ構築資材を変更した結果でも、Redirectが不可能と証明した結果でもありません。SQL/NSG資材の変更はローカル統合済みですが、Azureへは未適用です。方式の再評価を実環境の切替へ先行させます。

## トークンの位置付け

現在のPortalは、新規SQL接続時にAzure SDKからSQL用Entraトークンを取得してTediousへ渡します。ACAではManaged Identity、管理用のローカル実行ではAzure CLIの資格情報を使用します。利用者のPortalログインとKV証明書によるclient assertionとは別の認証です。

「Portalが取得済みトークンを渡す」は現在の実装方式であり、Redirectの要件ではありません。ODBCの標準`Authentication=ActiveDirectoryMsi`を使えば、ドライバー自身がManaged Identityのトークンを取得します。この方式にSQLパスワードやEntra client secretを追加する必要はありません。

## 標準ライブラリで確認できたこと

| 対象 | 確認できた事実 | 確認の限界 |
| --- | --- | --- |
| Microsoft ODBC18 | 公式にPE Redirect対応。標準の`ActiveDirectoryMsi`とUAMI指定で認証できる仕様 | 今回のACA・UAMI・SQLでの認証とRedirectは未実証 |
| Tedious 19.2.1（現行）、20.4.1（最新版のソース確認） | routing処理と案内先への再接続がある。現在のPortalはSQL用トークンと論理hostnameのTLS検証設定を渡している | 汎用routing処理の存在だけでPE Redirect成立とは判定できない |
| msnodesqlv8 6.2.2 | 接続文字列をODBCへ渡す標準APIがある。取得済みaccess tokenを接続前属性へ設定する公開APIは確認できない | 外部token注入APIの不足は、ODBC自身のMSI認証が使えないことを意味しない |
| node-odbc 2.6.0、公開fork 2.7.1 | 接続文字列とtimeoutの標準APIがある。確認した型宣言・接続処理に外部token属性を渡す入口はない | これらも標準MSI認証の接続文字列を利用する候補。今回の採用実績ではない |
| node-mssql 12.8.1のmsnodesqlv8経路 | configオブジェクトの`connectionString`で指定したraw ODBC文字列を標準ラッパーへ渡せる | string overloadは別のparse経路。既定driver名はODBC18ではなく、自動生成時は`config.driver`への明示設定とKyselyへの別の連携が必要 |
| Kysely 0.29.7（現行）、0.29.9（最新版）の標準MSSQL Driver | TediousのConnection・Request・型・transaction APIを使用する | ODBCラッパーとの標準の直接差し替え入口ではない |

MicrosoftのPE文書は、対応driver一覧以外からの接続をProxy経由と明記しています。したがって、「TediousがPE Redirectを実証できていない」と「SQL policyをRedirectにするとTediousが接続不能になる」は同じ判断ではありません。現段階で、接続不能になる具体的な原因は特定していません。

ODBC18の標準MSI認証について、公式ACA REST仕様は`IDENTITY_ENDPOINT`、`IDENTITY_HEADER`、`X-IDENTITY-HEADER`、API版`2019-08-01`を定義しています。Microsoft配布のODBC 18.5.1.1と18.7.1.1を公開メタデータのSHA256と照合して静的確認し、これらの文字列が含まれることを確認しました。これは対応の可能性を示す補助情報であり、UAMIの正しい選択・token取得・SQL認証が成功した実証には置き換えません。

## 公開Kyselyアダプターもそのままでは置き換えられない

`kysely-typeorm` 0.3.0にはMSSQL対応があり、placeholderを`@0`から生成する処理、結果行と影響行数の変換、QueryRunnerを使う接続層があります。TypeORMのSQL Server driverには標準のdriver-module差し替え設定があり、`extra.connectionString`からconfigオブジェクトへraw ODBC文字列を渡す候補があります。node-mssqlのstring overloadを同等の伝達経路とは扱いません。これらを組み合わせる候補が存在するため、「既存アダプターが一切ない」とは判断しません。

ただし、固定版TypeORM 0.3.28のSQL Server QueryRunnerでは、`connect()`は物理接続を取得しません。トランザクション外のqueryはpoolへRequestを作ります。現在のPortalの[同期処理](../../apps/portal/lib/subscription-sync/repository.ts)は、`connection().execute()`全体で同じ物理接続を保持し、Session所有のapplockを取得・外部HTTP処理・解放まで維持します。QueryRunnerという同じオブジェクトを使うだけでは、この契約を満たしません。

この公開アダプターを使う案でも、同一物理接続の確保、失敗時の破棄、lock解放、型・UTC・精度、SQLエラー、影響行数、既存transactionの回帰が必要です。HTTP処理までDB transactionへ入れて接続固定を代用する変更も、未判断のまま採用しません。TypeORM等の追加依存も含めて保守範囲を評価します。

最新版TypeORM 1.1.3のSQL Server QueryRunnerも、同じく非transactionのqueryはpoolへRequestを作り、物理接続を取得しないconnect処理でした。最新版へ更新するだけでこの契約を満たすとは確認できません。また、調査したKysely-TypeORMアダプターのpeer条件はTypeORM 0.3系で、1.1.3をそのまま組み合わせられる宣言ではありません。最新版への依存更新を実施した結果ではありません。

## 選択肢と保守負担

| 案 | Portalの経路と認証 | 追加負担と条件 |
| --- | --- | --- |
| 現行Proxyを維持 | Tedious＋Kysely、SDKのSQL token、MI・TLS・PEを維持 | Portalの接続層変更は不要。Redirectによる性能改善を見送る。現時点の推奨 |
| SQL policyはRedirect、Portalは既存driverを維持 | 公式仕様上、PortalはProxy経由となる候補。対応ODBC clientはRedirectを利用する候補 | Portal自身のRedirect目標は達成しない。全clientの実経路とNSG、運用を限定受入する |
| 標準ODBC MSI＋既存Nodeラッパーへ移行 | ODBC自身がtokenを取得。driver本体・C++の独自改修を避ける候補 | ACA/UAMI、Kysely連携、pool・接続固定、App/Jobs、型・transactionの回帰。現在のローカルAzure CLI token経路の代替設計も必要 |
| 外部token属性の独自native追加 | 現行SDK token方式をODBCへ持ち込む | C++ binding、tokenのDBC寿命・zero/free、失敗処理、native build、pool、Kysely層を継続保守。現時点では推奨しない |

Proxy維持で、Managed Identity・SQLのTLS検証・Private Endpoint・Portalログインの証明書方式を失うことはありません。主な差はSQL Gateway経由の通信経路です。MicrosoftはRedirectを遅延とスループットの改善のために推奨していますが、今回のPortalの負荷で得られる改善幅は未計測です。共有policyをProxyに維持すれば、Broker・管理client等のODBC側の改善も見送ります。

## 実行確認の結果と未確認

標準msnodesqlv8とODBC18をNode24/Debian12の独立コンテナで確認しようとしました。build contextは公開用source-only資材に限定し、実入力・checkout全体・私有領域を含めていません。実行時はAzure/実SQL通信を遮断する計画でした。

ビルド中にdpkgが読み取り専用ファイルシステムで失敗し、その後Docker daemonとの通信がEOFとなりました。標準ライブラリのインストール・ロード・合成接続診断を完了していません。この環境失敗を、ODBC・MSI・Redirectの非対応結果へ数えません。共有Dockerの再起動やディスク修復は行っていません。

独自native試作も、コード準備と依存lock生成までで、build・load・合成試験は未実施です。正式Portalへ組み込まず、未完成のまま保護退避しています。

## Redirectを引き続き評価する場合の最小確認

方式を採用する前に、次の具体的な受入計画を提示します。実環境の設定変更を自動で進める指示ではありません。

1. 実行基盤を利用できる状態で、未改変Nodeラッパーと固定ODBC18のロードを確認する。
2. ACAの標準MSI認証で、意図したUAMIを選択してSQL Proxyへ新規物理接続する。token本文を出力・保存せず、TLS検証を維持する。
3. Kysely連携案を選び、同じ物理接続でSession applockの取得から外部HTTP・解放までを保持できること、timeout・切断・解放失敗時にその接続を再利用しないことを確認する。
4. Portal本体とschema/reconciliation Job、既存認証・キー管理・同期処理の回帰を行う。Azure CLIを使う開発経路の変更がある場合は先に明示する。
5. 別判断の限定SQL切替で、policyの読戻しだけでなく、新しい物理接続の案内先・実socket宛先・portとPE限定経路を確認する。同じ条件のProxyと性能比較する。

標準の認証・接続機能で成立しない、既存Portalの契約を大きく変更する必要がある、または保守費用に見合う改善を示せない場合はProxy維持を選びます。業務SQLの自動再送、secret・SQLパスワードへのfallback、TLS検証の無効化で成立させません。

証明書によるPortalログインへの移行はSQL接続policyとは別の作業です。[工程3のAzure適用計画](portal-stage3-azure-acceptance-plan.md)でSQL/NSG等を含まない変更範囲を具体化できれば、SQL driverの選定と分けて進める案を提示できます。

## 根拠と参照先

- [Azure SQL Private EndpointのRedirect仕様](https://learn.microsoft.com/en-us/azure/azure-sql/database/private-endpoint-overview?view=azuresql#use-redirect-connection-policy-with-private-endpoints)
- [ODBCのMicrosoft Entra認証・標準MSI・access token属性](https://learn.microsoft.com/en-us/sql/connect/odbc/using-azure-active-directory?view=sql-server-ver17)
- [ACA Managed IdentityのREST仕様](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity#rest-endpoint-reference)
- [msnodesqlv8 6.2.2](https://registry.npmjs.org/msnodesqlv8/6.2.2)、[node-odbc 2.6.0](https://registry.npmjs.org/odbc/2.6.0)、[ODBC fork 2.7.1](https://registry.npmjs.org/@lukaselmer%2Fodbc/2.7.1)：型宣言と接続前処理を調査
- [node-mssql 12.8.1](https://registry.npmjs.org/mssql/12.8.1)：msnodesqlv8用ConnectionPoolの接続文字列伝達を調査
- [Kysely-TypeORM 0.3.0](https://registry.npmjs.org/kysely-typeorm/0.3.0)、[TypeORM 0.3.28](https://registry.npmjs.org/typeorm/0.3.28)：MSSQL QueryCompiler、Driver、SqlServerDriver、SqlServerQueryRunnerを調査
- 最新版の追加確認：[Kysely 0.29.9](https://registry.npmjs.org/kysely/0.29.9)、[Tedious 20.4.1](https://registry.npmjs.org/tedious/20.4.1)、[TypeORM 1.1.3](https://registry.npmjs.org/typeorm/1.1.3)。配布integrityを照合し、MSSQL Driver、routing、SQL Server QueryRunnerの対象箇所を調査
- 現行Portal：[SQL認証と接続設定](../../apps/portal/lib/auth-database.ts)、[Session applock](../../apps/portal/lib/subscription-sync/repository.ts)、[SQL切替計画](sql-redirect-migration-plan.md)
