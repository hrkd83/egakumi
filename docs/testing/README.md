# テスト・検証

| 確認したいこと | 文書 |
| --- | --- |
| 初回構築、再配備、撤去 | [構築テスト](construction-tests.md) |
| GatewayとPortalの実接続、認証、監査 | [動作テスト](integration-tests.md) |
| テキスト、画像、音声などの代表API | [Model Gateway](model-gateway-tests.md) |
| Graph、文書解析、REST、MCP | [Tool Gateway](tool-gateway-tests.md) |
| Portalの署名と認証 | [Portal受入](portal-authentication-tests.md)、[ローカル起動と認証](portal-local-auth.md) |
| ネットワーク、Firewall、NSG、実効権限 | [ネットワークの受入](network-security-validation.md) |
| WAFの適用範囲、正常入力と拒否 | [WAF](waf-compatibility.md) |
| アクセスログのクエリ保護 | [ログの変換と受入](log-privacy.md) |
| 更新、資格情報の保守、復旧 | [運用テスト](operations-tests.md) |
| テスト用の利用者・権限 | [Entraテストデータ](entra-fixtures.md) |
| 例外キーの利用量を観測する | [利用量観測と上限](subscription-limits.md) |
| 3万人想定のGateway・本文監査の容量を測る | [容量計画と負荷試験](gateway-capacity.md) |

ローカル試験は[開発手順](../../DEVELOPMENT.md#テストの実行方法)を参照してください。環境の結果や証跡は同じ環境の `.local/<environment>/evidence/` に保存します。
