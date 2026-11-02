# 第18回 内容レビュー

2026-11-02。元資料の group-observation / subscription-sync / maintenance と対応コードに基づき、独立した担当が全94台詞・設定表を監査。制作を止める事実相違はなし。

- Graph観測のactive / ineligible / deleted / unknownを区別。deletedは対象IDへの明示GET404、403・429・障害はunknown。
- 承認・観測・所有関係を分離。観測JobはAPIMを自動停止・削除しない。15分ごと、未確認・古い登録から最大100件、Graph照会最大4件並行。全件15分保証ではない。
- SQL確定値からAPIMへの同期。ID照合、通常表示名は会社、配備検証分は用途。更新・再作成・削除・対象外を区別。masterや途中状態は通常対象外。
- 再作成は主副キー刷新、更新は既存キーを維持しても状態変更で利用が再開し得る。
- 確認は最大10件、5分期限、署名。本人・管理者所属・接続先・最新値を実行直前にも再照合する。
- 開始監査を外部操作前に保存し、APIM再照会後に行別結果を追記。SQLの取消で外部操作は戻らない。
- 一覧の取得失敗を不存在にしない。入力・保存・権限・外部設定・通常テストと実SQLテストを分けて確認する。

主な確認先：apps/portal/lib/api-keys/graph.ts、api-key-administration/{service,repository}.ts、api-keys/repository.ts、subscription-sync/{differences,service,repository}.ts、api-keys/apim.ts、scripts/reconcile-api-key-groups.ts、対応テスト。

明瞭化：再作成対象の「有効なSQL確定値」を「対象になるSQL確定値」へ整える。activeだけを指す誤読を避けるためで、suspendedも再作成対象になる。

稼働環境への取得・変更・実機検証は行っていない。これは教材と実装の内容レビューである。
