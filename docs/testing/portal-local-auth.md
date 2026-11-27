# Portalのローカル認証テスト

専用の開発用Entraアプリと登録済み公開証明書、対応する保護済みの証明書記録を用意します。ローカルの署名にはAzure CLIでサインインした担当者に対象Key Vault鍵の署名権限が必要です。認証テナントと、Key VaultのあるAzureテナントはそれぞれ確認します。 [登録手順](../setup/09-egakumi-portal.md)

このページではローカルのログインとSQLへの認証データの保存を確認します。GatewayのBearer試用とGraph OBOは配備したHTTPS Portalの受入で確認します。[受入手順](../setup/13-verify-deployment.md)

Azure接続なしでSQLの動作だけを確認する場合は、[SQL統合試験だけを実行する](#sql統合試験だけを実行する)を使います。

## 1. 開発用Entraの戻り先

専用の開発用Entraアプリの「認証」→「Web」で、開発端末の戻り先を登録します。

```text
http://localhost:3000/api/auth/callback/microsoft
```

OIDCの `openid profile email` で本人確認します。アプリごとの登録値は開発環境の保護paramsに入力します。

## 2. ローカル設定の準備

リポジトリ直下で、[第01章](../setup/01-prerequisites.md#1-5-環境別のローカル領域を準備する)の`DEPLOY_PARAMS_FILE`と絶対パスの`DEPLOY_LOCAL_DIRECTORY`を設定して、まず変更予定を確認します。計画だけではファイル作成、秘密の読み取り、Azureへの操作は行いません。

```sh
python3 scripts/prepare_portal_local_auth.py --config "$DEPLOY_PARAMS_FILE"
```

`--execute`を付けて実行すると、指定した環境の証明書記録を使い、以下のGit管理外ファイルを保護して保存します。元のparamsは変更しません。設定が一致する既存ファイルは再利用し、上書きしません。

- `.local/<environment>/portal/runtime.env`: Next.jsサーバー用。Entra設定、Cookie署名鍵、SQL実行用ユーザーの資格情報。
- `.local/<environment>/secrets/portal-local-sql-admin.env`: SQL初期化用。管理者資格情報とDeveloper版の起動設定。Next.jsには読み込ませません。

SQL Server Developerは開発・テスト専用で、本番業務に使用できません。[Microsoftの説明](https://learn.microsoft.com/en-us/sql/sql-server/editions-and-components-of-sql-server-2022?view=sql-server-ver16#sql-server-editions)とライセンスを確認し、同意した場合だけ実行します。

```sh
python3 scripts/prepare_portal_local_auth.py \
  --config "$DEPLOY_PARAMS_FILE" --execute --accept-sql-eula
```

Windowsでは`python3`の代わりに利用中のPythonを指定できます。ファイルは実際に開発する本人のアカウントで作成します。`apps/portal/.env.local`は作成せず、設定は環境別の保護ファイルとlauncherで扱います。秘密値を表示・チャット貼付・`NEXT_PUBLIC_`環境変数へ設定しないでください。

## 3. SQLとアプリの起動

Docker EngineとComposeを利用できる開発環境で実行します。WindowsでWSL内のDockerを使う場合は、リポジトリと開発用設定を参照できる同じ環境から実行します。SQLは`127.0.0.1:14330`だけで待ち受け、専用の永続ボリュームと2GBのメモリ上限を使います。

SQL Server 2022のLinuxコンテナーを使う統合試験は、Intel／AMDのx86-64 Linux環境で実行します。Apple Siliconなどでのx86エミュレーションはMicrosoftのサポート対象外です。 [SQL Serverコンテナーの対応環境](https://learn.microsoft.com/en-us/sql/linux/sql-server-linux-docker-container-deployment?view=sql-server-ver16)

```sh
PORTAL_LOCAL_SQL_ENV_FILE="$DEPLOY_LOCAL_DIRECTORY/secrets/portal-local-sql-admin.env" \
  docker compose --env-file "$DEPLOY_LOCAL_DIRECTORY/secrets/portal-local-sql-admin.env" \
  -f apps/portal/compose.local.yml up -d --wait
npm --prefix apps/portal ci
python3 scripts/run_portal_local.py --config "$DEPLOY_PARAMS_FILE" \
  --admin -- npm run db:bootstrap-local
python3 scripts/run_portal_local.py --config "$DEPLOY_PARAMS_FILE" -- npm run dev
```

`run_portal_local.py`は対象環境の保護ファイルを子プロセスの環境へ読み込み、`apps/portal`を作業フォルダーとしてコマンドを実行します。`--admin`はSQL初期化・管理作業にだけ指定し、通常の`npm run dev`へ管理者資格情報を渡しません。

`db:bootstrap-local`は、ローカルに`egakumi_portal`データベースと未適用のテーブル定義を用意し、`portal_app`へ用途別の実行権限を付与します。実行用ユーザーにはログインとAPI認証の各テーブルで必要な操作だけを許可します。DDL権限は与えません。再実行しても既存データは削除しません。Azure SQLは対象外です。Node.js 24を使い、`node_modules`は実行するOS側で`npm ci`してください。

ブラウザーで`http://localhost:3000/ja/models`を開きます。未ログインならログイン画面へ移動します。「Microsoftでサインイン」から認証するとカタログへ戻り、右上に本人の名前が表示されます。英語は`/en/models`です。

### SQL統合試験だけを実行する

Intel／AMDのWindows端末では、DockerとNode.js 24を使えるWSLで `uname -m` が `x86_64` であることを確認します。SQL Serverをループバックの14330番に起動できる状態で、WSLのLinuxファイルシステム上にあるチェックアウトのルートから実行します。試験用の資格情報を使うため、Entra登録・Azure接続は不要です。

SQL Server Developerのライセンスを確認し、同意した場合に実行します。環境ファイルの生成は初回だけ行い、再試験では生成済みの試験専用ファイルを使います。

```sh
PYTHONPATH=scripts python3 -B - <<'PY'
from secrets import token_urlsafe
from deployment.helpers.paths import LocalPaths, REPOSITORY
from prepare_portal_local_auth import admin_settings, write_new_pair

workspace = LocalPaths(REPOSITORY, "portal-sql")
target = workspace.checked(workspace.secrets / "portal-local-sql-admin.env")
runtime = {
    "PORTAL_SQL_SERVER": "127.0.0.1",
    "PORTAL_SQL_HOST": "127.0.0.1",
    "PORTAL_SQL_PORT": "14330",
    "PORTAL_SQL_DATABASE": "egakumi_portal",
    "PORTAL_SQL_USER": "portal_app",
    "PORTAL_SQL_PASSWORD": "aA9_" + token_urlsafe(36),
}
write_new_pair({target: admin_settings(runtime, "aA9_" + token_urlsafe(36))}, REPOSITORY)
PY
```

```sh
(
  set -e
  sql_test_root="$PWD"
  export PORTAL_LOCAL_SQL_ENV_FILE="$sql_test_root/.local/portal-sql/secrets/portal-local-sql-admin.env"
  sql_test_compose() {
    docker compose --project-name egakumi-portal-sql-test --env-file "$PORTAL_LOCAL_SQL_ENV_FILE" \
      -f "$sql_test_root/apps/portal/compose.local.yml" "$@"
  }
  sql_test_compose up -d --wait
  trap 'sql_test_compose stop' EXIT
  npm --prefix apps/portal ci
  cd apps/portal
  NODE_ENV=development node --env-file="$PORTAL_LOCAL_SQL_ENV_FILE" --conditions=react-server --experimental-strip-types \
    scripts/bootstrap-local-sql.ts
  NODE_ENV=development PORTAL_SQL_INTEGRATION_TESTS=1 node --env-file="$PORTAL_LOCAL_SQL_ENV_FILE" \
    --conditions=react-server --experimental-strip-types --test \
    tests/*.sql.test.ts tests/auth-runtime-grants.test.ts
)
```

認証取引の一回消費・切断後の失効、セッション期限、実行用SQLユーザーの権限などを確認します。合否はこのローカルSQL試験の結果として記録し、Azure配備後の受入は[Portalの認証試験](portal-authentication-tests.md#実環境の構築試験)で行います。

## 4. 確認する動き

- 未ログインでカタログを直接開くとログイン画面へ移動する。
- Microsoft認証後は本人としてカタログを開ける。リロードしてもログインが継続する。
- ログアウトするとカタログを再び保護する。
- セッションの有効期間は8時間。CookieはHttpOnlyで、公開HTTPSではSecureも付く。
- SQLには本人の`tid + oid`、アカウント対応、セッションを保存する。OAuthトークンは保存しない。

Portalログイン後に、別のアカウントが同じセッションとして扱われないこと、ログアウト後にセッションが失効することを確認します。API認証用の戻り先と暗号鍵を使う実接続は、対象HTTPS Portalの受入に含めます。

## 停止・後片付け

Next.jsを起動した端末で`Ctrl+C`を押し、SQLを停止します。停止だけならデータは残ります。

```sh
PORTAL_LOCAL_SQL_ENV_FILE="$DEPLOY_LOCAL_DIRECTORY/secrets/portal-local-sql-admin.env" \
  docker compose --env-file "$DEPLOY_LOCAL_DIRECTORY/secrets/portal-local-sql-admin.env" \
  -f apps/portal/compose.local.yml stop
```

ローカル開発を終了して戻り先が不要になったら、開発用Entraアプリから追加したlocalhostの戻り先だけを削除します。使用中の公開戻り先と証明書は保持します。データを破棄する操作は自動では行いません。

Azure用の`sqldb-<環境>-<製品>-portal`は、[Portal配備コマンド](../setup/12-deploy-portal.md)が共通SQLサーバー上に作成します。同じ配備処理が手動SQL初期化ジョブを起動し、テーブルと実行用IDの権限を準備してからアプリを更新します。この配備は構築済みのCoreを再利用します。ローカルのDB・`portal/runtime.env`・管理者資格情報をAzureへコピーしません。
