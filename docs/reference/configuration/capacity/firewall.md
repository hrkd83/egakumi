# Azure Firewallの容量

[3環境の初期配備値](../environment-capacity.md)

設定正本は [サービス別JSON](../../../../infra/config/capacity/firewall.json)。

FirewallはTLS検査＋IDPS `Deny` を有効にした実経路で、帯域のほか、接続数、再接続集中、ヘルス、TLS処理の待ちを計測する。東西通信の無通信タイムアウトやスケールイン時のTCP切断があるため、15分間の継続配信と無通信後の復帰を分けて試験する。事前スケーリングは、標準容量で合格しない場合に必要容量を計測してから検討する。 [Firewallの性能・スケーリング](https://learn.microsoft.com/en-us/azure/firewall/firewall-performance) · [TCPタイムアウト](https://learn.microsoft.com/en-us/azure/firewall/tcp-session-behavior)

外向きのSNATポート使用率と閉域のTLS検査で成立した接続を別々に計測する。 [監視指標](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-network-azurefirewalls-metrics)
