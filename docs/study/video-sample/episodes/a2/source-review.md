# A2 技術内容と出典のレビュー

確認日：2026-11-02（JST）。対象は「ハブ接続後のDNS・通信制御と切替」。教材と現行リポジトリ定義に基づく設計説明であり、稼働Azureへの変更・問い合わせ・疎通実測は行っていない。A1の経路を前提に、中央DNSの利用から公開宛て通信の検証と再配備までを主例とする。

## 正本と参照範囲

- [コンテンツ計画](../../../CONTENT-PLAN.md)：A2の到達点・主例・A1との境界。
- [50節：中央DNS](../../../01-network-control.html#hub-dns)
- [51節：Private DNS](../../../01-network-control.html#hub-private-dns)
- [52節：通信許可・送信元IP](../../../01-network-control.html#hub-policy)
- [54節：切替・再配備](../../../01-network-control.html#hub-rollout)
- [セットアップ07](../../../../setup/07-hub-spoke.md)：補助資料。教材HTMLの8ゾーン・ACAの説明の方が詳しいため、古い簡略記載へ範囲を戻さない。

## 50節：VNetとFirewallのDNSは別設定

`infra/modules/network.bicep` 37・174・242行付近でVNetの`dnsServers`とNSG向け有効DNSを決定する。`network.dnsServers`の未指定・`[]`はAzure提供DNS、中央IP配列を指定するとVNet設定とAppGW・APIM・管理VMのNSG DNS宛先が変わる。単にDNSが回答できるだけでなく、既存クライアントへの反映も必要。

FW Policyの`dnsSettings`は同ファイル373〜376行付近。`enableProxy:true`、`servers: firewallSettings.?dnsServers ?? dnsServers`である。FWのDNS未指定ならVNet用値を継承、明示的な`[]`ならAzure提供DNSを使い、VNetの中央DNSへ追従しない。IP配列ならFW上流DNSを個別指定する。DNS Proxyの有効化だけではクライアントの問い合わせ先をFW private IPへ変えない。

中央DNSへのTCP/UDP 53、往復経路、実経路上のNSG/FWを別々に用意する。`allow-custom-dns` / `custom-dns`（456〜470行付近）の送信元は**APIM・管理VMのみ**。AppGW・ACAは含まれない。NSG宛先更新だけでFW許可まで完了と扱わない。APIMのネットワーク構成適用、AppGW Stop/Start、VM DNS再取得・必要な再起動を計画し、新しい接続で反映を確認する。

公式確認：[Azure Firewall DNS settings](https://learn.microsoft.com/en-us/azure/firewall/dns-settings)。FW上流DNSとDNS Proxyの設定、クライアントがFWへ問い合わせるには別途クライアント側のDNS指定が必要なことを照合した。既定のAzure FirewallではProxy無効だが、このリポジトリの現行値は明示的に有効。

## 51節：8ゾーン、回答、参照経路と所有

| 区分 | 現行ゾーン・レコード | 根拠 |
| --- | --- | --- |
| Foundry 3 | `privatelink.cognitiveservices.azure.com` / `privatelink.openai.azure.com` / `privatelink.services.ai.azure.com` | `network.bicep`300行付近、zone groupは`network-bindings.bicep` |
| Key Vault 1 | `privatelink.vaultcore.azure.net` | 同上 |
| ACR 1 | `privatelink.azurecr.io`、PE zone group | `portal-network.bicep`18〜49行付近 |
| SQL 1 | `privatelink.database.windows.net`、PE zone group | `credential-broker-network.bicep`19〜45行付近 |
| APIM 1 | 実Gateway FQDNと同名ゾーン、`@ A → 実APIM VIP` | `network-bindings.bicep`213〜242行付近 |
| ACA 1 | 実`defaultDomain`ゾーン、`* A → 実static IP` | `portal-dns.bicep`。apex `@`は現行定義なし |

ピアリングだけでPrivate DNS参照が共有されるとは言わない。現ゾーン維持なら、Azure側Resolver/forwarderが解決に使うVNetへ必要なゾーンをリンクし、中央DNSから転送する。Azure提供DNSを直接使うクライアント側のVNetリンクも必要。現行リンクの`registrationEnabled`はfalse。

中央ゾーンへ移管する場合はresource/scope/VNetリンク、PE zone groupの参照先、APIM/ACA実IPレコード、それぞれの管理元を確定する。現行モジュールはゾーンを新規宣言し、中央既存ゾーンIDの公開入力がないため、単なる設定値変更で完結すると説明しない。zone group自体をCore所有に残す設計も選べる。PE/zone groupの更新・削除が中央レコードへ及ぶことも設計する。

条件付き転送のSQL例は、`database.windows.net`というサービスの**公開ゾーン**からAzure Resolver/forwarderへ向け、Private Link名へのCNAME連鎖と最終private IPを確認する。同名ゾーンの分散、zone group管理レコードの手動二重管理を避ける。APIM・ACA内部名は別途参照できるようにする。公開Gateway/Portalの公開AレコードはAppGW公開IPを維持し、公開名を一括private化しない。

公式確認：[Private Endpoint DNS integration](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-dns-integration)。必要なVNetリンク、Resolver/forwarder経由、公開ゾーンへの条件付き転送、同名ゾーン分散の問題を照合した。

## 52節：実送信元と許可・出口IPを対応させる

ハブ接続やUDR変更はNSG/FW許可を自動移植しない。実際にNSGが見る送信元、宛先、ポート、FQDNで必要分を追加する。4NSG、受信終端拒否、APIM/管理VMの送信終端拒否、実PE IPの限定、Azure基盤用許可、管理VMへのprivate到達とSSH認証を維持する。ハブCIDR全体を全面許可する説明にしない。

`VirtualNetwork`タグにはpeering先・接続されたon-prem・UDR等の範囲も入り得る。特に独自送信終端拒否のないAppGW・PEは、既定許可も含めた実効NSGを接続後に再確認する。Public Network Access・IAMを一括緩和して接続問題を解消する扱いにしない。

ローカルFWはAppGW↔APIM/ACAの内部入口Network許可と往復UDRを維持。ハブFWには各区画の必要な基盤・認証・更新・監視・ビルド・証明書・外部API等を実効設定から用意する。NetworkルールはApplicationより先に評価され一致で終了するため、広いTCP 443許可でFQDN制御を代替しない。中央親ポリシーや既定拒否も確認対象。

公開SNAT元が変わったら、外部接続先・AppGWの許可元と期待値を実送信元に合わせる。`network-bindings.bicep`101行付近の`allow-certificate-observer`はローカルFW公開IP /32、`infra/main.bicep`629行付近の`expectedEgressIp`も`network.outputs.firewallPublicIpAddress`。いずれもハブIPへ自動追従しない。Portal→公開Gatewayも実送信元で照合する。`expectedEgressIp`は期待値であり、自動実測・自動照合の仕組みと説明しない。

NetworkのSNAT除外private rangesと、ApplicationのSNATは別。Applicationは宛先private/publicにかかわらずSNATする。企業内接続でもFW両側で実送信元と復路を確認する。

公式確認：[Service tags overview](https://learn.microsoft.com/en-us/azure/virtual-network/service-tags-overview)、[Firewall rule processing](https://learn.microsoft.com/en-us/azure/firewall/rule-processing)、[SNAT private ranges](https://learn.microsoft.com/en-us/azure/firewall/snat-private-range)。タグ範囲、Network→Applicationの評価順と一致終了、privateRangesはNetwork向けでApplicationは常にSNAT、を照合した。

## 54節：切替から再配備・削除まで

1. 現在のCIDR/peering、4RT全ルートと伝搬、VNet/FW DNS、NSG/FW、ゾーン・リンク・レコード、出口IPを保存。ハブ側の許可・復路・DNS転送・中央ログ検索先・担当・復旧条件を先に準備する。
2. 双方向peeringを確認し、必要な許可を先に追加。DNS・対象区画の出口UDR・伝搬を影響範囲ごとに切り替え、サービスへ反映。保守経路と権限を確保する。
3. **新規接続**で8ゾーン関連実FQDN、公開名・外部名のDNS回答、宛先ごとの実効経路、NSG/FWログ、公開SNAT元を確認。API応答だけで完了とせず、Portal・PE・APIM管理・SSH・証明書更新・image取得/build・監視も維持する。
4. 失敗時は保存したCore側の変更値へ戻す。共有ハブ全体を巻き戻さない。中央FW/DNS/peering診断とアラートの担当を別途決め、Core既存通知が共有資源まで自動で覆うと扱わない。
5. 恒久変更をconfig/IaC/配備処理へ反映し、次回upの適用値を確認。down時のpeering・中央ゾーン参照/レコード・FW許可・Gateway共有の解除担当も明確にする。

`scripts/deployment/lifecycle.py`342〜407行付近は4RTの既存routesを検証して親RT更新へ引き継ぐ。一方、伝搬値はコピーせず現在configをBicepへ適用し、`network.bicep`の子リソースが宣言する同名ルートは再適用する。したがって「手動default-egress→hub FWを既存ルート保持が恒久保証する」は誤り。次回配備でlocal FWへ戻る可能性があるため、宣言の管理元も変更する。

## レビュー状態

出典・要点の確認完了。台本は執筆中のため、台本の技術レビューは未実施。後続の台本レビューで本文・図・章の出典対応を確認する。

## 台本初稿の技術レビュー

82台詞・11章の初稿を全件確認した。各章と全体sourceReferencesのファイル・HTMLアンカーは実在。DNS設定の未指定/明示空配列、8ゾーンとAPIM/ACAレコード、zone group所有、NSGの実送信元とタグ、Network先一致、SNATの方式差、observer/期待値の非自動追従、段階切替・新規接続、切戻し・監視・up/down管理の到達点をすべて含む。

圧縮中の初稿に対して次を指摘し、執筆担当とrootへ連絡した。最終稿の反映確認は後続で行う。

- turn054の「中央DNSから公開依存先へ進む」はDNSが業務通信を中継する印象があるため、DNSで名前を解決した後、APIMから公開依存先へ接続する表現に分ける。
- turn023/024の図は問い合わせの転送経路なので、見出しも中央DNS→Azure側Resolverの転送としてCNAME名の連鎖と分ける。
- DNSを含む段階切替の図は「対象区画」より「対象範囲」とし、VNet単位のDNSをサブネットごとの設定と混同させない。
- 8ゾーンのまとめに合わせ、summary章sourceRefsにもhub-private-dnsを加える。

## 最終本文・図の技術レビュー

82台詞・11章、読み上げ5,748字の最終稿を全件確認し、**技術レビュー合格**。確認3問と回答、まとめ、第9回WAFへの接続も教材と計画に整合している。

初稿指摘の4点（DNSによる業務通信中継との混同、転送図とCNAME連鎖の分離、段階切替の対象範囲、summary章のPrivate DNS出典）は反映済み。全章・全体の参照ファイルとHTMLアンカーも実在した。

最終本文は次を正しく維持する：VNet/FW DNSの未指定と空配列、custom-dnsの送信元限定、6+2ゾーンのレコード、転送・VNetリンク・所有と削除、観測送信元による限定許可、VirtualNetworkタグ、Network先一致、SNAT方式の差、ローカルFW参照の許可・期待値、現状保存と事前準備、新規接続での照合、共有ハブ全体を巻き戻さない復旧、同名ルートと伝搬の再適用、監視担当・次回up/down管理。稼働環境への適用・実測済みとは説明していない。

確認時の台本SHA-256：`11ebdd43563317da7f6a6a93295a9c6f24b0b15c85ab20f20b55b1ce294b9137`（draft）。この後のready-for-generationへのstatus更新は本文・図の変更とは分けて扱う。

表示のみの軽微な最終推奨として、切戻しカードturn062/063の「対象区画のDNS・出口・伝搬」も「対象範囲のDNS・出口・伝搬」へ変更するようrootへ依頼した。本文の変更や再録は不要。その他の未解決の技術指摘はない。

### rootの最終補足の確認

82台詞・11章、読み上げ5,757字の更新稿で、turn050/065本文とturn050/062/063のカードを追加確認した。

- turn050：切替前に中央DNSへ直接問い合わせ、公開名と内部名の回答を確かめる事前確認を明示。50節の要求を正確に補強している。中央側許可・復路・転送・ゾーン参照・ログ、peering・保守経路の内容も維持。
- turn065：既存ルート保持で手動変更が恒久化されるかという問いを短縮。直後turn066の「同名ルート・伝搬の再適用」との接続と意味に変化なし。
- turn062/063：切戻しカードが「対象範囲のDNS・出口・伝搬」へ更新されたことを確認。VNet DNSと区画ごとの出口・伝搬を同じ単位と誤解させる表記を解消。

**更新後も技術レビュー合格。未解決の技術指摘なし。** 確認台本SHA-256：`602f8b1504bba0d5fa87d68008d77a487c67ba22734dba794e0b9594734bb05e`（draft）。

最終レビュー後はstatusだけready-for-generationへ変更。本文・字幕・図は同一。生成対象SHA-256：`440fd690c63be4a671e4bd7fbeaa5aa1782a41b1e48224dfd57f48c1f1697395`。


## teaching-settings-v2への改訂監査（2026-11-02）

最新CONTENT-PLAN.md/AGENTS.md/PRODUCTION-GUIDE.md/SETTINGS-VIEW.mdを確認。A2のテーマ・到達点・主例・元資料50/51/52/54節は初稿と同じで、追加すべき技術内容や再録を要する指摘はない。今回の必須差分は教材用の設定メニュー、固定カメラ、問答単位の同一viewId/構図/縮尺、説明対象への適時の赤枠、40〜70秒程度の連続した質問・回答の確認である。Azure Portalのブランドバー・検索・疑似操作を模倣しない。

82台詞のvisual以外の全turnデータ、章、出典を初稿保存版と照合し、完全一致を確認した。speech/caption/delivery/話者/ID/字幕chunk/間を維持して画面だけを変更すれば、既存の成功済み原音とミックスを再利用できる。波形や発音を新たに聴取して判定したものではない。

### 元資料・定義と公式仕様の再照合

- DNSの未指定/空配列は`network.bicep`37/174/242/374〜375行で照合。VNet未指定・[]はAzure DNS。FW未指定はVNet値を継承、FWの明示[]はAzure DNS。現行Proxyは有効だがクライアント側DNS宛先を自動変更しない。
- `custom-dns`のFW許可（同456〜470行）はAPIM/管理VMだけ。AppGW/ACAの経路がFWを通るなら別途必要な許可を設計する。
- Private Link6ゾーンはFoundry3・Key Vault・ACR・SQL、APIM実FQDNの`@`→実VIPとACA実defaultDomainの`*`→実static IPを合わせ計8ゾーン。`portal-dns.bicep`にACA apexのAはない。Zone/VNet link/zone groupと各レコードの所有を区別し、公開Gateway/Portalの公開AはAppGW公開IPを維持する。
- `network-bindings.bicep`101行のobserver許可、`infra/main.bicep`629行のexpectedEgressIpはローカルFW公開IPを参照する。ハブIPへ自動追従せず、期待値は実測結果と区別する。
- `lifecycle.py`342〜407行の既存4RTルートの引継ぎは、伝搬値やBicep同名ルートの再適用を無効にするものではない。手動default-egressの恒久保証と説明しない。
- 切替前は中央DNSへ直接照会して公開名/内部名を確認、許可と復路を先に用意し対象範囲ごと反映。切替後は新規接続でDNS/経路/NSG/FW/SNATを同じ通信について照合する。Coreの合意した変更値を切り戻し、共有ハブ全体は巻き戻さない。監視と次回up/downの管理元も決める。

Microsoft一次資料5ページを今回も再閲覧し、本文との整合を確認した：

- [Firewall DNS settings](https://learn.microsoft.com/en-us/azure/firewall/dns-settings)：FWの上流設定とクライアントがDNS Proxyを使う設定を区別。
- [Private Endpoint DNS integration](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-dns-integration)：公開ゾーンへの条件付き転送、VNet link、Resolver/forwarder、zone group更新/削除の関係。
- [Firewall rule processing](https://learn.microsoft.com/en-us/azure/firewall/rule-processing)：NetworkはApplicationより先で、一致すると終了。
- [SNAT private ranges](https://learn.microsoft.com/en-us/azure/firewall/snat-private-range)：NetworkのSNAT除外と、宛先にかかわらずSNATするApplicationは別。
- [Service tags overview](https://learn.microsoft.com/en-us/azure/virtual-network/service-tags-overview)：VirtualNetworkにはpeering等の範囲を含み得る。

### 改修画面への技術上の指示

DNSの問い合わせ転送（中央DNS→Azure側Resolver等）、名前のCNAME連鎖、Private DNSゾーンの参照、業務APIのHTTPS通信は異なる関係として描く。ゾーンを業務通信の中継サーバーとして見せない。必要なら同じ画面内へ、設定代表行とラベルを分けた小図を併記する。ハブの設備やIP・実効経路は未取得の接続案として表示し、推測の実IPを確定値にしない。

連続Q&Aの実長候補を旧版timelineから測定した。改修時も本文と間が同じなら時刻は同じになる：055〜059は648〜708秒の60.000秒、047〜050は57.250秒、051〜054は42.208秒、012〜015は42.708秒、019〜022は48.708秒、023〜025は40.542秒、065〜068は46.292秒。実装担当とrootへ同viewIdを保つ候補として共有した。

現時点は既存本文の再利用可否と根拠の監査完了。改訂後の設定・図の技術レビューは、実装完了後に追加する。


### 改訂後の設定画面・図の最終技術監査

**teaching-settings-v2実装の技術レビュー合格。未解決の技術指摘なし。** 全23 viewId（設定19＋図併記4系統、SVG15 variants）を元資料50/51/52/54節・関連IaC・82台詞と照合した。全78本編turnに設定ビューまたは同じ設定shell内の補助図が対応する。

再度、初稿保存版との非visual比較を実行し、82台詞のspeech/caption/subtitleChunks/delivery/ID/話者/章・間など全turnデータ、chapters、sourceReferencesが完全一致した。したがって、今回の改訂に伴い本文・声・styleを変更する技術上の理由はない。

全23 viewIdについて、settingsからhighlight指定、SVGからclass=highlightの矩形だけを除外して基本内容を比較した。各view内の設定行・値・図の全XML・見出し・配置は同一で、対象を変えるだけの赤枠variantになっている。質問〜回答で構図・縮尺を変える指示や、疑似ポインター・pulse・camera motionは含まれない。確認問題069/072/075は条件＋A/B選択肢のみで無強調、回答070/073/076から正しい選択肢Bを強調する。

設定・図の確認結果：

- `a2-dns-settings`：VNet未指定/[]→Azure、VNet IP配列→VNetと対応NSG宛先、FW未指定→VNet値継承、FW明示[]→Azure、FW IP配列→個別上流、Proxy有効でもVNet宛先自動変更なしを正しく区別。
- `a2-dns-reach`：TCP/UDP53の往復と、現行custom-dns送信元がAPIM/管理VMのみでAppGW/ACAを含まない点、必要時の追加許可とサービス反映を正確に表示。
- `a2-eight-zones`：Foundry3、Key Vault/ACR/SQL3、APIM実FQDNの@ A→実内部VIP、ACA実defaultDomainの* A→実static IP、ACA apexなしを正確に表示。IPは取得値を捏造せず参照対象として表記。
- `a2-dns-resolution`：上段は問い合わせ元→中央DNS→Azure側Resolver等のquery転送、下段は個別SQL名からPrivate Link名へのCNAME連鎖と最終PE IP。VNet link/内部名参照を代表設定として併記し、zoneやDNSが業務HTTPS通信の中継装置に見える構図にしていない。
- `a2-zone-ownership`/`a2-public-names`：既存中央ゾーンIDの公開入力なし、zone groupと実IPレコード・更新削除所有の調整、公開A→AppGW公開IPの維持を表示。
- 許可の各view：NSG実送信元・限定したPE許可・終端拒否、VirtualNetworkタグ、基盤/入口/直接経路、PNA/IAMを一括緩和しない点、Network先一致と広い443許可をFQDN制御の代替にしない点が本文と整合。
- `a2-snat`/`a2-egress-expectation`：Networkの宛先/private rangesによるSNATとApplicationの常時SNAT、FW両側の観測送信元と復路、公開先が見るSNAT元を区別。observer/expectedEgressIpのlocal FW公開IP参照と、期待値/実測/ハブへの非自動追従も明記。
- 保存/準備/切替/新規接続の各view：切替前の中央DNSへの直接照会、先行する許可・復路、対象範囲ごとの反映、新しい接続での8ゾーン/DNS/経路/NSG/FW/SNAT照合、管理系・証明書・image・監視、中央の診断/通知/担当を正しく示す。
- 切戻し/再配備の各view：Core側の合意した変更値を戻し共有ハブ/他スポーク全体を戻さないこと、既存4RT routes保持とBicepの同名route・伝搬再適用の区別、zone group削除が中央レコードへ及ぶ影響、次回up/downの責任を正しく表示。

実装レビューで表示を2点補強した。023〜025下段はzone suffixだけを個別FQDNと誤解しないよう、`<SQL名>.database.windows.net`→`<SQL名>.privatelink.database.windows.net`というplaceholderの例に変更した。044〜046はexpectedEgressIpの参照を「ローカルFW公開IP（期待値）」とし、「自動実測なし / ハブIPへ自動追従しない」を明記した。どちらも本文・再録を変更しない表示の補強である。

各図・設定には教材/接続案であり稼働取得値ではない注記を維持する。確認時のscript SHA-256：`7150a3e49cb179a436236e6e4ae0142a0b5235c0e9712d9742e4406256e5ca6f`。文字の実画面での収まり、完成MP4/連続動画の視認、音声の聴感は本監査と分けてQAで扱う。

最終表示調整：036/037の値を「Public Network Access/IAMを一括緩和しない。SSH認証維持」へ短縮。意味/音声対象は同一。生成対象script SHA-256：`5a6b4bbc61d5cdd724863cd3a5847a1ffd6184b9845df8db12ccfdd002c9a04c`。
