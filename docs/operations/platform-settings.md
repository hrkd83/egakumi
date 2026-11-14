# WAF・外向き通信・監視・容量を変更する

[共通確認](common.md) · [API変更](apim.md) · [運用テスト](../testing/operations-tests.md)

構築済み環境の設定を、目的の資源・項目に限定して変更する。初期構築の
`azd up`・`azd provision`や、全VNet・全ポリシーの作り直しは使わない。
変更前の実設定、入力の正本、戻す値、確認する正常要求と拒否条件を先に決める。
机上確認・ローカル回帰・実環境での反映結果は分けて記録する。

| 作業 | 既存の入口 | 正本・確認先 |
| --- | --- | --- |
| データ項目に対するWAF誤検知 | `scripts/operations/waf.py` | WAF manifestと対象パスの実ポリシー |
| 4 NSGの通信範囲・優先度 | 対象の規則に限定した操作を決める | [共通採番と規則の用途](network-security-groups.md)、params、Core state |
| 不要なDNS用NSG子規則の回収 | `scripts/operations/nsg_dns.py` | Azure提供DNS、所有する3 NSGの既知の6子規則 |
| AppGWの重複する送信規則の回収 | `scripts/operations/nsg_appgw_egress.py` | 同じVNetのAPIM・Vault PE宛ての既知の3子規則 |
| APIバックエンド追加に伴う外向き許可 | Gateway個別配備（G） | API定義、対象Firewall規則、APIM実設定 |
| 通知先・監視条件 | `scripts/operations/monitoring.py` | params・対応Bicep、実通知設定・条件・ログ |
| AppGW・Container Appの容量 | 対象資源の構成／スケール画面 | 対応config／Bicep、実設定・revision・稼働状態 |

実ID、宛先、本文、実測結果、設定の保存物は保護した環境別領域へ置く。
変更要求の応答が不明なら、現在状態と進行中の操作を読んでから再開する。
保存済み成功JSONだけで今回の反映を成功にせず、古い設定を資源全体へ
一括上書きして切り戻さない。

## WAFの誤検知を直す

既存のデータ用ポリシーについて、正常入力が誤って拒否された原因を直す。
入口は[WAF保守CLI](../../scripts/operations/waf.py)、データ項目の正本は
[manifest](../../apps/gateway/waf-data-exclusions.json)である。
GatewayとPortalでは本文の形とCookieの扱いが異なるため、
[互換性と適用範囲](../testing/waf-compatibility.md)も確認する。

1. 対象ホスト・APIパス・時刻と、WAFの実際の検出ルールを照合する。
   匿名401、APIMの入力拒否、バックエンド障害をWAF誤検知として扱わない。
   本文やCookieをまとめてログから取得せず、合成した最小入力で原因を確認する。
2. 対象profileと、そのprofileを共有する全パスを確認する。manifestの
   `matchVariable`・`selectorMatchOperator`・`selector`と、必要な
   `ruleGroupName`・個別`ruleId`だけを変更する。共有ポリシーの除外は、
   その全パスへ適用されるものとして影響を判断する。
3. 全体のPreventionを解除したり、ルール群全体を無効化したりしない。
   異常スコアの最終判定ルールを除外して通す方法も使わない。
   本文上限、raw本文の保護、Portal Cookie、対象外の除外は保持する。
4. 対象のローカル回帰と差分確認を行い、適用先だけを選んで計画する。
   次はGatewayの場合で、Portalだけなら`--scope portal`を使う。

   ```sh
   python3 -B scripts/operations/waf.py --config "$DEPLOY_PARAMS_FILE" --scope gateway
   ```

5. 計画したprofile・パス・除外を確認し、同じ入力で実行する。

   ```sh
   python3 -B scripts/operations/waf.py --config "$DEPLOY_PARAMS_FILE" --scope gateway --execute
   ```

このCLIは選択scopeのデータ用ポリシーと経路を照合する。単一ruleだけを
無条件にPATCHする入口ではない。固定の認証・Speech・Document用ポリシー等を
manifestで変更できるものとして扱わず、予定外の差分があれば停止する。
ETag競合・未完了時も、ポリシーやAppGWを削除して再作成しない。

反映後は実除外とパスの参照先を読み、元の正常合成入力が通ること、
同じパスの関係する攻撃入力が引き続き拒否されること、対象外の代表要求が
変わらないことを確認する。403だけで拒否原因を確定せず、必要なWAF記録を
照合する。過去のWAF要求全件を再送する必要はない。

戻す場合は今回変更したmanifest項目だけを旧値へ戻し、同じscopeで計画・反映する。
実除外・経路と正常／拒否を再確認する。旧資源JSON全体の復元で、他の変更を消さない。

## 不要なDNS用NSG子規則を回収する

Azure提供DNSを使う本構成では、`allow-dns-tcp`と`allow-dns-udp`を
AppGW・APIM・管理VMのNSGへ追加しない。Incremental配備は省いた子規則を
削除しないため、既存環境の回収には次の専用入口を使う。

```sh
python3 -B scripts/operations/nsg_dns.py --config "$DEPLOY_PARAMS_FILE"
python3 -B scripts/operations/nsg_dns.py --config "$DEPLOY_PARAMS_FILE" --execute
```

1. 同じ環境の保護params・Core stateを使い、最初に計画する。構成のVNet／
   Firewall DNS、実VNetの`dhcpOptions.dnsServers`、実Firewall Policyの
   `dnsSettings.servers`、実Firewall本体のDNSがすべてAzure提供DNSであることを
   確認する。独自DNSがあれば停止し、この入口でDNSの切替を行わない。
2. 保護された計画で、3 NSGの所有、規則名、Outbound・TCP／UDP・宛先53・
   Azure提供DNS宛先・全送信元と、既知の優先度を照合する。PE用NSG、同番号の
   Inbound規則、独自規則は削除しない。意味の異なる同名規則も停止する。
3. `--execute`は全対象の事前照合後、直前に再読取して子規則だけをDELETEする。
   親NSGのPUTは行わない。202応答後は子の不存在と親のSucceededを待ってから
   次へ進む。他規則・既定規則・タグ・サブネット／NIC／flow log等の関連付けと、
   DNS設定の保持を最後に照合する。
4. 再度計画・実行し、残存削除0件・Azure書込0件を確認する。管理VM等の通常の
   DNS解決と代表接続は別途確認し、規則不存在だけで疎通成功としない。

計画・試行・各削除・結果は環境別`state/operations/`へ保護保存する。
応答不明・未完了・競合時は自動再送しない。現在の子規則と親NSGを確認してから
残件を計画し直す。ETagを使える子ではIf-Matchを付けるが、直前読取と
ローカルロックは別端末の同時変更に対する原子的な排他を保証しない。

通常の`azd up`／`azd provision`と直接core配備でも、同じ限定回収を既存規則の
保持確認より先に実行する。新規構築や回収済み環境ではDELETEしない。
通常hookの保護記録は環境別`state/dns-nsg-retirement/`に置く。
切戻しが必要なら保存した対象規則と現在のDNS・他設定を確認し、今回の6子だけを
個別に復元する。古いNSG全体のJSONをPUTしたり、DNS設定を変更したりしない。

## AppGWの重複する送信規則を回収する

AppGWの送信にはカスタム規則を作成せず、既定65000・65001・65500を使う。
同じVNetのAPIMとKey Vault PEへの通信は65000で許可されるため、次の3本を
個別に許可する必要はない。対向NSGの受信Allowと4096のDenyは維持する。

| 方向 | 優先度 | 規則名 | プロトコル・宛先 |
| --- | --- | --- | --- |
| Outbound | 120 | `allow-apim-http` | TCP80・APIMサブネット |
| Outbound | 125 | `allow-apim-grpc` | TCP443・APIMサブネット |
| Outbound | 220 | `allow-key-vault` | TCP443・Key Vault PEのIP |

Incremental配備だけでは既存の子規則が残るため、構築済み環境では次の入口を使う。
基盤全体を再配備せず、指定した環境のこの3子規則だけをDELETEする。

```sh
python3 -B scripts/operations/nsg_appgw_egress.py --config "$DEPLOY_PARAMS_FILE"
python3 -B scripts/operations/nsg_appgw_egress.py --config "$DEPLOY_PARAMS_FILE" --execute
```

1. 同じ環境の保護params・Core stateを選択し、所有するAppGW用NSG、
   VNet内のAppGW・APIMサブネット、Vault PEと実IP、既定の送信許可を確認する。
   送信Denyや意味の違う同名規則等があれば停止する。
2. 計画で規則名・優先度・Outbound・Allow・TCP・送信元・宛先・ポートを
   全3本照合する。同番号のInbound、PEの同名`allow-key-vault`は削除しない。
3. 実行直前にも現物を再読取し、子規則だけを削除する。親NSGのPUT、
   サブネット関連付け、UDR、Firewall、DNSの変更は行わない。
   各子の不存在と親のSucceededを待ち、他規則・既定規則・関連付けの保持を照合する。
4. 再実行で残存削除0件・Azure書込0件を確認する。4 NSGの他規則と関連付け、
   UDR・Firewall・DNSを変更前と比較する。新しい接続で通常API、gRPC経路、
   公開Portalと転送先の応答を確認し、AppGWのVault証明書参照と公開TLSも照合する。
   TLSハンドシェイクや認証拒否だけを、gRPCの実モデル推論成功として報告しない。

計画・試行・各削除・結果は環境別`state/operations/`に保護保存する。
応答不明・競合・未完了時は自動再送せず、現物と記録を確認して残件を計画し直す。
ローカルロックとETagの直前照合は、別端末からの同時更新を完全には防がない。
切戻す場合も保存した対象3子だけを個別に復元し、古い親NSG全体をPUTしない。

通常の`azd up`／`azd provision`と直接core配備でも、同じ限定回収を
既存規則の保持確認より先に実行する。新規構築・回収済み環境ではDELETEしない。
通常hookの記録は環境別`state/appgw-egress-nsg-retirement/`へ保存する。
この入口は既存DNSルール廃止処理とは独立しており、DNS用6子の削除を同時実行しない。

## APIバックエンドに必要な外向き許可を追加する

対象は、API定義に登録する外部バックエンドの固定TLS宛先である。
[公開処理](../../apps/gateway/deploy/api_publication.py)は、APIM送信元の
HTTPS／443・FQDN許可を既存Firewall規則へ追加する。
任意サブネット、任意ポート、全インターネットを許可する入口ではない。

1. 許可されたバックエンドのFQDN、APIパス、操作、認証方式、正常応答を決める。
   `backendService: external`・`backendUrl`等を既存のAPI定義契約に合わせる。
   接続先URLへsecretやtokenを埋め込まず、IPやワイルドカードへの置換で広げない。
2. 現在採用中のAPI定義一覧に変更対象を加える。既存APIやProduct所属を
   意図せず外さず、認証・購読・許可モデル・既存バックエンドを保持する。
   新しい認証方式やAPIの実装を、この通信許可の作業だけで追加できると扱わない。
3. 対象Firewallの`rcg-egress` → `allow-apim-egress` →
   `apim-approved-services`と、元の許可先・送信元・HTTPS／443、他の規則を保存する。
   管理VM、Portal、DNS、他用途の許可を変更対象へ含めない。
4. [Gの計画・実行](common.md#計画してから個別に反映する)を行う。
   計画内の追加FQDNとAPI一覧を確認する。GはBroker・CM・SQL等も扱うため、
   それらに未確定の差分があれば一緒に反映しない。
5. Firewallの追加宛先と目的外設定の保持、実APIの接続先・認証・公開状態を読む。
   新バックエンドへの小さい認証付き要求、未認証拒否、既存APIの代表要求を確認する。
   下流の権限不足をFirewall許可の成功・失敗だけで説明しない。

公開処理は最新ETagで既存規則へ宛先を追加し、読戻しで保持を確認してから
API公開へ進む。途中で失敗しても追加許可が既に反映された場合がある。
現在のFirewall・配備履歴・APIを確認し、同じ定義で再開する。

元へ戻す場合は、既存APIの変更なら旧バックエンド定義をGで反映する。
専用APIを追加した場合は、その公開・Product所属・API自体の回収対象も確認する。
定義から省いただけでAPIやFQDNが自動削除されるとは扱わない。
今回の追加FQDNを他のAPIが使っていないことを確認した後、Azure Portalの
対象Firewall Policy → Rule collection groupsで、そのFQDNだけを取り除く。
現行の他の宛先・規則を保持し、旧バックエンドと既存APIの正常要求を再確認する。

### 外部サービスの準備が不要な追加例

公開クラウドのmetadataを、購読必須の`GET /ops/metadata/v1`として一時登録する。
下流はMicrosoft共通の公開GETであり、追加のサーバー・キー・Entra権限は作らない。
ここでは[Microsoft公式SDKの既定URL](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/ml/azure-ai-ml/azure/ai/ml/constants/_common.py)
に合わせ、`api-version=2019-05-01`を固定する。
[同SDKの取得処理](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/ml/azure-ai-ml/azure/ai/ml/_azure_environments.py)
も資格情報なしのGETを使用する。この例はVMのIMDSや、管理トークンを発行するAPIではない。

既存のAPI定義配列の**末尾へ追加する一要素**は次のとおり。環境が
購読キーヘッダーを変更している場合は、`subscriptionKeyHeader`を既存設定へ合わせる。

```json
{
  "name": "ops-metadata-v1",
  "displayName": "Operations metadata",
  "path": "ops/metadata",
  "apiType": "http",
  "backendService": "external",
  "backendUrl": "https://management.azure.com",
  "backendAudience": "https://management.azure.com",
  "backendAuthentication": { "mode": "none" },
  "productId": "connection-users",
  "released": true,
  "subscriptionKeyHeader": "api-key",
  "keySubscriptions": [],
  "operations": [{
    "name": "read-metadata",
    "displayName": "Read public cloud metadata",
    "method": "GET",
    "urlTemplate": "/v1",
    "backendUrlTemplate": "/metadata/endpoints?api-version=2019-05-01",
    "templateParameters": []
  }],
  "authorization": {
    "mode": "subscriptionKey",
    "operationConstraints": {
      "read-metadata": {
        "kind": "registeredOperation",
        "bodyFormat": "none",
        "maxRequestBytes": 1024
      }
    }
  }
}
```

1. 変更前のparamsと、Gの正常な計画を保護保存する。既定の計画保存先は
   `state/gateway-plan.json`である。API定義を変える前に保存し、後続の計画で上書きしない。
2. `apiDefinitions: []`を使っている場合は、変更前の計画の
   `parameters.configuration.apiDefinitions`を**全件**コピーし、paramsの
   `apiDefinitions`をその明示配列へ変更する。その際、各API直下と、存在する場合は
   `mcp`直下の`catalogDescription`だけを除く。これは配備向けに生成した説明であり、
   入力Schemaは受け付けない。他の契約項目を省かず、WAF用の簡略なAPI一覧も使わない。
   既に明示配列なら現在の全要素を保持する。
3. 末尾へ上記一要素だけを加える。既存のname・path・operations・Product・認証・
   profile制約が変更前と一致し、同名API／backend／Named Valueが未使用であることを確認する。
   共用する`connection-users`と`released-apis`の既存購読を削除・再発行しない。
4. Gの計画と実行を行う。計画の外部FQDNに`management.azure.com`が入ること、
   実Firewallの送信元・HTTPS／443・他の宛先が保たれることを確認する。
   通常のBroker構成ではこのFQDNは既に必要な許可であるため、変更なしでも正常である。
   新規FQDN追加を実測するために既存の管理通信許可を外さない。
5. 実APIで購読必須・GET一操作・固定の下流URL／query・`none`認証を読む。
   生成ポリシーが入口の購読キー・Authorization・L7内部認証ヘッダーを削除し、
   このAPIにMI認証・CM接続・下流キーを追加していないことを確認する。
   固定rewriteは未登録のqueryを転送しない。外部へsecretを送る検証要求は作らない。
6. `released-apis`に属する有効な専用キーで、本文なしのGETを一度実行し、
   200と、`name`・`resourceManager`・`authentication`を持つ公開クラウドmetadataの
   JSON構造を確認する。配列の場合はその要素を確認する。同じURIのキーなし要求は401、
   未登録の操作・パスは拒否され、既存APIの代表要求は継続することを確認する。
   実応答全体を公開資料へ貼らず、必要な状態・構造の判定だけを保護記録へ残す。

回収は追加前のparamsへ戻し、Gの計画・実行で既存全APIの正本を確認する。
Incremental配備は省いた資源を自動削除しないため、Azure Portalの対象APIMで
`ops-metadata-v1`のProduct所属とAPIを回収し、他APIから参照されていない
専用backend `ops-metadata-v1-backend`と、非秘密のNamed Value
`ops-metadata-v1-backend-audience`だけを削除する。既存Product・購読・共通Named Valueは残す。
API Centerへの同期登録がある場合も残存を確認し、今回の専用APIに対応する分だけを回収する。
`management.azure.com`の既存許可は回収しない。最後に専用APIの不存在と、
元のAPI定義・既存購読・代表要求の保持を確認する。

## 監視通知先・条件を更新し、ログを点検する

正本は[監視の関連付け](../../infra/modules/monitoring-bindings.bicep)と
`monitoring`設定である。通知は既存Action groupのメール宛先を使い、
障害を自動回収する新しい処理は追加しない。

1. 共通手順で対象環境を確認し、scope・監視対象・通知先・条件・有効状態・評価間隔・
   Action group参照の旧値を保存する。別環境や共有グループを一括変更しない。
2. paramsの`monitoring.operatorEmailAddresses`を、受信を合意した採用宛先の一覧へ変更する。
   旧宛先は新宛先の確認後に回収する。次の計画・実行は、この環境の既存Action groupだけを
   更新し、他の通知チャネルや設定を保持する。実際のテスト通知は送信しない。

   ```sh
   python3 -B scripts/operations/monitoring.py --config "$DEPLOY_PARAMS_FILE" --action recipients
   python3 -B scripts/operations/monitoring.py --config "$DEPLOY_PARAMS_FILE" --action recipients --execute
   ```

3. 閾値は対象一規則を選び、config正本または対応するBicepの採用値も合わせる。
   例えば取り込み量は参照configの`monitoring.appInsightsIngestionWarningGb`を更新し、
   以下の`90`を今回の採用値に合わせる。これは単一の静的条件の閾値だけをPATCHし、
   scope、KQL、集計、評価間隔、severity、有効状態を保持する。

   ```sh
   python3 -B scripts/operations/monitoring.py --config "$DEPLOY_PARAMS_FILE" \
     --action threshold --rule-kind log --rule appi-ingestion-high --threshold 90
   python3 -B scripts/operations/monitoring.py --config "$DEPLOY_PARAMS_FILE" \
     --action threshold --rule-kind log --rule appi-ingestion-high --threshold 90 --execute
   ```

   対応する既存metric規則は`--rule-kind metric`で選ぶ。未知の規則、複数条件、
   対象scopeやAction groupが違う規則へは適用しない。計画・要求・読戻しの結果は
   環境別`state/operations/`へ保護保存する。ETagが返れば条件付き更新を行い、
   返らないAPIでも直前比較と全設定の読戻しを要求する。後者は外部操作との原子的な
   競合防止ではないため、同時保守を避ける。空・不正ETagを無視して実行しない。
4. 保護記録で実通知設定・閾値・目的外設定の保持を確認する。通知経路を試すときは、
   Azure Portal → Monitor → Alerts → Action groupsを開き、
   受信を合意した宛先へのAction groupのTestで、テスト通知であることと受信を確認する。
   この結果を、実障害の検出や実ログ条件の発火まで確認した証拠にしない。
5. 対象LAWのLogsで、今回の代表要求の時刻と所有資源に限定して件数・最終時刻等を読む。
   条件が使う実テーブルと権限も確認する。要求本文・query・秘密をまとめて取得しない。

例えばAppGWのアクセス到達を点検する場合は、対象資源IDを保護した作業票から
設定し、短い期間の集約だけを使う。

```kusto
AGWAccessLogs
| where TimeGenerated > ago(15m)
| where _ResourceId =~ '<APPLICATION_GATEWAY_RESOURCE_ID>'
| summarize Rows=count(), Latest=max(TimeGenerated)
```

0件・照会失敗・取り込み待ちを区別し、設定の読戻しだけでログ到達を合格にしない。
WAF／API／Portalの代表要求を共用し、ログ生成のために大量の障害要求を送らない。
通知の試験と、監視クエリの評価は別に記録する。

切戻しは正本の通知先・条件を保存した旧値へ戻し、同じscriptで計画・反映する。
他の通知先、Action group、有効状態を消さず、最終値と通知経路を確認する。
監査保持、WORM、export、DCRの変換内容は、この条件変更に含めない。

## AppGW・Container Appの容量を調整する

容量調整は、負荷試験や可用性保証とは分ける。小さい正常要求と設定の反映を
確認し、最大負荷を発生させたり、常時必要なレプリカを0へ落としたりしない。

| 対象 | Azure Portalの入口と変更する値 | 継続採用する正本 |
| --- | --- | --- |
| Application Gateway | 対象資源のConfigurationで、既存autoscaleの最小／最大だけを調整 | `gateway.autoscaleMinCapacity`・`autoscaleMaxCapacity` |
| Broker Container App | config正本を変更し、`operations/release.py --component gateway`で計画・反映 | `credentialBroker.minReplicas`・`maxReplicas` |
| Portal Container App | 同じ画面で最小／最大レプリカを調整 | `apps/portal/deploy/application.bicep`の`template.scale`。専用のconfigキーはない |

1. 現在の値、SKU／workload profile、稼働revision・digest、公開ヘルスと
   切戻し先を記録する。変更後も最小値が最大値を超えないことを確認する。
2. 代表となるBrokerはconfig正本の`credentialBroker.minReplicas`・`maxReplicas`を変更し、`scripts/operations/release.py --component gateway --config "$DEPLOY_PARAMS_FILE"`で計画、`--execute`で反映する。他の対象も1資源の予定した容量だけを変更する。SKU・ゾーン・
   リスナー・WAF・バックエンド・認証・新しいscalerを同時に変更しない。
3. 実設定、処理の終了、ヘルスと代表要求を確認する。Container Appでは
   スケール設定の変更で新revisionが作られるため、稼働revision・digestと
   トラフィックの向き先も確認する。限度値の反映を、実際のscale-out実測と混同しない。
4. 継続採用する値は表の正本へ反映する。PortalのGUI変更だけを残すと次回Pで
   Bicepの値へ戻るため、正本も修正するか、今回限りの変更を旧値へ戻す。
   Brokerの設定反映にGを使う場合は、容量以外の工程も含む計画を確認する。
5. 戻す場合は同じ正本と入口で容量だけを旧値へ戻し、正本・実設定・revision・正常要求を
   再確認する。古い資源JSON全体をPUTして他の保守内容を巻き戻さない。

AppGWのautoscaleとContainer Appのrevision変更は、
[AppGWのスケーリング](https://learn.microsoft.com/en-us/azure/application-gateway/application-gateway-autoscaling-zone-redundant)、
[Container Appsのスケール設定](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)を参照する。

## 作業を共用する

バックエンド変更とその外向き許可は同じGで反映する。WAF修正の正常／拒否、
監視ログ点検、変更対象外の代表要求は、同じ要求を実施票へ対応付けて共用できる。
待機中は読取と記録を進め、同じ環境への更新を並行実行しない。
各業務のために環境を再構築せず、試験用の設定変更は最後にまとめて確認・回収する。
