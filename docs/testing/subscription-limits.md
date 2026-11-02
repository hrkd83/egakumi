# 購読単位の利用量観測と保護上限の検討

購読キーの保護上限を決める前に、[subscription-usage.kql](../../scripts/subscription-usage.kql)で平常時とピーク時の要求量を確認します。このクエリは読み取り専用です。APIMの設定変更・キー取得・課金計算は行いません。

## 計測の前提

- 対象APIMの診断ログ `GatewayLogs` が、Log Analyticsのリソース固有テーブル `ApiManagementGatewayLogs` に送られていることを確認します。`AzureDiagnostics` 用のクエリではありません。
- 診断ログが無効・間引き・欠落していた時間帯を、無通信だったとは判定できません。取り込み状況も別途確認してください。
- ログの `ApimSubscriptionId` は **APIMの購読ID** です。`_SubscriptionId` はAzureサブスクリプションの識別子なので、キー別の集計には使いません。
- 無効なキーなどで購読を確定できなかった要求は、`[unattributed]` として別枠にします。特定のグループや購読へ推測で割り当てません。

列名はMicrosoft公式の [GatewayLogsテーブル定義](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apimanagementgatewaylogs)と[GatewayLlmLogテーブル定義](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apimanagementgatewayllmlog)に合わせています。

## 実行する

1. 対象Log Analyticsワークスペースの「ログ」を開きます。
2. `scripts/subscription-usage.kql` の全体を貼り付け、冒頭の `apimResourceId` を対象APIMのリソースIDへ置き換えます。秘密のキー値は入力しません。
3. 必要なら `windowStart` を変更します。既定は1日間で、取り込み待ちとして直近5分を除外しています。画面側の時間範囲も観測期間を含めてください。
4. 実行し、`ApimSubscriptionId`とPortalの購読台帳にある`apimSubscriptionId`を照合します。Portal内部の`portal-UUID`とは区別し、所有グループや用途名は対応する台帳で確認します。

大量の要求がある環境では、最初は `windowStart = windowEnd - 1h` に狭めてください。詳細ログとtraceを読み込むため、長期間を一度に指定するとクエリのメモリ・件数上限へ達する場合があります。期間を分けたP95を平均して、全期間のP95とはしないでください。

時刻はUTCです。結果の `Minute` は要求のログ時刻を1分ごとに丸めた値です。長時間のStreamやWebSocketでは、接続中の要求がまだログに現れていない場合があります。後から同じ時間範囲を再取得すると件数が増えることがあります。

## 数値の読み方

| 列 | 意味 |
| --- | --- |
| `Requests` | その購読・その1分の要求数。`CorrelationId` ごとに1件 |
| `RequestsPerSecond` | 1分の要求数÷60。瞬間的な最大RPSではない |
| `Responses429` | クライアントに返った429の件数。APIMとバックエンドのどちらが原因かは、この件数だけでは区別できない |
| `Responses4xx` / `Responses5xx` | クライアントに返った状態コード別の件数。4xxには429も含むため足し合わせない |
| `UnknownResults` | 状態コードの欠落・矛盾・範囲外がある要求数。成功へ振り分けない |
| `P50TotalTime` / `P95TotalTime` / `MaxTotalTime` | 有効なネイティブ `TotalTime` の中央値・95パーセンタイル・最大。生の列値を保持し、このクエリでは単位を変換しない |
| `TotalTimeSamples` | 上記の処理時間統計に使えた要求数 |
| `PeriodRequests` | 指定期間の購読別総要求数。各minute行に同じ値を表示するため、結果表の行同士で合計しない |
| `P95RequestsPerMinute` / `MaxRequestsPerMinute` | 指定期間の1分単位の要求数のP95・最大。無通信の分も0件として含む |
| `P95MinuteAverageRps` / `MaxMinuteAverageRps` | 上記を60で割った値。瞬間バーストの大きさを保証するものではない |
| `ObservedMinutes` | 期間統計の分母となった分の数。既定の1日なら1440 |
| `DuplicateRows` | 同じ要求IDのため要求数へ二重加算しなかったログ行数 |
| `ConflictingResults` | 同じ要求IDで状態コード・処理時間が食い違った要求数 |
| `RowsWithoutCorrelationId` | 相関ID欠落・不正のため重複排除できず、要求数から除外した元ログ行数。全APIM期間共通の品質指標で、各行を足し合わせない |

通信があった分だけ行を表示します。ただし期間のレート統計は、指定期間内の0件の分も含みます。期間中に1件も記録されていない購読は行自体がありません。これをログ欠落と区別せず「未使用」と断定しないでください。

## トークンの扱い

既存の監査契約で `usageSource=ApiManagementGatewayLlmLog` と宣言された実行だけを、同一APIM・同一 `CorrelationId`・同一 `DeploymentName` のネイティブLLMログと結合します。現在の対応対象を広げたり、モデルの応答本文を解析して補完したりしません。

- `InputTokens`・`OutputTokens`・`TotalTokens` は、厳密に照合できた要求の分だけを合計します。
- 数値の欠落、文字列の数字、負数、合計不一致、複数の異なる使用量レコードは採用しません。
- 計測できた要求が0件なら、トークン欄は **null（未計測）** です。0トークンや0円ではありません。
- `MeasuredTokenRequests` は計測できた要求数、`UnmeasuredTokenRequests` はそれ以外の要求数です。後者には未対応API、拒否された要求、ログ欠落、音声・画像・リアルタイムも含まれます。
- `LlmEvidenceRequests` は現行監査契約によりLLM照合対象と分かった要求数です。自前のtraceが欠けた要求は、LLMであること自体を推測しません。
- `AmbiguousTokenRequests` は照合情報が矛盾した要求数です。曖昧な使用量を採用しません。
- LLMテーブルがまだない環境では `isfuzzy` の警告とともに要求集計だけを返します。その場合もトークン計測済みとは扱いません。

`TraceRecords` 内に `accepted` と `backend-response` があっても、HTTP要求は2件にしません。要求数の土台はネイティブGatewayLogsを相関IDでまとめたものです。LLM情報を結合する側も1要求・1モデルの1行へまとめてから結合します。

この数値は課金台帳ではありません。トークン単価・モデル・キャッシュ・音声・画像等をまとめて金額へ換算しません。個別の照合は[既存の監査クエリ](../../scripts/audit-usage.kql)を利用してください。

## 上限を検討する順番

1. 所有グループと用途ごとに、購読を共有する人数・バッチ・外部アプリ・再試行・ピーク時間帯を確認します。全社人数を1購読のRPSに置き換えません。
2. 平常日とピーク日について、購読別の最大1分要求数、P95、429、5xx、処理時間を確認します。
3. APIMのCapacity、バックエンドのTPM/RPM、長時間接続の数も合わせて確認します。1分平均RPSだけから瞬間バーストや同時接続数は分かりません。
4. 保護上限を設定するときは観測したピークへ余裕を加え、正規利用が429にならないか再確認します。ログの取り込み遅延があるため、直近の結果だけで解除・増加を自動判断しません。

購読別の制限は、有効な購読を使うアプリの暴走を抑える仕組みです。購読がない攻撃、複数の送信元からの攻撃、ネットワーク帯域への攻撃を含むDDoS全体への対策とは分けて考えます。

## 変更後の確認

- 同じ購読の主キー・副キーの要求が同じ `ApimSubscriptionId` に集計されること。
- 設定なしの購読は無制限のまま、設定した購読だけに上限が適用されること。
- 429になった要求も1件として記録され、5xxや処理時間の悪化がないか確認できること。
- 少数の要求で意図した境界を確認し、大量の負荷試験は別途合意した範囲で行うこと。

この資料とクエリには、実際の購読キーや要求本文を貼り付けないでください。
