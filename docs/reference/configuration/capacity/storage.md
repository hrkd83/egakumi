# 監査・証明書状態Blobの容量

[3環境の初期配備値](../environment-capacity.md)

設定正本は [サービス別JSON](../../../../infra/config/capacity/storage.json)。

監査用StorageV2は基盤・Foundryの2アカウント、証明書状態用は別の1アカウント。Standardの既定容量5 PiBに対し、将来の監査本文は最初の2,557日で約396 TB・ストレス時約45 MB/sの試算。WORMの保護期限が終わった後の削除は別途運用し、それまでに蓄積する量も追跡する。診断・Exportの追加データ量、WORM書込みと復元、監査照会との同時利用、500/503を測る。証明書状態は更新可能なBlobで、排他と同時実行の失敗を確認する。 [StorageV2の制限](https://learn.microsoft.com/en-us/azure/storage/common/scalability-targets-standard-account) · [WORMとライフサイクル](https://learn.microsoft.com/en-us/azure/storage/blobs/lifecycle-management-policy-structure#actions)
