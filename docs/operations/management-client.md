# 管理VMからの保守操作

[運用手順](README.md) · [担当者本人の接続](management-vm-access.md)

## 管理VMから保守データを取得する

保守の責任者は、担当者本人の Microsoft Entra Object ID に、作業の開始前に必要な権限を手動で付与し、終了時に回収します。作業票に対象・操作・開始／終了時刻・付与担当・回収担当を記録します。手動のAzure RBAC割当は期限で自動削除されません。PIMが利用できる場合は任意で使えます。

初回構築では、監査用のPE・DNS、管理VM上のクライアントと対象一覧、担当者本人向けのACA保守ロール定義を準備します。Gateway・Portalの配備時には、実際に構築したApp・Job・DBを対象一覧へ追加します。

### 保守の付与・回収

1. [管理VMへの本人接続](management-vm-access.md)を参照し、対象VMへ一時的に `Virtual Machine User Login` を本人へ付与します。クライアントを使う場合、OS管理者は本人を一時的に `core-maintenance` へ追加します。
2. 次のうち作業に必要なロールだけを付与し、新規の割当IDを保護記録へ保存します。SQLの場合は [DB単位の付与・回収手順](management-sql-access.md) を使います。
3. 本人で接続し、許可された対象と必要な操作を確認します。SQLの作業後はSQLセッションを閉じ、DBAが回収し、本人がVMにいる間に対象DBへの新規接続拒否を確かめます。
4. 残る保守ロールを回収して新規操作の拒否を確認し、SSH・ログ・execのセッションを終了します。ゲストの本人用CLIで `az logout` を実行し、OSグループとVMログイン割当を回収した後、新規SSH接続の拒否を確認します。直接・継承された権限と、セッション・トークンが残り得る時間を作業票に記録します。

| 操作 | ロールと割当スコープ | 権限の範囲 |
| --- | --- | --- |
| 監査Blob | `Storage Blob Data Reader` ／ 対象コンテナー | 指定Blobの読取 |
| 保存ログ | `Log Analytics Data Reader` ／ 対象Workspace | Workspaceのログ照会 |
| ACA Appログ | `appLogs` ／ 対象App | App・revision・replica、Appトークン、console・systemログ |
| ACA App exec | `appLogs` と `appExec` ／ 対象App | 対象コンテナーでのコマンド実行。参照できる秘密とAppのIDで利用できる接続先にも影響する |
| ACA環境ログ | `environmentLogs` ／ 対象ACA環境 | 環境情報とDev APIトークン。環境トークンはログ取得以外にも使える |
| ACA Jobログ | `jobLogs` ／ 対象Job | Jobと子資源の参照、Jobトークン、実行ログ |

ACAロール定義のIDは基盤の配備出力 `managementMaintenance.roleDefinitionIds`、対象のResource IDは配備出力から取得します。既存割当と継承を確認してから、権限管理者が手動割当を行います。以下のIDはプレースホルダーです。

```sh
az role assignment create --assignee-object-id '<OPERATOR_OBJECT_ID>' \
  --assignee-principal-type User --role '<ROLE_DEFINITION_ID>' \
  --scope '<EXACT_RESOURCE_ID>' --query id --output tsv

az role assignment delete --ids '<RECORDED_ASSIGNMENT_ID>'
```

空環境から構築した構成では、公開TLSのDNS更新・Key Vault操作とPortalの署名証明書を[専用ACA Job](certificate-jobs.md)が担当します。Gateway・Portalのイメージビルド中は、管理VMのIDへ一時的に `AcrPush` が追加されるため、担当者のシェル保守時間をビルドと重ねません。SSH権限を回収した後、本人の既存セッションを終了し、ローカルの公開鍵でも再接続できないことを実環境で確認します。

### 保護入出力とSQLを含む8種類の操作

[管理クライアント](../../scripts/operations/management_client.py) と対象一覧は `/opt/core/maintenance/` にroot所有で配置されます。担当者はVM内で本人のAzure CLIセッションを作り、`sudo`を付けずに実行します。入出力先は本人所有の700ディレクトリ、入力ファイルは600にします。チェックアウト内ではルートの `.local/<environment>/` を使います。出力は未作成のファイルを指定し、本文を端末や公開ログへ転記しません。

ブラウザーを使えない管理VMでは、組織がデバイスコード認証を許可している場合に以下のように本人でサインインします。認証コードは保護された接続で扱います。本人のCLIプロファイルは共有せず、保守クライアントの実行中にアカウントを切り替えません。条件付きアクセスで制限される場合は、組織で認められた本人認証を確認します。クライアントはAzure CLIの現在のテナントと本人のMicrosoft Graph Object IDを確認するため、ゲストからMicrosoft Graphへの既存Entra経路と、Azure CLIが本人プロファイルを取得できることも受入で確かめます。 [Azure CLIで本人のObject IDを確認する手順](https://learn.microsoft.com/en-us/entra/identity/users/how-to-get-signed-in-identity)

```sh
az login --tenant '<AZURE_TENANT_ID>' --allow-no-subscriptions --use-device-code --output none
```

```sh
/opt/core/maintenance/venv/bin/python -I -B /opt/core/maintenance/management_client.py \
  --operator-object-id '<OPERATOR_OBJECT_ID>' \
  --output "$MAINTENANCE_OUTPUT_DIRECTORY/blob.bin" \
  blob-read --resource-id '<AUDIT_STORAGE_RESOURCE_ID>' \
  --container '<CONSTRUCTED_CONTAINER_NAME>' --blob '<BLOB_NAME>'
```

ほかの操作では、毎回新しい `--output` を指定し、サブコマンドと追加引数を選びます。

| 操作 | サブコマンドと追加引数 | 選択・確認事項 |
| --- | --- | --- |
| 監査Blobの取得 | `blob-read --resource-id '<AUDIT_STORAGE_RESOURCE_ID>' --container '<CONSTRUCTED_CONTAINER_NAME>' --blob '<BLOB_NAME>'` | inventory内のStorageとコンテナーだけ。Blob PE/DNS到達と読取権限を確認 |
| 保存ログの照会 | `workspace-query --resource-id '<WORKSPACE_RESOURCE_ID>' --query-file "$MAINTENANCE_INPUT_DIRECTORY/query.kql"` | KQLは保護ファイルで渡す。本文を引数へ直書きしない |
| App consoleログ | `app-logs --resource-id '<APP_RESOURCE_ID>' --type console --revision '<REVISION_NAME>' --replica '<REPLICA_NAME>' --container '<CONTAINER_NAME>' --tail 50` | revision・replica・containerをすべて明示する |
| App systemログ | `app-logs --resource-id '<APP_RESOURCE_ID>' --type system --tail 50` | console用のrevision・replica・containerは指定しない |
| ACA環境systemログ | `environment-logs --resource-id '<ENVIRONMENT_RESOURCE_ID>' --tail 50` | App単位のsystemログと区別する。環境tokenで取得 |
| Job実行ログ | `job-logs --resource-id '<JOB_RESOURCE_ID>' --execution '<EXECUTION_NAME>' --replica '<REPLICA_NAME>' --container '<CONTAINER_NAME>' --tail 50` | 過去の実行を明示し、Jobを起動せずに読む |
| Appへの限定exec | `app-exec --resource-id '<APP_RESOURCE_ID>' --revision '<REVISION_NAME>' --replica '<REPLICA_NAME>' --container '<CONTAINER_NAME>' --command-file "$MAINTENANCE_INPUT_DIRECTORY/command.txt" --seconds 60` | 明示したコンテナーへ、保護コマンドファイルの内容だけを送る |
| SQL直接保守 | `sql-query --database credentials --query-file "$MAINTENANCE_INPUT_DIRECTORY/maintenance.sql"` | `credentials`または`portal`の構築DBだけを選択。SQL本文は保護ファイルへ保存 |

SQLは担当者本人のEntra IDで認証し、構築サーバーのFQDNへTCP1433で接続します。固定ODBC Driver 18で暗号化とサーバー証明書検証を維持します。SQL本文・結果・失敗時の詳細は保護ファイルへ保存し、tokenは保存しません。更新SQLの実行と結果保存は別の段階です。commit後に結果保存が失敗した場合も、更新を未実行と決めつけて再送しません。

ログ取得は`follow=false`の有限tailで、`--tail`は0〜300です。JobログはPreview APIを使い、client内でmetadata用とreplica／token用のAPI版を固定しています。実環境での提供状況・認可・実行ログ保持を確認するまでは取得成功を保証しません。

execの`command.txt`には、目的が明確で短時間で終了するコマンドだけを保護保存します。最初の確認なら`pwd`などを選び、対話shell、長時間の待受け、無制限な追尾、環境変数や秘密の一括表示を避けます。`--seconds`は1〜600で、既定60秒です。この値が制限するのはclientの取得待機時間であり、切断時にリモートコマンドが必ず終了するという保証ではありません。protocol上、正常切断でもコマンドの終了コードは確認できません。保存出力と対象の事後状態を確認し、応答不明の更新コマンドを無条件で再送しません。

### 実環境の確認結果を記録する

8操作それぞれについて、対象、権限scope、ネットワーク到達、取得内容、必要なら拒否条件を保護証跡へ記録します。合成入力でのローカル試験と担当者本人による実環境の結果は分けて記録します。
