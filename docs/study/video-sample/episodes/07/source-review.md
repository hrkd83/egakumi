# 第7回 技術出典レビュー

2026-11-02 JST。リポジトリ内の教材・IaCとMicrosoft Learnの列仕様を照合した。Azure実環境への問い合わせ、KQLの実行、通知メールの到着試験は行っていない。

## 到達点と編集範囲

`CONTENT-PLAN.md`第7回の到達点は「DNS・実効経路・NSG・Firewallログを同じ通信について照合し、ログ記録と通知を区別できる」。中心出典は `01-network-control.html` の38節 `observability-logs`、39節 `observability-triage`、40節 `observability-alerts`、46節 `network-control-map`。DNS/UDR/NSGの説明は既習事項を調査手順へ結び直す範囲にする。WAF、API認証、要求の処理結果の詳細は第13回へ残す。

## 一本の通信を追う主例

U1のAppGW → Firewall → APIM（通常HTTPのTCP 80）を主例にする。

- 利用者→AppGW、AppGW→APIM、APIM→Foundryはそれぞれ別のネットワーク接続。利用者の公開IPを、AppGWからAPIMへ張る接続のNSG送信元として扱わない。
- AppGWが接続するAPIM名は、APIMの実FQDNと同名のPrivate DNSゾーンの `@` Aレコードから実内部VIPへ解決する。APIM自身のPrivate Endpointではない（9節）。端末側のDNS回答だけで確認を済ませない（10節）。
- 往路は `network-bindings.bicep` の `apim-via-firewall`、実APIM VIP `/32` → Firewall private IP。復路は `network.bicep` の `agw-return-via-firewall`、AppGW区画 → 同じFirewall。既定 `0/0` の存在だけでこの経路を説明しない。
- AppGW側送信NSGは `allow-apim-http`、APIM側受信NSGは `allow-agw-api`。いずれもAppGW区画→APIM区画、TCP 80を許可する。Firewallの許可先は実APIM VIP。NSGはAPIMサブネットを宛先とするので、NSG宛先とFW/UDR宛先の粒度を混同しない。
- Firewall側のNetwork許可は `agw-to-apim-http`。Networkルールで一致すればApplicationルールへは進まない。「HTTPだからApplicationログ」と決めない。
- private宛てNetwork通信は公開設定の `snatPrivateRanges:["IANAPrivateRanges"]` ではSNATしないため、APIM側NSGでAppGW区画を送信元に照合する（37節）。設定変更がある実環境では、観測する送信元を確認する。

調査票には対象環境・送信元リソース/実IP・宛先FQDN/名前解決後IP・プロトコル/宛先ポート・失敗時刻とタイムゾーンをまとめる。FWリソースIDとLAWも対象環境へ合わせる。送信元ポートが分かれば照合に利用できるが、時刻だけで別通信を結び付けない。

## 比較例：FWログがないとき

D2のAPIM → Foundry PE（TCP 443）はPEへの具体的な直接経路。APIM送信 `allow-private-backends` とPE受信 `allow-apim-foundry` を確認する。APIMの既定経路がFirewall向けでもPE直接経路を優先し、標準構成ではFirewallで判定・SNATしない。FWログ不在から未接続・FW拒否を導かない。

DNS回答とPEの実IP群、PE接続状態、送受信側NSG、実際の経路、接続先サービスのログを確認する。Foundry PEは複数IPを持ち得るため、先頭IPやサブネット名から宛先を推測しない。FWルールが存在することはFW経由の証拠にならない。現行infraにVNet/NSG Flow Logs、Packet Captureを作る定義はなく、FW Flow Trace/Fat Flowも既定収集しない。

## ログの読み方と限界

- `AZFWNetworkRule`: `TimeGenerated`（UTC）、`_ResourceId`、`SourceIp`、`DestinationIp`、`DestinationPort`、`Protocol`、`Action`、`ActionReason`、`Policy`、`RuleCollectionGroup`、`RuleCollection`、`Rule`を照合。
- ルール不一致の既定拒否は `ActionReason` に `Default Action` と記録される場合がある。独自のDenyルール名だけを探さない。
- `AZFWApplicationRule`: Web通信がこのルール種別へ進む場合に `Fqdn`、`SourceIp`、`DestinationPort`、`Protocol`、`Action` とルール階層を確認。`TargetUrl` は全HTTPSで得られる列ではない。
- FW AllowはFWルールの許可であり、往復疎通やサービス認証/認可、要求処理完了の証明ではない。IDPS等の判定との分担は詳細回へ残す。
- AppGWアクセスログでは時刻・パス・status・backend・TransactionIdなどを補助的に照合。TransactionIdがFWへ共通で伝搬する前提にしない。WAFログとは別。
- FW経由の想定でログがない場合も、Resource ID/LAW/時刻範囲・タイムゾーン/取り込み遅延/診断設定/収集対象を確認し、ログ欠落を直ちに通信拒否へ結び付けない。
- Network WatcherのNext hopやIP flow verifyはVMを対象とする診断。APIM・AppGW等の管理サービスへ一律に実行できる前提にしない。動画では実効経路・関連付け・サービスで利用できる診断を照合する原理まで説明する。

## 収集・保存・通知を分ける

`firewall-diagnostics.bicep` はNetwork/Application/ThreatIntel、Premium時のみIDPSをDedicated形式でLAWへ保存する。監査Storageへの転送は `enableAuditCollection` と保護済み保存先を前提に有効化する。AllMetricsはLAWのみ。

AppGW WAFログはLAW＋監査Storage（WAF_v2、監査収集有効時）。AccessログはLAWでURL queryを取り除いてからStorageへexportし、WAF_v2では生queryをこの経路でStorageへ直接送らない（`application-gateway.bicep`、`log-privacy.json`）。

公開設定のLAW保持は90日。専用テーブル別の実環境上書きを確認する。本番監査StorageはGRS・Locked・2557日、不変保護期間は自動削除期限ではない。動画で保持値を全列挙する必要はない。

`monitoring-bindings.bicep` のAction Group `ag-<stem>-ops` は `monitoring.operatorEmailAddresses` 宛てCommon Alert Schemaのメール通知のみ。Webhookや自動修復・撤去処理は登録しない。

| 種類 | 現行定義の例 | 説明上の注意 |
| --- | --- | --- |
| メトリック | AppGW `UnhealthyHostCount` 平均>0、5分窓/1分評価 | 個別通信のDeny通知ではない。 |
| メトリック | FW `FirewallHealth` 平均<100、5分窓/1分評価 | 通知は機器状態の条件に対するもの。 |
| メトリック | Premium APIM `Capacity` 平均>80、15分窓/5分評価 | Developerには作らない。 |
| 管理操作 | `network-config-changed` のwrite/deleteとSucceeded/Failed | Failedは設定反映済みの意味ではない。 |
| KQLログアラート | 監査Blob書込失敗、App Insights 24時間取り込み80GB超、15分100要求以上でAPI失敗率5%超 | FW Deny全件の通知はない。 |

`enableLogAlerts` は上表3本のKQLアラート有効化だけを切り替える。本番公開configはfalse、テストはtrue。falseでもFW診断ログ収集、メトリック・Activityの通知は止まらない。「通知が来ないから通信は成功」「ログがあるから全件通知される」は誤り。

`networkResourceIds` はVNet/FW/FWPolicy/4NSG/agw・apim・vmの3RT。別宣言のACA用RT、PE、Private DNSゾーンはこの変更通知の一覧に含まれない。Activity収集範囲と通知対象範囲は一致しない。診断設定/LAW exportのwrite/deleteは別のSubscription範囲アラート。

## 推奨確認問題

1. APIM→Foundry PEが失敗し、FW Denyログが見当たらない。最初に何を照合するか。答え: DNS回答とPE IP、具体的な直接経路、両側NSG等。FW Allowの追加へ飛ばない。
2. AppGW→APIMのFW Network Allowが見つかった。API要求の成功まで分かったか。答え: まだ。復路・サービス到達/認証/処理結果の確認が残る。
3. `enableLogAlerts=false` ならFWログもメトリック通知も止まるか。答え: 止まらない。3本のKQLアラートの切替で、全Deny通知を制御するフラグではない。

## 直接読んだ主な定義

- `infra/modules/network.bicep`（NSG、往復UDR、監視対象ID）
- `infra/modules/network-bindings.bicep`（実APIM VIP、Foundry PE実IP、NSG・FW・往路）
- `infra/modules/firewall-diagnostics.bicep`
- `infra/modules/application-gateway.bicep`（診断設定）
- `infra/modules/monitoring-bindings.bicep`（全監視条件・通知先・範囲）
- `infra/modules/monitoring-subscription.bicep`（Activity収集8カテゴリ）
- `infra/modules/monitoring.bicep`（保持・監査・export）
- `infra/main.bicep`（モジュールへの受け渡し）
- `infra/log-privacy.json`
- `config/production.json` / `config/test.json`

Microsoft Learn照合先（2026-11-02参照）:

- [AZFWNetworkRuleの列仕様](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/azfwnetworkrule)
- [AZFWApplicationRuleの列仕様](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/azfwapplicationrule)
- [Next hop](https://learn.microsoft.com/en-us/azure/network-watcher/next-hop-overview)
- [IP flow verify](https://learn.microsoft.com/en-us/azure/network-watcher/ip-flow-verify-overview)

## 執筆済み台本レビュー

2026-11-02 JST、初稿 `script.json` 全91台詞と各図の技術的意味を照合した。音声生成前の技術上の必須修正はなし。

- U1の通常HTTP/TCP 80、AppGW送信とAPIM受信NSG、往復FW経路、AppGW private IP保持は定義と整合。
- D2のPE直接経路、ログ不在の限界、FW Allowから認証・要求処理成功を断定しない説明は適切。
- Network→Applicationの評価順と、既定拒否 `Default Action`、対象FW/LAW/時刻/通信条件の照合は適切。
- 演習は架空の仮定と明示され、実環境調査結果として説明していない。
- メトリックの閾値・評価窓、変更通知の範囲外、`enableLogAlerts`の3本のKQL切替範囲は定義と整合。
- 管理サービスへVM向けのNext hop/IP flow verifyを一律に実行させる誤った操作手順は含まれていない。

推奨する軽微な補強（根本的誤りではない）:

1. turn061: 日本側監査Storageへの転送に「監査収集を有効にした構成では」を足せば `enableAuditCollection` の条件も明確になる。
2. turn054のspeech `IaC`、turn090のspeech `VM` は読み上げ用に「アイエーシー」「ブイエム」へ固定できる。表示用captionは現行英字のままでよい。
3. DNS・経路部分の章の `sourceRefs` に既習節 `dns-maintenance` / `udr-associations` 等を補えば、詳細な原理の根拠へ戻りやすい。
