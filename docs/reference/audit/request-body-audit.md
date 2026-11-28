# 監査ログの確認

App Gateway、Azure Firewall、APIM、Foundryの診断ログをLog Analyticsで照合する。検索期間はテスト・本番とも90日。本番では監査対象を取り込み時からBlobにも送り、作成から2,557日のLocked WORMで保全する。資格情報を除去してから保管する経路と各リソースの診断分類は[監査設計](audit-log-design.md)に記載する。

## 初回構築

| 採取元 | 記録する内容 |
| --- | --- |
| App GatewayとAzure Firewall | 入口のHTTP結果、WAFの判定、通過した通信の許可・拒否・IDPS・TLS検査。Firewallの照合は時刻と通信元・宛先・ポートを使う |
| APIM | 認証主体・方式、API・操作、応答、ルーティング、要求ID、利用量と接続イベント。利用者の本文・購読キー・アクセストークンはAPIM監査に保存しない |
| FoundryのモデルREST API | 推論結果と所要時間、使用量。Chat Completions、Responses、Claude Messagesの直接呼び出しは、プロンプトや生成結果の全文・SSEイベントの本文を監査では保証しない |
| Foundryエージェント | Application Insightsに連携した標準トレースでプロンプトとモデル入出力を取得する。初回構築はモデル配備と連携設定まで。利用するエージェントごとに本文と長期保管の着信を確認する |
| WebSocket・WebRTC | 基盤が観測した接続、切断、失敗、セッション制御。セッション中の業務テキストや文字起こしが必要な場合は、利用する業務システム側で記録する |

直接呼び出しで本文保存を必須とするAPIは、公開前に採取元と保存先を確認する。診断ログの取り込みとBlobへの保管はAPIの応答と独立している。

## Foundryエージェントの本文トレース

Foundryのエージェントを追加するときは、[本文監査の有効化手順](../../operations/foundry-agent-audit.md)で、Application Insightsの接続、本文テーブル、アクセス権限とBlobへの着信を確認する。証跡はTrace IDとSpan IDを使って照合する。

## Azure受入

- WAFの遮断、Firewallの許可・拒否とTLS検査、APIMの認証・レート制限、Foundryの直接REST呼び出しで、それぞれの診断ログがLog Analyticsと該当する保管先に届くことを確認する。SSEでは最初のイベントと終了までの配信を確認する。
- App GatewayのTransaction ID、APIMの要求ID、Foundryの要求IDとTrace IDで、照合できる事実を調べる。Firewallは一致候補の通信を表示し、候補が一つに決まらない記録を明示する。 [監査の識別情報](audit-identification.md)
- 合成の秘密マーカーを使い、クエリ、WAFの一致値、資格情報ヘッダー、監査ログ検索のKQL本文が対象ログやBlobへ流れないことを確認する。
- Log Analyticsで90日の設定を確認し、専用の検証先で保管先への書き込み、Locked WORMによる上書き・削除の拒否を確認する。監査Blobは調査担当者の期限付き権限で閲覧する。
- Data Exportの失敗とBlobへの未着を検知し、Log Analytics上の対象期間を確認して不足分を補完できることを確認する。
