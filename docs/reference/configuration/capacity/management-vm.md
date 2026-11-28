# 管理VMとディスクの容量

[3環境の初期配備値](../environment-capacity.md)

設定正本は [サービス別JSON](../../../../infra/config/capacity/management-vm.json)。

128 GiBから開始し、最大イメージの閉域ビルド・検証時のCPU、メモリ、OSディスクI/O、空き容量、所要時間を測る。異なるイメージの同時ビルドも検証する。空き30%を増量の判断に使い、CPU・メモリならD4as_v6、持続I/OならディスクSKU、空き不足なら容量を個別に見直す。 [VMサイズの仕様](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dasv6-series) · [ディスクSKU](https://learn.microsoft.com/en-us/azure/virtual-machines/disks-types#standard-ssds)
