# SQL購読profileの取得

購読キーの利用枠を内部APIから取得し、共有APIポリシーへ生成時にinlineで差し込むsnippetです。XMLが初期値・社員分岐・cache・取得・拒否応答のフローを持ち、同名C#は`gatewayFetchedProfile`の受入検査式だけを所有します。新しいAzure Policy fragmentは登録しません。

## 入出力

| 入力・出力 | 用途 |
| --- | --- |
| `gatewayDecision`・`gatewayRateLimitExempt` | 社員分岐と既存の計数免除 |
| APIMの検証済み購読ID・生成済み設定 | 内部APIへの要求と既存cacheキー |
| `gatewayProfileResponse` | 内部APIからの取得応答 |
| `gatewayFetchedProfile`・`gatewayCachedProfile` | 受入検査済みJSONと30秒cache |
| `gatewaySubscriptionProfile` | 後段へ渡す回数枠・トークン枠 |

## MOD-66の変更

ローカル実装・関連検証済みです。正本をこのフォルダーのXML・同名C#へ移し、取得失敗の503応答直前に既存telemetryへ`policy-error`／`LIMIT_PROFILE_UNAVAILABLE`／503を1回記録します。取得障害を制限なしへ変換せず、後段へ進む前に停止します。

受入検査と503応答、Retry-After: 5・no-store・要求ID・既存エラーヘッダー、30秒cache、GET・専用鍵・5秒timeout、社員優先・免除・初期profile、明示null、生成時inline方式は維持します。

SQLは社員認証と別の供給経路なので、schemaVersion・返却購読ID・利用枠3項目の存在とnull／型／範囲／期間をここで受け入れ確認します。この検査を省くと、不備が後段のrate／tokenで制限なしと解釈される場合があるため、受付条件を維持します。読取り棚卸でも、削除を勧めるAPIM内の重複はありませんでした。

## 残件

MOD-66の関連164件が全件成功しました（33.446s、失敗・スキップなし）。実C#取得式と503・telemetry式で失敗51対照・成功8対照・cache／bypassの4対照、全HTTP・WebSocket・外部MCPの完成snippetと生成・有効化・実行順を確認しました。実Bicepコンパイル・生成一致・同名snippet所有・Razor・日本語説明も成功しています。独立レビューで旧取得式と完全一致し、監査4ノード以外の旧XMLも同一と確認しました。この取得snippetのローカルInboundは一区切りです。実SQL経路の受入と工程1全体は未完了、Azure未反映です。社員profileのMOD-65の125件（29.737s）とローカルInboundの区切りは別部品・別時点の記録です。
