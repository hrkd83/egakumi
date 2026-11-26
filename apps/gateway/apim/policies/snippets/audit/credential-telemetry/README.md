# CMの拒否監査

CMの接続・本人セッションの処理で、既存の拒否理由を記録する生成時inlineのsnippetです。新しいAzure Policy fragmentは登録しません。XMLはtraceのフローを持ち、同名C#はmessageの1式全体を所有します。

## 記録する情報

要求ID・API／操作ID、確認済み主体、認可結果と呼出者／利用枠の帰属、固定接続先の分類、既存のモデル名を記録します。要求本文・生トークン・CMセッション値・例外本文を新しく読み取ったり記録したりしません。

既存の要求情報は4096文字以内のJSONから許可項目だけを取り出し、各文字列は512文字以内として扱います。時刻と、日時として解析されたUser-Agentの文字列を戻す処理を維持します。

主体の投影もこのC#へ収めました。`gatewayAuthenticatedSubject`がなければunverified、取得値の型・長さ・JSON・状態・許可項目・ID対応が不整合ならinconsistentとします。1600文字上限、既知状態の転記、verifiedの項目数・文字列型・項目別上限、社員と購読の方式、callerId／allocationIdの対応を維持します。共通認証のproducerを再実行する処理には変更しません。

`gatewayDecision`と`credentialCallerId`の既存照合、社員・購読の分岐、認可のallowed／denied／unconfirmed、profileId、固定監査設定・targetModel、schemaVersion・auditSchemaVersion・イベント名を維持します。内部情報を読めない場合にcatchで既存の状態を残す処理も維持します。

## MOD-114の正規配置

旧XMLのtrace message全式を同名C#へ分離し、旧observerの実コードと説明をその式に収めました。専用markerは`__CREDENTIAL_TELEMETRY_CSHARP__`です。nested共通marker、新しい変数設定、JSON解析、fragmentは追加していません。

Bicep・Python・fixture・Razor・同名所有を結線し、直下の旧XMLを撤去しました。CMへのinline複製では既存のcompact説明を使い、full／compact／omitで移設前との構造・属性・実式トークン不変を確認しました。旧observerの独立sourceへの参照はこのconsumerから解消し、他consumerの移行まで旧source自体は保持します。

関連73件が39.047秒で全件成功しました。監査主体・認可・CM対照、配備／保護source・容量・構文・所有を確認し、失敗・エラー・スキップはありません。ローカル移設・検証済みで、Azure反映・実拒否監査の到達確認は未実施です。
