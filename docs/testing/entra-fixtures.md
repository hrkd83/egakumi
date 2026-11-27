# Entraテストデータの準備

対象環境の認証テナントで、サインイン・Bearer試用・Graph OBOを確認するテスト利用者を準備します。[PortalのEntra登録](../setup/09-egakumi-portal.md) · [構築後の受入](../setup/13-verify-deployment.md)

| 参照名 | 架空のUPN例 | 用途 |
| --- | --- | --- |
| U-A | `example-test-user-a@example.com` | 許可された利用者。架空の氏名、部署等をGraphの表示確認に使う |
| U-B | `example-test-user-b@example.com` | 許可された別利用者。U-Aのログインや試用の取引を引き継げないことを確認 |
| U-C | `example-test-unassigned@example.com` | 必要な割当を付けない対照。対象のEntraアプリで割当を強制している場合に使用 |

1. **Microsoft Entra ID → ユーザー → 新しいユーザー** でテスト利用者を作成する。
2. 割当を必須にしている場合は、対象アプリの **エンタープライズ アプリケーション → ユーザーとグループ** でU-AとU-Bを割り当てる。
3. 試験先と割当を再確認し、実際の識別子と証跡は環境の保護記録にだけ残す。
4. 作業終了後、専用に作成したテスト利用者と割当を回収する。購読キーの例外経路で使う購読は、基盤管理者が別に発行して回収する。[APIM購読運用](../operations/access-and-keys.md)
