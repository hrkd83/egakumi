# クエリ観測の経過を監査へ記録する

AzureのフラグメントIDは`core-query-telemetry`です。XMLが記録対象の選択と`trace`への出力を示し、同名C#がその2つの式を管理します。

```text
API設定から選んだラベル → core-query-observationの観測JSON
                                ↓
predicate：routeのID書式を確認 → 対象がなければ記録しない
                                ↓
message：要求情報・認証済み利用者・クエリ概要・処理段階をJSONにする
                                ↓
trace event=query-guard
```

C#の`BEGIN predicate`から`END predicate`は、XMLの`when`条件へ組み込む式です。`BEGIN message`から`END message`は、`trace/message`へ組み込む式です。ファイル全体を一つのメソッドとして実行する構成ではありません。生成時に日本語の説明も閉じたコメントへ変換してAzureに残します。

`route`は公開URLではなく、APIの`queryObservation`から選んだ観測ラベルです。文字列型、1〜64文字、ASCII英字で始まり英数字・`_`・`-`だけを使う書式を確認します。小文字の`none`（対象なし）と`unknown`（未確認）は完全一致の予約値です。設定したラベルだけを記録し、固定の3ラベルに限定しません。観測専用化により、modeや名前付きの値の読込みはありません。

HTTPメソッドは、1〜20文字のASCII大文字を記録できます。従来のPOST／GET／DELETEだけに限定しません。ルールIDとメソッドは、記録対象の判定と実際の出力それぞれで必要な型・書式を確認します。

`gatewayQueryObservation`のJSONは`route`・`httpMethod`・`presence`・`duplicates`だけです。監査側はそれらに`phase`・`statusClass`、要求ID・API／操作ID、共通の要求情報、認証済み利用者情報を合わせます。イベント名`query-guard`、`schemaVersion: 2`、共通監査の`auditSchemaVersion: 2`を維持し、modeは新規出力しません。本文、URL、クエリの名前や値、認証キー・トークン・Cookieを読み取って記録する処理はありません。他の監査イベントの`schemaVersion: 1`は維持し、KQLでは旧`query-guard`イベントのschemaVersion 1も読める互換性を維持する方針です。

認証済み利用者の読取りは、既存の共通式`expressions/legacy/audit-subject-observation.cs`を生成時に組み込み、説明も保持します。この部品がキーやトークンの認証を再実行する意味ではありません。

処理段階は`received`・`backend-response`・`policy-error`を記録します。棚卸⑦でこの部品による拒否を撤去するため、`query-rejected`・`guard-error`は新規出力しません。過去のログにある旧phaseとmodeは移行前の観測として区別します。応答区分と読取失敗時の`unknown`は維持し、観測できない値や未着の記録を正常成功に変換しません。後続の別検査で早期終了した要求には、終端記録がない場合があります。SSEやMCPのHTTP 200だけで、モデルやツールの完了を証明したとは扱いません。

棚卸⑦では、汎用ラベルとメソッドの選択・形式確認を保持したまま、queryの有無と複数値だけを観測します。この部品は許可／拒否の結果を出さず、APIの認証や下流転送を変更しません。⑤までのmode設定不備で停止する方式は移行前の設計として廃止し、先行する要求準備の認証完了確認と503は維持します。

クエリ検査⑥の維持方針のうち、有無・複数値の取得は保持します。件数区分`countBucket`だけは不要と判断し、その計算とログ出力を削除する方針を承認済みです。この出力項目の変更を区別するため、`query-guard`イベントだけschemaVersionを2へ進めます。

query観測のローカル改修・検証と、承認済みソースのAzure反映・読戻しは完了しました。APIM共通処理の限定受入では、[管理構成](../../../README.md)に記載した要求に対応する認証済み利用者情報・応答・拒否理由とAppGW監査の相関を確認しました。query観測を含む全入口の受入は未完了です。旧fragment・旧Named Valueは参照消滅を確認してAzureから退役し、不在の読戻しも完了しました。保存した正本checkpointは現行生成元へ更新し、旧資材を含まない状態に整理済みです。後続の入口検査と不備時の400監査もローカル整備済みで、Azure反映・読戻しは完了しています。`gatewayApiInputValidationPassed`がfalseの場合の`INVALID_CONTENT_TYPE_HEADER`監査と、理由`Invalid Content-Type header`の本文なし400は共有HTTP APIテンプレートに残し、APIM要求IDと`Cache-Control: no-store`を維持します。この拒否監査は`core-gateway-telemetry`が担当し、query観測の拒否機能を再導入する意味ではありません。trueは宣言条件の通過または対象外による検査省略で、完全な構文や要求全体の妥当性は保証しません。操作・モデル設定の選択は今回棚卸・移設済みです。後段の本文／モデル制約・URI変換・WAFは未棚卸のまま保持し、この観測専用化だけでqueryの通過やサービス互換性が整ったとは扱いません。以後の推奨は、各処理の必要性・効果・負荷・複雑性を確認して提示します。ソースの実装、ローカル検証、環境への適用・接続受入は分けて確認します。
