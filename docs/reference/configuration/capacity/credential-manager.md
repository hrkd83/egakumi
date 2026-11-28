# Credential Manager・Entra・外部APIの容量

[3環境の初期配備値](../environment-capacity.md)

設定正本は [サービス別JSON](../../../../infra/config/capacity/credential-manager.json)。

| サービス | 容量を測る単位と受入 |
| --- | --- |
| Entra・マネージドID | 初回サインイン、OBO交換、同時起動・資格情報更新を別に集計し、テナント・アプリごとの429・遅延を測る。Azureと利用者Entraが異なる構成でも試験する。 |
| Microsoft Graph・APIM Credential Manager | Graphはアプリ・テナント・APIのスロットリングを確認する。個人CMではAPIMの本人照合とGraphの業務呼び出しを両方数える。CMは接続登録枠と認可呼び出し枠を別に見積もる。現行の12プール×8,000は登録枠で、1接続あたりの認可は最大250回/分。 [Graphの枠](https://learn.microsoft.com/en-us/graph/throttling-limits) · [Credential Managerの枠](https://learn.microsoft.com/en-us/azure/api-management/credentials-overview#limits) |
| 追加REST・MCPの提供先 | 提供先が公布したRPS、同時接続、処理・ストリーム時間、429の扱いを個別に登録する。異なる提供先での余剰を相互に使える前提を置かない。 |

ACMEの発行・再試行は共有ドメインの注文枠を消費する。環境並行構築ではARM・DNS更新・RBAC付与・登録のスロットリングと再開を試験する。 [公開DNSと発行手順](../../../setup/05-public-dns.md) · [ARMのスロットリング](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/request-limits-and-throttling)
