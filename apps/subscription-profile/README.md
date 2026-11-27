# APIM利用枠照会サービス

APIMからのHTTPS照会を内部Azure Container Apps環境のロードバランサーで受け付けます。APIMサブネットのIngress許可と照会専用の共有鍵を使用し、SQLの読取り専用プロシージャから購読の制限値を取得します。

起動点は[server.mjs](server.mjs)です。接続の実装は[共有ランタイム](../portal/lib/subscription-service/)にあり、[正式なPortal配備](../../docs/setup/12-deploy-portal.md)で同じイメージに組み込み、専用の実行IDを持つACAとして起動します。
