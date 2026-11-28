# 仕様と構成のリファレンス

構築時に選ぶ設定、資材が実装する契約、運用時に参照する制約を扱います。初回構築と配備後の手順は[ドキュメント目次](../README.md)から進んでください。

## 基盤とネットワーク

- [基盤の構成](architecture/platform.md)
- [プラットフォームの通信](architecture/platform-communications.md)と[業務・管理の通信要件](architecture/network-communication-requirements.md)
- [NSG・経路](architecture/network-security-design.md)と[Firewallポリシー](architecture/firewall-policy-design.md)
- [SQLのPrivate Endpointと接続ポリシー](architecture/sql-connectivity.md)

## 設定項目

- [環境別params](configuration/parameters.md)と[検証・本番の標準config](configuration/config-options.md)

## Gatewayとモデル

- [公開API契約](gateway/api-contracts.md)、[API正本の定義](gateway/api-definitions.md)、[APIMのポリシー](gateway/apim-policy-catalog.md)
- [Foundryモデル](gateway/foundry-model-catalog.md)、[リアルタイム音声](gateway/realtime-audio.md)、[文書解析](gateway/document-analysis.md)

## 認証と本人接続

- [基盤のEntraアプリ](identity/entra-apps.md)と[Gatewayの認可](identity/gateway-authentication.md)
- [Credential Managerの本人接続](identity/credential-manager-login.md)、[接続プール](identity/credential-pool-design.md)、[クライアントのセッション契約](identity/connection-session-clients.md)

## 監査

- [監査ログの設計](audit/audit-log-design.md)、[監査ログの確認](audit/request-body-audit.md)、[監査で使う識別情報](audit/audit-identification.md)
