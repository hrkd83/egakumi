# Portalのローカル認証テスト

証明書方式の新構成では、専用の開発用Entraアプリと登録済み公開証明書、対応する保護certificate stateを先に準備します。ローカル署名だけAzureCliCredentialを使い、Azure側KV tenantを指定します。KV Private Endpointへ到達できる端末・DNS/RBACが必要です。認証Entra tenantとAzure tenantを混同しません。以下の共用secret手順は切替前の旧構成用です。[新構成の登録](../setup/09-egakumi-portal.md)と[認証試験](portal-authentication-tests.md)を参照してください。

Microsoft Entra IDへのログイン、ローカルSQL Serverへの本人・セッション保存、Tool GatewayのGraph本人接続を確認します。正式な[PortalのEntra登録](../setup/09-egakumi-portal.md)には公開URLだけを記載し、以下のlocalhost設定は開発用だけに追加します。

Azure配備の前にDBの動作だけ確認する場合は、[SQL統合試験だけを実行する](#sql統合試験だけを実行する)を使います。

## 1. 開発用Entraの戻り先

使用するparamsの`portalAuthentication.credentialProviderName`が参照するEntraアプリの「認証」→「Web」で、既存の公開用戻り先を残したまま追加します。[公開用の戻り先](../setup/03-entra-tools.md#cmのweb戻り先を確認する)とローカル開発用の戻り先を区別します。

```text
http://localhost:3000/api/auth/callback/microsoft
```

`openid profile email`で本人確認します。既存の有効なclient secretをサーバー側で共用し、再発行やGraph権限の追加はしません。

## 2. ローカル設定の準備

リポジトリ直下で、[第01章](../setup/01-prerequisites.md#1-5-環境別のローカル領域を準備する)の`DEPLOY_PARAMS_FILE`と絶対パスの`DEPLOY_LOCAL_DIRECTORY`を設定して、まず変更予定を確認します。計画だけではファイル作成、秘密の読み取り、Azureへの操作は行いません。

```sh
python3 scripts/prepare_portal_local_auth.py --config "$DEPLOY_PARAMS_FILE"
```

`--execute`を付けて実行すると、指定したparamsが参照する既存Entraシークレットを使い、以下のGit管理外ファイルを保護して保存します。元のparamsは変更しません。設定が一致する既存ファイルは再利用し、上書きしません。

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

`db:bootstrap-local`は、ローカルに`egakumi_portal`データベースと未適用のテーブル定義を用意し、`portal_app`へ用途別の実行権限を付与します。Graphの保存表は`010_connection_sessions.sql`で追加し、実行用ユーザーにはSELECT・INSERT・UPDATEだけを付与します。DDL権限は与えません。再実行しても既存データは削除しません。Azure SQLは対象外です。Node.js 24を使い、`node_modules`は実行するOS側で`npm ci`してください。

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

Portalログインと、Graph用の`X-Core-Connection-Session`は別です。Portalへのログインだけでは、Graphの本人接続を作成しません。

```sh
npm --prefix apps/portal test
npm --prefix apps/portal run lint
python3 scripts/run_portal_local.py --config "$DEPLOY_PARAMS_FILE" -- npm run build
npm --prefix apps/portal audit
```

## 5. Tool GatewayのGraph確認

Gatewayには本人接続v2とGraph APIを配備しておきます。実APIの選択項目は`apps/api-gateway/apis/graph-profile-v1.json`の固定`$select`で、氏名・所属・連絡先・アカウント状態など本人の12属性です。変更を反映したGateway→Portalの順に配備します。ローカル画面の確認だけならAzure側のPortal配備は不要です。

同じ環境の`portal/runtime.env`にサーバー専用の`PORTAL_GATEWAY_URL=https://<GATEWAY_HOST>`を設定します。購読の参照にはAPIキー管理と同じAPIM接続・Entraアプリ権限を使用します。必要な所属グループ・製品承認・既存購読のローカル台帳登録は[Portal配備手順](../setup/12-deploy-portal.md#ローカルsqlへの既存購読登録)に従います。ローカルとAzureのSQLは同期しません。

1. 上のlauncherから`--admin -- npm run db:bootstrap-local`で未適用の010と実行権限を準備し、管理者設定なしの`-- npm run dev`を起動する。
2. `/ja/tools`のRESTタブでMicrosoft Graph /meカードを開く。自分のグループ所有で、Graph APIを含む有効な購読を選ぶ。
3. 「自分のユーザー情報を取得」で、先にGraph APIが呼ばれることを確認する。接続がなければMicrosoft認証のリンクが表示される。
4. 別タブのMicrosoft認証でPortalと同じアカウントを使用する。Portalへ戻ると完了確認後、本人のユーザー情報を自動で一度取得する。
5. 表示名・名・姓・UPN・オブジェクトID・役職・会社・部署・従業員ID・勤務先所在地・メール・アカウントの有効状態と実応答JSONを確認する。null・未返却は「－」、有効状態のfalseは「無効」と表示する。APIキー値・pollSecret・本人接続セッションはブラウザーの応答に含まれない。
6. 続けて実行すると、期限内ならMicrosoft認証画面を開かず応答する。認証待ちでページを離れる・キーを変える・タブを隠すと不要な完了確認を続けない。

認証用URLは最長10分で期限切れになり、画面から再実行します。違う本人で認証した場合は不一致として停止し、その本人の応答を表示しません。認証条件や権限による拒否を、繰り返しの自動ログインに変換しません。

Microsoftトークンの保管・更新はCM、Portalが送る本人接続セッションと短命な認証取引はローカルSQLの暗号文に分担します。暗号鍵は`BETTER_AUTH_SECRET`から用途別に導出するため、この値の変更後は管理者が既存の接続状態を初期化してから再認証する必要があります。画面の再実行だけでは復旧しません。Portalログインの8時間とGraph本人接続の7日／30日は別の期限です。

上記の手順はローカル確認用です。ローカルで成功しても、Azure側のGateway・Portalが同じ資材へ更新済みであることや、Azureでの通し試験が済んだことは示しません。対象環境の配備結果と実取得を別途確認してください。

## 停止・後片付け

Next.jsを起動した端末で`Ctrl+C`を押し、SQLを停止します。停止だけならデータは残ります。

```sh
PORTAL_LOCAL_SQL_ENV_FILE="$DEPLOY_LOCAL_DIRECTORY/secrets/portal-local-sql-admin.env" \
  docker compose --env-file "$DEPLOY_LOCAL_DIRECTORY/secrets/portal-local-sql-admin.env" \
  -f apps/portal/compose.local.yml stop
```

ローカル開発を終了して戻り先が不要になったら、開発用Entraアプリから追加したlocalhostの戻り先だけを削除します。既存の公開用戻り先や有効なシークレットは残します。データを破棄する操作は自動では行いません。

Azure用の`sqldb-<環境>-<製品>-portal`は、[Portal配備コマンド](../setup/12-deploy-portal.md)が共通SQLサーバー上に作成します。同じ配備処理が手動SQL初期化ジョブを起動し、テーブルと実行用IDの権限を準備してからアプリを更新します。この配備は構築済みのCoreを再利用します。ローカルのDB・`portal/runtime.env`・管理者資格情報をAzureへコピーしません。
