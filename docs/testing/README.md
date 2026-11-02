# テスト・検証

## テスト仕様書

| 文書 | 対象 |
| --- | --- |
| [構築テスト](construction-tests.md) | 入力検証、新規構築・再配備・構成変更、証明書、SQL初期化、API公開、撤去 |
| [動作テスト](integration-tests.md) | 構築後の接続・認証・認可、Gateway・Portal、モデル・Tool、監査・運用 |

ローカル試験の実行方法と共通ルールは[開発手順](../../DEVELOPMENT.md#テストの実行方法)を参照してください。

## 検証用の補助資料

- [Entraテストデータの準備例](entra-fixtures.md)
- [Portalのローカル認証テスト](portal-local-auth.md)
- [アクセスログのquery非保持](log-privacy.md)
- [App Gateway WAFの互換性・遮断確認](waf-compatibility.md)
- [購読単位の利用量観測と保護上限](subscription-limits.md)
