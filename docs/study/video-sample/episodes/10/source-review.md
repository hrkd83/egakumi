# 第10回 出典・技術レビュー

確認日: 2026-11-02 JST。対象は「Firewallの許可とIDPSの検知」。教材HTMLの9〜11節を主範囲とし、経路の復習とログの読み分けに限って2〜3節・19節を補足参照した。Azure実環境への接続・設定変更・攻撃試験は行っていない。以下の「本番」「テスト」はリポジトリ定義および公開params例を指す。

## 論点と正本

| 論点 | 確認結果 | リポジトリ根拠 |
| --- | --- | --- |
| 本番の動作 | Firewall Premium、idpsMode=Alert、threatIntelMode=Alert、signatureOverrides=[] | `config/production.json:15`、教材 `../02-external-protection.html#idps-settings`（129〜143行） |
| テストの動作 | Standard、idpsMode=Off。Offは正規化時に除去され、IDPS propertyをAzureへ送らない。有効なIDPS設定の混入は検証エラー | `config/test.json:11`、`scripts/deployment/helpers/configuration.py:362`、`infra/modules/network.bicep:363` |
| PremiumのIDPS設定 | `intrusionDetection.mode` と privateRanges / signatureOverrides / bypassTrafficSettings。空配列はプロパティを省略 | `infra/modules/network.bicep:364`〜369 |
| 通常規則の代表例 | `agw-to-apim-http` はNetworkRule、送信元AppGW subnet、宛先APIMのprivate IP、TCP80。gRPC有効時はbackend portを追加 | `infra/modules/network-bindings.bicep:190`〜207 |
| AlertとDeny | Alertは通信規則と並列。Denyは通常規則処理後inline。規則AllowとIDPSの遮断は両立し得る。Denyを現行配備値のように話さない | 教材 `#idps-detection`（146〜152行）、Microsoft Learn rule-processing |
| 経路/TLS | 通常APIのAppGW→APIMはHTTP80。PortalはFW上ではHTTPS443、gRPCはTLSでAPIM443へ。TLS復号設定なし。暗号化本文を読めるとはしない。PE直接経路はFW検査範囲外 | 教材 `#ingress`（41〜49行）、`infra/modules/application-gateway.bicep:563`、`infra/modules/portal-network.bicep:134`、各ApplicationRuleのterminateTLS=false |
| IDPS方向分類 | idpsPrivateRanges=[] は独自範囲の指定なし。既定RFC1918。private同士のAppGW→APIMはInternal/East-West。元利用者がInternetでもInbound専用署名が全部適用されるわけではない | 教材 `#idps-coverage`（155〜166行）、`params/production.example.json:40`、Microsoft Learn premium-features |
| 署名例外 | signatureOverridesは署名単位、bypassTrafficSettingsは通信単位のIDPS除外。WAFのパス/引数単位の例外とは粒度が違う。全署名がDeny可能とは限らない | `config/production.json:24`、`params/production.example.json:41`、教材 `#idps-coverage`、Microsoft Learn idps-signature-categories |
| SNATとの分離 | snatPrivateRangesはNetworkRuleの送信元変換の判断。IDPS方向分類とは別。private宛の代表NetworkRuleはAppGWの送信元IPを保持。ApplicationRuleは常にSNAT | `infra/modules/network.bicep:377`〜379、`params/production.example.json:37`、Microsoft Learn snat-private-range |
| ログの分離 | Network/ApplicationのAllow/DenyとIDPSのActionを別に見る。PremiumのみIDPSログを追加。Alertはメール通知の設定ではない | `infra/modules/firewall-diagnostics.bicep:35`〜39、`infra/modules/monitoring-bindings.bicep:216`〜224、教材 `#idps-logs`（287〜301行） |
| 照合キー | 対象FW Resource ID、UTC時刻、送受信IP/port/protocol、署名ID等。AZFWIdpsSignatureにTransactionId列はない | 教材 `#idps-logs`、Microsoft Learn AZFWIdpsSignature列一覧 |

## 台本で保つ判断の順

1. 対象通信の両端・経路を選ぶ。FWを通る区間かを確認する。
2. 通常規則が何をAllow/Denyしたかを読む。Allowは業務処理成功の保証ではない。
3. IDPSのモードと署名の有効モードを読む。現行AlertではIDPS検知自体は遮断しない。
4. TLSで見える範囲とprivate IPによる方向分類を確認する。
5. 同じ通信のログActionと下流の到達・処理結果を照合する。

確認問題には「Allowログ＋IDPS Alertの一致ならIDPS遮断か」「Internet利用者由来のAppGW→APIMは常にInboundか」「HTTPSをFWへ通せば暗号化本文を読めるか」が適している。正解を質問画面の赤枠で先取りしない。

## 誤解を防ぐ注記

- Alertは“安全を保証して通す”という判定ではなく、検知・記録の動作モード。通信規則と別の判断を表す。
- IDPSログが見つからないことは、攻撃がないことや本文が検査済みであることの証拠にならない。経路・TLS・署名方向・プロトコル・例外・ログ収集範囲に左右される。
- DenyはIDPSポリシー全体のラベルだけで最終動作を断定しない。一部署名は既定Alert Onlyで、動作固定の署名もある。
- IDPSの遮断はTCP RSTを返さず、timeoutとして見える場合がある。HTTP403はWAF等の別機能もあり、症状だけで原因を決めない。
- Standard環境ではPremium IDPSの検知/遮断を実証できない。切替の詳細手順や実環境試験はこの回で実施していない。
- 本編の値・図は定義に基づく教材用であり、稼働Azureの取得画面ではない。

## 確認したMicrosoft一次資料

Webで2026-11-02に参照。直接引用はせず、技術条件を要約した。

- [Azure Firewall rule processing logic / IDPS](https://learn.microsoft.com/en-us/azure/firewall/rule-processing#idps): Alert並列、Deny inline、RSTなし。
- [Azure Firewall Premium / IDPS private ranges](https://learn.microsoft.com/en-us/azure/firewall/premium-features#idps-private-ip-ranges): TLS非復号時の制約、署名の方向条件、RFC1918既定、Internal分類、署名有効モード。
- [IDPS signature categories / override limitations](https://learn.microsoft.com/en-us/azure/firewall/idps-signature-categories): 署名モードの上書きと、変更できないcontext-setting署名。
- [SNAT private ranges](https://learn.microsoft.com/en-us/azure/firewall/snat-private-range): Networkのprivate宛SNAT除外、Application常時SNAT。
- [AZFWIdpsSignature columns](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/azfwidpssignature): Action/SignatureId/送受信情報/UTC時刻等。TransactionId列なし。
- [Threat intelligence filtering](https://learn.microsoft.com/en-us/azure/firewall/threat-intel): 既知の悪性IP/FQDN等との照合、AlertとAlert and Denyの独立した設定。

## レビュー状態

出典・論点の独立確認は完了。完成台本の文言と画面は別途照合する。


## 補助図の内容レビュー

`diagrams/coverage-paths.svg`、`direction.svg`、`idps-modes.svg` のテキスト・ノードと矢印を出典に照合した。重大な不正確さなし。

- coverage-paths: 通常APIのHTTP80、PortalのHTTPS443とTLS復号設定なし、Foundry PE直接経路を区別している。
- direction: 独自指定なしのRFC1918既定とInbound/Outbound/Internal分類、FWが観測するIPで分類する点を示している。
- idps-modes: 現行本番Alertと機能説明Denyを区別し、並列/inlineと、規則Allow後にも署名によりdropし得ることを示している。Deny下段はAllowだった場合の例として妥当。
- 任意の表現改善: 「規則Denyは開かない」は「規則Denyは覆さない」のほうが判断の独立性を明確に表せる。rootへ提案済み。

この段階ではSVGの内容・座標関係の確認であり、完成動画の視聴確認を意味しない。
