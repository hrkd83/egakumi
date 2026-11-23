# PortalのODBC18接続方式の成立確認と提案

状態：公開API・配布ソースと現行Portalの静的調査を実施済み。具体部品の選定判断前。native追加対応、Linux/Node24でのビルド、合成SQL接続試作、Azure配備・Redirectの実証は未実施です。Portalの接続コードとDockerfileは変更していません。

## 確認結果

現行Portalは`@azure/identity`で新規接続時にSQL用トークンを取得し、Tediousへ渡しています。Kyselyの標準`MssqlDialect`はTediousのConnection・Request・型定義に依存しているため、ODBC用ライブラリを同じ設定へ渡すだけでは置き換えられません。Portal本体、schema初期化、Graph照合等のSQL利用は共通の`auth-database.ts`を使います。

候補の公開npm配布物は、配布物のSHA-512 integrityを照合してからソースと型定義を確認しました。

| 対象 | 静的に確認した内容 | 現時点の判定 |
| --- | --- | --- |
| `msnodesqlv8` 6.2.2 | Node >=22、Node-APIを使用。公開`ConnectDescription`は`conn_str`と`conn_timeout`。接続前処理には取得済みaccess tokenを設定するAPIがない | 追加対応が必要。Node24は宣言範囲内だが、対象コンテナでの実動作は未検証 |
| `odbc` 2.6.0（node-odbc） | Node >=20。公開`ConnectionParameters`は接続文字列と2種類のtimeout。接続前の属性設定はtimeout用で、取得済みaccess tokenの受渡しAPIがない | 追加対応が必要。単純な依存入替では要件を満たさない |
| ODBC18のtoken認証 | Microsoftの`SQL_COPT_SS_ACCESS_TOKEN`は接続前のポインター属性。tokenと`UID`・`PWD`・`Trusted_Connection`・`Authentication`の併用はエラー | tokenを接続文字列へ追加する代替は使えない |
| `msnodesqlv8`のpool | native ODBC環境が`SQL_CP_ONE_PER_HENV`を設定する | Node側poolだけの交換では、新規物理接続・主体固定を確認できない |
| 現行Kysely接続 | 標準MSSQL DriverがTediousのRequest、パラメーター型、transaction操作を使う | ODBC用Driver層と接続・型・transactionの回帰が必要 |

「ODBC18本体がEntra認証を扱える」ことと、「Node接続部品が取得済みトークンを渡せる」ことは別の条件です。上記2候補を現状のまま選定したとは扱いません。

## 推奨する試作案

SQL Server向けの型・結果・transaction機能を持つ`msnodesqlv8`を第一候補とし、次の追加対応を含む独立試作を提案します。公開依存をそのまま使う案より保守範囲が増えるため、この方式の判断を受けてからnative実装へ進みます。

1. 固定版のnative接続入口へ取得済みtokenを渡す項目を追加します。Node側で取得したtokenを、ODBCの`ACCESSTOKEN`形式にして接続前に設定します。tokenを接続文字列・ログ・例外本文へ含めません。Microsoftの保持条件は「connection handleがallocatedの間」です。`SQLDisconnect`やcallback完了ではbufferを解放せず、`SQLFreeHandle(SQL_HANDLE_DBC)`の完了後にzero/freeする寿命管理を扱います。
2. native ODBCの内部poolを無効にし、PortalのTarn poolだけで接続を所有します。専用コンテナの実効`odbcinst.ini`の`[ODBC]`セクションで`Pooling=No`を起動前に設定し、library側でもNULL HENVへの`SQL_CP_OFF`をHENV割当前に設定・確認します。各poolをDB・SQL用主体・接続先へ固定し、物理接続の作成ごとにAzure SDKからtokenを取得します。複数のpoolや異なるSQL主体の接続が再利用されないことを確認します。
3. KyselyのMSSQL構文・Adapter・Introspectorを維持し、ODBC用Driverを用意します。標準QueryCompilerの`@1`・`@2`をそのまま使わず、compiler subclass等でplaceholderを`?`へ生成変更し、parametersの順序を保持します。SQL文字列の事後置換や値の連結では対応しません。結果はKyselyの`rows`と`numAffectedRows: bigint`の契約へ対応させます。transactionだけでなく、外部HTTP処理を含む`connection().execute()`全体のSession applockも同じ物理接続へ固定します。
4. Azure接続はSQL論理hostname、暗号化、証明書検証を維持します。証明書の検証名を固定し、Redirect先の実宛先がSQL PE限定のNSG・直接経路で成立することを実受入します。ローカルSQL認証は従来の開発用loopback構成だけで扱います。
5. 固定版・整合性検証・再現可能なnativeビルド・差分を管理します。Node24/Debian12用の最小コンテナで先に試し、成立後にPortal本体とschema/reconciliation Jobへ同じ接続層を組み込みます。取得先が増える場合はbuild通信の変更を別途提示します。

この案には依存のnative追加対応と独自Kysely Driverの保守が含まれます。上流へ自動送信する処理や、固定secret・SQLパスワードへの認証変更は含めません。node-odbcを代替にする場合もtoken用native追加が必要で、SQL Serverの型・結果の対応を比較して判断します。

native変更は、固定版の`lib/index.d.ts`、`lib/sql-client.js`・`lib/connection.js`の接続引数、`cpp/src/odbc/odbc_connection.cpp`と対応headerの接続前属性・buffer寿命、`cpp/src/odbc/odbc_environment.cpp`のpool設定、および引数を渡すNode-API層を対象にします。改変差分を管理し、依存取得後・nativeビルド前に適用する方法を第一案とします。配布物全体をPortalソースへ複製せず、版・配布物のintegrity・改変差分・ビルド条件を固定して保守します。

試作はNode24/Debian12の独立Linuxコンテナとし、C++・make・unixODBC開発環境・固定ODBC18をコンテナ内へ用意します。build contextは独立した公開用試作ソースだけに限定し、checkout全体、`.local/`・`.azure/`、実入力を含めません。基底イメージ・依存・取得先と改変差分を固定します。

6.2.2は`prebuilds/*.node`を同梱し、installに`node-gyp-build`を使います。source patchの適用やnpm install成功だけでは改変版のロードを証明できません。from-source rebuildを行い、実ロードされたaddonの改変capabilityと保持・解放イベントを確認します。

## 独立試作で確認する範囲

| 項目 | 判定に必要な確認 |
| --- | --- |
| token API | 合成tokenで接続前属性、禁止した認証設定との併用拒否、ログ非露出を確認。DBCがallocatedの間はbufferを保持し、DBC解放→buffer zero/freeの順序をinstrumentationで確認。実SQL認証成功とは区別 |
| native後始末 | successful openだけでなく、failed-open、timeout、放棄open、GC遅延を確認。未接続・failed-openのclose callbackがsuccessでも、実際のDBC解放・drain成功とは判定しない |
| Linux/Node24 | 固定ODBC18とnative部品を対象コンテナでfrom-source rebuildし、実ロードaddonの改変capabilityを確認。未改変prebuiltのロードを成功扱いにしない |
| native pool | 実効unixODBC設定と、HENV割当前の`SQL_CP_OFF`を両方確認。Node側poolだけの設定で物理接続非再利用を判定しない |
| SQL型と結果 | Unicode、NULL、Boolean、Buffer、BigInt、DateTime2のUTC・精度、影響行数、SQLエラー番号、parameter bindingを確認 |
| 接続制御 | 同一物理接続でtransaction・分離レベル・Session applock・commit/rollbackと、外部HTTP処理を含む接続固定を確認。timeout・切断・rollback/lock解放失敗の接続をpoolへ返さず、destroy完了まで確認 |
| アプリ互換性 | Better Auth、既存セッション・キー管理・業務SQL、schema/reconciliation Jobの回帰 |
| Azure/Redirect | 別の実受入。MI/Azure CLI token、TLS、Proxy下の対応Portal版、新規物理接続のRedirect先・PE内宛先、切戻しを確認 |

標準ライブラリの型宣言、nativeモジュールのロード、合成tokenの属性設定だけで、全条件の成立と判断しません。native接続部品の小さな試作結果を示してから、本体の改修範囲を確定します。

## 旧版へ戻す方法

Driver更新前に、Portal本体とschema/reconciliation Jobの固定イメージ、認証設定、SQL主体・接続先を保護した配備記録へまとめます。試作・Driver更新ではSQL schemaを変更しません。Proxy受入中の復旧は、処理中のSQL要求を終えてpoolとJobを停止し、保存した旧イメージ・設定へ戻して受入します。副作用を伴うSQL要求は自動再送しません。

Redirect切替後の復旧は、SQL側の限定復旧手順でProxyへの復帰を確認してから旧Portal版へ戻します。旧NSG規則の復旧はSQL側の確認順序に従います。通常Core再配備で暗黙にProxyへ戻したり、Driver失敗をsecret・SQLパスワードへのfallbackで回避したりしません。

## 配備とSQL切替の順序

工程3とSQL資材のソース統合はローカルで完了しています。Driverは別変更として選定・実装・検証します。対応PortalイメージをSQL Proxyのまま実受入し、pool・Jobの停止と新規接続の確認を含むruntime verifierを整備した後、限定CLIによるSQL切替を別判断で実施します。既定がRedirectのCore資材を未対応Portalの既存環境へ先行適用しません。

証明書発行とSQL接続方式は異なる処理です。既存環境で証明書準備だけを先行する適用案では、SQLのpolicyとNSGを変更しない範囲を確認します。[工程3のAzure適用計画](portal-stage3-azure-acceptance-plan.md)と[SQL切替計画](sql-redirect-migration-plan.md)で、適用対象と受入順を照合します。

## 根拠

- [Microsoft ODBCのEntra認証と接続属性](https://learn.microsoft.com/en-us/sql/connect/odbc/using-azure-active-directory?view=sql-server-ver17)
- [msnodesqlv8 6.2.2の公開配布情報](https://registry.npmjs.org/msnodesqlv8/6.2.2)：`lib/index.d.ts`、`cpp/src/odbc/odbc_connection.cpp`、`cpp/src/odbc/odbc_environment.cpp`
- [odbc 2.6.0の公開配布情報](https://registry.npmjs.org/odbc/2.6.0)：`lib/odbc.d.ts`、`src/odbc.cpp`
- 現行ソース：[auth-database.ts](../../apps/portal/lib/auth-database.ts)、[Dockerfile](../../apps/portal/Dockerfile)
