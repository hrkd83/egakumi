# 認証済みEntraユーザーの利用枠

`core-entra-user-profile`は共通認証が確認した`gatewayUserAuthentication`から`limits`を取り出し、`gatewayUserLimits`へ渡します。回数枠・毎分トークン枠・累積トークン枠を持ち、値の検証は共通認証で行います。

REST・WebSocket・外部MCPのポリシーは`gatewayDecision.method`が`user`の場合にこのフラグメントを呼びます。内部入力の取得に失敗した場合は既存の`on-error`で処理します。
