# Event Hubs（本文監査の後続設計）の容量

[3環境の初期配備値](../environment-capacity.md)

`prod`・`stg` はPremium。23／45 MB/sに対し `prod` の通常6 PU・ストレス10 PU、64パーティションを検証候補とする。`stg` は1 PUから開始。`dev` は共通機能が成立するStandard 1 TUから開始する。ピークのバイト数、イベント数、パーティション偏り、消費の遅れ、CaptureからLocked WORMへの書込み、保持期間内の再処理を測る。1 TUの受信枠は1 MB/sまたは毎秒1,000件の先着。Premium専用機能を採用した場合は `dev` のSKUを見直す。検査ワーカーのチェックポイントの書込みは更新可能な専用の状態領域で測る。 [Event Hubsのスケール](https://learn.microsoft.com/en-us/azure/event-hubs/event-hubs-scalability) · [階層別の制限](https://learn.microsoft.com/en-us/azure/event-hubs/compare-tiers) · [チェックポイント](https://learn.microsoft.com/en-us/azure/event-hubs/event-processor-balance-partition-load#checkpoint)
