# アクセスログのquery非保持

Application Gatewayのアクセスログでは、URLのqueryを含み得る`RequestQuery`・`OriginalRequestUriWithArgs`・`RequestUri`を対象に非保持を設定します。WAFの`logScrubbing`はWAFログを対象にするため、アクセスログには別の取り込み変換が必要です。本書は配備資材の保存経路と既存環境の更新手順を説明します。実環境への反映状態・実測結果は[動作テストの判定](../../INTEGRATION_TESTS.md)と、その試行の証跡で確認してください。

## Coreの保存経路

`infra/log-privacy.json`をBicepと既存環境更新スクリプトで共用します。`AGWAccessLogs`に対する変換は次のとおりです。path、status、TransactionId、backend、時刻等の調査項目は保持します。

```kusto
source
| extend RequestQuery = '',
         OriginalRequestUriWithArgs = tostring(split(OriginalRequestUriWithArgs, '?')[0]),
         RequestUri = tostring(split(RequestUri, '?')[0])
```

```text
AppGW access → LAW WorkspaceTransforms DCR → AGWAccessLogs → LAW export → am-agwaccesslogs
AppGW WAF    → 既存WAF logScrubbing → LAW、および監査Storageへの直接診断
```

CoreのアクセスログはStorageへ直接送る設定から外し、変換後のLAW exportへ変更します。既存の6テーブル（AppRequests／AppDependencies／AppExceptions／AppTraces／AppEvents／Syslog）は同じexport設定に残します。新しい`am-agwaccesslogs`にも環境で選択した保持保護を適用し、既存のLocked保護を短縮・解除しません。以前の直接診断用コンテナーは既存記録を保持します。

通常の構築ではmonitoring段階でDCRと監査保存先を作成し、hookがWorkspaceへの関連付けを確認します。続くcore段階へ実DCR IDを引き継ぎ、Workspaceの再PUTで関連付けを消さないようにします。他者のWorkspace変換DCRが既に関連付いている場合、Coreがそれを置換せず、管理者による変換の統合を要求します。

## 既存環境の段階更新

既定のコマンドはGETだけで計画を作ります。値を含む詳細な変更前後は、指定したGit管理外ファイルへ保護保存できます。

```sh
python3 scripts/configure_log_privacy.py --config 'params/<environment>.local.json' \
  --plan-file .state/log-privacy-plan.json
```

更新は以下の3段階です。これは承認済みの対象へだけ実行してください。

1. `--execute --stage prepare`：CoreのWorkspaceTransforms DCR・関連付け・監査コンテナーと保持を準備する。
2. 新しい合成要求を送り、pathが残りqueryが消えたログを確認する。その後`--execute --stage export`で既存6テーブルへAGWAccessLogsを追加する。
3. `am-agwaccesslogs`に変換後の合成記録が到着したことを確認してから`--execute --stage route`を実行する。Access→LAWだけの専用診断設定`diag-<環境>-agw-access`を作成・確認してから、既存`diag-<環境>-agw`のAccessカテゴリだけを除去する。既存設定のWAFカテゴリ・LAW・Storage・その他の設定は保持する。同じWAFカテゴリを同じStorageへ重複登録する設定は作らない。

スクリプトのreadback成功は構成の確認です。取り込み変換の有効化や実Blob到達の成功とは分けます。Microsoftは取り込み変換の反映に最大60分、exportルールの開始にも待ち時間があると説明しています。時刻の新しい合成要求で、変換済み行とStorage到達を確認してください。過去に保存された記録はこの変更では書き換え・削除しません。

切替時はAccess→LAWが短時間だけ2設定から送られる可能性があります。証跡は`TransactionId`で照合し、重複行を要求数として数えません。新規設定が拒否・未確定の場合は既存診断設定を変更せず停止します。Microsoftの診断設定資料には1設定につき宛先種類1つ・1リソース最大5設定という制約があります。WAFの同一Storage重複が許可されることを前提にしない切替です。

## 企業共通監査の別Workspace

Azure Policyの`setByPolicy-LogAnalytics`等が同じAppGWから企業共通監査へ送る場合、それは共通管理チームが意図して運用する別の信頼境界です。CoreのWorkspace変換はその送信先には適用されません。診断設定から各宛先へ独立して送信されるためです。CoreはPolicy所有設定や別Subscriptionを変更せず、共通監査からの除外・削除も要求しません。Coreの合格を全宛先での機密値非保持とは表現しません。実キー等をクライアントがURLへ付けないことは、これとは別に検査します。

共通監査の所有者が別途query非保持を採用する場合の参考KQLを記載します。Coreの配備・更新スクリプトの実行範囲には含めません。Dedicatedの`AGWAccessLogs`なら上記3列の変換、`AzureDiagnostics`なら所有者が実schemaの列名を照合し、AppGWアクセスカテゴリだけへ例えば次を適用できます（実在する列だけを指定）。既存の別テーブルの変換は保持します。

```kusto
source
| extend _stripQuery = ResourceProvider =~ 'MICROSOFT.NETWORK'
                       and Category == 'ApplicationGatewayAccessLog'
| extend requestQuery_s = iff(_stripQuery, '', requestQuery_s),
         originalRequestUriWithArgs_s = iff(_stripQuery,
             tostring(split(originalRequestUriWithArgs_s, '?')[0]), originalRequestUriWithArgs_s),
         requestUri_s = iff(_stripQuery, tostring(split(requestUri_s, '?')[0]), requestUri_s)
| project-away _stripQuery
```

企業共通監査はCore運用ログの変更・検査範囲外として記録します。設定を読めない場合に、収集されていない・queryが残らないと推測しません。

## 検証記録

`RequestQuery`と`OriginalRequestUriWithArgs`に合成要求の識別値が残らないことを、変換・export・Storageの各経路で確認します。ヘッダー・Cookie・本文も個別に試験します。受入条件は[動作テスト仕様書](../../INTEGRATION_TESTS.md)を参照し、実環境の結果・残件はリポジトリ外の非公開運用用ワークスペースで管理します。

## 一次資料

- [AGWAccessLogsのschemaとWorkspace変換対応](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/agwaccesslogs)
- [診断ログに対するWorkspace transformation DCR](https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-transformations)
- [Workspace transformation DCRの作成と反映待ち](https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-transformations-create)
- [変換後データのLAW exportと非対応テーブル](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/logs-data-export)
- [WAFログに対するSensitive Data Protection](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/waf-sensitive-data-protection)
- [診断設定の宛先・設定数の制約](https://learn.microsoft.com/en-us/azure/azure-monitor/platform/diagnostic-settings)
