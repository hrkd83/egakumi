# Key Vaultの容量

[3環境の初期配備値](../environment-capacity.md)

設定正本は [サービス別JSON](../../../../infra/config/capacity/key-vault.json)。

既存の公開TLS用と業務用に加え、OBOの署名処理を専用のStandard Vaultへ分離する。Firewall検査用の中間CAは導入先が準備するVaultの版付き参照を維持する。 [既存の公開TLS運用](../../../setup/05-public-dns.md) · [Key Vaultのスロットリング対策](https://learn.microsoft.com/en-us/azure/key-vault/general/overview-throttling)

| 用途 | 負荷の見積もりと受入 |
| --- | --- |
| 公開TLS用Vault | 証明書の発行・更新とApp Gatewayによる参照。API要求数から切り離し、更新、App Gateway増設、設定更新が重なる場合を確認する。 [App GatewayとKey Vault](https://learn.microsoft.com/en-us/azure/application-gateway/key-vault-certs) |
| 業務用Vault | APIM Named Values、Portal・Brokerの秘密参照、Portal認証の署名。更新と同時起動、Portalの集中ログインを別に計測する。 [APIMのNamed Values](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-properties) |
| OBO署名用Vault | BrokerのGraph OBO用。非エクスポートRSA 3072、Private Endpoint、鍵単位の署名権限を使う。通常100／ストレス200 OBO RPSで署名上限、Entra交換、Graphの429を別々に確認する。 |
| Firewall中間CAのVault | Firewallの構築・再設定・CA更新が対象。既存の参照とRBACを保ち、CA更新時の取得とTLS検査の継続を確認する。 |

要求のたびに署名すると、100〜200 RPSでVaultが先に飽和し得る。RSA 3072ソフトウェア鍵の上限は1 Vault・リージョンあたり1,000件／10秒で、同区分内の他の鍵操作も加重計上される。100 RPSでは余裕がなく、200 RPSは上限を超す。アプリ自身を証明する短命な署名済みclient assertionをBrokerのプロセス内で再利用する。 鍵・アプリ・テナント・宛先の単位で分ける。期限と鍵の切替、同時再生成の抑止、利用者別の認証とclaims challengeを検証する。有効期間120秒を維持し最長60秒再利用する実装では、10レプリカ・1アプリ／テナント／宛先の組で平常10署名/分。全レプリカの起動・鍵更新・429時の復旧も試験する。この方式でもEntraへのOBO交換やGraphのAPI呼び出しは容量確認が必要。 [現行の署名コード](../../../../apps/credential-broker/credential_broker/obo.py) · [Key Vaultのトランザクション上限](https://learn.microsoft.com/en-us/azure/key-vault/general/service-limits) · [Microsoftのclient assertion再利用](https://learn.microsoft.com/en-us/dotnet/api/microsoft.identity.client.confidentialclientapplicationbuilder.withclientassertion)

各Vaultで10秒ごとの署名数、サービスの飽和率、429、p95/p99を記録する。通常ピークの目安は鍵操作枠の70%以内。1つの署名用Vaultで受入できず、要求ごとに署名する方式で200 RPSを支える場合は、専用Vault 3個に分ける案を比較する。均等な場合はストレス時に各約67署名/秒、通常100 RPSで1個停止した場合は残り各50署名/秒。実際の偏り、証明書登録・更新、サブスクリプション合計枠を確認する。Premiumへの変更だけではRSA 3072ソフトウェア鍵の枠は増えない。 [Key Vaultの上限](https://learn.microsoft.com/en-us/azure/key-vault/general/service-limits) · [Vaultの飽和監視](https://learn.microsoft.com/en-us/azure/key-vault/general/alert)
