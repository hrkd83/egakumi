# 第6回の技術根拠確認

2026-11-02（JST）確認。対象は教材とリポジトリ定義・公開設定例であり、稼働Azureの設定取得・疎通・出口IP実測は行っていない。

## 教材と実装の対応

| 論点 | 教材・主な実装 |
| --- | --- |
| Web依存先を送信元・プロトコル・FQDNで許可する | 教材31節 `fw-base-applications`。`infra/modules/network.bicep` のAPIM・管理VM Applicationルール（530〜578行付近）。Network一致後にApplicationのFQDN許可を重ねて要求しない。標準ルールは `terminateTLS:false`。 |
| アプリ実行・ビルドにも公開依存先がある | 教材32節 `fw-portal-applications`。`infra/modules/portal-network.bicep`、`credential-broker-network.bicep`、`apps/portal/deploy/gateway-egress.bicep`。ACR PE通信とGitHub・Node・Docker等の公開FQDN許可を区別する。 |
| 公開paramsの空配列は最終許可先ではない | 教材33節 `fw-generated-settings`。`params/production.example.json`・`params/test.example.json` の `apimAllowedFqdns`・`managementAllowedFqdns` は空。`scripts/deployment/helpers/configuration.py` の1266〜1280行付近が選択CA・DNS API・PowerShell配布先・公開Gateway名を追加、1389行付近がPortal公開名を追加する。 |
| API公開は既存許可を保持して追加する | `apps/gateway/deploy/api_publication.py` の128〜161行付近でAPI本体・Operation上書きのHTTPS/WSS・443・明示DNS名を抽出、230〜263行付近で未登録ホストを追加。API削除・変更だけで旧FQDNを自動削除しない。 |
| 基盤再配備でも検査した既存APIM許可を引き継ぐ | `scripts/deployment/lifecycle.py` の216〜221行付近、419〜481行付近。環境所有・対象コレクション・APIM送信元・HTTPS境界を検査する。全グループを無条件にコピーしない。 |
| 暗黙出口無効・経路・許可・SNATは別 | 教材36節 `egress-explicit`。`network.bicep` の250/265/274/283/293行付近で5サブネットへ `defaultOutboundAccess:false` を明示。APIM・管理VM・ACAの `default-egress` はFirewall向け。PEの直接経路やAPIM管理例外を一律に含めない。 |
| 入口IPと出口IPは別リソース | `network.bicep` の `pip-afw-*` と `pip-agw-*` は各々Standard・Static・IPv4。Firewallの実IPは `firewallPublicIpAddress` 出力。AppGWの入口IPを外部サービスの許可元IPと取り違えない。 |
| 非SNAT範囲は通信のAllow一覧ではない | 教材37節 `egress-snat`、`network.bicep:379` 付近の `snat.privateRanges`。公開例の `IANAPrivateRanges` はNetworkルール用の非SNAT宛先。AppGW→Firewall→APIM/ACAのprivate通信ではAppGWのprivate IP保持と復路の整合へつながる。IDPS用privateRangesとも別。 |
| ApplicationとPE直接経路のSNATを区別 | Applicationルールは宛先がprivateでも透明プロキシでSNAT。PEへの直接接続はFirewallを通らず、FirewallのSNATも受けない。 |
| 期待値の配布と実測済み結果を区別 | `infra/main.bicep:629` → `scripts/deploy_portal.py:94` → `apps/portal/deploy/application.bicep:43` で `expectedEgressIp` → `EXPECTED_EGRESS_IP` を配布。`portal-network.bicep:162` 付近で `api.ipify.org` を許可するが、Portalアプリにはこの変数を使う自動実測・照合処理がない。 |

## 公式一次資料の確認

- [Azure Firewall SNAT private IP address ranges](https://learn.microsoft.com/en-us/azure/firewall/snat-private-range)：Networkのprivate宛て非SNATと、Applicationは宛先に関係なくSNATする仕様を確認。
- [Default outbound access in Azure](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/default-outbound-access)：暗黙出口の無効化後も明示的な出口を使えることを確認。

後者にはmanaged/delegated PaaSサブネットへのprivate subnet機能の適用除外も記載されている。動画は「Bicepで5サブネットへ属性を明示」「管理VM等の暗黙出口に依存しない」という教材の範囲で説明し、ACA/APIMへの効果まで一般化しない。仕様変更の時期・移行手順は本回の範囲外とする。

第5回で扱うルール評価順・内部中継・基盤Network許可は前提の短い復習に留める。第7回のDNS・実効経路・NSG・Firewallログの詳細な照合手順へ広げず、今回はWeb許可・生成される許可先・SNAT・期待値と実測の区別を到達点とする。

## 台本の独立内容レビュー

2026-11-02、87台詞・読み上げ6051字の圧縮後原稿を確認した。確認時の `script.json` SHA-256は `4fe1786785349e96a782454aa09a9f289495d38eb27f81df64ca34479c24f49c`。技術上の重大な問題、教材・実装との矛盾、到達点の欠落は見つからなかった。公開設定例と稼働Azureの実測を区別し、`defaultOutboundAccess` のPaaS機能への効果も一般化していない。

確認問題3問は、空の入力配列から最終許可先を決めないこと、非SNAT範囲が通信許可ではないこと、期待値・確認先の設定だけでは自動実測済みと言えないことを正しく確認している。`sourceReferences` と全章の `sourceRefs` にある5節のローカルファイル・アンカーはすべて存在を確認した。

初稿で指摘した2図は修正済み。期待値の受け渡しと確認先の許可を別カードに分け、自動実測の未実装を表示した。AppGW→Firewall→APIM/ACAの図も、送信元がAppGWのprivate IPであることを示す表現に変更され、ACAにNSGがあるとの誤読を避けている。

字幕の英単語分断（turn063〜064など）は別途作者へ伝達した。これは表示品質の確認項目であり、上記は映像・音声の完成検証を代替しない。
