# Realtimeの通話作成と終了

`realtime-create.xml`と`realtime-hangup.xml`は、既存のRealtime操作へ登録する正本です。同名C#はcreateのSDP入力・短期token要求・短期token検査・call ID取得・通話チケット発行・SDP応答検査の固定6区画と、hangupの所有者照合式をそれぞれ所有します。Bicepの配備値と保護された読戻しsource、Python rendererは同じ正本を説明付きで展開します。

## MOD-141の整理

現XMLの5式だけをmarkerへ置き換え、既存のチケット発行の全文読取りも同名C#の固定区画へ切り替えました。短期tokenの固定JSON要求だけは本文encoder、他5区画は属性encoderを使います。Bicepでは5追加区画を展開し、既存owner挿入処理へチケット区画だけを渡します。通信・owner先行・署名・本文読取り・SDP条件・失敗時補償・監査・応答／on-errorは維持します。

actual-beforeの3mode生成物と実行構造・全属性・C#tokens・string／char値が一致し、既存35件37.534秒は全件成功しました。Realtime実式の所有者・署名・offer・短期token・SDP応答・補償、配備／保護source一致・容量・構文・所有を確認し、独立レビューに追加所見はありません。ローカル整理済みでAzure反映・実Realtime受入は未実施です。

MOD-93では、[共通owner受取りsnippet](../../snippets/authentication/realtime-owner-context/README.md)をinboundのbase直後へinlineします。createは短期token取得・通話作成前、hangupはチケット照合・終了要求前に、確認済みcallerと期限設定を受け取ります。後段では社員JSON・認証成功印・GUID・購読とのID一致を再検査しません。

| C#の所有範囲 | PolicyId | marker |
|---|---|---|
| createの`realtimeCallTicket` | `core-realtime-ticket-generation` | `__REALTIME_CREATE_TICKET_CSHARP__` |
| hangupの`realtimeCallOwnerValid` | `core-realtime-hangup-owner` | `__REALTIME_HANGUP_OWNER_CSHARP__` |

発行するv3／v4チケットの項目・順序・Base64url・HMAC-SHA256・期限は維持します。終了時は外部チケットの署名、call ID、方式、caller、subscriptionId、期限を照合します。外部チケットの不正形式・JSON不成立・所有者不一致は従来の403です。hangupのquery不許可、チケットの下流削除と固定の終了先も維持します。

内部ownerとTTLは外部チケット解析のcatchより前で読みます。内部受取りやチケット処理のPolicyIdだけをon-errorのbase前で捕捉し、安全な監査と要求ID・no-store付き本文なし500を返します。TTL受取りは`OPERATION_SETTINGS_INVALID`、owner／チケット処理は`AUTHENTICATION_PROCESSING_ERROR`です。別PolicyIdは従来のbaseへ進みます。

SDP入力、短期token取得、提供元のLocation／MIME／SDP応答、作成後の終了補償、未確認補償時の復旧情報は変更しません。外部応答不成立の502と、内部データ不成立の500を区別します。backendは従来どおりAPIのforward-requestをbaseで継承し、自動再送を追加しません。

MOD-93はローカル実装・関連検証済みです。関連30件、別実行の配置等13件とsource／容量1件が成功しました。独立レビューの最終所見はありません。検証の失敗と修正は[テンプレート説明](../../api-templates/README.md)へ記録しています。Azure反映・実Realtime受入は未実施です。
