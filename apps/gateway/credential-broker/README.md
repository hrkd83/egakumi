# Credential broker

APIM経由で本人接続を管理する内部FastAPIサービスです。MSALの認可コードフローで本人を確認し、Azure SQLの永続台帳でプール割当・接続・取引・セッションを管理します。業務API用のアクセストークンと更新トークンはCredential Managerが管理します。

初期配備は`azd up`のGateway工程で行います。構築済み環境を個別に更新する場合は、リポジトリのルートから`python3 scripts/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE" --execute`を実行します。既存管理VMでのイメージ作成・非公開ACR登録、credentials DB・CM・実行用ID、SQL初期化、本体と照合ジョブ、APIMのAPI登録まで一括実行します。`--execute`なしはローカル計画だけです。[配備手順](../../../docs/setup/credential-sql.md#4-gatewayを配備する)と[CM共通接続手順](../../../docs/setup/credential-manager-login.md)を参照してください。

## 起動に必要な設定

- `BROKER_PUBLIC_ORIGIN`: GatewayのHTTPS origin。
- `BROKER_SHARED_KEY`: APIMと共有する32バイト以上の専用秘密。Key Vaultの参照から設定します。
- `BROKER_PROFILES_JSON`: 論理プロファイルごとのtenantId、clientId、clientSecretEnv、identityEndpoint、identityIdProperty、poolsのJSON。
- `OIDC_SECRET_0`など: clientSecretEnvが指す秘密の環境変数。
- `AZURE_SUBSCRIPTION_ID`、`AZURE_RESOURCE_GROUP`、`APIM_NAME`、`APIM_PRINCIPAL_ID`、`AZURE_TENANT_ID`、`AZURE_CLIENT_ID`: 接続先APIMとbrokerのマネージドID。
- `AZURE_SQL_SERVER`、`AZURE_SQL_DATABASE`: 暗号化接続する台帳。Azure SQLはbrokerのマネージドIDで認証します。
- `BROKER_TRANSACTION_TTL_SECONDS`: 60〜600、既定300。
- `BROKER_LOGIN_CALLS_PER_MINUTE`: サブスクリプションごとの認証開始回数上限。既定10。`start`と`challenge`の合計をSQLで直近60秒間集計します。

本人セッションの設定は、`gateway.authentication.credentialLogin`から次の環境変数へ渡します。秒単位です。

| 環境変数 | paramsの項目 | 既定値 |
| --- | --- | --- |
| `BROKER_IDLE_TIMEOUT_SECONDS` | `idleTimeoutSeconds` | 604800（7日） |
| `BROKER_ABSOLUTE_LIFETIME_SECONDS` | `absoluteLifetimeSeconds` | 2592000（30日） |
| `BROKER_ROTATION_INTERVAL_SECONDS` | `rotationIntervalSeconds` | 86400（24時間） |
| `BROKER_ROTATION_GRACE_SECONDS` | `rotationGraceSeconds` | 300（旧値の猶予5分） |
| `BROKER_ROTATION_HANDOFF_SECONDS` | `rotationHandoffSeconds` | 600（更新確定まで10分） |
| `BROKER_ROTATION_CANDIDATE_TTL_SECONDS` | `rotationCandidateTtlSeconds` | 600（更新候補10分） |

認証完了時に期限方針をSQLへ保存し、設定変更で既存セッションの最長期限を伸ばしません。無操作期限は本人検証済みの業務APIだけで更新し、最長期限は認証完了から固定します。ポーリング・ヘルスチェック・更新確定では延長しません。

例の構造は次のとおりです。実際のID・秘密値をリポジトリへ保存しません。

```json
{"graph-delegated":{"tenantId":"<TENANT_ID>","clientId":"<CLIENT_ID>","clientSecretEnv":"OIDC_SECRET_0","identityEndpoint":"https://graph.microsoft.com/v1.0/me","identityIdProperty":"id","pools":[{"name":"graph-delegated-a","capacity":8000}]}}
```

SQLスキーマとpool行は、配備用IDで事前に初期化します。アプリ起動時にスキーマの準備状態を確認し、失敗時は起動を停止します。プロセスメモリーを永続台帳の代わりに使うフォールバックはありません。

`sql/001_initial.sql`、`002_session_lifecycle.sql`、`003_idle_connection_cleanup.sql`を番号順に一度ずつ適用します。002は旧取引・旧セッション証明だけを回収し、CM接続とプール・本人対応表を保持します。再配備で適用済み002を繰り返さないため、新方式のセッションは削除しません。本人・接続の失効世代、認証取引に保存した本人・接続の世代スナップショット、現行／旧値／候補のハッシュをSQLで照合します。Microsoftのアクセストークン・更新トークンはSQLに保存しません。003はスキーマ版3として接続の利用日時と回収用のSQL処理を更新します。既存の`LastUsedAt`は巻き戻さず保持します。

## 内部API

`/v1/start`、`/v1/challenge`、`/v1/launch`、`/v1/identity-callback`、`/v1/status`、`/v1/logout`、`/v1/callback-context`、`/v1/complete`は新しいセッション方式でも認証取引の入口として使います。本人セッションの内部入口は次のとおりです。

| 内部API | 処理 |
| --- | --- |
| `/v2/session/authorize` | 購読・本人・接続・期限・失効を確認する。期限を延長しない |
| `/v2/session/use` | APIMがCM／Graphで本人照合した業務利用を再検証し、期限更新と更新候補の発行を行う |
| `/v2/session/commit` | 保存済み候補へ原子的に切り替える。再送でも期限・旧値の猶予を伸ばさない |
| `/v2/session/logout-all` | 確認済み本人の全セッションを失効させる |
| `/v2/session/disconnect` | SQLで利用を禁止してからCM接続を削除する。GET 404の確認後だけ容量を解放する |
| `/v2/connection/status` | CMの既知の認証失効と、一時障害・不明状態を区別する |

これらは`X-Core-Broker-Key`を定数時間で照合し、その後にAPIMから渡された購読IDを検証します。ヘルスチェックは`/health`です。

業務APIから認証を案内するとき、APIMは`POST /v1/challenge`へ`providerName`、`connectionSession`（未指定時は空文字）、`reason`を送ります。`reason`はセッションの再取得を表す`session_required`か、CMでの再認証を必要とする`token_rejected`です。後者は既存セッションの証明を必須とします。

応答は`protocolVersion: 2`、`status: need_login`、`loginUrl`、`transactionId`、`pollSecret`、`statusUrl`、`expiresIn`と、独立した乱数の`provisionalConnectionSession`です。呼び出し元のバックエンドが仮セッションを保存してから、固定Gateway originの認証URLを利用者へ案内します。Microsoftサインインが完了するまで、SQLには一時取引だけを作成し、CMの接続枠は割り当てません。`status`は状態・本人・セッションID・期限だけを返し、セッション値を再発行しません。

有効期限が切れたセッションも、証明を照合できれば元の本人に結び付けた新しい再認証へ進みます。古いセッション自体は延長・復活させません。異なる本人での認証は403です。証明ハッシュは期限切れ後24時間保持し、その間もAPI利用を許可しません。保持期間後に削除されたセッションは403となるため、呼び出し元は既知の期限を過ぎた保存済みセッションを破棄して再取得できます。誤った証明・購読・プロファイルは403、真正でも期限切れ・失効済みなら利用を拒否します。開始回数の上限超過は429と`Retry-After: 60`を返します。

初回も再利用時も、最終的なCM tokenとGraph本人IDの照合はAPIMで行います。`/v1/complete`が期待する本人との一致をSQL内で確認してからセッションを利用可能にします。セッションは購読・論理プロファイル・有効期限に結び付けます。

SQLにある接続がCM側で削除・切断されていた場合は、保存版を照合して再認証状態へ戻し、同じプール・接続名を使って修復します。新規接続と再認証はMicrosoftの接続画面へ進み、本人照合済みの接続はそのまま再利用します。CMの不明な`Error`や一時障害だけを理由に、定期照合が接続消失と決め付けることはありません。

ブラウザーのCookieはSecure・HttpOnly・SameSite=Laxです。起動秘密、OIDC state、callback ticketは一度だけ利用します。鍵・token・Cookie・OAuth queryをアクセスログへ記録しません。起動は`uvicorn credential_broker.app:app --host 0.0.0.0 --port 8000 --no-access-log`です。

## 未使用接続の回収

既存の照合ジョブが毎時17分に、60日以上使われていないCM接続も回収します。新しいジョブやEntra同期は追加しません。`Connections.LastUsedAt`は新規予約時を起点とし、その後は本人照合が成功して許可された業務利用だけで更新します。ログイン・認証完了・照合・ポーリング・値の更新確定では延長しません。SQL時刻との差が60×86400秒以上の接続が対象で、有効なセッションがあるだけでは回収を免れません。

1回の実行は最大100件の試行とし、開始から300秒を過ぎたら新しい試行を受け付けません。各接続は所有者・`rowversion`・300秒のleaseを照合してSQLで回収対象として確保し、`deleting`への変更と古いセッション・候補・途中認証の無効化を先に確定します。有効なleaseや未完了の認証がある接続はスキップします。直前の業務利用や再認証が先に確定した場合は回収を止めます。

CMの個別GETで対象のID・種類・名前を確認し、SQLの確保状態を再検証してから`If-Match`付きDELETEを送ります。有効なETagがあればその値を使います。ETagヘッダー自体がない場合だけ、明示されたSQLの本人・接続・世代の検証が成功すれば`If-Match: *`を使います。空・複数・不正・ワイルドカード値のETagや、検証関数がない場合は、この代替を使いません。新規割当ごとに異なる接続名を使い、遅延した旧DELETEが新しい接続を指さないようにします。既存対応表の接続名は保存値をそのまま使います。CMのGET 404を確認した後だけ、同じ確保状態をSQLで照合して対応表の削除と容量減算を一つのトランザクションで行います。CMの接続状態値は未使用期間の判定元にしません。通信失敗・412・削除結果が不明な場合や削除保留では枠を保持し、次の実行へ持ち越します。`LastCheckedAt`の試行順で少しずつ回収し、特定の失敗接続だけを繰り返しません。利用者が解除を要求済みの`deleting`接続は、60日未満でも同じ手順で再試行します。

この方式へ切り替える際は、旧割当リビジョンと進行中の旧照合ジョブが終了したことを確認し、接続名を再利用する旧処理を残しません。

`reconcile`の既定は読取・集計だけです。定期ジョブは`reconcile --execute`を使い、一時データ回収・状態照合・未使用接続回収を実行します。集約件数だけを記録し、CMトークンや接続の本人情報はログへ出しません。結果の`cmCleanupAttempted`は削除確認用のcallbackを開始した論理的な試行を表し、HTTP DELETEを実際に送った証拠ではありません。先行GETで404を確認した場合や、不正ETag・SQL検証不成立で止めた場合も、試行として数えます。

## ローカル検証

`requirements.txt`の依存関係を導入し、このディレクトリで次を実行します。実SQLの接続変数が未設定なら、そのケースだけをスキップします。通常の単体テストはEntra・CMへ接続しません。

```sh
PYTHONPATH=. python -B -m unittest discover -s tests -v
```

実RSA署名によるID token検証、MSALによるstate・nonce・PKCE生成、期限、更新応答の消失、並行更新、全失効、接続解除を検証します。未使用接続回収は、60日の境界、利用・認証・leaseとの競合、条件付きCM削除、404後の容量解放と再試行を制御した応答で確認します。偽cursorはSQL条件・引数・処理順を確認するもので、SQL Serverの行ロックや分離を実行した結果ではありません。

実SQLテストは専用のローカルDBで行います。DB名は`egakumi_broker_test_`で始まるものだけを許可し、各ケースで`broker`のデータを消去します。空の専用DBへ`sql/001_initial.sql`、`sql/002_session_lifecycle.sql`、`sql/003_idle_connection_cleanup.sql`を順に適用し、接続文字列を保護された環境変数`BROKER_SQL_TEST_CONNECTION`へ外部から設定してください。秘密の値をコマンド例・Gitへ記載しません。

```sh
PYTHONPATH=. python -B -m unittest discover -s tests -p 'test_session_sql.py' -v
```

このテストは実トランザクションで期限境界・更新競合・失効・ロック待ちと、未使用接続回収と業務利用・再認証の競合を確認します。接続変数が未設定のSKIPを実SQLでのPASSへ換算しません。Azure上の権限とAPIMを通るMicrosoftサインインは、配備後の確認として分けます。
