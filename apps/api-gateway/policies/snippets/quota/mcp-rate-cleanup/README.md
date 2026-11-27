# MCPの取消・終了と利用回数枠

外部MCPの取消・終了要求を利用回数枠から免除する生成時inlineのsnippetです。Azure Policy fragmentを新しく登録しません。XMLは既存の説明と変数設定を持ち、同名C#は`gatewayRateLimitExempt`を選ぶ1式だけを所有します。

## 選択条件

| 要求 | 既存の判定 |
| --- | --- |
| `DELETE` | 終了要求としてtrueを返す |
| `POST`以外 | falseを返す |
| `POST` | 本文を保持してJSONオブジェクトとして読み、下記の取消形式だけtrueを返す |

取消形式は`jsonrpc`が`2.0`、`method`が`notifications/cancelled`で、本文直下の項目を`jsonrpc`・`method`・`params`に限定します。`params`はオブジェクトで、項目は`requestId`と任意の`reason`だけです。`requestId`は整数、または空白だけでなく512文字以下の文字列です。`reason`を指定する場合は1024文字以下の文字列とします。読取り・型変換に失敗した場合は従来どおりfalseを返します。

この値は利用回数枠の免除だけを決めます。本人認証、固定接続先、下流のJWT・session所有者確認を省略する値ではありません。要求本文・ID・取消理由を変更せず、公開ヘッダーによる免除も追加しません。

## MOD-105の正規配置

元の`mcp-rate-cleanup.xml`から1式だけを`__MCP_RATE_CLEANUP_CSHARP__`へ分離しました。DELETE・POSTの選択、JSON-RPCの形式・許可項目・文字数、falseを返すcatchを維持しています。

POSTでは既存の`Body.As<JObject>(preserveContent: true)`を使います。この読取りは増減しません。外部MCP中継の要求バッファリングへの影響は、別の棚卸で評価します。

Bicep・Python renderer・fixture・Razor・同名所有検査を正規XML／C#へ結線し、直下の旧XMLを撤去しました。外部MCPの配備値と保護された読戻し期待sourceは同じ正本を使います。full／compact／omitの各生成で、移設前と構造・属性・実式トークンが一致しました。

関連42件が30.149秒で全件成功しました。失敗・エラー・スキップはありません。ローカル移設・検証済みで、Azure反映と実MCPの取消・終了の受入は未実施です。
