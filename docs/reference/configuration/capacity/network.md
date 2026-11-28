# VNet・Private Link・DNSの容量

[3環境の初期配備値](../environment-capacity.md)

| 用途 | 現行サイズでの確認 |
| --- | --- |
| App Gateway `/24` | 最大30インスタンスに30アドレス、Azure予約5。規模を増やしても現行区画に収まる。 |
| APIM `/26` | 12ユニット・内部VIP・Azure予約で30/64アドレス。34アドレスが残る。 |
| Firewall `/26`、管理VM `/26` | FirewallはPremiumの専用サブネット要件を満たす。管理VMの台数は現行どおり。 |
| Private Endpoint `/24` | Azure予約を引いて251アドレス。Foundryアカウントを追加する場合も現行区画を使い、Private Endpointに割り当てられるサービス別IP、DNS、許可ルールを確認する。 |
| Portal・Broker各 `/26`、証明書 `/27` | 現行のWorkload Profiles環境と各アプリ・Jobの規模を収容する。 |

公開例のVNetには、ACA用区画の末尾に未割当の `/26` と、別に予備の `/24` が残っている。将来の監査中継とワーカーを専用ACA環境に置くときは、まず空き `/26` を候補とし、選定したingress・ノード数・リビジョン切替時の同時起動を含めて確定する。今回の容量改修で既存のサブネットサイズを変更する必要はない。 [公開paramsの配置](../../../../params/prod.example.json) · [Firewallの専用サブネット](https://learn.microsoft.com/en-us/azure/networking/design-guide/azure-firewall#subnet-requirements) · [ACAのサブネット容量](https://learn.microsoft.com/en-us/azure/container-apps/custom-virtual-networks#subnet)

既存の区画を使い、構築後に使うIP、PE数、ルール、DNSレコード・リンク、名前解決時間を確認する。ハブ接続を使う環境はピアリングと名前解決の経路も試験する。公開DNSはAzure DNSのA／TXT更新と反映時間を確認する。 [リージョン別・ネットワークの制限](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits) · [Private DNSの制限](https://learn.microsoft.com/en-us/azure/dns/dns-private-records)
