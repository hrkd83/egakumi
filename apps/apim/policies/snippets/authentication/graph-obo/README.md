# Microsoft Graph /me の委任認証

`api-publication.bicep`は`graph-profile-v1`のAPIポリシーへ同名XMLとC#を展開します。登録済み`graph_profile_self`のGETは、共通認証・API認可が確定した本人BearerでBrokerの`POST /v1/graph/obo`を呼びます。購読キー経路は現在のCredential Manager本人セッションを確認します。

Brokerの入力は`Gateway.Invoke`を持つ元の委任トークンと、APIMが確定した本人のtenant ID／object IDです。Brokerはegakumi-apiの証明書に対応するKey Vaultの非エクスポート可能な鍵を使い、Managed Identityで短命のPS256 client assertionへ署名します。署名対象の鍵versionと公開証明書のSHA-256 thumbprintを組にして設定します。認証テナントのEntraへGraphの`.default`を指定し、返された委任scopeが`User.Read`だけであることを確認します。APIアプリには、この処理に使うGraph委任`User.Read`だけを同意します。

APIMは元のBearer・接続セッションをGraphへ渡す前に除去します。交換済みトークンをGraphに提示し、`/me`の`id`と認証済みの本人を照合します。アクセストークン・署名済みassertion・claims要求を監査レコードへ含めず、Bearer経路の固定の監査項目`backendAuthentication`に`entraObo`を記録します。交換済みトークンを保存せず、1回の要求で1回交換します。BrokerはMicrosoft Graphへ追加認証を要求できるように、固定のクライアント機能`cp1`を交換時に指定します。

新ポリシー適用後に本人BearerでGraphを利用するには、OBO用の証明書、署名権限、Entraの委任権限が必要です。OBO設定が省略されたBrokerはBearer交換へ503を返し、購読キーのCM処理を利用できます。

| HTTP | エラーコード | 呼出し元の扱い |
| --- | --- | --- |
| 401 | `GRAPH_REAUTHENTICATION_REQUIRED` | `WWW-Authenticate`に従い、新たなGateway向けトークンを取得する。構造を確認できたclaimsをbase64で返す。Portalは同一テナントの有効な要求だけでAPI試用の追加認証を開始する |
| 403 | `GRAPH_ACCESS_DENIED` | Graphの権限と本人性の受入条件を確認する |
| 429 | `GRAPH_RATE_LIMITED` | Graphからの`Retry-After`の秒数が示された場合は、その間隔を待つ |
| 503 | `GRAPH_OBO_UNAVAILABLE` | Broker、Key Vault署名、Entra交換、Graphの状態を要求IDで確認する |

Azureでの受入では、別テナントのKey Vault署名、Entraの証明書・管理者同意、Graph本人一致、実際のConditional Access要求、キーの既存CM経路を確認します。
