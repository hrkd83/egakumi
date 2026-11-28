# Application Gateway・WAFの容量

[3環境の初期配備値](../environment-capacity.md)

3環境とも[サービス別config](../../../../infra/config/capacity/application-gateway.json)で `WAF_v2` を使う。AppGWと入口のStandard公開IPのゾーン番号は省略し、Azureに配置を任せる。可用性ゾーンに対応するリージョンでは、両方が自動でゾーン冗長になる。自動スケールの最小・最大インスタンス数はparamsの `capacity.applicationGateway` で指定する。 [容量の入力項目](../parameters.md#環境ごとに確定する容量) · [AppGW v2のゾーン設定](https://learn.microsoft.com/en-us/azure/reliability/reliability-application-gateway-v2#configure-availability-zone-support) · [Standard公開IPの可用性ゾーン](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/public-ip-addresses#availability-zone)

モデルと追加HTTPの平均本文で、通常約138 Mbps・ストレス約275 Mbps。30%の試算余裕を含めると、App Gatewayの帯域成分は切り上げで81・162 Capacity Units。20インスタンスで最低200 Capacity Unitsを事前確保する。TLSとWAF処理、同時接続、大きいJSONにかかる計算量は別途計測し、20〜30の範囲が妥当か判断する。現行の専用 `/24` はこの最大数を収容できる。App Gatewayでは応答のバッファを無効にする現行設定を使い、イベント間の無通信とスケールインでSSEがどう切断されるかを確認する。 [App Gatewayの容量単位](https://learn.microsoft.com/en-us/azure/application-gateway/understanding-pricing) · [サブネット要件](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure#size-of-the-subnet) · [SSEの要件](https://learn.microsoft.com/en-us/azure/application-gateway/use-server-sent-events)

WAFの負荷はルール・除外・入力サイズで変わる。Preventionと経路別の検査を維持し、生成した実ポリシーのルール数、コード・JSONのWAF評価時間、正常要求の拒否率を確認する。 [WAFの上限](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#application-gateway-limits) · [監視指標](https://learn.microsoft.com/en-us/azure/application-gateway/monitor-application-gateway-reference)
