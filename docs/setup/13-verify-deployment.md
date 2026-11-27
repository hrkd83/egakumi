# 13. 初回構築の受入を確認する

前：[12. Portalを配備](12-deploy-portal.md) · [README](../../README.md)

[Core](08-provision-core.md)、[Entra登録](09-egakumi-portal.md)、[Gateway](11-deploy-gateway.md)、[Portal](12-deploy-portal.md)の各工程で成功を確認した環境を対象にします。配備結果と実際のAPI・認証・運用の確認を、それぞれ記録します。

## 配備結果・公開DNS・TLS

1. `azd up`による共用基盤の構築、Gateway個別配備、Portal個別配備の終了コードがそれぞれ`0`であることを確認します。今回更新された`.local/<environment>/state/`の`latest.json`、`gateway-deployment.json`、`portal-deployment.json`を、対象Subscriptionとリソースグループに照合します。アプリの記録では`status: deployed`、Portalでは`publicHealthVerified: true`も確認します。
2. `state/public-dns.json`でGateway・Portal用Aエイリアスの接続先が、`latest.json`の`outputs.gateway.publicIpId`と一致することを確認します。Gateway・Portalの公開ホストを解決し、TLS証明書のSAN・信頼チェーン・有効期限を確認します。
3. Azure Portalで対象の5つのリソースグループ、APIM・Foundry・App Gateway、選択したモデル、Portal・Brokerの状態を確認します。Portal本体への切替後に公開`/api/health`がHTTP 200を返すことと、App GatewayのPortalバックエンドが正常であることを確認します。

## GatewayとPortalの実接続

| 確認範囲 | 合格を判断する操作 |
| --- | --- |
| Gateway認証 | 正規の購読キーと、`Gateway.Invoke`を持つ利用者のBearerで、対象APIの実応答を確認する。無効な認証が拒否されることも確認する |
| Gateway経由のGraph | キー経路でCredential本人確認とToolsのCM接続を確認し、Bearer経路ではAPIアプリを使うOBOで実際に利用者のGraph情報を取得する |
| Portal認証 | Microsoftサインインとサインアウト、管理画面、Graphを使う所属・定期照合を確認する |
| PortalでのAPI試用 | 購読キーと利用者のBearerをそれぞれ選んでAPIを呼ぶ。別個のOAuth戻り先とセッションを確認する |
| 購読管理 | `Subscription.Manage`の委任で発行・一覧・失効を確認し、所属外の操作の拒否とAPIMからの制限照会を確認する |

対象APIの通常HTTP・SSE・WebSocket・MCPは、使う方式ごとに応答を確認します。App GatewayからFirewallを経由したAPIM／ACAへのHTTPS 443、TLS検査、入口証明なしの拒否も照合します。 [統合試験の手順](../testing/integration-tests.md) · [Portal認証の試験](../testing/portal-authentication-tests.md)

## 監視・保守・通信の受入

- 共用ACAとBroker用ACAの両方向の末尾DenyとFirewall経路を読み戻します。ACR Private Endpointからのイメージ取得、App／Jobの起動・スケールと監視、新規の未許可通信の拒否を確認します。 [ネットワークの受入条件](../reference/network-security-validation.md#実環境の受入確認)
- 本文監査が有効な環境では、採取対象APIの応答、監査記録の保存、権限と復元の条件を確認します。 [本文監査の受入](../reference/request-body-audit.md#azure受入)
- 通知先を使う場合は初回確認とテスト通知の受信を確認します。証明書専用ACA Jobの初回発行と定期更新設定、DNS用UAMIの関連付け・DNS側RBACを確認します。 [証明書用Job](../operations/certificate-jobs.md)

確認結果は同じ環境の保護領域に保存します。運用は[更新と運用](../operations.md)、構築試験が終わって資源を削除する場合は[撤去手順](../operations.md#撤去する)へ進みます。
