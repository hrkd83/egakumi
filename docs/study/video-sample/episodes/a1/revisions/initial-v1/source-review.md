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
