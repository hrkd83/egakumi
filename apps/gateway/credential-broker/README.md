# Credential broker

APIM経由で本人接続を管理する内部FastAPIサービスです。MSALの認可コードフローで本人を確認し、Azure SQLの永続台帳でプール割当・接続・取引・セッションを管理します。業務API用のアクセストークンと更新トークンはCredential Managerが管理します。

初期配備は`azd up`のGateway工程で行います。構築済み環境を個別に更新する場合は、リポジトリのルートから`python3 scripts/deploy_gateway.py --config params/<environment>.local.json --execute`を実行します。既存管理VMでのイメージ作成・非公開ACR登録、credentials DB・CM・実行用ID、SQL初期化、本体と照合ジョブ、APIMのAPI登録まで一括実行します。`--execute`なしはローカル計画だけです。[配備手順](../../../docs/setup/credential-sql.md#4-gatewayを配備する)と[CM共通接続手順](../../../docs/setup/credential-manager-login.md)を参照してください。

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

`sql/001_initial.sql`、`002_session_lifecycle.sql`を番号順に一度ずつ適用します。002は旧取引・旧セッション証明だけを回収し、CM接続とプール・本人対応表を保持します。再配備で適用済み002を繰り返さないため、新方式のセッションは削除しません。本人・接続の失効世代、認証取引に保存した本人・接続の世代スナップショット、現行／旧値／候補のハッシュをSQLで照合します。Microsoftのアクセストークン・更新トークンはSQLに保存しません。

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

SQLにある接続がCM側で削除・切断されていた場合は、保存版を照合して再認証状態へ戻し、同じプール・接続名を使って修復します。新規接続と再認証はMicrosoftの接続画面へ進み、本人照合済みの接続はそのまま再利用します。CMの不明な`Error`や一時障害で、定期照合が本人の接続を失効させることはありません。

ブラウザーのCookieはSecure・HttpOnly・SameSite=Laxです。起動秘密、OIDC state、callback ticketは一度だけ利用します。鍵・token・Cookie・OAuth queryをアクセスログへ記録しません。起動は`uvicorn credential_broker.app:app --host 0.0.0.0 --port 8000 --no-access-log`です。

## ローカル検証

`requirements.txt`の依存関係を導入し、このディレクトリで次を実行します。実SQLの接続変数が未設定なら、そのケースだけをスキップします。通常の単体テストはEntra・CMへ接続しません。

```sh
PYTHONPATH=. python -m unittest discover -s tests -v
```

実RSA署名によるID token検証、MSALによるstate・nonce・PKCE生成、期限、更新応答の消失、並行更新、全失効、接続解除を検証します。

実SQLテストは専用のローカルDBで行います。DB名は`egakumi_broker_test_`で始まるものだけを許可し、各ケースで`broker`のデータを消去します。空の専用DBへ`sql/001_initial.sql`と`sql/002_session_lifecycle.sql`を順に適用し、接続文字列を保護された環境変数`BROKER_SQL_TEST_CONNECTION`へ外部から設定してください。秘密の値をコマンド例・Gitへ記載しません。

```sh
PYTHONPATH=. python -m unittest discover -s tests -p 'test_session_sql.py' -v
```

このテストは実トランザクションで期限境界・更新競合・失効・ロック待ちを確認します。Azure上の権限とAPIMを通るMicrosoftサインインは、配備後の確認として分けます。
