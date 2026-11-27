# 要求と利用者を監査ログへ記録する

`core-gateway-telemetry`は、呼び出した時点の要求情報・認証主体・APIの利用許可・処理結果を、1件のJSONにまとめてAPIMの`trace`へ渡す部品です。同名XML／C#をこのフォルダーで管理し、AzureのPolicy fragment名も`core-gateway-telemetry`を使います。

ログを出すために認証やモデル呼び出しを実行する部品ではありません。先行処理が確認した要求内の情報と、呼び出し元が指定した処理段階を使います。要求の拒否やバックエンド応答を記録するため、認証成功とAPIの利用許可を別の項目として残します。

## 現正本の分担

| 資材 | 現在の所有範囲 |
| --- | --- |
| [core-gateway-telemetry.xml](core-gateway-telemetry.xml) | `trace`の呼出し・severityと、C#を展開する`message`の位置を管理します |
| [core-gateway-telemetry.cs](core-gateway-telemetry.cs) | `message`の監査JSON生成全体。主体情報の読取り・整合性確認、段階・理由・状態、要求情報、APIの利用許可、接続先・モデル・相関情報を組み合わせます |

MOD-134で現XMLの`message`全体だけを同名C#へ結線しました。PythonとBicepは、本文用に&／<／>だけをescapeし、引用符は原値を保ちます。全messageは単式の所有なのでBEGIN/END区画には分けません。full／compact／omitのactual-before生成物と実行構造・属性・C#tokens・string/char値が一致しました。

共通認証・入口拒否に使う監査は、現在のmessage markerがある場合だけ指定modeでC#を展開してから、従来のuser監査区画を除去します。markerのない固定旧XMLはcurrent C#を読みません。Entra失敗時の主体readerは本文のraw quoted keyを専用主体名へ1回だけ置き換え、通常主体の変更を避けます。XMLの資格情報・本文を読む新処理は追加しません。

初回47件は77.763秒で2失敗・1エラー、他44件成功でした。試験クラス名の誤指定と、生成字下げの差を補正しました。次の89件は105.011秒で1失敗・1エラー、他87件成功で、旧形の二重inline marker fixtureと、C#展開前に行っていたuser監査区画の除去が原因でした。fixtureと上記の生成順を正式方式へ揃え、最終89件は98.971秒で全件成功しました。別の実監査43件は4.457秒、round-trip4件は0.070秒で成功しました。主体・要求・利用許可の既存出力、全protocolの配備／保護source一致・容量・構文・同名所有を確認しました。ローカル整理済みでAzure未反映です。

再開後の追加依存の独立レビューも、現在のsource-flow範囲で所見なしとして閉じました。key-onlyの境界・呼出し元mode・FailureSubjectの固定reader・正式fixtureを確認しています。helperの実装前checkpointは保存していないため、旧helperとのbyte／runtime同等性を独立に証明したとは扱いません。

## 先行処理と呼び出す位置

```text
STEP1 core-request-context             要求情報を保存する
STEP4 core-common-authentication       キー／Bearerを認証する
STEP5 core-audit-authenticated-subject  確認できた利用者情報を保存する
各API・操作                           利用許可や接続先等を確認する
呼び出し元                            段階・理由・HTTP状態を指定する
core-gateway-telemetry                 確認できた情報で1件の監査JSONを作る
APIM trace                            既存の診断設定で収集する
```

拒否の位置によってはSTEP4やSTEP5、各APIの処理まで到達していません。その場合は未確認の情報を補わず、APIMの要求・API・操作IDと、到達した段階で確認できた項目を記録します。`accepted`・`backend-response`等は同じ要求の別時点の観測であり、その数をモデル実行回数として数えません。

## 使う変数と保持条件

| 入力 | 扱い |
| --- | --- |
| `gatewayAuditPhase` | `accepted`・`rejected`・`backend-response`・`policy-error`の4種類を採用し、欠落・他の値は`unknown`にします |
| `gatewayAuditCode` | ASCII大文字で始まる最大64文字の大文字・数字・`_`を採用し、それ以外は`UNSPECIFIED`にします |
| `gatewayAuditStatus` | 呼出元が指定した整数の状態を優先し、未指定なら現在の応答状態、応答がなければ0を使います |
| `gatewayRequestContext` | 4096文字以内のJSONから、受信時刻・HTTPメソッド・操作ひな形・peer IP・申告UA・AppGW相関候補の6項目だけを追加します。要求・API・操作IDは現在のAPIM情報から用意します |
| `gatewayAuthenticatedSubject` | 同名C#が、1600文字以内のJSONと状態・項目・呼出主体の対応を確認し、監査に採用する主体を準備します |
| `gatewayDecision` | API側の利用許可結果を読み、キー利用ならAPIMの購読、Bearer利用なら認証成功印と社員JSON・IDの対応に照合します |
| `gatewayAuditSettings`・`gatewayControlSettings`・`gatewayOperationSelection`・`gatewayAuditModel` | API設定と先行する選択・検査結果から、接続先、モデル、設定された別名とLLMログの照合方法を追加します。ここではモデル本文を読みません |
| `gatewayBackendResponseIds`・`gatewayAgentTrace` | 専用処理が用意した下流応答IDや親要求との相関候補を引き継ぎます。親要求の`candidate`を確定した関係として扱いません |
| `gatewayConnectionExpiresOn` | `long`型で2000年1月1日～2100年1月1日の範囲にあるUnix秒だけ、接続先資格情報の絶対期限として記録します。未提供なら省略します |

要求情報の追加項目は512文字以内の文字列を採用し、条件外は`null`にします。受信時刻がJSON解析で日時型になった場合は`"O"`形式へ戻します。User-Agentが日時型として解釈された場合も元の文字列を読み直し、512文字以内の文字列として扱います。読取りが成立しない場合は、先に作った要求・API・操作IDを残します。

同名C#は主体を`unverified`から始め、変数があるのに型・形式・対応が成立しなければ`inconsistent`にします。`verified`を採用する場合は、社員／購読と認証方式の組合せ、項目名と文字列型、長さ、`callerId`の対応を確認します。社員では`allocationId`も同じ主体か確認します。購読IDは256文字、購読の`callerId`は269文字、その他の文字列は253文字以内という既存条件を維持します。キーの値やトークンを読み直して不足を補いません。

## 出力するJSON

`trace`の`source`は`core-gateway`、`severity`は`information`です。`message`は`Formatting.None`のJSON文字列を返します。

| 出力 | 意味 |
| --- | --- |
| `schemaVersion: 1`・`auditSchemaVersion: 2`・`event: "gateway-request"` | 既存イベント形式と追加した監査識別項目の版を分けます |
| `requestId`・`apiId`・`operationId`・`requestContext` | どの要求・API・操作の記録かと、取得できた要求情報です |
| `phase`・`code`・`status` | 呼出元が示した処理段階、理由、HTTP状態です |
| `authenticatedSubject` | `verified`・`unverified`・`inconsistent`を持つ認証主体です。確認できた場合だけ、社員／購読の識別項目を含めます |
| `authorization` | API側の利用許可を`allowed`・`denied`・`unconfirmed`で記録します。利用許可結果がなくても、確認済み認証主体を未認証へ置き換えません |
| `callerId`・`method`・`subscriptionId`、または社員の利用枠・検証済みID等 | 許可結果とAPIMの購読または社員情報の対応を確認できた場合に、利用の帰属先を追加します |
| 接続先・モデル・別名・親要求・下流応答ID・接続期限等 | 先行処理の情報がある場合に追加する補助項目です。設定上のモデル別名は、許可済み選択と実モデルへの対応が一致する場合だけ記録します |
| `backendRequestId`・`usageStatus`・`usageSource` | APIMの要求IDと使用量ログの照合方法です。既定は使用量情報なし、対象LLM操作では`native-log-join-required`と`ApiManagementGatewayLlmLog`を示します |

`backendRequestId`はAPIMの要求IDです。提供元が返した応答IDは別の`backendResponseIds`で扱います。このJSON自体は消費トークン数を算出せず、HTTP成功・SSE接続・親要求候補から、業務成功や使用量を推測しません。`profileId`等の既存補助項目も、監査側が確認できた条件で保持します。

## 読み取らない情報とログの収集

この部品は要求・応答の本文、APIキー、Bearerトークン、Cookie、実要求のURL・クエリ、任意の要求ヘッダーを読み取りません。保存済みの要求内JSONとAPIMの情報を扱い、新しい外部通信、本文バッファリング、認証・認可、要求本文の書換えを追加しません。生JSONや例外の詳細を監査項目へ転記しません。

APIMが取得した実要求の識別項目を監査へ出す処理と、公開ソースへ実環境の値を書き込むことは区別します。この正本・説明書へ環境のID・IP・資格情報を埋め込みません。収集先、ストレージ保存、保持期間、閲覧権限は既存の診断・監査基盤で設定し、この部品が保存先を作るものではありません。

## 生成・説明・確認の現況

[Pythonの生成処理](../../../../../../scripts/deployment/helpers/apim_audit.py)と[Bicepの公開処理](../../../../api-publication.bicep)は、同名XMLの主体投影マーカーへ同名C#を展開します。正本では処理の説明を保持し、他の部品へ生成時に組み込む監査は、容量を抑えるため説明をこの正本への案内にまとめます。C#の`//`説明は安全な`/* ... */`へ変換し、XMLの式としてAPIM内で実行します。新しい監査サービスを呼び出す構成ではありません。

一部の認証失敗監査では、生成時に主体の読取り先を`gatewayEntraFailureSubject`へ差し替えます。認証後の会社設定・内部処理で停止しても、先行処理が確認できた本人を記録するためです。監査のために認証成功印を成功へ変える処理ではありません。

[識別情報の出力契約](../../../../../../docs/reference/audit-identification.md)、[部品の生成・展開試験資材](../../../../../../tests/test_audit_fragments.py)、[識別情報の合成試験資材](../../../../../../tests/test_audit_identification.py)を参照してください。このREADMEの追加では過去の試験を再実行せず、Azureの反映・ログ受入も実行していません。既存の限定受入と全体の残件は[管理構成](../../../README.md)に従います。全`message`のC#化は統合待ちであり、文書追加を実装・実環境検証の完了として扱いません。
