# 7. ハブ＆スポーク対応（任意）

前：[6. azd upでの構築](06-deploy.md) · [README](../../README.md)

**`azd up`で独立VNetのGatewayを作った後、企業方針に応じて必要な項目だけ変更します。** 一律の切替スクリプトはありません。ハブのFirewall・DNS・ExpressRoute／VPN・変更権限は企業ごとに異なるため、担当者と対象・順序を決めます。

第1〜6章で独立VNetのCoreを構築した後、本章では必要な企業接続だけを扱います。[構築手順の全体](../../README.md)も確認してください。企業ポリシーが独立VNet・Azure提供DNS等を禁止する場合は、Coreの配備前に構築方法を調整します。

## 最初に決めること

| 確認 | 決めるもの |
| --- | --- |
| 接続 | ハブVNet、CIDR重複の回避、双方向ピアリング・復路の担当 |
| 出口 | ローカルFWを維持するか、APIM・管理VMの外向けを集約FWへ送るか |
| DNS | Azure提供DNSを維持するか、中央DNS／Resolverへ問い合わせるか |
| Private DNS | ローカルゾーンを維持するか、中央管理ゾーンへ統合するか |
| 経路伝搬 | ExpressRoute／VPN経路を使うサブネットと、除外時に必要な社内・DNS宛経路 |

基本構成はAppGW・PE・Firewall・APIM・管理VM・ACAの6サブネット、4NSG、4ルートテーブルです。全区画のCIDRは`network.subnetPrefixes`で指定します。PEと`AzureFirewallSubnet`以外へRTを関連付けます。利用者設定`network.bgpRoutePropagation`の`agw`・`apim`・`management`は既定`true`（伝搬する）ですが、独立VNetには伝搬元ゲートウェイがありません。企業接続時は実効ルートを確認します。

## デフォルトルートを変える

| 対象 | 変更・維持する内容 |
| --- | --- |
| APIM用 `rt-<environment>-<product>-apim` | 集約出口を使う場合、`default-egress`の`0.0.0.0/0`を承認済みハブFWのprivate IPへ変更。AGWサブネット宛の復路はローカルFW、`ApiManagement`宛は`Internet`を維持。外部ツール・Entra・サービス依存先への許可も集約側で確保 |
| 管理VM用 `rt-<environment>-<product>-vm` | 必要なら`default-egress`を集約出口へ変更。認証・監視・OS更新・DNS事業者API・証明書更新・管理接続の許可を先に準備 |
| AGW用 `rt-<environment>-<product>-agw` | APIM VIPの`/32`宛のローカルFW経由を維持。通常のAppGW v2へFW／NVA向けデフォルトルートを一律追加しない |
| PEサブネット | RTを一律追加せず、Foundry・Key Vault PEへのprivate接続と実PE IP群のNSG許可を維持 |
| `AzureFirewallSubnet` | 利用者NSG・上記RTを関連付けない。集約出口を使ってもローカルFWはAGW↔APIMの受信検査に使用 |

Foundry本体は基盤VNetのサブネット内にありません。PE側のUDRでマネージド側アウトバウンドまで変更できるとは扱いません。[AppGWのUDR要件](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure#supported-user-defined-routes)、[APIM classicの必須通信](https://learn.microsoft.com/en-us/azure/api-management/virtual-network-reference)

集約FWへの変更でAPIMの外部backend接続元IPが変わる場合は、接続先の許可元も照合します。

## DNSサーバー・ゾーンを変える

VNetの「DNSサーバー」を変更する前に、切替先へ直接問い合わせ、必要な公開名・private名を解決できることを確認します。対象はFoundry用3ゾーン、Key Vault用1ゾーン、構築時に取得したAPIM Gateway FQDNの専用ゾーンです。`azure-api.net`全体をローカルゾーンにしません。

- **ローカルゾーンを維持：** 中央DNSからAzure側Resolver等へ転送し、必要なVNetリンクを準備する。ピアリングだけではPrivate DNSの名前解決は共有されない。
- **中央ゾーンへ統合：** 既存ゾーンのResource ID、PEのDNS zone group、APIM実VIPのレコード、VNetリンクをDNS担当者と対応付ける。PE自動レコードを別IaCで二重管理しない。
- **NSG・FW：** 問い合わせ元から中央DNS IPへのTCP／UDP `53`を許可する。経路上のFWと、FW自身がFQDNルールに使うDNSも確認する。

公開GatewayとPortalのAレコードはCoreのAppGWへ向けます。中央DNSへの変更後も、採用する外部backendの公開FQDNを解決できることを確認します。

切替後はAPIMのネットワーク構成の適用、AppGWのStop／Start、管理VMのDNS再取得／必要な再起動を保守時間に行います。既存接続の成功に依存せず、新規接続で確認します。[APIMのDNS反映](https://learn.microsoft.com/en-us/azure/api-management/api-management-using-with-vnet#custom-dns-server-setup)、[AppGWのDNS反映](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure#dns-servers-for-name-resolution)、[PEのDNS統合](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-dns-integration)

## ExpressRoute／VPNの伝搬を止める

対象RTの「構成」でゲートウェイのルート伝搬を無効にします。管理中の利用者設定では`network.bgpRoutePropagation`の対象キーを`false`（伝搬しない）にします。Azure ARMの属性名は`disableBgpRoutePropagation`のため、Bicep内部で値を反転して`true`を渡します。利用者設定を`true`（伝搬する）にした場合、ARM属性は`false`です。

既定経路だけを除外する設定ではありません。失われる社内宛・中央DNS宛経路と復路を確認し、必要なUDR等を先に設計します。AppGWへ伝搬されるデフォルトルートにも注意します。[Azureの経路選択・BGP伝搬](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-networks-udr-overview)

## 実施する順番

1. DNS・全ルート・伝搬・NSG／FW・ゾーン関連付けを保存し、変更対象と切戻し方法を決める。
2. 双方向ピアリング、集約FWの許可・復路、中央DNSの転送・ゾーン参照を先に準備する。
3. 必要なNSG／FW許可を追加してから、DNS・出口・伝搬を対象ごとに変更する。
4. Coreへ反映し、管理VMの名前解決・外向け、AppGW→FW→APIM、APIM→Foundry PE／採用backend、証明書更新・監視を新規接続で確認する。Portalからの公開Gateway呼出しとSQL／ACR接続も確認する。
5. 不合格なら保存した値へ戻し、同じ通信を再確認する。企業共有資源の既存設定は戻し対象に含めない。

変更・反映待ち・保持・追加設備の概算費用を実施前に確認します。

## azd up／downとの関係

企業接続に伴う変更は自動追従しません。`up`で構築時の設定へ戻る可能性があるため、再実行前に差分を確認します。継続採用する企業設定はレビュー後に管理中の設定・IaCへ反映します。中央ゾーンへ管理範囲を移す場合は、既存ゾーンを自動所有させず配備方式も対応させます。

`down`はCoreの5RGを対象にします。中央DNS・ハブ側ピアリング・FW許可・Entra・公開DNSは各担当者が処理します。PEのzone group削除が中央レコードへ与える影響も事前確認します。本番の`Locked`監査保管は保持期間中に削除できません。

企業接続は、企業設定・実通信・再配備／撤去の分担を確認して完了とします。

## 完了条件

- 接続先の企業方針に合わせ、変更対象・担当者・切戻し方法を記録した。
- DNS・往復経路・NSG／FWを照合し、変更後の通信を新規接続で確認した。
- 今後の`azd up/down`で維持する設定と、企業側で管理・撤去する設定を明確にした。

ハブ接続が不要な環境では、本章の作業はありません。通常の`azd up`はGateway・Portalまで配備するため、完了済みなら追加配備は不要です。`azd provision`で基盤を先に作る段階構築では、本章のネットワーク設定を確認した後に[READMEの8. Gateway配備](../../README.md#8-gatewayを配備する)、続いて[9. Portal配備](../../README.md#9-portalを配備する)へ進みます。アプリ配備後にネットワークを変更した場合も、公開API・Portal認証・SQL／ACRへの接続を再確認します。運用は[更新・撤去の手順](../operations.md)を参照してください。
