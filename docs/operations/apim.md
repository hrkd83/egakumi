# APIMのAPI定義・ポリシーを変更する

[共通確認](common.md) · [API定義](../../apps/gateway/apim/apis/openai-inference-v1.json)

構築済みの隔離環境で、既存Chat APIの本文上限を変更し、正常要求・上限超過の拒否・元への復旧を確認する。新APIやバックエンドの追加、初期構築のやり直しは不要。以下は操作例であり、記載だけで試験済みにはしない。

| 変更内容 | 正本 |
| --- | --- |
| API・operation、許可モデル、入力制約 | `apps/gateway/apim/apis/<API名>.json`。制約から操作ごとのポリシーも生成される |

API別制約はJSON定義を変更してGで反映する。XMLは定義から生成するため、この手順でXMLを手編集する必要はない。共通認証・監査等の処理自体を変更する場合は、[開発者向けの共通ポリシー改修](../../DEVELOPMENT.md#apim共通ポリシーを改修する)として扱う。以下はAPI別制約の変更例である。

## Live・Realtime・PII MCPのquery拒否

次の3入口は、`gateway.queryGuards`で個別に観測・拒否を選ぶ。実装先はAPIMのAPIポリシーと共通fragmentであり、PortalのサーバーコードやWAFポリシーの変更ではない。

| 設定キー | 対象 |
| --- | --- |
| `liveCreate` | `POST /models/openai-live/v1/sessions` |
| `realtimeCreate` | `POST /models/openai-realtime/v1/calls` |
| `piiMcp` | `/tools/pii-v1-mcp/mcp`のPOST・GET/SSE・DELETE。末尾の`/`も対象 |

入口は固定公開path・HTTP method・選択済みの操作制約で識別する。Live・Realtimeでは操作templateも照合するため、内部API名や操作名だけの変更で検査が無効にならず、別の公開pathへ対象も広げない。

```json
{
  "gateway": {
    "queryGuards": {
      "liveCreate": "observe",
      "realtimeCreate": "observe",
      "piiMcp": "observe"
    }
  }
}
```

省略時・標準設定は`observe`で、既存の受付動作を維持する。paramsでは対象キーだけを上書きできる。`reject`へ切り替えた入口では、query引数が1件以上ある要求をHTTP 400／`QUERY_PARAMETERS_NOT_ALLOWED`で拒否する。名前・値・重複の内容による許可は追加しない。末尾の`?`だけで解析済み引数が0件なら拒否しない。query形状を取得できない場合、観測では`unknown`として受付動作を維持し、拒否では400にする。不正なモード設定は正規化で拒否し、実行時に不正設定があれば503／`QUERY_GUARD_NOT_CONFIGURED`となる。

共通認証の完了後に検査する。Live・Realtimeはprofile選択や本文読取りより前、native PII MCPは購読認可後の本文引数検査より前で拒否する。本文なしのGET/SSE・DELETEへMIME条件は追加しない。別APIの正規query（WebSocketの固定パラメーター、Realtime observerの`call_id`、外部MCPの`session_id`、embeddingの限定`api-version`等）はこの切替の対象外である。

観測は独立した`query-guard` traceへ固定schemaで記録する。入口・HTTP method・mode・段階・queryの有無・件数bucket・重複の有無・応答classだけを記録し、query名・値、URL、認証情報、本文、要求ID・購読IDを含めない。`received`を分母とし、`query-rejected`・`guard-error`・`backend-response`・`policy-error`を区別する。後続の別検査で早期終了した要求やログ未着では、終端記録が欠け得る。未着・`unknown`を「queryなし」や正常成功と数えない。SSEのHTTP 200やMCPの2xxも機能完了の証明にはしない。

**実環境で拒否を有効にする前の確認：** 全3入口とMCPの各methodについて、有効な正常要求を観測し、必要なqueryがないことを呼出し元と照合する。queryありの正常利用があれば用途確認まで`observe`を維持する。新traceの固定schemaだけで、既存診断のURL非保持まで保証したとは扱わない。APIMの標準診断は既知query名のマスクのみで、Log AnalyticsとStorageへの配送もあるため、任意query名・値の非保持は保証しない。既存診断設定は保持し、試験のquery名・値は独立した合成入力だけを使う。キー・Cookie等の機密値はqueryへ入れない。この設定の追加だけでWAF除外を拡大しない。

ローカルでは実XMLのC#式、認証後・本文前の順序、生成XMLへの設定反映を検証する。APIMによるURL解析、実際の下流呼出し停止、ログ到達、正常利用への無影響は別途実環境で確認する。復旧は対象キーを`observe`へ戻すことで行い、認証や他の既存入力検査は維持する。

## 変更する箇所

paramsの`apiDefinitions`が空配列の場合は、標準の `apps/gateway/apim/apis/openai-inference-v1.json` の次の1箇所を変更する。明示した定義を使う環境では、実際に読まれる同じAPI定義を対象とし、標準ファイルを変えただけで反映されると考えない。

```text
authorization.operationConstraints["chat-completions"]
  .profiles の profileId == "chat" の maxRequestBytes
```

変更前の値を保護記録へ控え、隔離した試験だけ4096 bytesへ変更する。現在の標準AI定義は500 MiB（524,288,000 bytes）であり、4096を通常利用の推奨値にはしない。復旧時は必ず控えた値へ戻す。`vision`、`compatible`、`router`のprofile、許可モデル、認証、WAF、他operationは変更しない。

`profiles`を持つoperationの外側の`maxRequestBytes`は、読込時に全profileの最大値から組み立てられる。外側だけを4096にしてもChatの上限変更にはならない。Visionの大きい上限が残るため、外側とChatの値が同じになるとも限らない。

## モデル名の別名を設定する

JSON本文に`model`を持つFoundry OpenAIのChat／Responsesでは、個別profileの`modelAliases`に別名と実配備名の対応表を定義できる。XMLへ対応表を直接書かない。標準定義ではResponsesの`response`だけに`{"codex-auto-review":"gpt-5.6-luna"}`を設定する。これはGateway側のモデル選択であり、OpenAI側の内部モデルが同一という意味ではない。

対応先は、そのprofileの`allowedModels`にある構築済みモデルに限る。既存モデル名の上書き、別名の連鎖・循環、ワイルドカード、別profileへの引継ぎは許可しない。別名を`allowedModels`やFoundryのモデル配備一覧へ追加しない。

処理順はprofile選択と別名解決、購読認可、元の受信byte数と実モデルの出力上限、操作の対象検査、`model`の変換、既存の監査・購読上限・native token制限、転送とする。Responsesの既存`store:false`変換は維持する。別名は認証・権限・利用上限の免除条件ではない。SSE応答を書き換えない。

監査の`targetModel`は転送先の実配備名とし、設定済み別名を変換した場合だけ`requestedModel`と`modelAliasApplied:true`も記録する。任意の未許可モデル名・本文・資格情報は記録しない。共通変換処理を初めて導入するときはAPIポリシーと関連fragmentの更新が必要で、その後の対応表はJSONを正本として生成・配備する。

## 変更前の準備

1. 同じ環境のparams・state・APIM識別子と、同時実行中の配備がないことを確認する。
2. API定義の変更前を保存し、差分がChat profileの上限一箇所だけになることを確認する。他人の未確定差分を含めない。
3. `chat`の`allowedModels`に含まれ、実配備済みの検証モデルと、対象APIを呼べる専用キーを使う。値は秘密保管から読み、引数・通常ログへ出さない。
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

要求先は既存の `/models/openai/v1/chat/completions`、profile指定は `X-Egakumi-Profile: chat` とする。本文は通常の合成テキストだけを使い、画像・SQL攻撃文字列等を混ぜない。

- **正常：** 有効な検証キーと許可モデルで、小さいJSONを1回送る。WAF通過や401だけでなく、モデルの正常応答を確認する。
- **境界：** 同じ正しいJSON構造で、送信するUTF-8本文全体が4096 bytesと4097 bytesになる2種類を用意する。差は`messages[].content`の合成文字列だけにする。空白・改行・JSON記号もbytesへ含める。
- 4096 bytesは本文サイズ検査を通過し、4097 bytesはAPIMの413／`REQUEST_TOO_LARGE`となることを確認する。403だけを本文上限の成功と数えない。
- `chat`以外のprofileや別APIの上限が今回の差分で変わっていないことを、定義と配備先の対象設定で確認する。

APIキーを含むcurlコマンドを作業票へ貼らない。HTTPクライアントは保護ファイルからキーを読み、秘密値を表示せず、status・公開エラーコード・要求bytesだけを必要範囲で記録する。

## 元へ戻す

1. Chat profileの`maxRequestBytes`だけを変更前の値へ戻し、差分を確認する。
2. Gの計画を確認し、同じコマンドに`--execute`を付けて反映する。全体ポリシーやAPI自体を削除しない。
3. 元の上限が4097 bytes以上なら、先ほどの4097 bytesの正常JSONが本文サイズ検査を通過し、通常要求も成功することを確認する。
4. 最終のソース・配備先設定が変更前に戻ったことと、他API・既存キーの保持を記録する。専用要求ファイル等の保持・回収も決める。

応答不明なら直ちに再配備を重ねず、実行履歴と現在設定を読む。計画・反映・確認・復旧を一組で行い、同じ変更を試験行ごとに再配備しない。
