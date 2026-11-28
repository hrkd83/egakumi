# 基盤の構成と通信の境界

[初回構築手順](../../../README.md#新規クローンから構築する)で、共用基盤、Entraの登録、GatewayとPortalの個別配備を順に行います。[構築テスト](../../testing/construction-tests.md)で工程ごとの完了を確認します。

| 工程 | 主な資材と役割 |
| --- | --- |
| Core | VNet・NSG・経路、App Gateway、Firewall、APIM、Foundry、SQLサーバー、ACA環境、非公開ACR、初期公開TLSとEntra登録用証明書 |
| Gateway | 公開API・ポリシー・Product・WAF、Credential Manager、Credential Broker・定期照合Jobと資格情報DB |
| Portal | Entraサインイン、本人のBearerによるAPI試用、専用セッションDBとアプリ |
| 管理・監査 | 管理VMの保守経路、Log Analytics、Application Insightsと監査Storage |

## APIと認証の参照先

- 社員は `Gateway.Invoke` を持つ本人Bearerを使います。例外発行の購読キーはAPIMで管理します。GraphはBearerでOBO、購読キーでCredential Managerの本人接続を利用します。 [Entraアプリの役割](../identity/entra-apps.md) · [Gatewayの認可](../identity/gateway-authentication.md)
- 公開URL・操作・モデルは[API契約](../gateway/api-contracts.md)と[Foundryモデル](../gateway/foundry-model-catalog.md)を参照してください。会話・ファイル音声・双方向音声の接続方式は[リアルタイム音声](../gateway/realtime-audio.md)で確認します。
- 診断ログ、利用量集計と、取得できるFoundryのトレースを相関します。 [監査に使う識別情報](../audit/audit-identification.md) · [本文監査](../audit/request-body-audit.md) · [ログのクエリ保護](../../testing/log-privacy.md)

## 構築後の保守

[証明書の運用](../../operations/certificate-jobs.md)と[監視・基盤設定](../../operations/platform-settings.md)を参照してください。稼働中のAPIとPortalの更新は[個別配備と再開](../../operations/deployment.md)で行います。

## 公開入口と内部HTTPS

AppGWが公開TLSを終端してWAFで検査し、`X-Core-L7-Ingress`を上書きします。APIMの共通ポリシーが入口の証明を検査します。AppGWは公開側でHTTP/2を受け付け、内部のAPIMとPortalにはFirewallのTLS検査を経由してHTTPS 443で接続します。APIMのServer.Http2は文字列`"false"`です。 [通信要件](network-communication-requirements.md#公開入口と業務内部接続)

APIMの既定Gatewayはカスタムドメイン追加後も利用でき、削除できません。HTTPS保守では`<APIM_NAME>.azure-api.net`をPrivate DNSで内部VIPへ解決し、同名のHost／SNIを使うとMicrosoft管理の既定証明書で接続できます。公開URL・OAuth／CMの案内はAppGWの公開名を維持します。[既定GatewayとSNIの公式説明](https://learn.microsoft.com/azure/api-management/configure-custom-domain)、[Internal VNetの既定FQDNとDNS](https://learn.microsoft.com/azure/api-management/api-management-using-with-internal-vnet)
