# 購読管理サービス

Portalと同じ内部Azure Container Apps環境で、環境内限定のHTTPS ingressを提供します。Portalクライアント向けに発行された本人の委任トークンと現在のグループ所属を検証し、SQL台帳の購読管理、グループ・会社設定、利用枠の更新、APIM購読との同期を実行します。

起動点は[server.mjs](server.mjs)です。管理操作と認可の実装は[共有ランタイム](../portal/lib/subscription-service/)にあり、[正式なPortal配備](../../docs/setup/12-deploy-portal.md)で同じイメージに組み込み、専用の実行IDを持つACAとして起動します。
