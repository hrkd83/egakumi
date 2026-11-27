# APIMのAPI定義・ポリシーを変更する

[共通確認](common.md) · [API定義](../../apps/api-gateway/apis/openai-inference-v1.json)

構築済みの隔離環境で、既存Chat APIの本文上限を変更し、正常要求・上限超過の拒否・元への復旧を確認する。新APIやバックエンドの追加、初期構築のやり直しは不要。以下は操作例であり、記載だけで試験済みにはしない。

| 変更内容 | 正本 |
| --- | --- |
| API・operation、許可モデル、入力制約 | `apps/api-gateway/apis/<API名>.json`。制約から操作ごとのポリシーも生成される |

API別制約はJSON定義を変更してGで反映する。XMLは定義から生成するため、この手順でXMLを手編集する必要はない。共通認証・監査等の処理自体を変更する場合は、[開発者向けの共通ポリシー改修](../../DEVELOPMENT.md#apim共通ポリシーを改修する)として扱う。以下はAPI別制約の変更例である。

## 操作・MCP受付のURLクエリ観測

棚卸⑦で、URLクエリの部品を観測専用にする方針を承認した。正本は[core-query-observationの専用フォルダ](../../apps/api-gateway/policies/fragments/request/core-query-observation/README.md)にあり、同名XMLが式の実行、同名C#が対象選択とクエリ概要の組立てを担当する。C#の日本語説明も生成時にXMLへ埋め込み、反映後はAPIM → Policy fragments → `core-query-observation`で確認する。ローカル改修・検証とAzureへのまとめ反映・読戻しは完了した。[限定受入](../../apps/api-gateway/policies/README.md)ではResponses JSON・SSE、認証・入力拒否、TTS WAV・DTD拒否と対応する監査照合を確認した。旧query fragment・Named Valueは参照不在を確認して退役し、読戻しで不在を確認した。現行生成元・構築入力・正本checkpoint・通常配備の期待値にも旧資材を含まない。query観測・転送を含む残る受入は未完である。

| 標準の観測ラベル | API定義が明示する対象 |
| --- | --- |
| `liveCreate` | `POST /models/openai-live/v1/sessions` |
| `realtimeCreate` | `POST /models/openai-realtime/v1/calls` |
| `piiMcp` | `/tools/pii-v1-mcp/mcp`のPOST・GET/SSE・DELETE。末尾の`/`も対象 |

HTTPは`operations[].queryObservation`を操作IDから選ぶ。生成MCPは`mcp.queryObservation`、外部MCPは`mcpProxy.endpoints[].queryObservation`を、HTTPメソッドと公開相対pathの辞書へ生成する。外部MCPのstreamableはGET／POST／DELETE `/mcp`、SSE受信はGET `/sse`、送信はPOSTと公開message pathで区別する。公開pathはAPI登録と同じ関数で決め、下流の`uriTemplate`と混同しない。`/mcp/`だけは従来どおり同じ受付として扱う。

Bicepが作る対応表は、`gatewayControlSettings`の`queryObservations`（操作ID）と`mcpQueryObservations`（メソッドと公開相対path）である。サービス・モデル形式・入力制約kindから対象を推測しない。ラベルはASCII英字で始まる1～64文字の英数字・`_`・`-`とし、小文字の`none`／`unknown`を完全一致で予約する。同じラベルを複数APIで共有でき、未指定の入口は観測対象外である。

HTTP操作定義の合成例は次のとおり。既存APIの`operations`に記載する観測ラベルの例であり、認証・下流接続を含む完全なAPI定義ではない。

```json
{
  "name": "invoke",
  "displayName": "Invoke example tool",
  "method": "POST",
  "urlTemplate": "/invoke",
  "queryObservation": "exampleInvoke"
}
```

観測結果は要求ごとの変数`gatewayQueryObservation`へ保存する。次の4項目だけを持ち、クエリの名前・値・URL・本文・キーやトークン等の資格情報は含めない。

| 項目 | 意味 |
| --- | --- |
| `route` | 選択した観測ラベル。入口URLではない |
| `httpMethod` | 1～20文字のASCII大文字のHTTPメソッド |
| `presence` | 解析済みクエリ引数の有無。`none`／`present`、取得できなければ`unknown` |
| `duplicates` | 同名引数の複数値の有無。`yes`／`no`、取得できなければ`unknown` |

共通認証の完了後に観測する。HTTPは操作・モデル設定の選択や本文読取りより前、生成MCPは認可後の本文引数検査より前、外部MCPは要求準備の直後に実行する。認証完了の確認は先行する`core-api-request-preparation`が担当し、ここで重複確認しない。WebSocketにはqueryフローを追加せず、本文なしのGET/SSE・DELETEにもMIME条件を追加しない。

観測部品はmode・許可判定・応答statusを作らず、その結果を理由とする400／500も返さない。取得に失敗した情報は`unknown`として扱い、正常な「queryなし」に置き換えない。後続の[入力検査](../../apps/api-gateway/policies/fragments/request/core-api-input-validation/README.md)ではquery制限・メソッド再確認・入口MIME一覧比較の撤去を承認し、対象操作のContent-Type宣言が1値・非空MIME部分であることだけを判定する。メソッドとURLはAPIMの操作登録で照合する。URL資格情報拒否を維持し、後段のMIME制御・Speech転送、URI変換、モデル本文検査、WAFは別棚卸とする。未知メソッドの実APIM照合と下流非到達は受入で確認し、ローカルC#検証だけでは成功としない。入口整理だけで任意形式の下流受付、query転送、各モデル・ツールの互換性を確認したとは扱わない。

監査は独立した`query-guard`イベントへ記録する。⑥で承認した有無・複数値の取得を維持し、件数区分`countBucket`の計算・出力を削除する。`schemaVersion: 2`と`auditSchemaVersion: 2`は維持する。要求ID・API／操作ID・共通要求context・認証済み主体に、上記4項目と`phase`・`statusClass`を合わせる。新規出力は`received`・`backend-response`・`policy-error`の3段階で、mode・`query-rejected`・`guard-error`は出力しない。移行前のログは旧phase・modeを持つ記録として区別し、旧query/schema 1のKQL読取り互換を維持する方針である。

`received`を分母として要求単位で集計する。後続の別検査で早期終了した要求やログ未着では、終端記録が欠け得る。未着・`unknown`を「queryなし」や正常成功と数えない。SSEのHTTP 200やMCPの2xxも機能完了の証明にはしない。

新traceの固定schemaだけで、既存診断のURL非保持まで保証したとは扱わない。APIMの標準診断は既知query名のマスクのみで、Log AnalyticsとStorageへの配送もあるため、任意query名・値の非保持は保証しない。既存診断設定は保持し、試験のquery名・値は独立した合成入力だけを使う。キー・Cookie等の機密値はqueryへ入れない。この改修でWAF除外を拡大しない。

### 旧構成からの移行と受入

旧`gateway.queryGuards`、APIの`queryGuard`参照、`core-query-guard`fragment、`core-query-guard-modes` Named Valueは廃止対象であり、新規設定に使わない。拒否modeを変更する旧GUI手順は現行手順として使用しない。ローカル入力・API定義は新契約へ移行済みである。新fragmentと参照先APIのAzure反映・読戻し、および旧参照の不在確認後、旧fragment・旧Named Valueを退役し、不在の読戻しを完了した。現行生成元・構築入力・正本checkpoint・通常配備の期待値に旧資材が含まれないことも確認した。所有・currentの一般照合は維持し、過去の保護証跡は削除しない。

ローカルでは対象選択、ラベルの型・64文字境界、認証後・本文前の順序、4項目だけのJSON、HTTP／生成MCP／外部MCPの生成、監査3段階とschemaの整合を検証する。実環境では、両認証方式でのログ到達・正常要求・後続の拒否／転送経路を、観測部品による拒否とは区別して確認する。未確認のURL解析・SSE・ツールの実動作はまとめ工程で別に受け入れ、完了した旧query資材の退役とは分けて管理する。ソースの実装や合成試験の結果を、Azure反映・接続成功へ読み替えない。

## Document解析の入力棚卸

Document解析は[専用入力部品](../../apps/api-gateway/policies/fragments/request/core-document-input-validation/README.md)で残るmodelの条件を、利用回数制限・下流接続準備・`accepted`監査より前に確認する。操作設定`requestBodySizeCheck=declared`で申告サイズ500,000,000 bytes超の共有413を残し、共有／個別のサイズ確認だけの全量読取りを撤去する。Document独自のMIME一覧・先頭形式・空本文・Content-Encodingの拒否は撤去し、共通Content-Type宣言検査には追加しない。原本または提供元JSONの`urlSource`／`base64Source`を変換せず転送し、入力の適否は提供元へ任せる。[公開契約](../reference/document-analysis.md)に送信本文と復号後・URL先文書の上限の違いをまとめる。

棚卸①〜㉝の変更はローカルの資材へ反映済みであり、Azure反映・読戻し・実経路受入は未実施である。反映時はfragmentとHTTP API・開始／状態取得操作を同じ生成元で揃え、[追加受入](../testing/integration-tests.md#document入力棚卸の追加受入)を確認する。modelの必須・単一値・非空と`.`／`..`の400を維持し、2モデル一覧による拒否と、そのためだけの内部設定JSON再読取りは撤去する。利用可否は提供元が判定し、固定接続先・チケット・返却URIとのモデル照合・応答の構造条件は維持する。内部変数のモデル引渡し失敗は停止を維持して500・`OPERATION_SETTINGS_INVALID`／`DocumentAnalysisUnavailable`とし、model入力不備の400・`DocumentInvalidInput`と分ける。他クエリの有無による一律400を撤去し、開始POSTではmodelをクエリから削除した後、pathだけの標準URI書換えと`copy-unmatched-params=true`でpages・featuresなどを転送した後、標準`set-query-parameter`でapi-versionは対応版へ上書きし、stringIndexTypeは`exists-action=skip`で指定値を保持して未指定時だけ`unicodeCodePoint`を補完する。API版は公開Schemaの単一値（現在`2024-11-30`）を正本として両操作へ正規化で補完する。明示不一致は配備前に拒否し、同じ版をBicepのAPI版markerと共通API設定・Portalの既存生成物へ渡す。baseURL markerの生成も維持する。⑱ではStatusの結果照合が準備済み`gatewayOperationConstraint`から`apiVersion`・`maxResponseBytes`を既存outbound try内で直接読み、専用設定の埋込みBase64復元とBicep展開を撤去する。上限・版・チケット・結果の条件は維持する。⑲ではStatusの操作設定`gatewayOperationConstraint`の取得・JSON解析失敗だけを500へ分類し、応答前に`core-gateway-telemetry`で`policy-error`・`OPERATION_SETTINGS_INVALID`・500を1回記録する。㉙では開始・状態取得の認証結果`gatewayDecision`の取得・JSON解析と認証方式・owner引渡し不備だけを500・`policy-error`／`AUTHENTICATION_PROCESSING_ERROR`へ分け、⑲の操作設定不備と区別する。㉜でGETのチケット・認証結果確認の403・410・500を単一`document-operation-rejection`へ集約し、応答前に既存telemetryを1回呼ぶ。403は`rejected`／`DOCUMENT_OPERATION_DENIED`（文書チケット段階の拒否）、410は`rejected`／`DOCUMENT_OPERATION_EXPIRED`（期限切れ）、500は`policy-error`／`AUTHENTICATION_PROCESSING_ERROR`を記録する。旧GET500専用chooseを統合し、POST500専用、既存拒否条件・公開応答・要求ID・no-storeを維持する。理由は既存の汎用文字列・KQLで読み取り、監査schemaは追加しない。生チケット・JSON・例外を出力しない。捕捉済みの例外は`on-error`へ進まないため明示監査する。先行する共通`<base />`の`BACKEND_RESPONSE`は維持し、提供元200と後段Gateway500を別段階として記録する。提供元本文・版・容量などの既存502、公開`DocumentAnalysisUnavailable`・要求ID・no-storeは維持する。網羅的な再検査は追加しない。実行時の追加検査は行わず、公開仕様とチケット形式を維持する。⑭で文字位置の単位の指定を受け付け、不正値・複数値の適否は提供元へ任せる形へ整理する。㉑では状態取得GETの追加クエリを無視して同じジョブを取得し、クエリの有無だけで403にしない。チケット・UUID・本人・期限の照合、固定URI・対応版とURLへのAPIキー指定の共通拒否は維持する。㉒では`copy-unmatched-params=false`による未使用クエリの非転送を維持し、目的コメントを整える。対応版の結果取得GETはapi-versionのみを使う。将来正式なGET追加クエリが必要になれば、公開結果契約と合わせて見直す。WAF設定とクエリ名・値の監査出力は追加しない。先行する設定検査は維持し、URI・チケット・応答変換・`on-error`の詳細条件は続く棚卸で扱う。Portalの原本アップロード・2モデル選択・BFF制約は今回変更しない。

## 変更する箇所

paramsの`apiDefinitions`が空配列の場合は、標準の `apps/api-gateway/apis/openai-inference-v1.json` の次の1箇所を変更する。明示した定義を使う環境では、実際に読まれる同じAPI定義を対象とし、標準ファイルを変えただけで反映されると考えない。

```text
authorization.operationConstraints["chat-completions"]
  .modelSettings["<MODEL_DEPLOYMENT_NAME>"].maxRequestBytes
```

`<MODEL_DEPLOYMENT_NAME>`は、対象操作で許可され、実際に配備済みの検証モデル名へ置き換える。対象モデルの差分に`maxRequestBytes`があれば元の値を、なければ未指定だったことと操作の基本値を保護記録へ控える。隔離した試験だけ、そのモデルの差分へ4096 bytesを設定する。現在の標準AI定義は500 MiB（524,288,000 bytes）であり、4096を通常利用の推奨値にはしない。復旧時は元の値または未指定へ戻す。ほかのモデルの差分、操作の基本設定、許可モデル、認証、WAF、他operationは変更しない。

操作の`maxRequestBytes`は、配備時に基本値とモデル別差分の最大値へ集約され、モデルを選ぶ前の本文確認で使われる。今回のように1モデルの差分だけを4096にした場合は、大きい基本値が残るため操作の集約値も残り、選択後の本文検査でそのモデルの4096が適用される。基本値や全モデルの設定を一括変更せず、モデル選択後の有効設定を照合する。`settingId`は監査用ラベルであり、設定の検索キーには使わない。

## モデル名の別名を設定する

JSON本文に`model`を持つFoundry OpenAIのChat／Responsesでは、操作直下の`modelAliases`に別名と実配備名の対応表を定義できる。XMLやモデル別差分へ対応表を書かない。標準定義では`responses-create.modelAliases`に`{"codex-auto-review":"gpt-5.6-luna"}`を設定する。これはGateway側のモデル選択であり、OpenAI側の内部モデルが同一という意味ではない。

対応先は、同じ操作の`allowedModels`にある構築済みモデルに限る。既存モデル名の上書き、別名の連鎖・循環、ワイルドカード、別操作への引継ぎは許可しない。別名を`allowedModels`やFoundryのモデル配備一覧へ追加しない。

処理順は共通のキー／Bearer認証、操作・モデル設定の自動選択と別名解決、認証結果の引き渡し、選択後の本文サイズ・出力上限、操作の対象検査、`model`の変換、既存の監査・利用枠・native token制限、下流認証と転送とする。モデル選択に本文JSONを読む場合は、その解析前にも元の受信byte数を確認する。クライアントにprofileを指定させず、残る`X-Egakumi-Profile`は無視して下流転送前に削除する。内部設定は操作の基本設定＋モデル名をキーとした`modelSettings`差分を使い、`profiles`・`defaultProfile`・`modalities`は使わない。モデル別の出力項目・上限・固定URIと既存受付条件は維持する。Responsesの既存`store:false`変換も維持する。別名は認証・権限・利用上限の免除条件ではない。SSE応答を書き換えない。

監査の`targetModel`は転送先の実配備名とし、設定済み別名を変換した場合だけ`requestedModel`と`modelAliasApplied:true`も記録する。任意の未許可モデル名・本文・資格情報は記録しない。共通変換処理を初めて導入するときはAPIポリシーと関連fragmentの更新が必要で、その後の対応表はJSONを正本として生成・配備する。

## 変更前の準備

1. 同じ環境のparams・state・APIM識別子と、同時実行中の配備がないことを確認する。
2. API定義の変更前を保存し、試験用差分が対象モデルの`maxRequestBytes`一箇所だけになることを確認する。他人の未確定差分を含めない。
3. `chat-completions.allowedModels`に含まれ、実配備済みの検証モデルと、対象APIを呼べる専用キーを使う。値は秘密保管から読み、引数・通常ログへ出さない。
4. 実値・試行結果・要求ファイルは `.local/<environment>/evidence/<work-id>/` に保護保存する。

## 計画して反映する

```sh
python3 -B scripts/operations/release.py --component gateway --config "$DEPLOY_PARAMS_FILE"
```

計画と今回のソース差分を確認した後、同じ入口で実行する。

```sh
python3 -B scripts/operations/release.py --component gateway --config "$DEPLOY_PARAMS_FILE" --execute
```

この入口はAPIだけの軽量更新ではなく、Broker・CM・SQL・API・WAF等を含めて照合する。計画は実Foundryの読取照合を行わず、実行ではBroker／SQL処理の後にモデル照合が行われるため、事前に人が現物とparamsを確認する。モデル不一致の負例を作るためにGを実行しない。目的外の差分がある場合は先に分離する。既存イメージを再利用できるかも計画・記録で確認し、API変更のたびに新しい環境を構築しない。

今回の終了コードと配備記録を確認する。古い成功JSONやhealthだけを、今回のAPI更新成功の証拠にしない。

## 正常と拒否を確認する

要求先は既存の `/models/openai/v1/chat/completions`とし、上限を変更した検証モデル名を本文の`model`に指定する。`X-Egakumi-Profile`は付けない。基本設定とモデル別差分から、変更した上限が選ばれることを事前に照合する。本文は通常の合成テキストだけを使い、画像・SQL攻撃文字列等を混ぜない。

- **正常：** 有効な検証キーと許可モデルで、小さいJSONを1回送る。WAF通過や401だけでなく、モデルの正常応答を確認する。
- **境界：** 同じ正しいJSON構造で、送信するUTF-8本文全体が4096 bytesと4097 bytesになる2種類を用意する。差は`messages[].content`の合成文字列だけにする。空白・改行・JSON記号もbytesへ含める。
- 4096 bytesは本文サイズ検査を通過し、4097 bytesはAPIMの413／`REQUEST_TOO_LARGE`となることを確認する。403だけを本文上限の成功と数えない。
- ほかのモデルの差分・操作の基本設定・別APIの上限が今回の試験で変わっていないことを、定義と配備先の対象設定で確認する。

APIキーを含むcurlコマンドを作業票へ貼らない。HTTPクライアントは保護ファイルからキーを読み、秘密値を表示せず、status・公開エラーコード・要求bytesだけを必要範囲で記録する。

## 元へ戻す

1. 対象モデルの`maxRequestBytes`だけを変更前の値へ戻す。元が未指定なら追加した項目を削除し、既存のほかの差分は残して照合する。
2. Gの計画を確認し、同じコマンドに`--execute`を付けて反映する。全体ポリシーやAPI自体を削除しない。
3. 元の上限が4097 bytes以上なら、先ほどの4097 bytesの正常JSONが本文サイズ検査を通過し、通常要求も成功することを確認する。
4. 最終のソース・配備先設定が変更前に戻ったことと、他API・既存キーの保持を記録する。専用要求ファイル等の保持・回収も決める。

応答不明なら直ちに再配備を重ねず、実行履歴と現在設定を読む。計画・反映・確認・復旧を一組で行い、同じ変更を試験行ごとに再配備しない。
