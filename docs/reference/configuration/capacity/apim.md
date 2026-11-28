# API Managementの容量

[3環境の初期配備値](../environment-capacity.md)

SKUは[サービス別config](../../../../infra/config/capacity/apim.json)、ユニット数はparamsの `capacity.apim.units` で指定する。`prod` の公開例は12ユニットで、入力は2ユニット以上とする。`stg` の公開例は1ユニット、`dev` は1ユニット固定。 [容量の入力項目](../parameters.md#環境ごとに確定する容量)

`dev` のAPIM Developerは現行テスト資材でも採用している。内部VNet、閉域バックエンド、TLS、Bearer／購読キー認可、Graph OBOとCredential Manager、通常HTTP・SSE・WebSocket・MCP、診断ログを使う開発・機能試験を維持する。DeveloperはSLAがなく、複数ユニット、自動スケール、可用性ゾーン、複数リージョン、Workspacesを使えない。バックエンド同時接続は1ホストあたり1,024本、組み込みキャッシュは10 MB。資材のPremium向け容量アラートや可用性・スケールの確認は `stg` で行う。`stg` は自動ゾーン方式のPremium 1ユニットから構築し、1ユニットではゾーン障害に備えた複数ユニットの冗長性は得られない。縮退や本番負荷の試験では本番相当のユニット数を用意する。 [APIMのSKU比較](https://learn.microsoft.com/en-us/azure/api-management/api-management-features) · [APIMの実行時上限](https://learn.microsoft.com/en-us/azure/api-management/api-management-gateways-overview#gateway-runtime-limits) · [APIMのゾーン分散](https://learn.microsoft.com/en-us/azure/api-management/enable-availability-zone-support)

Classic Premiumのバックエンド同時接続上限は、1ユニット・HTTP接続先あたり2,048本。HTTP/1.1で各SSEが1本を占有する場合、12ユニットは同一ホストあたり24,576本。12,000本で約49%、24,000本で約98%になるので、ストレス時は実負荷が異なるホストへ分かれるようにする。短いHTTP、再接続、ユニット間の偏りも測る。ユニットの増設完了を待たずに負荷が来る前提で12ユニットを配備する。現行のAPIM専用 `/26` は、12ユニット分の24アドレス、内部VIP 1、Azure予約5の合計30を収容できる。ゾーンと管理用公開IPの要件は構築リージョンとAPIで事前確認する。 [実行時上限](https://learn.microsoft.com/en-us/azure/api-management/api-management-gateways-overview#gateway-runtime-limits) · [SKU比較のリージョン別ユニット数](https://learn.microsoft.com/en-us/azure/api-management/api-management-features) · [VNetとIP消費](https://learn.microsoft.com/en-us/azure/api-management/virtual-network-injection-resources)

SSEはホスト別に、合成バックエンド側でも同時接続を数える。WebSocketは、5,000同時接続/ユニットと1サービス60,000本を起点に、開始集中と再接続を試験する。 [実行時上限](https://learn.microsoft.com/en-us/azure/api-management/api-management-gateways-overview#gateway-runtime-limits)
