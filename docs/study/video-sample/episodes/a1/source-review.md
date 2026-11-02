# A1 技術出典レビュー

確認日：2026-11-02（JST）。担当範囲は A1「ハブ＆スポーク接続と往復経路」。稼働 Azure への照会・設定変更・接続試験は行っていない。以下はリポジトリ定義と公式仕様に基づく設計説明である。

## 正本と境界

- `../../../CONTENT-PLAN.md` の A1 到達点：private 接続・出口集約・Gateway 共有の採用範囲を選び、双方向ピアリング、区画別 UDR、復路、Gateway 経路伝搬を説明する。
- `docs/study/01-network-control.html` の 47 `#hub-spoke`、48 `#hub-peering`、49 `#hub-udr`、53 `#hub-propagation` を中心にする。
- `docs/setup/07-hub-spoke.md`、`infra/modules/network.bicep`、`infra/modules/network-bindings.bicep`、`infra/modules/portal-network-bindings.bicep`、`scripts/deployment/helpers/configuration.py` を照合した。
- 中央 DNS・Private DNS・NSG/FW 許可・出口 IP の詳細、段階切替・再配備は A2。A1 では経路成立の前提として触れる範囲にとどめる。

## 必須の説明

1. 現行 IaC は独立 VNet の Core を作る。企業ハブ、VNet ピアリング、VPN/ExpressRoute Gateway、中央 DNS Resolver は自動構築しない。ハブの採用機能と企業側・Core 側の担当を別途設計する。
2. private 接続、公開宛て出口集約、中央 DNS、Gateway 共有は個別に選ぶ。ハブへ接続しただけで全てを変更する必要はない。ハブ FW/DNS だけを使うために Gateway 共有を追加しない。
3. 既存 6 サブネット、4 NSG、4 RT、公開入口 AppGW、ローカル FW を経由する AppGW と APIM/ACA の往復、PE への private 接続を維持する接続案である。ローカル FW 撤去や公開入口の中央化は別の再設計。
4. Core の全 VNet CIDR をハブ・他スポーク・オンプレミスと照合する。直接ピアリングする VNet のアドレス重複は先に解消し、他接続先との重複も経路衝突として設計する。例示 CIDR を実測値としない。
5. スポーク→ハブとハブ→スポーク両方のピアリング、VNet アクセス許可、両側 Connected を確認する。Connected は NSG/FW/経路まで保証しない。
6. ハブ FW/NVA が他ネットワークから転送したトラフィックを受ける方向では `allowForwardedTraffic` を許可する。スポーク側の対ハブ設定が基本例。逆方向にも転送を受けるならハブ側も必要。これは UDR/NVA/復路を自動で作る機能ではない。
7. ピアリングは推移的接続を自動提供しない。他スポークやオンプレミスへの到達には中継する FW/NVA/Gateway、宛先別経路、復路、許可を一組で用意する。

## 主例：APIM の公開宛て通信をハブ FW へ集約

前提は、企業側が指定するハブ FW の到達可能な private IP、許可、公開宛て SNAT、スポークへの復路を用意すること。

| 通信 | 往路 | 復路・維持条件 |
| --- | --- | --- |
| APIM→公開 backend | APIM の `default-egress`（`0.0.0.0/0`）→ハブ FW private IP→承認された公開先。主例ではハブ出口で SNAT | 公開先→同じハブ出口→SNAT の対応を戻す→ピアリングを通って APIM。ハブ FW の状態とスポークへの復路を確認 |
| 公開 API の入口 | 利用者→AppGW→ローカル FW→APIM | APIM→ローカル FW→AppGW→利用者。外向き backend 通信の応答と、AppGW へ返す入口側の応答は別の接続 |
| APIM→PE | 内部で解決した PE private IP への具体経路 | 既存 private 接続と許可を維持。`0/0` の変更だけでハブへ移らない |

ハブ FW を経由する APIM の公開 backend 接続と、AppGW↔APIM の入口接続を同じ一往復として混ぜない。外側の利用者からハブ FW へ直接戻す図にしない。

## 区画別 UDR の要点

| 区画 | A1 で説明する維持・変更 |
| --- | --- |
| APIM | 採用時だけ `default-egress` をハブ FW へ。`agw-return-via-firewall`（AppGW 区画→ローカル FW）と `apim-management-direct`（`ApiManagement`→`Internet`）は残す |
| AppGW | `apim-via-firewall`（実 APIM VIP `/32`）と `portal-via-firewall`（実 ACA 入口 IP `/32`）→ローカル FW を維持。通常 AppGW v2 の `0/0` をハブ FW/NVA/オンプレミスへ一律に向けない |
| 管理 VM | 採用時だけ `default-egress` を変更。運用元への復路と、認証・更新・ビルド・証明書・監視等への必要経路/許可を確保 |
| ACA | 採用時だけ `default-egress` を変更し、`agw-return`→ローカル FW を維持。ACR/SQL/Key Vault PE と APIM→ACA の直接経路を確認 |
| PE / AzureFirewallSubnet | 現行は独自 RT を関連付けない。ハブ接続を理由に一律追加しない。PE の UDR で Foundry 等サービス本体の outbound を変更できるとは説明しない |

現在の `default-egress` は Bicep でローカル FW の実 private IP を参照する。公開 config に「ハブ FW next-hop IP」はなく、恒久採用は IaC/配備側の対応を別途行う（A2 の詳細）。

## Gateway 共有と経路伝搬

- ハブの VPN/ExpressRoute Gateway を共有するときだけ、ハブ側 `allowGatewayTransit`、スポーク側 `useRemoteGateways` を設定。スポーク自身の Gateway とは併用不可、利用する remote Gateway は 1 ピアリングのみという制約を確認する。
- 社内側にもスポーク CIDR へ戻る経路が必要。社内向けを中央 FW で検査する場合は双方の宛先別 UDR と中継装置の経路も整える。
- AppGW/APIM/管理 VM：利用者設定 `network.bgpRoutePropagation.agw/apim/management` は既定 `true`。Bicep は値を反転して ARM `disableBgpRoutePropagation:false` を渡す。利用者設定 `false` は ARM `true`。
- ACA：`disableBgpRoutePropagation:true` 固定。ACA 用公開切替キーはない。必要な企業宛て到達性は明示 UDR または要件に応じたコード変更で設計する。
- Gateway 伝搬を止めることは既定経路だけの除外ではない。必要な社内/中央 DNS 宛ても失う可能性があるため、明示経路と復路を先に用意する。ピアリングのシステム経路を消す設定ではない。
- 基本は最長一致。同一プレフィックスなら UDR→BGP→system。VNet/peering/service endpoint 等 system 経路には BGP に対する優先例外があるため、BGP の方が細かければ常に勝つとは言わない。
- AppGW がハブから `0.0.0.0/0` を受けるかも確認する。必要時 `agw:false` 等で伝搬を抑えつつ、社内/DNS経路と Azure 制御系の要件を維持する。

## 台本・図レビュー用チェック

- [x] 設計案であり稼働環境へ適用・実測した説明ではないと明示。
- [x] 3人の役割を生かし、目的の選択→双方向接続→主例の往復→維持する区画→Gateway 伝搬の順で説明。
- [x] 公開 backend の復路と AppGW への復路を図で区別。
- [x] `allowForwardedTraffic` の設定方向を受信側として説明。
- [x] ピアリングの自動推移接続を否定し、社内側復路を示す。
- [x] `0/0` と具体経路の関係、AppGW v2 の制約、ACA 固定設定を示す。
- [x] 確認問題に少なくとも「出口を変えたら入口もハブ？」「Gateway 伝搬停止は default だけ？」「peeringだけで他スポーク？」のうち2つを含む。
- [x] まとめと A2 の DNS・許可・切替への接続を含む。

## 公式仕様の確認（2026-11-02）

Microsoft Learn を直接確認。以下は要約で、原文の長い引用は行わない。

- [VNet ピアリングの作成・変更](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-manage-peering)：転送通信受け入れの方向、別途必要な UDR/NVA、Gateway 共有の対設定と既存 Gateway 制約。
- [Azure の経路選択](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-networks-udr-overview)：最長一致、同一 prefix の優先順位、VNet/peering 等の例外、Gateway 経路伝搬停止の対象。
- [AppGW のインフラ要件](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure#supported-user-defined-routes)：通常 v2 における FW/NVA・ハブ・オンプレミスへの forced tunneling の非対応と、伝搬 default route を避ける設計。Network Isolation 等別条件の設計はこの回の対象外。

## 台本レビュー

82 台詞・10 章・読み上げ 5,780 字の台本（`script.json` SHA-256 `af93a11acdc1e8eabc9347d1f9a159756c6cb542e8e98b6323fca2724076c2be`）について、speech、caption、visual の意味、章・全体の出典を確認した。

- 本文の技術説明は A1 の正本と整合。APIM が公開 backend へ開始する接続と、AppGW から受ける接続を別の接続と明示し、2 種類の復路を説明できている。
- 区画別 UDR、Gateway transit の設定方向・制約、`allowForwardedTraffic` の方向、伝搬の真偽反転、ACA 固定設定、最長一致の例外、AppGW 通常 v2 の制約、3 問の確認問題を確認。
- `sourceReferences` 6 件のファイルが存在し、HTML 4 アンカーは実在。各章の出典と内容も対応する。
- 生成前の修正推奨：Gateway 共有図の帰り道が `spoke→onprem` の 1 本で Gateway を迂回して見えるため、`spoke→gw→onprem` の 2 本へ変更する。4 台詞で同じ図を使用。
- 軽微な表記改善推奨：管理直接経路の「ApiManagementサービス宛て」は「ApiManagementサービスタグ宛て」とすると APIM VIP との混同を避けられる。

上記 2 点は root と a1_script 担当へ報告し、修正後の 82 台詞・5,782 字の台本（SHA-256 `bf5248d25e7ec8a3cb8e9a06e3243442d700f09a205094ae8fde895177194614`）で反映を確認した。Gateway 共有図の復路は 4 台詞すべてで `spoke→gw→onprem` となり、管理直接経路の speech/caption ともサービスタグと明示されている。

**技術レビュー結果：合格。未解決の技術修正指摘なし。** 映像の収まり・音声の聴感はこの技術レビューの対象外。

生成用の最終台本は `status` を `ready-for-generation` に変更し、内容は同一。最終 SHA-256 は `34d6eff2ef28b31170b9df8c634954123a4cb8747b9941c9181dac3bfcf2b08d`。


## 最新計画に合わせた設定ビュー改訂の監査（2026-11-02）

最新版のCONTENT-PLAN、AGENTS、PRODUCTION-GUIDE、SETTINGS-VIEWを再読した。A1のテーマ、到達点、参照範囲47・48・49・53節、主例「APIM公開宛てをハブFWへ集約する往復」は初稿時と同一。教材本文と現行IaCの関連定義にも、既存82台詞の技術内容を変える必要がある差分はない。

新しい必須差分は画面基準 `teaching-settings-v2`。本編を教材用の設定メニューと補助図へ変更し、問いから回答・反応まで同じviewId・構図・縮尺を保持する。自動ズーム・パン・赤枠pulse・疑似ポインターを入れず、質問中に赤枠で正解を先取りしない。静止画に加えて40〜70秒程度の連続した問答動画を確認する。Azure Portalのブランドバーや検索欄の部分模倣は使用しない。

### 既存本文・音声の再利用判定

**82台詞の本文は再利用可能。本文変更・再録を必要とする技術指摘なし。** 現行の参照と照合した結果は次のとおり。

- 個別に採用するprivate接続・出口集約・中央DNS・Gateway共有、両側peering Connected、転送を受け入れる側のallowForwardedTraffic、非推移性とGateway共有制約は教材48節と整合。
- APIM公開backendとの往復をhub FW経由、AppGW↔APIM/ACAの往復をlocal FW経由に分けている。PE直接、ApiManagementタグ→Internet、実APIM/ACA入口IPの/32維持も整合。
- `network.bicep`の基本3RTは利用者伝搬値を反転してARMへ渡し、ACAはdisableBgpRoutePropagation:true固定。APIMのdefault-egress/具体的AppGW復路/管理直接経路、ACAのdefault-egress/agw-returnの扱いも一致する。
- 通常AppGW v2の0/0 forced tunnelingを避ける説明、Gateway由来の全経路が伝搬停止の対象になり得ること、具体経路とピアリング優先の例外の説明は整合。

Google Cloud生成証跡は82/82成功・complete。speech/caption/delivery、章名・章ID、順序、話者・声、間とstyleに関わる入力を保ったままvisualだけを変更すれば、`prepare --replace`→`generate`で検証済み原音を再結合し、`build --reuse-audio true`で追加API送信なく改訂できる。追加送信0件と再利用ハッシュはrootによる生成・合成時の実結果で確定する。

### 図への言及と保持単位

| 台詞 | 必要な画面上の対応 |
| --- | --- |
| 003 | 冒頭の予告。本編024〜032で公開宛て往復を実際に図示 |
| 024〜032（特に027） | default-egressの代表設定とAPIM→hub FW→公開依存先、公開応答→同じhub FW→APIMを併記。027の「図の矢印」に対応し、質問030から回答031・反応032まで同一図を保持 |
| 033〜034 | 0/0とPE/VNetの具体経路を比較し、同じ問いの中で配置・縮尺を保つ |
| 035〜041 | AppGW↔local FW↔APIM/ACAの別接続と、APIMのAppGW区画向け復路・管理直接経路・実入口/32を同画面で対応付ける |
| 052〜058 | Gateway共有の条件・両側の設定・社内復路をGW経由図で保持。Coreと社内の復路を直結で描かない |
| 059〜065 | agw/apim/managementのtrue→ARM disable false、false→disable trueとACA固定を比較。反転の問答と縮尺を保つ |
| 070〜078 | 各確認問題で質問中に正解行を強調しない。質問と回答の間に異なる全体図へ戻さない |
| 082 | 「今日描いた往復」への振返り。まとめで公開/hubと入口/localの二つの往復の対応を維持 |

### Microsoft一次資料の再確認

2026-11-02に再閲覧し、現行教材の範囲に関する整合を確認した。実環境への変更・疎通実測は行っていない。

- [VNet peering設定](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-manage-peering)：受け入れ方向の転送許可と、UDR/NVAは別途必要、Gateway共有は両側設定と既存Gatewayの制約を持つ。
- [Azure経路選択](https://learn.microsoft.com/en-us/azure/virtual-network/virtual-networks-udr-overview)：最長一致、同一prefixのUDR/BGP/system順とVNet/peering等の例外、伝搬停止の対象を照合。
- [AppGW infrastructure](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure#supported-user-defined-routes)：通常v2のFW/NVA/hub/on-premへの0/0強制トンネル制約を照合。Network Isolation等の別条件はこの教材の対象外。

今回の判定は本文・技術範囲の監査である。改訂後SVG/設定ビューの技術対応、視認性、連続問答の保持は完成後のレビューで別途確定する。


### 設定ビュー実装の技術レビュー

改訂script（確認時SHA-256 `28ae7b6de92d9d5e77b10fe756eff7958a700d0b7417b887be820183f3a22acc`）、`render-settings-v2.mjs`、生成SVGを全件確認した。82台詞のid/speaker/chapterId/speech/caption/subtitleLines/subtitleChunks/gapAfterSeconds/pauseAfterSeconds/deliveryは保存した初稿と完全一致した。

**技術レビュー結果：合格。技術内容を理由とする修正・再録は不要。** 本編78台詞を18のviewIdへまとめている。5系統の主図は転送、APIM公開出口、内部入口、Gateway共有、まとめ。設定表・欄は13系統。各表/図は接続案を教材として示し、ハブ側資源が現行IaCで構築済みであるとの表示や、推測した実IP/実CIDRを確定値として置く箇所はない。

| 設定ビュー | 技術内容の確認 |
| --- | --- |
| scope / ownership / preserve | 採用範囲を個別選択し、現行IaCがハブ・peering・共有Gateway・中央Resolverを作らない点を表示。6区画、local FW入口検査、PE接続を維持 |
| address-plan / peering | 全CIDRと接続先全体を照合、双方向接続、両側Connectedと個別許可を区別。実状態を取得した値ではなく確認事項として示す |
| forwarded | 元送信元→hub FW/NVA→Core、Core側の対hub peeringで受入方向にallowForwardedTraffic。逆も受けるならhub側に設定。UDR/中継装置/許可/復路を別途準備 |
| public-egress | APIM→hub FW→公開依存先、応答は同じhub FW→APIMへ戻る。default-egress 0/0→hub FW private IPは企業側指定の接続案、依存先許可/SNAT/Core復路を併記 |
| specific-routes | 0/0とPE/VNet/peering宛ての具体経路を区別。公開出口変更だけで全宛先を上書きする表現はない |
| local-return / agw-default | AppGW↔local FW↔APIM/ACAの別接続。APIMのAppGW区画向け復路、実APIM/ACA入口/32、ApiManagementタグ→Internetを対応。通常AppGW v2の強制トンネル制約を明示 |
| subnet-routes | 管理VM/ACAは採用時のみ公開出口変更、VM保守元復路・ACA agw-return維持。PE/APIM→ACAの直接経路、PE/FW区画の現行RTなしを保持 |
| gateway-sharing | 社内↔hub Gateway↔Coreの双方経路。復路がGWを迂回する直結図になっていない。hub allowGatewayTransit / spoke useRemoteGatewaysの方向と条件が正しい |
| propagation / agw-propagation | agw/apim/managementの既定true→ARM disable false、false→disable true、ACA固定true。既定経路だけの除外ではなく必要社内/DNS経路も対象になり得る。優先順位と例外、peering削除とは別という説明も整合 |
| quiz 3問 | 条件とA/B選択肢は技術的に妥当。質問070/073/076は無強調、回答071/074/077から正しいBを赤枠で表示 |
| summary | 公開backendとAPIMのhub FW往復、公開入口と内部入口のlocal FW往復を別段で整理。次回A2のDNS/許可/出口IP/切替へ接続 |

18 viewIdごとに、settingsのhighlight指定だけ、SVGのhighlight矩形だけを除いた基本内容が同一であることを機械照合した。78台詞すべてで同じviewId内の構図・項目・基図は一致する。赤枠以外の内容が問答の途中で差し替わる箇所はない。字幕chunkごとの映像保持と連続動画での判断はrootおよび視認QA担当が別途確認する。

本レビューは技術内容と設定・図の対応を対象とする。文字収まり、連続動画、聴感は別の確認として扱う。
