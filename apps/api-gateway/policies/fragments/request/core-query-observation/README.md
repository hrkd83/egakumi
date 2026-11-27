# 操作・MCP受付のURLクエリを観測する

AzureのフラグメントIDは`core-query-observation`です。XMLが観測式を呼び、同名C#が`gatewayQueryObservation`を作ります。生成時にC#と日本語説明をXMLへ組み込み、Azureポータルでも処理を読めるようにします。

```text
API定義のqueryObservation
    ↓ Bicepが操作ID・MCPの公開メソッドとpathへ対応付け
gatewayControlSettingsのqueryObservations／mcpQueryObservations
    ↓ 同名C#が観測ラベルとクエリ概要を選ぶ
gatewayQueryObservation
    ↓ core-query-telemetryが要求情報・認証済み利用者と合わせて記録
後続のAPI処理へ続行
```

この部品は要求の許可・拒否を判定せず、HTTP応答やクエリを変更しません。観測用のmode設定、名前付きの値、`allowed`・`status`は使いません。本文やクエリの名前・値は監査データにしません。

## 選択する入口

| API定義の指定 | 生成した設定での選択 |
| --- | --- |
| HTTPの`operations[].queryObservation` | `queryObservations[context.Operation.Id]`から、その操作の観測ラベルを選ぶ |
| 生成MCPの`mcp.queryObservation` | `mcpQueryObservations`の`GET /mcp`・`POST /mcp`・`DELETE /mcp`へ同じラベルを生成する |
| 外部MCPの`mcpProxy.endpoints[].queryObservation` | 公開方法と公開相対pathから`METHOD + 空白 + path`を生成し、同じ辞書から選ぶ |

外部MCPはstreamableのGET／POST／DELETE `/mcp`、SSE受信のGET `/sse`、送信のPOSTと公開message pathを区別します。下流の`uriTemplate`と公開pathは別です。MCPではAPIMが選んだ`Api.Path`を元のPathから境界付きで除き、相対pathを求めます。`/mcp/`だけは従来どおり`/mcp`と同じ受付として扱い、他のpathの表記を正規化しません。

ラベルはASCIIの英字から始まる64文字以内で、以降は英数字・`_`・`-`を使います。`none`・`unknown`は完全一致で予約します。未指定・不正な参照やAPIを特定できない場合は、クエリを観測せず初期値を返します。同じラベルを複数APIで共有でき、サービス名・モデル形式・入力制約kindから対象を推測しません。

標準の対象はLiveの作成操作`liveCreate`、Realtimeの作成操作`realtimeCreate`、PIIのMCP受付`piiMcp`です。PII RESTやRealtimeの終了操作へ観測を追加しません。この3ラベルは全許可一覧ではありません。

## 作るデータと実行順

観測JSONは`route`・`httpMethod`・`presence`・`duplicates`の4項目です。ログ互換のため、`route`は既存の名前のまま、選択した観測ラベルを表します。APIMの解析済み項目からクエリの有無と、同じ引数に複数値があるかを取得します。末尾の`?`だけで解析済み項目が0件なら`presence: none`です。取得に失敗した場合は`unknown`を残し、要求を拒否しません。

HTTPは`<base />`直後の`core-api-request-preparation`に続き、操作・モデル設定の選択と本文読取りより前に観測します。生成MCPは要求準備・API認可後、ツール引数の本文検査前に観測します。外部MCPは[query-observation-inbound](../../../snippets/audit/query-observation-inbound/README.md)を`mcp/external.xml`で指定した受付区画へ差し込み、要求準備の後に実行します。認証完了は要求準備が確認し、この部品では再確認しません。

`core-query-telemetry`は観測結果を共通の要求情報・認証済み利用者に関連付けます。イベント名`query-guard`、`schemaVersion: 2`、`auditSchemaVersion: 2`は維持し、modeと件数区分は出力しません。新規の処理段階は`received`・`backend-response`・`policy-error`です。旧schema 1の記録の読取り互換はKQLで扱います。

## 棚卸と移行の状態

⑥で承認した有無・複数値の取得は維持し、件数区分`countBucket`だけを削除しました。⑦で承認した観測専用化に沿い、mode設定とクエリ拒否・設定不正応答の枝をローカルソースから撤去しました。⑤で検討した設定不正の500応答も、その設定自体の撤去に伴いローカルソースから削除済みです。

ローカル改修と合成データによる検証、最新の承認済みソースによる新しい部品・API参照のAzure反映・読戻しは完了しました。APIM共通処理の限定受入は[管理構成](../../../README.md)に記載し、query観測を含む全入口の受入は未完了です。旧`core-query-guard`と名前付きの値`core-query-guard-modes`は参照消滅を確認して退役し、不在の読戻しも完了しました。保存した正本checkpointは現行生成元へ更新し、旧資材を含まない状態に整理済みです。旧資材の所有・現在値の一般照合は維持します。

後続の入口検査は`gatewayApiInputValidationPassed`でContent-Type宣言の存在・1値・非空MIME部分を判定します。不備時の`INVALID_CONTENT_TYPE_HEADER`監査と理由`Invalid Content-Type header`の本文なし400を維持し、短い拒否フローは共有HTTP APIテンプレートに残す方針を承認しました。APIM要求IDと`Cache-Control: no-store`も維持します。入口検査と不備時の400監査はローカル整備済みで、承認済みソースのAzure反映・読戻しは完了しています。空本文でContent-Type宣言がない要求の400と対応する拒否監査は実接続で確認済みです。その他のContent-Type不備は未検証であり、入口検査の受入全体は未完了です。未指定操作のtrueは検査省略で、対象操作のtrueも完全な構文や要求全体の妥当性は保証しません。操作・モデル設定の選択は今回棚卸・移設済みです。後段の本文／モデル制約・URI転送・WAFは未棚卸のまま保持します。観測専用にしただけで、全クエリが下流へ転送されることや、モデル・ツールの互換性が整ったとは扱いません。以後の推奨は現状維持を前提にせず、必要性・効果・負荷・複雑さを比較して判断します。
