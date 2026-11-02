# ドキュメントの履歴

このフォルダーは、現行仕様へ文書を整理する際に分離した調査・設計・検証の記録です。本文の日付・対象revision・当時の範囲で参照し、現在の構成・配備状態・試験の合格や操作手順として使いません。

現行仕様は [参照資料](../reference/platform.md)、開発共通ルールは [DEVELOPMENT.md](../../DEVELOPMENT.md)、受入条件と現在の判定は [動作テスト](../../INTEGRATION_TESTS.md)・[構築テスト](../../CONSTRUCTION_TESTS.md)で管理します。進行中の試行の詳細と再開記録は各仕様書から参照してください。

## 2026-11-01の整理前記録

| 保存した資料 | 保存範囲 | 現行の参照先 |
| --- | --- | --- |
| [音声通信・遅延の旧調査](pre-20261101-realtime-latency-review.md) | 整理前の文書全体。対象revision、過去の実測・検査結果、当時の接続・地域比較と検討案 | [音声通信と遅延評価](../reference/realtime-latency-review.md) |
| [Gatewayキー認証の旧変更計画](pre-20261101-gateway-key-auth-plan.md) | 整理前の文書全体。旧18 API時点の移行・実装計画 | [Gatewayの購読キー認証](../reference/gateway-key-auth-plan.md) |
| [API定義の整理前記録](pre-20261101-api-design-proposal.md) | 整理前の文書全体。初期配備の確認記録と変更時の説明を含む | [Core API・Operation定義](../reference/api-design-proposal.md) |
| [Portalの整理前検証記録](pre-20261101-portal-validation.md) | 整理前READMEの「検証」節。2026-10-30のnpm audit等の記述 | [Portal README](../../apps/portal/README.md) |
| [整理前の費用試算](pre-20261101-cost-estimate.md) | READMEの費用節。2026-10-22単価による一部基盤の概算 | [現行構成の費用確認](../../README.md#費用を確認する) |
| [Portalの旧リソースグループ移行資料](pre-20261101-portal-resource-group-migration.md) | 旧配置・CI・確認APIを前提とする整理前の手順全体 | [現行Portal配備](../setup/portal.md) |

各スナップショットは整理開始前に保存した本文から作成しました。相対リンクは配置先へ合わせており、参照先のソースコード・外部資料そのものを当時の版として固定するものではありません。旧実装を調べる場合は本文のrevisionとGit履歴を照合してください。
