# 運用の受入試験

現行資材で構築した検証環境を使います。先に対象、保護params、前回の配備記録、復旧に使う値を照合し、計画モードがある操作は計画を実行します。変更後に実効設定と代表フローを確認し、試験用の変更を戻します。業務を中断する試験と復旧の試験は専用環境で実施します。

| 作業 | 確認すること |
| --- | --- |
| [Gateway・Portalの再配備](../operations/deployment.md#同じ環境を更新する) | 対象の設定・digest、既存セッションまたは購読の保持、代表APIの成功と未認証の拒否、中断後の再開 |
| [API・モデルの変更](../operations/apim.md) | ポリシーの適用先、公開操作、固定バックエンドとFirewall許可、必要なWAF、監査、元の設定への復旧 |
| [WAF・監視の保守](../operations/platform-settings.md) | 対象ポリシーだけの変更、Prevention、正常な本文と意図した拒否、テスト通知と相関ログ |
| [公開TLS・Entraシークレット更新](../operations/secrets-and-certificates.md) | 新旧の版、Key Vault参照、AppGWへの公開TLSの反映と、内部接続のSNI・証明書検証、CMの新規本人接続、旧資格情報の回収 |
| [Portalの署名証明書更新](../operations/portal-authentication.md) | ACA Jobによる候補発行、手動登録後の切替、ログイン・API試用、旧版での復旧と旧鍵の扱い |
| [APIM購読キー](../operations/access-and-keys.md) | 検証用キーの例外発行、対象外APIの拒否、主副の切替、旧値と停止後の拒否 |
| [Credential Broker](../operations/credential-broker.md) | 照合Jobが対象台帳とCMを照合し、未使用接続の回収・再試行を行う。SQL復元後にCM側と再照合し、正規の新規認証で再開する |
| [管理VMとSQL](../operations/management-vm-access.md) | 対象の担当者だけが付与中に接続・保守でき、付与前と回収後は拒否される。 [SQL側の担当者権限](../operations/management-sql-access.md) |
| [NSGと経路](../operations/network-security-groups.md) | 採用する通信の実効規則、必要な業務・管理通信と未許可通信、変更の再開と正常化 |

## 判定と記録

計画、実行結果、読戻し、実際の業務呼び出し、復旧を分けて記録します。証跡は同じ環境の `.local/<environment>/evidence/` に保護保存します。 [テストの共通ルール](../../DEVELOPMENT.md#テストの共通ルール)
