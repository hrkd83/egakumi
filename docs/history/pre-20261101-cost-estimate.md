# 整理前の費用試算

> 履歴資料：2026-11-01の文書整理開始前のREADMEから保存した記録です。2026-10-22時点の単価と、当時列挙した一部の基盤資源を対象にしています。現在のCore全体の費用見積もりではありません。特に共用SQL、Portal・BrokerのContainer Apps、ジョブ、非公開ACRと追加のPrivate Endpoint等を含む全体費用は、この金額から判断できません。

現行構成の確認項目は [README](../../README.md#費用を確認する)を参照してください。以下は整理前の本文であり、今回再計算・再検証したものではありません。

## 月額費用の目安

**月額費用は「基盤の稼働費＋LLM等の利用料＋通信・監視・保存等の利用料」です。** 次の金額は、現行configの主要な基盤リソースを1か月（730時間）稼働させた場合の目安です。APIを呼ばなくても基盤の稼働費は発生します。

| 構成 | 主な設定 | 主要基盤の稼働費／月 | 別途加算される費用 |
| --- | --- | --- | --- |
| テスト：[test.json](../../config/test.json) | Firewall Standard、APIM Developer 1ユニット、AppGW WAF v2 最小0台 | **約23万円** | LLM・画像・音声等の利用料、通信量、AppGWの使用CU、ログ・監査データ等 |
| 本番：[production.json](../../config/production.json) | Firewall Premium、APIM Premium 1ユニット、AppGW WAF v2 最小2台 | **約75万円** | LLM・画像・音声等の利用料、通信量、AppGWの最低容量を超えるCU、ログ・監査データ等 |

算定は**2026年10月22日確認のAzure公開従量単価（円参考値・税別、契約割引・予約なし）**に基づきます。リージョンは公開paramsの初期値（ネットワーク・APIM・管理VMはJapan East、FoundryはEast US 2、API CenterはEast US）を想定しています。実際の請求は契約・リージョン・SKU・利用量によって変わり、この表は支払総額や上限を示すものではありません。[Azure公開単価APIの説明](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)

<details>
<summary>主要基盤の月額内訳と計算条件</summary>

| 費目 | テスト | 本番 |
| --- | ---: | ---: |
| Firewallの稼働費 | 約143,764円 | 約201,270円 |
| APIMの稼働費 | 約7,568円 | 約440,379円 |
| AppGWの固定費＋最低予約容量 | 約60,036円 | 約93,159円 |
| Linux管理VM 1台＋OSディスク | 約15,084円 | 約15,084円 |
| Public IP 2個＋Private Endpoint 2個＋Private DNS 5ゾーン | 約3,844円 | 約3,844円 |
| **主要基盤の小計** | **約230,296円** | **約753,736円** |

計算は時間単価×730時間に、月額のディスク・DNSゾーン料金を加えたものです。管理VMは`Standard_D2as_v6`を常時起動し、OSディスクは128 GiBのStandard SSD LRSを1枚使う前提です。AppGWは最小0台でも固定費がかかります。本番の最小2台は20 CUの予約容量に相当し、その料金を小計に含めています。テストの使用CUと、本番の20 CUを超える使用分は追加です。APIM Premiumは1ユニット・単一リージョンとして計算しています。[AppGWの課金方式](https://learn.microsoft.com/en-us/azure/application-gateway/understanding-pricing)、[APIMの料金・プラン](https://azure.microsoft.com/en-us/pricing/details/api-management/)

小計に含めていない費用は、次のとおりです。

- **モデル・AI機能：** LLM・Embedding・リランク等のトークンや処理量、画像生成枚数、音声の処理時間・文字数、PII解析等。現行configのモデルは従量課金で、モデルごとの単価と実際の利用量に応じて加算します。
- **通信・運用：** Firewall／Private Endpointの処理データ量、データ転送、追加CU、監視アラート・通知、ログ取り込み・エクスポート・保持、監査Storageの保存・操作・複製、Key Vaultの操作、ディスクI/O等。本番の監査StorageはGRS・2557日保持の設定で、蓄積した保存量に応じた費用が継続します。
- **Core外の共有資源：** 公開DNS・ドメイン、共有ハブ・ExpressRoute／VPN・中央DNS Resolver等。

API Centerは現行configのFreeプランを前提とし、プランの適用条件や有料プランへの移行は別途確認します。

</details>

短時間の検証では、構築・試験・撤去までの実稼働時間に応じて時間課金部分を抑えられます。検証が終わったら[撤去手順](../operations.md#撤去する)で削除を確認してください。SKUや台数を変更する場合は、変更後の設定で再見積もりします。


[履歴一覧](README.md)
