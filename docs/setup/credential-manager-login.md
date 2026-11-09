# APIからCredential Managerの本人接続を使う

本人接続セッションv2の契約。Graph等の業務APIへ購読キーで要求し、必要なときだけMicrosoft認証を案内する。有効なセッションを保存していれば、通常の継続利用で認証画面を開かない。Portal自身のBetter Authセッションとは別の仕組みである。

## 保存先と役割

| 情報・処理 | 担当 |
| --- | --- |
| アクセストークン・リフレッシュトークンの保存と更新 | APIM Credential Manager（CM） |
| 本人・購読・CM接続・セッション・期限・失効の対応 | Azure SQLのbroker台帳 |
| Microsoft本人確認、Graph本人照合、接続再利用とプール割当 | APIMと内部credential broker |
| 購読キー・接続セッション・更新候補・短命の取引情報 | 呼び出しアプリのバックエンド |
| 認証URLの表示、完了確認、保存更新、元要求の再開 | 呼び出しアプリ |

本人は検証済みの`tenantId + oid`で識別する。UPN申告だけで本人を確定しない。アプリ自身が検証したログイン利用者と、認証完了応答の`tenantId + ownerId`を比較し、別利用者のセッションを使い回さない。保存先も`Gateway origin + 購読ID + 論理プロファイル + 検証済みtid/oid`で分離する。

## 有効期限と設定

`gateway.authentication.credentialLogin`に設定する。

```json
{
  "providerNames": ["graph-delegated"],
  "transactionLifetimeSeconds": 600,
  "idleTimeoutSeconds": 604800,
  "absoluteLifetimeSeconds": 2592000,
  "rotationIntervalSeconds": 86400,
  "rotationGraceSeconds": 300,
  "rotationHandoffSeconds": 600,
  "rotationCandidateTtlSeconds": 600,
  "loginCallsPerMinute": 10
}
```

| 設定 | 既定値・意味 |
| --- | --- |
| 無操作期限 | 最終の対象業務API利用から7日 |
| 最長期限 | 本人認証完了から30日。利用・日次更新で伸ばさない |
| 値の更新間隔 | 24時間経過後、最初の対象業務API利用で更新候補を発行 |
| 旧値の猶予 | 更新確定から固定5分。並行利用や再試行で延長しない |
| 更新引き渡し猶予 | 更新要求後10分を過ぎると、業務実行前に`SessionUpdateRequired`を返す |
| 候補の期限 | 10分。失われた候補から平文を復元・再取得しない |

実際の期限は`min(最終業務利用 + 無操作期限, 認証完了 + 最長期限)`。SQLで期限・失効を確認してから更新する。期限切れ・失効・改ざんを延長・復活させない。期限設定は認証完了時のセッションへ記録され、設定変更だけで既存セッションの最長期限を変更しない。

対象は、APIMに登録した本人接続用の業務Operationで、セッション検証・CMトークン取得・Graph本人照合を通過した要求。ポーリング・ヘルス・更新確定・ログアウトでは延長しない。業務APIのタイマー呼び出しと人の操作を基盤だけで完全には区別できないため、アプリは延命用の背景通信を行わない。SQL・CMの一時障害は再認証に置き換えない。

## Entraと基盤の準備

[Entraの事前準備](02-entra.md)で同じWebアプリに次を設定する。

- 委任権限：`openid`・`profile`・`email`・`offline_access`・`User.Read`
- CM標準のWeb戻り先
- 本人確認のWeb戻り先：`https://<gateway-host>/auth/connections/browser/identity-callback`
- 有効なclient secret

論理プロファイル`graph-delegated`に対して、初期値は`graph-delegated-a`〜`graph-delegated-l`の12プール、各8,000件。プールと接続名をクライアントに選択させない。[接続台帳とプールの設計](../reference/credential-pool-design.md)を参照する。

`azd up`のGateway工程、または個別の`deploy_gateway.py --execute`で、SQL・Broker・APIMのAPIとポリシーまで配備する。[Gatewayの配備](credential-sql.md)を参照する。組み込みロールを使用し、カスタムロールは作らない。

### 本人確認callbackのWAF調整

自前のWAFを作成する構成では、`/auth/connections/browser/identity-callback`の完全パスに専用ポリシー`wafpol-g-auth-<ID>`を関連付ける。`<ID>`はparamsの`services.apimInstanceId`に保存した構築識別子を使う。認可コードのSQLi誤検知を抑えるため、現行資材は`code`引数の**値**だけをOWASP 3.2の`942430`・`942440`・`942450`から除外する。引数名、`state`、他の引数・URI・規則は除外しない。本文検査、サイズ制限、秘密値のログ除去、追加の企業規則は共通WAFと同じに保つ。

既存の`pathmap-protocols`へ`credential-identity-callback`の規則を追加し、既存APIの経路を保持する。gRPCを併設する場合も、HTTP入口の`trusted-l7-ingress`書き換えをcallbackと既定経路の両方に適用する。初期構築用BicepとGateway配備処理が同じ構成を使用し、Portalの認証callback用ポリシーは変更しない。

企業管理の既存WAFを指定する構成では、そのポリシーを自動で置換・複製しない。管理者が同じcallback・引数値・規則の範囲で調整する。`Standard_v2`にはWAF調整を作成しない。

## 初回認証

1. バックエンドから業務APIへ購読キーを付ける。保存済みの接続セッションは`X-Core-Connection-Session`で送る。
2. 有効な場合は、CMのトークンとGraph本人IDを照合し、そのまま業務APIを実行する。
3. 認証が必要な場合はHTTP409の`authentication`を受け取る。
4. **バックエンドは仮セッションと取引を、呼び出し元の検証済み本人に対応付けて永続保存してから、`loginUrl`だけを利用者へ表示する。** 仮セッションは本人確認完了前には利用できない。
5. 利用者がMicrosoft認証を終える。CM接続と本人の一致確認後に、初めてセッションが有効になる。
6. バックエンドが購読キーと`X-Core-Connection-Poll: <pollSecret>`で`statusUrl`を確認する。完了応答の本人IDをアプリの利用者と照合する。
7. 保存済み仮値を有効な接続セッションとして扱い、取引秘密を保存先から除去する。元の読み取りGETを一度再開する。

```json
{
  "error": { "code": "ConnectionRequired" },
  "authentication": {
    "protocolVersion": 2,
    "status": "need_login",
    "provisionalConnectionSession": "cs2.<SESSION_ID_64HEX>.<SESSION_SECRET_64HEX>",
    "loginUrl": "https://<GATEWAY_HOST>/auth/connections/browser/launch#transactionId=<TRANSACTION_ID>&launch=<LAUNCH_SECRET>",
    "transactionId": "<TRANSACTION_ID>",
    "pollSecret": "<POLL_SECRET>",
    "expiresIn": 600,
    "statusUrl": "https://<GATEWAY_HOST>/auth/connections/status?transactionId=<TRANSACTION_ID>"
  }
}
```

`provisionalConnectionSession`と認証取引の秘密は独立した乱数である。URL・セッション値の接続先を任意のホストへ変更してはいけない。statusの完了応答にはセッション値を含めず、次のメタデータだけを返す。

```json
{
  "status": "complete",
  "protocolVersion": 2,
  "sessionId": "<SESSION_ID_64HEX>",
  "generation": 1,
  "idleExpiresAt": 1794000000,
  "absoluteExpiresAt": 1796000000,
  "nextRotationAt": 1793500000,
  "expiresAt": 1794000000,
  "providerName": "graph-delegated-a",
  "tenantId": "<VERIFIED_TENANT_ID>",
  "ownerId": "<VERIFIED_OBJECT_ID>"
}
```

古い`transactionId`・`pollSecret`から、その後の現行セッション値を取得するAPIは用意しない。仮値の保存前にURLを表示してしまうと、認証完了後に値を取り戻せない。

## 日次の自動更新

業務本文のJSONスキーマは変更しない。バックエンドは次の応答ヘッダーを処理する。

| ヘッダー | 内容 |
| --- | --- |
| `X-Core-Connection-Update` | 次の接続セッション候補値。秘密情報 |
| `X-Core-Connection-Update-Id` | 更新候補ID |
| `X-Core-Connection-Update-From-Generation` | 現在の世代 |
| `X-Core-Connection-Update-Generation` | 確定後の世代 |
| `X-Core-Connection-Update-Expires-At` | 候補の期限（Unix秒） |
| `X-Core-Connection-Generation` | この要求で検証した世代 |
| `X-Core-Connection-Idle-Expires-At`・`Absolute-Expires-At`・`Rotate-After` | 現行の各期限（後二つも同じ`X-Core-Connection-`接頭辞） |

バックエンドは同じ保存キーの処理を共有ロックで直列化し、更新候補を現行値と併せて保存する。**保存に成功してから**次を実行する。

```http
POST /auth/connections/session/commit
api-key: <SUBSCRIPTION_KEY>
X-Core-Connection-Session: <CURRENT_SESSION>
Content-Type: application/json

{"rotationId":"<UPDATE_ID>","candidateSession":"<SAVED_CANDIDATE>","fromGeneration":1}
```

200応答は値を含まないメタデータ。世代と最長期限が期待どおりであることを確認し、現行値を候補へ切り替えて候補を除去する。遅れて返った応答で新しい世代を上書きしない。旧値の猶予は固定であり、旧値から新たな更新候補を作ったり猶予を延長したりしない。

- 確定要求を送った事実も、送信前に保存する。確定応答を失った場合は、次回に保存済み候補をセッションヘッダーにして同じ確定要求を再送する。候補が既に現行なら、その証明で200を再取得できる。旧値の5分猶予後でも、既確定の現行候補から確認できる。
- 候補の証明が403`ConnectionNotAuthorized`なら、未確定の可能性があるため保存済み現行値で確定を試す。
- 現行値で409`SessionRotationExpired`なら、失効していない現行セッションを保持し、候補だけを破棄する。次の業務要求で新しい候補を受け取る。Microsoft再認証には戻さない。
- 409`SessionRotationSuperseded`は共有保存先を読み直す。他の処理が保存した新世代を採用する。不明なまま旧値へ戻したり、認証画面を開いたりしない。
- 成功済み業務の後に更新確定だけが失敗しても、その業務を再実行しない。本文・HTTP状態を保持し、候補を残して次回は確定から再開する。Graphが429/500を返した場合も、付いている更新ヘッダーは処理する。
- `SessionUpdateRequired`は業務実行前の409。この場合だけ更新を完了してから元要求を再試行できる。自動再試行は一度に制限する。

秘密ヘッダー・購読キー・Cookie・取引情報をログへ記録しない。バックエンドからブラウザーへGraphの応答を転送する際には、更新ヘッダーを除去する。更新のためのバックグラウンド延命タイマーは不要。

## ログアウト・失効・再認証

| 公開操作 | 効果 |
| --- | --- |
| `POST /auth/connections/logout` | 現在の本人セッションを失効。CM接続は維持 |
| `POST /auth/connections/logout-all` | 有効な本人セッションから確定した本人の全セッションを失効 |
| `POST /auth/connections/disconnect` | 対象本人接続と関連セッションをSQLで利用禁止にしてからCM接続を削除 |

いずれも購読キーと`X-Core-Connection-Session`を使用する。自由入力UPNやObject IDで対象を選ばせない。現行値・旧値・候補は同じ失効に従う。アプリ側でも保存値を削除する。SQLで毎回確認するため失効は次の要求から反映するが、既に開始した下流処理の取り消しは保証しない。

CMの確定した更新不能・接続失効は認証案内へ戻す。一時的なCM/Graph障害、通常のGraph権限不足、基盤のclient secret期限切れを、利用者の再認証ループへ置き換えない。未知のCM状態は503として停止する。Entraの条件付きアクセス・MFA要求を無視しない。現在のCMログインリンクAPIでは任意claimsの引き渡しを保証できないため、Graphのclaims challengeは403`ConnectionClaimsRequired`で利用を停止する。自動ログインを繰り返して解決したことにはしない。claims要求をCMのフローで満たせる範囲は、実テナントの受入試験で確認する。

SQLでの独自失効とEntra側の失効は別である。Entraの変更が独自セッションへ即時届くとは扱わず、Graphの拒否・CMの更新不能を検知した時点で利用を停止する。Entraが短いサインイン頻度や追加MFAを要求すれば従う。再認証はMicrosoftの既存SSOを利用するため、追加操作の要否はEntraが決める。

## クライアント実装・診断

[Next.js・FastAPI・PHP・Difyの組み込みガイド](../reference/connection-session-clients.md)を参照する。TypeScriptとPythonのサーバー専用参照実装を用意している。

WSL/POSIX上の診断コマンドは、利用者別の所有者限定ファイルへ現行値・候補・世代を原子的に保存する。

```bash
python3 scripts/credential_client.py \
  --origin 'https://<GATEWAY_HOST>' \
  --api-path '/tools/graph/v1/me' \
  --key-file '<OWNER_ONLY_KEY_FILE>' \
  --session-file '<OWNER_ONLY_USER_SESSION_FILE>'
```

これは手動の疎通診断用。無指定ではアプリ自身のログイン本人を確認できないため、本番の本人対応実装として使わない。BFFとして試すときは、アプリが検証した値から`--expected-tenant-id`・`--expected-owner-id`・`--subscription-id`を設定する。ブラウザーの任意入力をそのまま渡さない。通常のアプリでは共有DBアダプターを使う。WindowsのACLをPOSIXの0600で保証したとは扱わず、この診断ファイル保存はWSL/POSIXに限定する。

`--provider graph-delegated`は認証窓口を単独で確認する診断用。通常は業務APIから始める。

## 配備時の切り替え

旧`transactionId.pollSecret`形式を受け入れる互換処理は作らない。SQL初期化でセッション関連定義をv2へ切り替え、旧取引・旧セッションを無効化する。CM接続・本人とプールの対応は保持する。SQL→Broker→APIMの順で、同じGateway資材から配備する。[SQL配備と復旧](credential-sql.md)を参照する。

## APIMポリシーの定義場所

すべて`apps/gateway/apim/policies`で管理する。APIM共通の購読認可は`service.xml`を維持し、本人接続の処理だけを以下の共通フラグメントで組み込む。

| 定義 | APIM上の部品・処理 |
| --- | --- |
| `credential-connection.xml` | `core-credential-connection`。Brokerの`/v2/session/authorize`、CMトークン取得、Graph本人照合 |
| `credential-session-use.xml` | `core-credential-session-use`。照合後の`/v2/session/use`で失効・期限を再検証し、業務利用と更新候補を確定 |
| `credential-connection-outbound.xml` | `core-credential-connection-outbound`。業務応答の更新ヘッダー、Graphの再認証・claims要求を分類 |
| `credential-challenge.xml` | 認証が必要な場合に短命取引と仮セッションを発行。各共通部品へ組み込むテンプレート |
| `credential-login/*.xml` | 公開start/status/commit/logout/logout-all/disconnect・ブラウザー戻り先をBrokerへ中継 |

GUIでは「API」→「ポリシーフラグメント」と、対象API・OperationのInbound/Outbound processingを確認する。`core-credential-session-use`は本人照合の後に呼ぶ。ヘルスやstatusには含めない。公開API・フラグメントの登録処理は`apps/gateway/apim/api-publication.bicep`で管理し、手動設定だけで済ませない。

受入試験では、7日/30日の境界、ポーリングでの非延長、候補保存失敗、確定応答消失、並行更新、旧値猶予の固定、失効との競合、条件付きアクセスの要求を確認する。ローカルの固定時計テストが通っても、実APIM・CM・Entraの結合確認を完了したとは扱わない。
