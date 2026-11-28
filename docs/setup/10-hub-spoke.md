# 10. ハブ＆スポーク接続（任意）

前：[09. 基盤構築後のEntra登録](09-egakumi-portal.md) · [README](../../README.md) · 次：[11. Gatewayの個別配備](11-deploy-gateway.md)

企業ハブを使う環境では、[基盤](08-build-infra.md)の構築後、GatewayとPortalの個別配備前にハブへ接続します。この順にすると、イメージ作成と非公開ACRへの登録、SQL初期化、APIへの接続を実際に使う経路で検証できます。独立VNetを使う環境は[第11章](11-deploy-gateway.md)へ進みます。

企業ポリシーで初回から中央DNS・集約出口・ハブ接続が必須の場合は、`azd up`の前にネットワーク担当者と配備方法を決めます。基盤自身も証明書の初回発行にAzure DNS・ACMEへの経路と非公開ACR・Key Vaultへの到達性を必要とします。ハブのFirewall・DNS・ExpressRoute／VPN・変更権限は企業ごとに異なるため、許可する通信と切戻しの順序を確認します。 [Azureのハブ＆スポークの設計](https://learn.microsoft.com/azure/architecture/networking/architecture/hub-spoke)

## 最初に決めること

| 確認 | 決めるもの |
| --- | --- |
| 接続 | ハブVNet、CIDR重複の回避、双方向ピアリング・復路の担当 |
| 出口 | ローカルFWを維持するか、APIM・管理VMの外向けを集約FWへ送るか |
| DNS | Azure提供DNSを維持するか、中央DNS／Resolverへ問い合わせるか |
| Private DNS | ローカルゾーンを維持するか、中央管理ゾーンへ統合するか |
| 経路伝搬 | ExpressRoute／VPN経路を使うサブネットと、除外時に必要な社内・DNS宛経路 |

既存の基盤にはAppGW・PE・Firewall・APIM・管理VMに加え、Portal、Broker、証明書Jobの3つのACA用サブネットがあります。各区画のCIDRは`network.subnetPrefixes`と基盤の配備出力で照合し、PEと`AzureFirewallSubnet`以外に対応するルートテーブルを確認します。利用者設定`network.bgpRoutePropagation`の`agw`・`apim`・`management`は既定で伝搬を許可します。企業接続時は、各ACA環境のDNSと内部VIPに対して、正規経路と拒否される経路の実効ルートを確認します。

## デフォルトルートを変える

| 対象 | 変更・維持する内容 |
| --- | --- |
| APIM用 `rt-<environment>-<product>-apim` | 集約出口を使う場合、`default-egress`の`0.0.0.0/0`を承認済みハブFWのprivate IPへ変更。AGWサブネット宛の復路はローカルFW、`ApiManagement`宛は`Internet`を維持。外部ツール・Entra・サービス依存先への許可も集約側で確保 |
| 管理VM用 `rt-<environment>-<product>-vm` | 必要なら`default-egress`を集約出口へ変更。認証・監視・OS更新・非公開ACR・管理接続の許可を先に準備 |
| 証明書用ACA | DNS管理API・ACME・ACA管理通信・各Private Endpointへの名前解決と経路を確認 |
| AGW用 `rt-<environment>-<product>-agw` | APIM VIPの`/32`宛のローカルFW経由を維持。通常のAppGW v2へFW／NVA向けデフォルトルートを一律追加しない |
| PEサブネット | RTを一律追加せず、Foundry・Key Vault PEへのprivate接続と実PE IP群のNSG許可を維持 |
| `AzureFirewallSubnet` | 利用者NSG・上記RTを関連付けない。集約出口を使ってもローカルFWはAGW↔APIMの受信検査に使用 |

Foundry本体は基盤VNetのサブネット内にありません。PE側のUDRでマネージド側アウトバウンドまで変更できるとは扱いません。[AppGWのUDR要件](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure#supported-user-defined-routes)、[APIM classicの必須通信](https://learn.microsoft.com/en-us/azure/api-management/virtual-network-reference)

集約FWへの変更でAPIMの外部backend接続元IPが変わる場合は、接続先の許可元も照合します。

## DNSサーバー・ゾーンを変える

VNetの「DNSサーバー」を変更する前に、切替先へ直接問い合わせ、必要な公開名・private名を解決できることを確認します。対象はFoundry用3ゾーン、2つのVaultのレコードを持つKey Vault用1ゾーン、APIM Gateway FQDNの専用ゾーン、Portal用とBroker用の各ACA環境のゾーンです。VaultはそれぞれのPrivate Endpoint IP、ACAはそれぞれの内部VIPに解決されることを確認します。APIM用のprivate DNSは専用Gateway FQDNに限定します。

- **ローカルゾーンを維持：** 中央DNSからAzure側Resolver等へ転送し、必要なVNetリンクを準備する。ピアリングだけではPrivate DNSの名前解決は共有されない。
- **中央ゾーンへ統合：** 既存ゾーンのResource ID、PEのDNS zone group、APIM実VIPのレコード、VNetリンクをDNS担当者と対応付ける。PE自動レコードを別IaCで二重管理しない。
- **NSG・FW：** 問い合わせ元から中央DNS IPへのTCP／UDP `53`を許可する。経路上のFWと、FW自身がFQDNルールに使うDNSも確認する。

公開GatewayとPortalのAレコードは基盤のAppGWへ向けます。中央DNSへの変更後も、採用する外部backendの公開FQDNを解決できることを確認します。

切替後はAPIMのネットワーク構成の適用、AppGWのStop／Start、管理VMのDNS再取得／必要な再起動を保守時間に行います。既存接続の成功に依存せず、新規接続で確認します。[APIMのDNS反映](https://learn.microsoft.com/en-us/azure/api-management/api-management-using-with-vnet#custom-dns-server-setup)、[AppGWのDNS反映](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure#dns-servers-for-name-resolution)、[PEのDNS統合](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-dns-integration)

## ExpressRoute／VPNの伝搬を止める

対象RTの「構成」でゲートウェイのルート伝搬を無効にします。管理中の利用者設定では`network.bgpRoutePropagation`の対象キーを`false`（伝搬しない）にします。Azure ARMの属性名は`disableBgpRoutePropagation`のため、Bicep内部で値を反転して`true`を渡します。利用者設定を`true`（伝搬する）にした場合、ARM属性は`false`です。

既定経路だけを除外する設定ではありません。失われる社内宛・中央DNS宛経路と復路を確認し、必要なUDR等を先に設計します。AppGWへ伝搬されるデフォルトルートにも注意します。[Azureの経路選択・BGP伝搬](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-networks-udr-overview)

## 実施する順番

1. DNS・全ルート・伝搬・NSG／FW・ゾーン関連付けを保存し、変更対象と切戻し方法を決める。
2. 双方向ピアリング、集約FWの許可・復路、中央DNSの転送・ゾーン参照を先に準備する。
3. 必要なNSG／FW許可を追加してから、DNS・出口・伝搬を対象ごとに変更する。
4. 基盤の既存経路に反映する。各区画の名前解決、往路と復路、管理VMから非公開ACRやSQLへの到達、AppGW→FW→APIM、APIM→Foundry PE、証明書発行・更新に必要な通信と監視を確認する。Gateway・Portalの公開API、PortalからのGateway呼出し、アプリのSQL／ACR接続は個別配備後に[第13章](13-verify-deployment.md)で確認する。
5. 不合格なら保存した値へ戻し、同じ通信を再確認する。企業共有資源の既存設定は戻し対象に含めない。

変更・反映待ち・保持・追加設備の概算費用を実施前に確認します。

## 基盤の再配備・撤去との関係

企業接続に伴う変更は自動追従しません。基盤を`azd up`または`azd provision`で再配備すると、変更した経路やDNS設定が基盤資材の定義に戻る場合があります。再配備前に差分を確認し、採用した企業設定を管理する方法を決めます。中央ゾーンへ管理範囲を移す場合は、既存ゾーンの所有者と配備方式を合わせて確認します。

`down`は基盤の5RGを対象にします。中央DNS・ハブ側ピアリング・FW許可・Entra・公開DNSは各担当者が処理します。PEのzone group削除が中央レコードへ与える影響も事前確認します。本番の`Locked`監査保管は保持期間中に削除できません。

企業接続は、企業設定・実通信・再配備／撤去の分担を確認して完了とします。

## 完了条件

- 接続先の企業方針に合わせ、変更対象・担当者・切戻し方法を記録した。
- DNS・往復経路・NSG／FWを照合し、変更後の通信を新規接続で確認した。
- 基盤の再配備・撤去で維持する設定と、企業側で管理・撤去する設定を明確にした。

基盤とEntra登録、ハブ接続の確認が完了したら[Gatewayの個別配備](11-deploy-gateway.md)へ進みます。アプリ配備後にネットワークを変更した場合は、公開API・Portal認証・SQL／ACRへの接続を再確認します。 [更新・撤去の手順](../operations/README.md)
