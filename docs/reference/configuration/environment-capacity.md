# prod・stg・devの容量設計

[Gatewayの性能目標](../../testing/gateway-capacity.md)に沿った新規構築の標準値。App Gateway・APIM・Broker・Broker SQLの標準容量は公開paramsに記入してある。Foundryのモデルは全環境で新規配備ごとに最小容量を使い、構築後に各サブスクリプションのクォータと必要な処理量に合わせて割当を調整する。処理能力は本番相当のAzure経路で検証する。 [必須の容量設定と公開例](parameters.md#環境ごとに確定する容量) · [Foundryの容量とクォータ](capacity/foundry.md) · [本番の概算月額](production-running-cost.md)

## 環境ごとの役割

| 環境 | 利用者 | 機能と容量 | 受入 |
| --- | --- | --- | --- |
| `prod` | 本番利用を認可した利用者 | 全機能を有効化し、本番の処理量、同時接続、縮退時の容量を確保 | 通常ピーク500 RPS・同時SSE 12,000本を60分、ストレス1,000 RPS・同時SSE 24,000本を15分、平均60秒のSSEを混在させる試験、240秒超および15分のSSE |
| `stg` | 指定した管理者と承認済みの検証ID | `prod` と同じSKU・ポリシー・閉域接続・診断・保全・ゾーン設定方式。ユニット数やレプリカ数などの容量を最小化 | 同じ認証・検査・公開経路で機能受入。容量受入をここで実施するときは、試験中だけ `prod` と同じ容量とバックエンド条件に合わせる |
| `dev` | 指定した開発者・検証ID | 本番と同じ認証・API・ネットワーク制御・診断・保全の機能を使える最小構成。SKUを下げる場合は、必要機能と運用手順が成立するSKUを選ぶ | 開発時の構築、API・モデル試用、保守・復旧手順の機能受入 |

## 公開例の初期配備値

| 対象 | `prod` | `stg` | `dev` |
| --- | --- | --- | --- |
| [App Gateway・WAF](capacity/application-gateway.md) | `WAF_v2`、Prevention、20〜30インスタンス | `WAF_v2`、Prevention、1〜2 | `WAF_v2`、Prevention、1〜2 |
| [Firewall・Policy](capacity/firewall.md) | Premium、TLS検査、IDPS `Deny`、標準の自動スケール | 同左 | 同左 |
| [APIM](capacity/apim.md) | Classic Premium、12ユニット、内部VNet統合、自動ゾーン分散 | 同SKU・同統合・同ゾーン方式、1ユニット | Classic Developer、内部VNet統合、1ユニット |
| [Foundry・モデル](capacity/foundry.md) | `S0` 1アカウント。各モデルを最小容量で初期配備し、公開前にクォータと処理量に合わせて配備の容量を調整する | 同じアカウントSKUとモデル／版／配備種別。初期容量は配備ごとの最小値。接続先ごとの経路も少量で検証 | 同じアカウントSKUとモデル／版／配備種別。初期容量は配備ごとの最小値。同じバックエンド機能を確認 |
| [ACA](capacity/container-apps.md) | 機能ごとにCPU・メモリ・レプリカ数を配備 | 同じWorkload Profiles環境。常駐アプリのレプリカを縮小 | 開発・試用に必要な最小容量 |
| [証明書Jobの状態Blob](capacity/storage.md) | `Standard_LRS` | 同左 | 同左 |
| [Azure SQL](capacity/azure-sql.md) | 共有論理サーバー1つ、DBはBroker `GP_Gen5_4`、Portal認証 `GP_Gen5_2`。 | 同じGeneral Purpose、Broker・Portal認証は各 `GP_Gen5_2` | Broker `GP_Gen5_2`。Portal認証はBasic 5 DTU |
| [Key Vault](capacity/key-vault.md) | Standard。公開TLS用、業務用、OBO署名用の3用途を分離。 | 同左 | 同左 |
| [ACR](capacity/acr.md) | Premium 1個・Private Endpoint・公開アクセス無効 | 同左 | 同左 |
| [標準監査ストレージ](capacity/storage.md) | 用途別2アカウントのStorageV2 `Standard_GRS`、Locked WORM 2,557日 | 同左 | 同じ保全・復元機能を用意し、Locked WORMは合成データで7日の初期値。StorageV2 `Standard_GRS` |
| [Azure Monitor](capacity/azure-monitor.md) | Log Analytics `PerGB2018` 1つ・90日、ワークスペース型Application Insights、診断・Data Export。ワークスペースは日次上限なし。Application Insightsは100 GB/日、80 GBで警報 | 同左 | 同じ収集・検索・エクスポートを検証 |
| [管理VM・ディスク](capacity/management-vm.md) | `Standard_D2as_v6` 1台（2 vCPU・8 GiB）、`StandardSSD_LRS` 128 GiB、閉域保守・ACRビルド | 同左 | 同じ保守・ビルド手順を維持する初期値として同左 |
| [ネットワークとDNS](capacity/network.md) | VNet、サブネット、NSG、UDR、Standard Public IP、Private Endpoint、Private DNS。公開Azure DNSゾーンは事前準備 | 同じネットワーク機能、入口は管理者用の接続元に限定 | 同じネットワーク機能、環境別の接続先 |
| [Credential Manager・外部API](capacity/credential-manager.md) | 接続登録・認可枠、Entra・Graph・各バックエンドのクォータを個別に確認 | 同じ機能を管理者の負荷で確認 | 同じ機能を最小負荷で確認 |

`stg` でも保護期間中のWORMデータは残る。撤去時は保護されたBlobを保持し、作り直しを繰り返す検証は保持が短い `dev` を使う。 [Blobの保持ポリシー](https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-container-level-worm-policies)

## 環境の選択とstgへのアクセス

- `params/{prod,stg,dev}.example.json` と `infra/config/{prod,stg,dev}.json` を使う。準備コマンドで `--profile prod|stg|dev` を必須にし、サンプルと正本の組を選択する。`environmentName` は従来どおり最大4文字のリソース識別子とし、SKUは名前から推測しない。保護されたparamsの `configFile` は、選択した標準configを指す生成項目とする。初回、再開、個別配備、更新、撤去で同じプロファイルを使う。配備出力とチェックポイントにもプロファイルを記録し、操作前に照合する。
- paramsは導入先の容量、リージョン、VNet・サブネット、公開名、ID等の環境入力を持つ。paramsの容量は各公開例から引き継ぎ、必須値が欠落していれば設定検証で停止する。configでSKU・ゾーン・冗長化・保持・共通機能と、Foundryのモデル・版・配備SKUを管理する。3環境の公開DNS、Entraアプリ、マネージドID、秘密、運用状態を分ける。ハブに接続するVNetのアドレスは重複させない。
- `stg` は管理用VPN／踏み台等の承認済み送信元をparamsで必須にする。App GatewayサブネットのHTTP/HTTPS許可をその送信元に絞り、Azureの管理通信と正常性プローブを維持する。初回構築・証明書確認・PortalからのAPI試用で使う既定の内部経路と送信元を実環境相当の経路で確認する。管理経路外からは到達を拒否する。 [App GatewayのNSG要件](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure#inbound-rules)
- `stg` のPortalとGatewayのBearer認証では、環境専用Entraアプリに割り当てた管理者・検証IDを許可する。必要なアプリロールは受信側でも検証し、環境間のトークンを拒否する。購読キーは機能受入で必要な場合だけ `stg` 専用を発行し、同じ管理経路に限定する。ネットワーク制限、本人認証、RBACは別々に受入確認する。 [Entraアプリの割り当て](https://learn.microsoft.com/en-us/entra/identity-platform/howto-restrict-your-app-to-a-set-of-users)

## Azureでの受入順序

1. [新規構築手順](../../../README.md#新規クローンから構築する)で共用基盤を構築し、GatewayとPortalを個別に配備する。対象リージョンでSKU、ゾーン、容量枠を確認し、`stg` では接続元とアプリロールの許可・拒否も確認する。
2. 合成バックエンドで通常・ストレス負荷をかけ、RPS、同時SSE、99.9%以上の正常完了、15分のSSE、縮退時の余力を計測する。Foundry・認証・ツールは各サービスの実クォータと実測で確認する。 [負荷試験の判定基準](../../testing/gateway-capacity.md#azureでの受入目標)
3. 本文監査を追加した段階で、監査中継、[Event Hubs](capacity/event-hubs.md)、検査ワーカーを含む経路に同じ業務負荷をかける。
