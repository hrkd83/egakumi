# 共有Azure SQLの接続方式

共有Azure SQLは接続ポリシーを`Proxy`に固定する。クライアントの通信許可を、構築SQL Private EndpointへのTCP1433に絞れるためである。

対象はPortal、Credential BrokerとそのJob、SQL初期化Job、管理VMが利用する共有SQLサーバーである。APIM製品自身のSqlサービスタグ向け通信は別の経路として定義する。 [APIMのSQL基盤通信](network-communication-requirements.md#flow-apim-sql-platform)

## ネットワーク上の採用理由

Azure SQLのProxyは、接続中のSQL通信をAzure SQL Gateway経由で転送する方式である。クライアントからSQL Private EndpointへはVNet内で直接接続する。

| 接続方式 | Private Endpointで必要なポート | この基盤での判断 |
| --- | --- | --- |
| Proxy（採用） | TCP1433 | ACA・管理VMの送信とSQL PEの受信を1433に揃え、宛先を構築SQL PEの全IPに限定する。保守clientも接続前にProxy設定を照合する。 |
| Redirect | TCP1433–65535 | MicrosoftのPrivate Endpoint仕様に従い、クライアント側の送信とPE側の受信許可を広げる必要がある。送信元とSQL PEへの宛先制限は維持できるが、driver・NSG・構築・保守の整合した変更と実通信確認が必要になる。 |

Private EndpointのRedirect要件は、公開endpointで使うTCP1433とTCP11000–11999の要件とは異なる。[MicrosoftのPrivate Endpoint仕様](https://learn.microsoft.com/en-us/azure/azure-sql/database/private-endpoint-overview?view=azuresql#use-redirect-connection-policy-with-private-endpoints)を基準にする。Proxyの選択はPrivate Endpoint、SQL接続時のTLS検証、EntraのManaged Identity認証と両立する。

実装の正本は[共有SQLのconnection policy](../../../infra/modules/services/shared-sql.bicep)、[SQL向けNSG](../../../infra/modules/network/credential-broker-network.bicep)、[管理clientの前提照合](../../../scripts/deployment/helpers/management_client_assets.py)である。通信の対応は[ACAからSQL](network-communication-requirements.md#flow-aca-sql)と[管理VMからSQL](network-communication-requirements.md#flow-mgmt-sql)を参照する。

## Portalの用途

PortalのSQLは、サインインとAPI試用の保存・有効性確認に使う。試用トークンを更新するときはDB時刻に基づく共有リースを使い、Entraとの通信を終えてから更新結果を照合する。APIM購読は基盤管理者がAzureポータルで管理する。[認証の保存実装](../../../apps/portal/lib/api-auth/) · [購読運用](../../operations/access-and-keys.md)
