# Egakumi勉強会：動画と対応資料

[YouTube再生リスト：企業向けAI基盤を学ぶ｜Egakumi勉強会](https://www.youtube.com/playlist?list=PLNZqnNo-J21s) · [公開リポジトリ：hrkd83/egakumi](https://github.com/hrkd83/egakumi)

開発者・インフラエンジニア・データエンジニアなど、企業・組織のAI基盤に関わる方へ向けた勉強会です。3人の対話と教材用の設定画面・図を使い、通信経路、各層の防御、Portalによる管理を学びます。

受講順は **導入回 → 第01〜08回 → 補足A1 → 補足A2 → 第09〜18回** です。各回の対応資料から、設定の意味・実装・確認先を読み進められます。

音声には生成AIを使用しています。設定値・画面は説明用です。稼働環境の操作記録や実測結果を示すものではありません。

| 順番 | 動画 | 長さ | 対応資料 |
| --- | --- | ---: | --- |
| 1 | [【Egakumi勉強会】導入回｜AIを組織の働き手にする](https://youtu.be/ECH7GexAMJM) | 16:29 | [学習ガイド](index.html)・[Coreの全体像](01-network-control.html#network-overview) |
| 2 | [【Egakumi勉強会】第01回｜ネットワークの全体像](https://youtu.be/wWu86yBSXTw) | 15:58 | [構造と配置](01-network-control.html#purpose)・[全体図](01-network-control.html#network-overview) |
| 3 | [【Egakumi勉強会】第02回｜DNSとPrivate Endpoint](https://youtu.be/Qo7AB-61YuU) | 16:49 | [DNS・PE](01-network-control.html#dns-vnet) |
| 4 | [【Egakumi勉強会】第03回｜NSGによる通信制御](https://youtu.be/LFx_SwSVD08) | 24:40 | [NSG](01-network-control.html#nsg-associations) |
| 5 | [【Egakumi勉強会】第04回｜UDRと往復経路](https://youtu.be/pbwSQDHiJ0I) | 15:35 | [UDR](01-network-control.html#udr-associations)・[往復経路](01-network-control.html#network-egress-management) |
| 6 | [【Egakumi勉強会】第05回｜Firewallは何を判断するか](https://youtu.be/hEbt0PxWLmg) | 15:48 | [Firewallの役割](01-network-control.html#fw-roles)・[評価と判断](01-network-control.html#fw-decisions) |
| 7 | [【Egakumi勉強会】第06回｜外向き通信と出口IP](https://youtu.be/fbn9nT1C5eg) | 16:35 | [Web依存先](01-network-control.html#fw-base-applications)・[出口IP・SNAT](01-network-control.html#egress-explicit) |
| 8 | [【Egakumi勉強会】第07回｜つながらないときの調べ方](https://youtu.be/0AHwkKfdd7I) | 16:48 | [ログ・監視](01-network-control.html#observability-logs)・[制御対応表](01-network-control.html#network-control-map) |
| 9 | [【Egakumi勉強会】第08回｜基盤管理者の接続と作業実行](https://youtu.be/56Qi3exn3io) | 16:40 | [管理接続](01-network-control.html#maintenance-network) |
| 10 | [【Egakumi勉強会】補足A1｜ハブ＆スポーク接続と往復経路](https://youtu.be/-jikPDlFf4Q) | 15:47 | [接続範囲](01-network-control.html#hub-spoke)・[往復経路](01-network-control.html#hub-udr) |
| 11 | [【Egakumi勉強会】補足A2｜ハブ接続後のDNS・通信制御と切替](https://youtu.be/zXCn0GZ3ktU) | 16:16 | [中央DNS](01-network-control.html#hub-dns)・[切替・再配備](01-network-control.html#hub-rollout) |
| 12 | [【Egakumi勉強会】第09回｜WAFの検査と誤検知への対応](https://youtu.be/yI3xykarge0) | 14:50 | [WAFポリシー](02-external-protection.html#policy) |
| 13 | [【Egakumi勉強会】第10回｜Firewallの許可とIDPSの検知](https://youtu.be/k78GtGOSUEI) | 16:15 | [IDPS](02-external-protection.html#idps-settings) |
| 14 | [【Egakumi勉強会】第11回｜APIMの認証と入力検査](https://youtu.be/BR1a8IjU8m8) | 15:49 | [APIMの実行順](02-external-protection.html#apim-scope) |
| 15 | [【Egakumi勉強会】第12回｜利用制限と下流認証](https://youtu.be/vv18zK2_NBc) | 15:55 | [利用制限](02-external-protection.html#apim-rate) |
| 16 | [【Egakumi勉強会】第13回｜エラーを追い、安全に設定を変える](https://youtu.be/ZHtRVaY9GsY) | 17:00 | [調査・保守](02-external-protection.html#observability) |
| 17 | [【Egakumi勉強会】第14回｜誰が何を管理できるのか](https://youtu.be/_AbT5d3WaJs) | 16:04 | [Portal全体像](03-subscription-management.html#purpose)・[認証](03-subscription-management.html#signin) |
| 18 | [【Egakumi勉強会】第15回｜APIキー発行の裏側](https://youtu.be/brqKjbfVxOM) | 16:48 | [APIキー発行](03-subscription-management.html#key-create) |
| 19 | [【Egakumi勉強会】第16回｜仕様を調べ、APIを試す](https://youtu.be/i7eReCtsTWo) | 17:11 | [カタログ](03-subscription-management.html#catalog)・[API試用](03-subscription-management.html#execution) |
| 20 | [【Egakumi勉強会】第17回｜発行後のキー管理](https://youtu.be/IlNexsGGdqY) | 16:04 | [キー管理](03-subscription-management.html#key-operations)・[利用制限](03-subscription-management.html#limits) |
| 21 | [【Egakumi勉強会】第18回｜組織・SQL・APIMの状態を整える](https://youtu.be/OEDPp3tTwDA) | 16:35 | [グループ観測](03-subscription-management.html#group-observation)・[SQL・APIM同期](03-subscription-management.html#subscription-sync) |

第19回以降とPortal実装編を含む全体の計画・各回の到達点は、[コンテンツ計画](CONTENT-PLAN.md)を参照してください。資料の一覧は[勉強会README](README.md)、導入・構築の入口は[リポジトリREADME](../../README.md)にあります。
