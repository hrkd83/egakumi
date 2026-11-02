# 第14回 内容レビュー

2026-11-02 JST。CONTENT-PLAN.md第14回の到達点と、03-subscription-management.htmlのpurpose / signin / company、関連Portal実装を照合した。

80台詞を独立レビューし、重大な事実相違・範囲逸脱・speech置換による意味変化なし。Container Appの読みは「コンテナーアプリ」に調整した。

確認した内容：

- Portalは画面操作の受付と業務判断、SQLは本人・ログイン状態・会社・所有グループ・製品承認・キー台帳・監査、APIMはAPIMサブスクリプションとキー実値を担当する。
- Entraの検証済みIDトークンからテナントID＋Object IDで本人を識別する。Better AuthがSQLセッションを管理し、以後はCookieとSQLセッション・有効期限を照合する。
- 個人とキーを直接つなぐSQL外部キーや所属メンバー一覧表はない。Graphで現在所属を照会し、SQLの所有グループ・製品承認・キー状態などを照合する。
- 会社は費用帰属で、会社の一致だけでキー管理権限を与えない。会社機能は利用量集計・金額算定・請求を行わない。
- 会社管理は現在の基盤管理グループ所属を確認し、重複・競合を確認して会社情報と監査を同じSQL処理で保存する。
- 会社の選択停止は新規候補から外し、既存キーの参照を保持する。キー停止やグループ所属解除とは別。
- 改名はSQLへ保存し、通常のAPIM表示名は同期機能で反映する。

実装根拠：lib/auth-identity.ts、auth-session.ts、auth-options.ts、auth-schema.ts、api-keys/graph.ts、api-keys/service.ts、company-administration/service.ts、company-administration/repository.ts（いずれもapps/portal/配下）。稼働環境の取得・変更はしていない。

第15回の払い出し手順、第17回のキー管理、第18回の組織観測・同期、第19回以降の本人接続の詳細へ踏み込まず、各回へつながる入口に絞った。
