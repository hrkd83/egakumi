# 管理VMから本人のEntra IDでSQLを保守する

SQLの保守では、管理VMに接続する担当者のMicrosoft Entra IDを使います。Azure RBACによるVMへのログインとSQL Database内の権限は、別々に付与・回収します。管理画面や共有SQLパスワードの開放は不要です。VM接続の準備は[管理VMの一時アクセス](management-vm-access.md)を参照してください。

作業開始前に、依頼番号、担当者本人のテナントとObject ID、対象のDB、必要なオブジェクトと権限、終了予定時刻、付与・回収を行うDBAを保護された運用記録に残します。DBはGatewayとPortalの配備出力 `managementMaintenance.sqlDatabaseIds` と管理clientの構築済みDB一覧で照合します。別名 `credentials` はCredential BrokerのDB、`portal` はPortalのDBです。両方が必要なら、DBごとに承認、付与、回収を行います。

## 初回構築直後に付与できる状態を用意する

標準構築時のSQL論理サーバーのMicrosoft Entra管理者は、スキーマジョブ用ユーザー割り当てマネージドIDです。対象DBでSQLユーザー作成と対象権限の付与・回収をすでに認められているDBAがいる場合は、そのDBAが実施します。初回構築直後などそのDBAがいない場合は、承認済みのAzure管理者が、担当者とは別のDBAへSQL論理サーバーのEntra管理者を短時間だけ切り替えます。サーバー全体に及ぶ操作のため、付与と回収で必要な時間ごとに切り替え、DBAの作業が済み次第、元のIDを復元します。切り替え中はCoreの再配備とスキーマジョブを伴うGateway・Portalの配備を止めます。

1. Azure管理者は作業対象のSQLサーバーのAzureリソースIDを構築出力と照合します。操作には対象SQLサーバーの参照と更新権限が必要です。以下の取得コマンドで現在の管理者の `login`、`sid`、`principalType`、`tenantId` を確認します。`sid` は構築した `schemaIdentity.principalId`、`principalType` は `Application` と照合します。Entra専用認証が有効であることも確認します。
2. Azure管理者は承認済みDBAの同一テナント内のサインイン名とObject IDを使って、以下の切り替えを実施します。DBAは本人として許可されたネットワークから、SQLの標準FQDN `<SQL_SERVER>.database.windows.net` と対象DB名を指定して接続し、DB名を確認します。管理VMから接続する場合は、DBAにも本人のVMアクセスを作業時間だけ付与します。
3. DBAが付与または回収を行って接続を閉じたら、Azure管理者は構築時のスキーマジョブ用IDを復元します。中断や失敗時も元のIDを復元し、管理者の4項目とEntra専用認証を再取得してから配備・ジョブを再開します。

SQLサーバーの更新APIを使い、`properties.administrators` だけを更新します。以下の形式で切り替え用と復元用のJSONを作り、`.local/<environment>/generated/` の保護ファイルに保存します。切り替え用にはDBAのサインイン名とObject ID、`User` を指定します。復元用には元の管理者から取得した名前とPrincipal ID、`Application` を指定します。テナントIDはどちらも構築時と同じ値を指定します。

```json
{
  "properties": {
    "administrators": {
      "administratorType": "ActiveDirectory",
      "principalType": "<USER_OR_APPLICATION>",
      "login": "<ADMIN_LOGIN_NAME>",
      "sid": "<ADMIN_OBJECT_ID>",
      "tenantId": "<AZURE_TENANT_ID>"
    }
  }
}
```

```bash
sql_server_url="https://management.azure.com${SQL_SERVER_RESOURCE_ID}?api-version=2025-01-01"
az rest --method get --url "$sql_server_url" --query properties.administrators
az sql server ad-only-auth get --subscription "$SQL_SUBSCRIPTION_ID" \
  --resource-group "$SQL_RESOURCE_GROUP" --name "$SQL_SERVER_NAME"
```

切り替え時は `SQL_ADMIN_PATCH_FILE` に切り替え用、復元時は復元用の保護ファイルのパスを指定して以下を実行します。更新を受け付けた後は取得コマンドで期待値への反映を確認します。

```bash
az rest --method patch --url "$sql_server_url" --body "@${SQL_ADMIN_PATCH_FILE}" --output none
```

[SQLサーバー更新API](https://learn.microsoft.com/en-us/rest/api/sql/servers/update?view=rest-sql-2025-01-01) · [Entra専用認証のAzure CLI](https://learn.microsoft.com/cli/azure/sql/server/ad-only-auth#az-sql-server-ad-only-auth-get)

## 必要なテーブル権限を付与する

DBAは、作業前に担当者本人が対象DBへ接続できないことを確認します。既存のSQLユーザーやEntraグループ経由で接続できる場合は、既存権限を確認してから保守作業を開始します。次は承認された1テーブルの読み取りだけを付与する例です。書き込みやストアドプロシージャ実行は、承認された対象だけへSQL権限を個別に付与します。担当者の識別にはユーザーのObject IDから作ったSQL SIDを使います。アプリ・マネージドIDのSQL SIDにはClient IDを使います。[SQLのCREATE USERとEntraのSID仕様](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-user-transact-sql?view=azuresqldb-current#k-create-a-contained-database-user-from-a-microsoft-entra-principal-without-validation)

復元や誤付与を防ぐため、承認されたDB名・作業名・Object IDを作業記録と照合し、同じDBに同名または同SIDのユーザーがすでにいる場合は停止します。変数を設定したSQL本文は運用環境の保護領域で取り扱います。

```sql
SET XACT_ABORT ON;

DECLARE @expected_db sysname = N'<CONSTRUCTED_DB_NAME>';
DECLARE @task_user sysname = N'<WORK_SQL_USER_NAME>';
DECLARE @operator_object_id nvarchar(4000) = N'<OPERATOR_ENTRA_OBJECT_ID>';
DECLARE @schema_name sysname = N'<APPROVED_SCHEMA>';
DECLARE @table_name sysname = N'<APPROVED_TABLE>';
DECLARE @operator_guid uniqueidentifier = TRY_CONVERT(uniqueidentifier, @operator_object_id);
DECLARE @operator_sid binary(16) = CONVERT(binary(16), @operator_guid);
DECLARE @target nvarchar(517) = QUOTENAME(@schema_name) + N'.' + QUOTENAME(@table_name);

IF DB_NAME() <> @expected_db OR DB_NAME() = N'master'
    THROW 51000, 'Connect to the approved application database.', 1;
IF LEN(@operator_object_id) <> 36 OR @operator_guid IS NULL
    OR @operator_sid = 0x00000000000000000000000000000000
    THROW 51000, 'Use the approved operator object ID.', 1;
IF OBJECT_ID(@target, N'U') IS NULL
    THROW 51000, 'The approved table does not exist.', 1;

BEGIN TRANSACTION;
IF EXISTS (SELECT 1 FROM sys.database_principals WHERE name = @task_user OR sid = @operator_sid)
    THROW 51000, 'Review the existing principal before granting access.', 1;

DECLARE @create nvarchar(max) =
    N'CREATE USER ' + QUOTENAME(@task_user) + N' WITH SID = '
    + CONVERT(varchar(34), @operator_sid, 1) + N', TYPE = E;';
EXEC sys.sp_executesql @create;

DECLARE @grant nvarchar(max) =
    N'GRANT SELECT ON OBJECT::' + @target + N' TO ' + QUOTENAME(@task_user) + N';';
EXEC sys.sp_executesql @grant;
COMMIT TRANSACTION;
```

付与後、担当者は管理VMで本人のAzure CLIセッションを用意し、管理クライアントの `sql-query` を本人のEntra IDで実行します。最初の接続で `SELECT DB_NAME(), USER_NAME();` を実行し、対象DB名と作業用SQLユーザー名を確認します。テーブルの読み取り権限は `SELECT TOP (0)` で照合できます。許可したテーブルへの読み取りが成功し、承認外のテーブルへの同じ操作は拒否されることを確認します。他方の構築DBへの権限も付与していない場合は、そちらへの接続拒否を確認します。 [管理クライアントの引数と保護ファイル](../operations.md#保護入出力とsqlを含む8種類の操作)

## 回収して再接続の拒否を確認する

作業が終わったら、担当者はSQLクライアントの接続を閉じます。DBAは同じDB名、作業用SQLユーザー名、本人のObject IDを照合して、その作業のSQLユーザーを削除します。所有オブジェクトなどで回収できなければ、DBAが依存を確認して承認範囲内で解消します。終了予定時刻を過ぎた場合も、回収を作業完了まで遅らせる運用は採りません。

```sql
SET XACT_ABORT ON;

DECLARE @expected_db sysname = N'<CONSTRUCTED_DB_NAME>';
DECLARE @task_user sysname = N'<WORK_SQL_USER_NAME>';
DECLARE @operator_object_id nvarchar(4000) = N'<OPERATOR_ENTRA_OBJECT_ID>';
DECLARE @operator_guid uniqueidentifier = TRY_CONVERT(uniqueidentifier, @operator_object_id);
DECLARE @operator_sid binary(16) = CONVERT(binary(16), @operator_guid);

IF DB_NAME() <> @expected_db OR DB_NAME() = N'master'
    THROW 51000, 'Connect to the approved application database.', 1;
IF LEN(@operator_object_id) <> 36 OR @operator_guid IS NULL
    OR @operator_sid = 0x00000000000000000000000000000000
    THROW 51000, 'Use the approved operator object ID.', 1;

BEGIN TRANSACTION;
IF NOT EXISTS (
    SELECT 1 FROM sys.database_principals WHERE name = @task_user AND sid = @operator_sid AND type = 'E'
)
    THROW 51000, 'Review the expected work principal before removing access.', 1;

DECLARE @drop nvarchar(max) = N'DROP USER ' + QUOTENAME(@task_user) + N';';
EXEC sys.sp_executesql @drop;
COMMIT TRANSACTION;
```

DBAは対象DBに作業用ユーザーと残存セッションがないことを確認します。担当者が閉じられなかった接続が残る場合は、接続先DBと本人のセッションをDBAが確認して、該当セッションだけを終了します。VMログインを回収する前に、回収後の新規SQL接続が拒否されることを確認します。スキーマジョブ用IDを一時的に切り替えた場合は、復元後に正規のスキーマジョブが使えることも検証します。実施時刻と結果は保護された運用記録に残します。[Azure SQLのアクティブセッション終了手順](https://learn.microsoft.com/en-us/azure/azure-sql/database/ransomware-recovery-contain?view=azuresql#action-2-revoke-all-active-sessions-for-suspicious-accounts)

旧資材で管理VMの恒常的なSQLユーザーを作成した環境は、構築済みの各DBとVM IDを照合し、既存環境の切り替え作業としてDBAが回収します。今回の新規構築では同ユーザーを作成しません。[Azure SQLの構築と復旧](../setup/11-deploy-gateway.md)
