# APIMのAPI定義とポリシーを変更する

APIMへの公開設定は、標準APIの再配備と、構築後にAzureポータルから追加する独自APIに分かれます。 [更新と配備の再開](deployment.md) · [ポリシーの適用先](../../apps/api-gateway/policies/portal-examples/scope-map.md)

## API・ポリシー・購読を変更する

- **標準API**のAPI・操作・許可モデル・入力制約は [API定義](../../apps/api-gateway/apis/) を変更し、Gatewayを配備します。操作ポリシーはAPI定義から生成します。認証・監査などの共通処理の変更には[共通ポリシーの改修](../../DEVELOPMENT.md#apim共通ポリシーを改修する)を使います。
- **独自API**は管理者がAzureポータルでAPIと操作を登録し、Product所属・購読設定・固定の下流宛先と認証・公開パス・監査を設定します。`<base />`で共通処理を継承し、有効なポリシーを確認します。標準APIとのID重複を避け、所有者と変更点を運用台帳に残します。Gateway配備は登録対象外の独自APIを保持します。 [Claudeを追加する例](claude-messages.md)
- **購読キー**が必要な場合は基盤管理者がAPIMで対象APIを絞って発行します。 [例外発行と利用上限](access-and-keys.md)

Gatewayは、Foundryにすでに配備されたモデルのID・版・形式・SKUと状態を照合し、現在のcapacityを保持します。容量やモデルを変更する場合は[Foundryの運用](foundry.md)を参照してください。標準APIの公開後、共通ポリシーをETag付きで統合・読戻し確認します。競合で止まったら現物と今回の差分を確認してから同じ入口で再試行します。手動統合に使う確認用XMLは[レンダリングスクリプト](../../scripts/policy/render_apim_policy.py)から未作成のファイルへ出力できます。

Gatewayの配備記録にある `defaultSampleCleanup` は、未使用と判定したAPIM初期サンプルの整理結果を示します。WebSocketを提供する場合は `webSocketDiagnostics` で接続診断の設定を確認します。実際の要求と監査を[本文監査の受入](../reference/audit/request-body-audit.md#azure受入)に従って照合してください。

## 操作・MCP受付のURLクエリ観測

HTTP操作は `operations[].queryObservation`、生成MCPは `mcp.queryObservation`、外部MCPは `mcpProxy.endpoints[].queryObservation` に観測ラベルを指定します。標準の対象はLive作成 `liveCreate`、Realtime作成 `realtimeCreate`、PIIのMCP受付 `piiMcp` です。 [ラベルと生成される設定](../../apps/api-gateway/policies/fragments/request/core-query-observation/README.md)

共通認証の後にラベル、HTTPメソッド、クエリの有無、同名引数の複数値の有無を取得します。`query-guard` 監査の段階は `received`、`backend-response`、`policy-error` です。クエリの名前や値、本文、資格情報はこのイベントへ記録しません。取得不能は `unknown` として、クエリなしと区別します。

反映後は、対象操作の有効なポリシーと `core-query-observation` フラグメントを照合します。独立した合成要求で正常応答・後続ポリシーの拒否・監査の到達を確認してください。APIM本体やWAFの診断ログは別経路で点検します。 [ログの点検](platform-settings.md#監視通知先条件を更新しログを点検する)

## Document解析の入力

解析開始では、`model` の単一値と申告されたリクエストサイズを確認し、選択したモデルを固定のDocument Intelligence宛先へ渡します。文書・MIME・モデルの適否は提供元が判定します。状態取得はチケットと本人・対象・期限を照合し、APIの対応版で実行します。このAPIを変更する場合は開始と状態取得を同じAPI定義から配備し、正常系と拒否を確認してください。 [公開APIの契約](../reference/gateway/document-analysis.md) · [統合テスト](../testing/integration-tests.md)

## 変更する箇所

隔離した検証環境では、標準の `apps/api-gateway/apis/openai-inference-v1.json` の対象モデルについて、次の1箇所を変更して本文サイズの境界と切戻しを確認できます。

```text
authorization.operationConstraints["chat-completions"]
  .modelSettings["<MODEL_DEPLOYMENT_NAME>"].maxRequestBytes
```

`<MODEL_DEPLOYMENT_NAME>`は、対象操作で許可され、実際に配備済みの検証モデル名へ置き換える。対象モデルの差分に`maxRequestBytes`があれば元の値を、なければ未指定だったことと操作の基本値を保護記録へ控える。隔離した試験だけ、そのモデルの差分へ4096 bytesを設定する。現在の標準AI定義は500 MiB（524,288,000 bytes）であり、4096を通常利用の推奨値にはしない。復旧時は元の値または未指定へ戻す。ほかのモデルの差分、操作の基本設定、許可モデル、認証、WAF、他operationは変更しない。

操作の`maxRequestBytes`は、配備時に基本値とモデル別差分の最大値へ集約され、モデルを選ぶ前の本文確認で使われる。今回のように1モデルの差分だけを4096にした場合は、大きい基本値が残るため操作の集約値も残り、選択後の本文検査でそのモデルの4096が適用される。基本値や全モデルの設定を一括変更せず、モデル選択後の有効設定を照合する。`settingId`は監査用ラベルであり、設定の検索キーには使わない。

## モデル名の別名を設定する

JSON本文に`model`を持つFoundry OpenAIのChat／Responsesでは、操作直下の`modelAliases`に別名と実配備名の対応表を定義できる。XMLやモデル別差分へ対応表を書かない。標準定義では`responses-create.modelAliases`に`{"codex-auto-review":"gpt-5.6-luna"}`を設定する。これはGateway側のモデル選択であり、OpenAI側の内部モデルが同一という意味ではない。

対応先は、同じ操作の`allowedModels`にある構築済みモデルに限る。既存モデル名の上書き、別名の連鎖・循環、ワイルドカード、別操作への引継ぎは許可しない。別名を`allowedModels`やFoundryのモデル配備一覧へ追加しない。

処理順は共通のキー／Bearer認証、操作・モデル設定の自動選択と別名解決、認証結果の引き渡し、選択後の本文サイズ・出力上限、操作の対象検査、`model`の変換、既存の監査・利用枠・native token制限、下流認証と転送とする。モデル選択に本文JSONを読む場合は、その解析前にも元の受信byte数を確認する。有効なモデル設定は操作の基本設定に、モデル名に対応する`modelSettings`の差分を適用して得る。モデル別の出力項目・上限・固定URIと既存受付条件は維持する。Responsesの既存`store:false`変換も維持する。別名は認証・権限・利用上限の免除条件ではない。SSE応答はそのまま返す。

監査の`targetModel`は転送先の実配備名とし、設定済み別名を変換した場合だけ`requestedModel`と`modelAliasApplied:true`も記録する。任意の未許可モデル名・本文・資格情報は記録しない。別名の対応表はJSONから生成して配備する。

## 変更前の準備

1. 同じ環境のparams・state・APIM識別子と、同時実行中の配備がないことを確認する。
2. API定義の変更前を保存し、試験用差分が対象モデルの`maxRequestBytes`一箇所だけになることを確認する。他人の未確定差分を含めない。
3. `chat-completions.allowedModels`に含まれ、実配備済みの検証モデルと、対象APIを呼べる本人のBearerを使う。トークンを引数・通常ログへ出さない。
4. 実値・試行結果・要求ファイルは `.local/<environment>/evidence/<work-id>/` に保護保存する。

## 計画して反映する

```sh
python3 -B scripts/operations/release.py --component gateway --config "$DEPLOY_PARAMS_FILE"
```

計画と今回のソース差分を確認した後、同じ入口で実行する。

```sh
python3 -B scripts/operations/release.py --component gateway --config "$DEPLOY_PARAMS_FILE" --execute
```

Gateway配備はBroker・CM・SQL・API・WAFを照合する。計画は実Foundryの読取照合を行わず、実行ではBroker／SQL処理の後にモデル照合が行われるため、事前に人が現物とモデル設定を確認する。モデル不一致の負例を作るためにGを実行しない。目的外の差分がある場合は先に分離する。既存イメージを再利用できるかも計画・記録で確認し、API変更のたびに新しい環境を構築しない。

今回の終了コードと配備記録を確認する。古い成功JSONやhealthだけを、今回のAPI更新成功の証拠にしない。

## 正常と拒否を確認する

要求先は既存の `/models/openai/v1/chat/completions`とし、上限を変更した検証モデル名を本文の`model`に指定する。`X-Egakumi-Profile`は付けない。基本設定とモデル別差分から、変更した上限が選ばれることを事前に照合する。本文は通常の合成テキストだけを使い、画像・SQL攻撃文字列等を混ぜない。

- **正常** 有効な本人Bearerと許可モデルで、小さいJSONを1回送る。WAF通過や401だけでなく、モデルの正常応答を確認する。
- **境界** 同じ正しいJSON構造で、送信するUTF-8本文全体が4096 bytesと4097 bytesになる2種類を用意する。差は`messages[].content`の合成文字列だけにする。空白・改行・JSON記号もbytesへ含める。
- 4096 bytesは本文サイズ検査を通過し、4097 bytesはAPIMの413／`REQUEST_TOO_LARGE`となることを確認する。403だけを本文上限の成功と数えない。
- ほかのモデルの差分・操作の基本設定・別APIの上限が今回の試験で変わっていないことを、定義と配備先の対象設定で確認する。

要求のstatus・公開エラーコード・本文のbyte数だけを必要範囲で記録する。認証ヘッダーを作業票や通常ログに保存しない。

## 元へ戻す

1. 対象モデルの`maxRequestBytes`だけを変更前の値へ戻す。元が未指定なら追加した項目を削除し、既存のほかの差分は残して照合する。
2. Gの計画を確認し、同じコマンドに`--execute`を付けて反映する。全体ポリシーやAPI自体を削除しない。
3. 元の上限が4097 bytes以上なら、先ほどの4097 bytesの正常JSONが本文サイズ検査を通過し、通常要求も成功することを確認する。
4. 最終のソース・配備先設定が変更前に戻ったことと、他API・既存の購読の保持を記録する。専用要求ファイル等の保持・回収も決める。

応答不明なら直ちに再配備を重ねず、実行履歴と現在設定を読む。計画・反映・確認・復旧を一組で行い、同じ変更を試験行ごとに再配備しない。
