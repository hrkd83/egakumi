# Realtimeの確認済み所有者の受取り

先行認証が作った`gatewayDecision`のmethodとcallerIdを受け取り、既存の通話チケットで使う`v`・`subscriptionId`・`callerId`を`realtimeOwnerContext`へ保存します。社員はv4・空のsubscriptionId、キー利用者はv3・確認済みcallerの`subscription:`以降です。callerの表記は変えません。

このsnippetを追加する理由は、create・hangup・observerの3正本へ同じowner受取りを複製しないためです。BicepとPython rendererが生成時にXMLをinline展開します。新しいAzureフラグメントは作らず、既存observerフラグメントにもinclude-fragmentの入れ子を追加しません。

同名C#が`realtimeOwnerContext`の式だけを所有します。社員JSON・認証成功印・GUID・購読とのID一致は先行認証で確認済みなので、ここで検査し直しません。内部データを受け取れなければExceptionを送出し、呼出元がPolicyIdを限定して安全な500で停止します。

XMLの短い別setterは、検査済み`sessionTtlSeconds`をintとして`realtimeSessionTtlSeconds`へ受け取ります。範囲の再検査は行いません。外部チケットの期限照合は後続のチケット部品で維持します。

| 項目 | 値 |
|---|---|
| ownerのPolicyId | `core-realtime-owner-context` |
| TTLのPolicyId | `core-realtime-session-settings` |
| C#の挿入marker | `__REALTIME_OWNER_CONTEXT_CSHARP__` |
| 親XMLの挿入marker | `__REALTIME_OWNER_CONTEXT_XML__` |

create・hangupではinboundのbase直後、observerでは先行認証を終えたonHandshakeが呼ぶフラグメントの先頭で受け取ります。owner不成立は`AUTHENTICATION_PROCESSING_ERROR`、TTL受取り不成立は`OPERATION_SETTINGS_INVALID`の安全な監査と、要求ID・no-store付き本文なし500に分類します。別PolicyIdの共通エラー処理は変更しません。

MOD-93はローカル実装・関連検証済みです。受取り不備の通話前停止と内部500、既存チケットの主体・署名・期限の対照を確認しました。実行結果は[テンプレート説明](../../../api-templates/README.md)へ記録しています。Azure反映・実Realtime受入は未実施です。
