# 各APIで共通認証後の要求を準備する

`core-api-request-preparation`は、通常HTTP・WebSocketの接続開始・外部MCP・REST操作から生成するMCPのポリシーで、`<base />`から戻った直後に呼び出す共通フラグメントです。
共通認証そのものは `service.xml` のSTEP4が担当し、この部品は後続のAPI固有処理へ渡す要求を整えます。

```text
各APIの設定準備 → <base />で共通Inbound → この部品 → 各APIの権限検査・下流接続
```

棚卸で承認した次の3処理を、この順に実行します。

| 順序 | 処理 | 理由 |
| --- | --- | --- |
| 1 | `X-Core-Caller-Id`と`X-Core-Subscription-Id`を削除して続行 | クライアントの申告値を内部の本人・購読情報として渡さない。認証済み情報はAPIMの購読情報と変数から取得する |
| 2 | `x-ms-client-request-id`を`context.RequestId.ToString()`で上書き | 下流へ送る追跡IDを、APIMが要求ごとに付けたIDへ揃える |
| 3 | `gatewayCommonAuthenticationValidated`の存在とtrueを確認 | 未設定またはfalseなら、共通認証の設定不足として503を返し、後続処理へ進めない |

503の応答は `Common authentication is not configured` と、同じ要求の `apim-request-id` だけを設定します。
キー／Bearer認証をやり直したり、この部品からトークンを取得したりしません。完了マーカーは要求ごとのboolであり、利用可否を切り替える配備設定ではありません。
通常のキー／Bearer経路はSTEP4の確認が成功するとtrueを保存します。CMのブラウザー受付は別の取引認証を使うため、この部品を呼びません。

監査の `requestId` とクライアントへ返す `apim-request-id` は、ヘッダーの申告値ではなくAPIMの `context.RequestId` を使います。
診断で採取するfrontendの要求ヘッダーとは区別し、その採取タイミングや値を監査の主キーとして扱いません。
Brokerへ渡す本人・購読の内部ヘッダーは、必要な操作が後で検証済み情報から生成します。

呼出元は `gateway.xml`、`protocol.xml`、`mcp.xml` です。各ファイルにはこの部品の見出し・目的・処理と、同名の `include-fragment` を置きます。
XMLだけで3処理を定義するため、同名C#ファイルやフラグメント内の別フラグメント呼出しはありません。
本文・モデル・ツール・SSEの処理を追加しません。後段の要求ID再設定とBearerの整合確認は、別の棚卸対象として維持します。
