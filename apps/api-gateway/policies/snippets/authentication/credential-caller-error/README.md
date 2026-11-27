# CMの内部呼出者IDエラー

HTTP・CM付き外部MCPのAPIポリシーへ展開する短い監査・応答フローです。Azureのフラグメントとしては登録しません。接続部品の直後、本人セッション使用確認の前に展開します。

`core-credential-connection`は内部callerIdを読めなければ既存の空ID停止条件でallowed:false／status:500を返し、Broker・CM・Graphへ進みません。このsnippetはその500だけを受け、既存の`core-gateway-telemetry`でpolicy-error／AUTHENTICATION_PROCESSING_ERROR／500を記録し、要求ID・no-store付きの本文なし500応答で停止します。

短い状態分岐と応答はXMLに保持します。内部IDの型・GUID・空白文字等の新しい検査は追加しません。接続設定・所有者・本人セッション・期限・失効の既存403／409／503はこの分岐の対象外です。

MOD-77はローカル実装・関連検証済みです。関連164件が初回から全件成功しました（39.283s、失敗・スキップなし）。実ポリシー式の内部不備500と既存CM拒否の対照、HTTP／外部MCPの同じguard配置・完成XML／保護source一致、CMなし／WebSocket／生成MCPへの非追加、容量・構文・説明を確認しました。独立レビューに所見はありません。

最初のinline監査追加の容量precheckは、CMの完成XMLが33,537bytesとなり32KiB上限を超えたため、API側で既存監査フラグメントを呼ぶこの配置へ調整しました。調整後の標準26,762bytes・12pool構成29,894bytesは上限内です。監査やコメントを省略して収める変更ではありません。容量precheckと上のまとめ試験は別の実行として扱い、Azure未反映・実受入未確認です。
