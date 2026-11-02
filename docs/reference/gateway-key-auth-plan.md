# Gatewayの購読キー認証

本書は現行のGateway認可とPortal・本人接続の分担を説明します。APIの正本は [登録定義](../../apps/gateway/apim/apis/)、共通認可は [service.xml](../../apps/gateway/apim/policies/service.xml)、配備は [Gateway配備コマンド](../../scripts/deploy_gateway.py)です。実環境への反映状況は配備記録と試験結果で確認します。

## 利用経路と認証の分担

| 場所 | 担当すること |
| --- | --- |
| Portal | Entraサインイン、現在のグループ所属に基づくキーの管理・利用、利用者別の本人接続状態を管理する |
| Portalのサーバー処理・APIクライアント | 有効な購読キーを秘密保管先から取得し、Gatewayの公開APIへ付ける |
| APIM共通ポリシー | 信頼した入口経路、設定済みキーヘッダー、APIMが検証した購読を確認する |
| API・Operation | 操作・モデル・入力・出力上限、購読プロファイル、必要な本人接続を検査する |
| APIMの下流接続 | Foundry等にはマネージドID、本人のGraphにはCMの委任tokenを使う |

購読キーはAPIの利用範囲と利用枠を表します。Portalへログインした本人や、Graphを利用する本人をキーだけで識別しません。キー・pollSecret・本人接続セッションはサーバー側で保持し、ブラウザーのJavaScript・URL・公開設定へ埋め込みません。

## 受信するキー

既定ヘッダーは`api-key`です。環境共通の名前は`gateway.authentication.subscriptionKeyHeader`で設定し、APIMのAPI設定と共通ポリシーへ同じ値を反映します。

```http
POST /models/openai/v1/chat/completions HTTP/1.1
Host: gateway.example.com
api-key: <APIM_SUBSCRIPTION_KEY>
Content-Type: application/json
```

APIM標準の購読検証と、共通ポリシーの`context.Subscription.Id`確認を使います。キーの未指定・空値・重複や、URL queryでのキー提示を拒否します。`Authorization: Bearer <APIキー>`は標準ヘッダーと自動的に同じものとして扱いません。別のクライアントを接続するときは送信するヘッダー、公開パス、要求・応答方式を契約に合わせます。

入口のキーやAuthorizationは下流へ引き継がず、対象サービス用の認証を設定します。匿名healthとブラウザー認証の一時取引は用途を限定した経路です。業務APIの購読認可を解除して実装しません。

## ProductとPortalのキー管理

通常のPortal発行キーは`released-apis`へ所属します。標準APIは各定義の`released`に従って関連付けられます。用途別の`model-users`・`pii-users`・`connection-users`も登録されますが、利用可否は実際の購読scope・Product/API関連付け・APIの公開操作で判断します。

PortalはEntraグループをキーの所有者として管理し、現在の所属により発行・閲覧・更新・停止・移管・削除を認可します。APIMが業務呼出しごとに所有グループの所属を再照会する構成ではありません。配布済みのキーを止める場合は購読の停止やキー交換も行います。

primaryとsecondaryは同じ購読ID・利用枠・音声セッションの所有主体を使います。APIキーの値はPortal SQLの正本データとして保存しません。台帳とAPIMの同期は [Portal](../setup/portal.md)の管理機能で行い、配備資材の`keySubscriptions`を通常のキー台帳へ置き換えません。

## 本人接続が必要なAPI

Graph `/me`は、購読キーに加えて`X-Core-Connection-Session`を要求します。CMはMicrosoftのaccess/refresh tokenを保管・更新し、BrokerとSQLが確認済み本人、CM接続、購読、期限、失効、更新世代を管理します。

未接続時は409 `ConnectionRequired`と認証案内を返します。Portalのサーバー処理は仮セッションと取引を保存し、利用者へ`loginUrl`を案内します。statusは状態・本人・期限のメタデータを返し、セッション平文を再発行しません。確認した本人がPortalのログイン本人と一致した場合に、保存済みセッションで元の要求を再開します。

現在の本人接続はv2です。無操作期限・最長期限・日次更新、共有保存先の排他・世代比較、logout・全失効・接続解除を別々に扱います。契約は [CM接続手順](../setup/credential-manager-login.md)、[プールとセッション](credential-pool-design.md)、[クライアント実装](connection-session-clients.md)を参照してください。

## 音声セッションと利用上限

Realtimeの作成・終了・observerは、検証済み購読IDを所有者とし、署名ticket・call ID・期限を照合します。同じ共有キーを使うPortal利用者間の分離は、Portalが保持する実行とログイン本人の対応でも確認します。

HTTP・SSE・WebSocketの接続開始・gRPCのRPC開始には、対象ポリシーの購読単位の利用上限を適用します。Portalの購読プロファイルと共通設定の扱いは [購読単位の制限・観測](../testing/subscription-limits.md)を参照してください。WebSocket接続後のframeとWebRTCメディアは、APIMのHTTP本文検査・要求回数とは別の境界です。

## 配備・確認の入口

`azd up`は共通基盤の完了後、Gateway→Portalを順に配備します。Gateway更新は同じparams・配備記録を使い、計画確認後に`--execute`を付けて実行します。DB・CM・Broker起動確認の後にAPIと共通ポリシーを反映します。詳細は [運用手順](../operations.md)を参照してください。

共通ルール・ローカル実行は [DEVELOPMENT.md](../../DEVELOPMENT.md)、動作の受入条件・結果は [INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)、資材と構築・更新・撤去の受入条件・結果は [CONSTRUCTION_TESTS.md](../../CONSTRUCTION_TESTS.md)で管理します。構築・登録成功だけで業務APIの実通信を合格にしません。

## 整理前の記録

変更前の調査・設計・検証記録は [2026-11-01整理前のスナップショット](../history/pre-20261101-gateway-key-auth-plan.md)に保存しています。現在の仕様・手順・受入判定と区別して参照してください。
