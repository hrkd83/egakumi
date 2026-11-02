# Portalのローカル認証テスト

Microsoft Entra IDへのログイン、ローカルSQL Serverへの本人・セッション保存、Tool GatewayのGraph本人接続を確認します。正式な[Entra事前準備](../setup/02-entra.md)には公開URLだけを記載し、以下のlocalhost設定は開発用だけに追加します。

## 1. 開発用Entraの戻り先

使用するparamsの`portalAuthentication.credentialProviderName`が参照するEntraアプリの「認証」→「Web」で、既存の公開用戻り先を残したまま追加します。[公開用の戻り先](../setup/02-entra.md)とローカル開発用の戻り先を区別します。

```text
http://localhost:3000/api/auth/callback/microsoft
```

`openid profile email`で本人確認します。既存の有効なclient secretをサーバー側で共用し、再発行やGraph権限の追加はしません。

## 2. ローカル設定の準備

リポジトリ直下で、まず変更予定を確認します。ファイル作成、秘密の読み取り、Azureへの操作は行いません。

```sh
python3 scripts/prepare_portal_local_auth.py --config 'params/<environment>.local.json'
```

`--execute`を付けて実行すると、指定したparamsが参照する既存Entraシークレットを使い、以下のGit管理外ファイルを保護して保存します。元のparamsは変更しません。設定が一致する既存ファイルは再利用し、上書きしません。

- `apps/portal/.env.local`: Next.jsサーバー用。Entra設定、Cookie署名鍵、SQL実行用ユーザーの資格情報。
- `secrets/portal-local-sql-admin.env`: SQL初期化用。管理者資格情報とDeveloper版の起動設定。Next.jsには読み込ませません。

SQL Server Developerは開発・テスト専用で、本番業務に使用できません。[Microsoftの説明](https://learn.microsoft.com/en-us/sql/sql-server/editions-and-components-of-sql-server-2022?view=sql-server-ver16#sql-server-editions)とライセンスを確認し、同意した場合だけ実行します。

```sh
python3 scripts/prepare_portal_local_auth.py \
  --config 'params/<environment>.local.json' --execute --accept-sql-eula
```

Windowsでは`python3`の代わりに利用中のPythonを指定できます。ファイルは実際に開発する本人のアカウントで作成します。秘密値を表示・チャット貼付・`NEXT_PUBLIC_`環境変数へ設定しないでください。

## 3. SQLとアプリの起動

Docker EngineとComposeを利用できる開発環境で実行します。WindowsでWSL内のDockerを使う場合は、リポジトリと開発用設定を参照できる同じ環境から実行します。SQLは`127.0.0.1:14330`だけで待ち受け、専用の永続ボリュームと2GBのメモリ上限を使います。

```sh
cd apps/portal
docker compose -f compose.local.yml up -d --wait
npm ci
npm run db:bootstrap-local
npm run dev
```

`db:bootstrap-local`は、ローカルに`egakumi_portal`データベースと未適用のテーブル定義を用意し、`portal_app`へ用途別の実行権限を付与します。Graphの保存表は`010_connection_sessions.sql`で追加し、実行用ユーザーにはSELECT・INSERT・UPDATEだけを付与します。DDL権限は与えません。再実行しても既存データは削除しません。Azure SQLは対象外です。Node.js 24を使い、`node_modules`は実行するOS側で`npm ci`してください。

ブラウザーで`http://localhost:3000/ja/models`を開きます。未ログインならログイン画面へ移動します。「Microsoftでサインイン」から認証するとカタログへ戻り、右上に本人の名前が表示されます。英語は`/en/models`です。

## 4. 確認する動き

- 未ログインでカタログを直接開くとログイン画面へ移動する。
- Microsoft認証後は本人としてカタログを開ける。リロードしてもログインが継続する。
- ログアウトするとカタログを再び保護する。
- セッションの有効期間は8時間。CookieはHttpOnlyで、公開HTTPSではSecureも付く。
- SQLには本人の`tid + oid`、アカウント対応、セッションを保存する。OAuthトークンは保存しない。

Portalログインと、Graph用の`X-Core-Connection-Session`は別です。Portalへのログインだけでは、Graphの本人接続を作成しません。

```sh
npm test
npm run lint
npm run build
npm audit
```

## 5. Tool GatewayのGraph確認

Gatewayには本人接続v2とGraph APIを配備しておきます。実APIの選択項目は`apps/gateway/apim/apis/graph-profile-v1.json`の固定`$select`で、氏名・所属・連絡先・アカウント状態など本人の12属性です。変更を反映したGateway→Portalの順に配備します。ローカル画面の確認だけならAzure側のPortal配備は不要です。

`.env.local`にサーバー専用の`PORTAL_GATEWAY_URL=https://<GATEWAY_HOST>`を設定します。購読の参照にはAPIキー管理と同じAPIM接続・Entraアプリ権限を使用します。必要な所属グループ・製品承認・既存購読のローカル台帳登録は[Portal配備手順](../setup/portal.md#ローカルsqlへの既存購読登録)に従います。ローカルとAzureのSQLは同期しません。

1. `npm run db:bootstrap-local`で未適用の010と実行権限を準備してから`npm run dev`を起動する。
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
docker compose -f compose.local.yml stop
```

ローカル開発を終了して戻り先が不要になったら、開発用Entraアプリから追加したlocalhostの戻り先だけを削除します。既存の公開用戻り先や有効なシークレットは残します。データを破棄する操作は自動では行いません。

Azure用の`sqldb-<環境>-<製品>-portal`は、[Portal配備コマンド](../setup/portal.md)が共通SQLサーバー上に作成します。同じ配備処理が手動SQL初期化ジョブを起動し、テーブルと実行用IDの権限を準備してからアプリを更新します。構築済みのコア基盤に対し、Portal配備のための`azd up`再実行は不要です。ローカルのDB・`.env.local`・管理者資格情報をAzureへコピーしません。
