# Entraユーザーの回数制限

`core-rate-limit`はAPIMの入口で認証済みEntraユーザーの要求を計数します。APIMが検証した購読キーには共通回数枠を適用しません。例外発行したキーに上限が必要になった場合はAzureポータルのAPIMポリシーで設定します。

APIポリシーが`core-entra-user-profile`から受け取った`gatewayUserLimits.requestRate`を使用します。同じテナント・ユーザーはAPIをまたいで`ai-gateway-rate-v3:user:<Base64(tid:oid)>`の同じカウンターを使います。`requestRate`が`null`のときと認可済みの取消・切断操作は計数を免除します。

`core-rate-limit.xml`が有効な場合に`rate-limit-by-key`を実行し、上限超過時の429には`rate-limit-error`が要求IDと`Retry-After`を付けます。
