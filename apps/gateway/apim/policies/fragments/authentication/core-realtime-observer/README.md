# Realtime補助接続の所有者照合

WebSocketのonHandshakeから呼ぶ、既存Azureフラグメント`core-realtime-observer`の正本です。同名XML／C#へ移設し、C#は`realtimeObserverOwnerValid`の式だけを所有します。Azureの名前と呼出位置は変更しません。

先頭へ[共通owner受取りsnippet](../../../snippets/authentication/realtime-owner-context/README.md)を生成時inlineします。親のonHandshakeが確認したcaller・既存v3／v4表現と期限設定を受け取り、社員JSON・認証成功印・GUID・購読とのID一致を再検査しません。include-fragmentの入れ子は追加しません。

外部チケットの署名・長さ・形式、call ID、認証方式、caller、subscriptionId、期限の照合を維持します。queryは従来どおり`call_id`の単一値だけです。不正な外部チケット・本人不一致は本文なし403で拒否し、使用したチケットを下流ヘッダーから削除します。

内部ownerとTTLの読取りは外部チケットのcatchより前です。`core-realtime-owner-context`・`core-realtime-session-settings`・`core-realtime-observer-owner`だけ、親protocolのon-errorがbase前で安全な監査と要求ID・no-store付き本文なし500へ分類します。TTL受取りは`OPERATION_SETTINGS_INVALID`、owner／チケット処理は`AUTHENTICATION_PROCESSING_ERROR`です。別PolicyIdの既存分類は維持します。

C#のmarkerは`__REALTIME_OBSERVER_OWNER_CSHARP__`、照合setterのPolicyIdは`core-realtime-observer-owner`です。配備fragmentと保護された読戻しsourceはBicepの同じ展開関数を使い、Python rendererも同じXML／C#を読みます。

確立後のWebSocketフレームはonHandshakeの検査対象ではありません。BFFによる受信専用中継と、所有者が直接接続する双方向sidebandの既存区別は維持します。

MOD-93はローカル実装・関連検証済みです。関連30件、別実行の配置等13件とsource／容量1件が成功しました。独立レビューの最終所見はありません。検証の失敗と修正は[テンプレート説明](../../../api-templates/README.md)へ記録しています。Azure反映・実Realtime受入は未実施です。
