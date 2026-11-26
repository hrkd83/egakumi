# 工程1・2のWebSocket／MCP受入

[apim_phase12_transports.py](apim_phase12_transports.py)は、[REST受入](apim_phase12_acceptance.md)と同じ反映ゲートを使い、既存APIのWebSocketと生成PII MCPをキー／Bearerで確認する入口です。これ自体では工程1・2全体の完了や監査の照合成功を宣言しません。実環境での実施結果は、選択環境の保護記録で別に管理します。

## 実行前の条件

実行ソース、利用者が指定した主チェックアウト、保護入力を、絶対パスの`GW_PHASE12_SOURCE_ROOT`、`GW_PHASE12_RUNTIME_ROOT`、`GW_PHASE12_CONFIG_FILE`で明示します。worktreeから実行しても、実情報の入力・秘密・state・証跡は主チェックアウト直下の`.local/<environment>/`を使います。別の保存先から自動探索・複製しません。

同じ環境のoperation lockを取得して、次を通信前に確認します。plan-onlyでもこれらの保護設定・反映記録は読みます。

- `state/phase12-two-stage-migration.json`のStage Aが検証済みで管理APIから読戻し済み。
- Stage Bが検証済みで、PIIの500定義復元・API反映・Azure上の500定義読戻し・現在のcheckpoint照合が完了。
- Stage Bの`endSnapshot`と現在のinput／Core state／checkpointが一致し、API反映モジュールの内容も反映記録と一致。
- `state/phase12-waf-result.json`の`scopeGatewayOnly`、`managementReadbackVerified`、`inputCoreAndCheckpointUnchanged`が全てBooleanのtrue。
- 現在のソースから再構成したAPI checkpointが保存済みcheckpointと一致。

条件不足・設定ドリフトは、資格情報の読込みとネットワーク通信より前に停止します。各ケースの前後と結果確定前にもsnapshotを確認します。API反映・WAF変更・ログイン・権限変更はこの入口に含みません。

## 実行方法

以下は公開用のコマンド例です。環境選択は保護した設定で行い、実値を例へ書き込みません。

```sh
python3 -I -B scripts/operations/apim_phase12_transports.py websocket key
python3 -I -B scripts/operations/apim_phase12_transports.py mcp bearer
```

標準はplan-onlyです。資格情報を読まず、通信も結果ファイルの保存もせず、対象数・除外数だけを表示します。実接続では`--execute`を付けます。

```sh
python3 -I -B scripts/operations/apim_phase12_transports.py websocket key --execute
python3 -I -B scripts/operations/apim_phase12_transports.py websocket bearer --execute
python3 -I -B scripts/operations/apim_phase12_transports.py mcp key --execute
python3 -I -B scripts/operations/apim_phase12_transports.py mcp bearer --execute
python3 -I -B scripts/operations/apim_phase12_transports.py websocket bearer --case openai-live-ws-v1 --execute
```

資格情報は`state/phase12-acceptance-credentials.json`に記録した、同じruntime・入力に属する`secrets/`内のファイルだけを使います。キーは宣言した購読キー用ヘッダー、Bearerは`Authorization`へ送ります。Bearerのローカルclaims確認は適格性確認であり、トークンの署名と認可はAPIMの実応答で確認します。期限切れならこの入口で更新せず、既存のサインイン手順で本人が更新した後に再実行します。除外対象だけを選んだ場合は資格情報も読みません。

WebSocketとMCPも[RESTと同じTLS context](../deployment/helpers/client_tls.py)を使います。Windowsは[固定依存](../integration-requirements.txt)の`truststore`でOS標準の証明書・ホスト名検証を行い、他のOSはPythonの既定検証を維持します。信頼ストアと検証条件を変更せず、Windowsの依存欠落時は接続前に停止します。

終了値は、0が選択したケースの合格（plan-onlyも0）、1が未検証・不合格を含む結果、2が事前確認または実行の停止です。標準の全WebSocket実行には下記の除外2件が含まれるため、4件が合格しても終了値1です。

## WebSocketの確認対象

正本は[API定義](../../apps/gateway/apim/apis/)です。6個のWebSocket API、外部WSS backend、公開定義上の論理操作`GET invoke /`、購読キーヘッダーを照合します。APIMが実際に登録・実行する接続操作IDは`onHandshake`です。transport結果の論理名`invoke`と分け、管理読戻しとnative監査は公開Bicepに定義した`onHandshake`へ対応付けます。接続先は選択環境の公開HTTPS originをWSSへ変換し、API pathと末尾`/`を加えます。backendへ直接接続しません。次のモデルやAPI版は、公開宣言と選択環境の配備モデルから決めます。

| API ID | 公開パス | 実施する確認 |
| --- | --- | --- |
| `openai-realtime-ws-v1` | `/models/openai-realtime/v1/ws/` | 宣言済みdefault modelをqueryへ設定。101、最初の`session.created`、正常closeを確認 |
| `azure-voice-live-ws-v1` | `/models/voice-live/v1/ws/` | [public-bindings.json](../../apps/gateway/apim/public-bindings.json)のmodelとapi-versionをqueryへ設定。101、最初の`session.created`、正常closeを確認 |
| `openai-live-ws-v1` | `/models/openai-live/v1/ws/` | model queryを付けず、Portalと同じ`session.start`で配備モデルを送る。`session.started`、`session.close`に対する`session.closed`、正常closeを確認 |
| `openai-transcription-ws-v1` | `/models/openai-transcription/v1/ws/` | queryは`intent=transcription`。配備モデルを`session.update`の音声入力設定へ送り、`session.updated`と正常closeを確認。最初の`session.created`も許容 |
| `openai-realtime-observer-v1` | `/models/openai-realtime/v1/observer/` | 未実施。既存callに本人が紐づくowner-bound ticketが必要 |
| `azure-voice-live-webrtc-v1` | `/models/voice-live/v1/webrtc/` | 未実施。ブラウザーで生成したaudio-only SDPによるcalls signalingが必要 |

Voice Liveのモデルは独立サービスの公開設定です。Foundryの`modelDeployments`に同名があることを前提にしません。Live／Transcriptionは[Portalのプロトコル](../../apps/portal/lib/realtime-protocol.ts)に合わせてmodelを開始イベントへ渡します。受信したsessionのtop-level modelは開始・更新の両イベントで宣言値へ照合します。Transcriptionは`session.audio.input.transcription.model`と旧形式の`session.input_audio_transcription.model`も照合し、1つでも不一致なら不合格です。モデル未返却は`modelReturned: false`、更新ackでの未返却は`ackModelReturned: false`とし、観測件数は`modelObservationCount`へ記録します。未返却でも接続は合格し得ますが、正確なbackend model routingの証明は未了です。

WebSocketは自動再送・redirect・proxyを使いません。45秒のdeadline、接続待ち最大15秒、close待ち最大5秒、1イベント64 KiB、開始・Live終了の各段階8イベントを上限とします。101だけ、想定外の最初のイベント、service error、異なるmodel、未完了、close codeが1000以外なら合格しません。音声、`response.create`、SDP、外部ツール実行は送りません。接続によるサービス側の課金・セッション生成が生じる可能性は残ります。

Observerはticketを捏造せず、Voice Live WebRTCはSDPを捏造しません。この2件は`executed: false`と理由を記録し、合格数へ含めません。完全な受入には、別工程で実際の本人call/ticketとブラウザーSDPを使い、signaling・本人照合・終了／後始末を確認する必要があります。

## 生成PII MCPの確認

PII APIの`mcp.name=pii-v1-mcp`、`operationNames=[pii_redact_text]`を確認し、宣言したMCP pathに`/mcp`を付けて接続します。一般的なRESTのPII URLへ直接POSTする確認ではありません。

[integration_client.py](../integration_client.py)の公式MCP SDKによるStreamable HTTPを再利用し、`strict_tool_contract=True`を指定します。SDKのinitialize、tools capability、tools/listの完了とページなし、`pii_redact_text`の存在、object schemaで`text`／`language`だけがstring型かつ両方requiredであることを、`tools/call`の前に確認します。型・必須項目・ページ完了の不一致ではツールを呼びません。受入結果もこの事前確認のBoolean `toolContractCheckedBeforeCall`、string型のdescriptor、1回のPOST・HTTP 200、PII結果の構造と正常完了を要求します。既存clientは厳格モードを指定しなければ標準動作を保ちます。入力は既存の合成PII例だけです。出力テキストや任意のtool名・schema・session IDは記録しません。

MCPも45秒で停止し、既存clientのredirect・自動ツール再実行・stream resumption禁止を維持します。manual credentialでの認可とSDKプロトコルの確認であり、Gateway OAuthのdiscovery、PKCE、token refreshを検証したとは扱いません。`gatewayOAuthVerified: false`を維持します。SDKはclient側の通信を閉じますが、server sessionのDELETEによる終端はこの入口の受入対象に含みません。

## 記録と監査の照合

各ケースと最終結果は、主チェックアウトの選択環境の`evidence/apim-phase12-transports-<generated-run-id>/`へ`context.save_private`で保護保存します。時刻、APIM相関ID、固定API／operation ID、認証方式、HTTP状態、既知イベント数、構造比較のBoolean、固定した例外クラスを記録します。HTTPヘッダー全体、URL、query、token、本文、service session ID、任意の例外メッセージは保存しません。公開用の結果には実環境の時刻・相関ID・実測値を転載しません。

この入口は監査queryを実行せず、全結果で`auditVerified: false`、`phase12Completed: false`を維持します。実照合は[管理VMの正式入口](../../docs/operations.md#管理vmから保守データを取得する)で、Linux管理VM自身のMIと所有inventoryを使う`management_client.py workspace-query --resource-id <WORKSPACE_RESOURCE_ID> --query-file <PROTECTED_QUERY_FILE>`から行います。inventoryと出力指定を含む正式手順に従い、実値入りquery・結果は許可された保護領域で扱います。Windowsの人間認証cacheをVMへコピーしません。

[audit-identification.kql](../audit-identification.kql)を基に、対象時刻・資源・API・operation・要求の全行とtraceを取得します。APIM応答の`apim-request-id`とログの`CorrelationId`が主な照合キーです。clientの`x-ms-client-request-id`だけではAPIM監査の照合完了としません。共通要求準備がこのヘッダーをAPIMの要求IDへ上書きするため、送信したclient値を`CorrelationId`へ代入しません。応答IDを取得できなかった要求は、正式診断に実際に保存された値と、固定した所有APIM・API・メソッド・状態の条件から一意なnative行を観測できた場合だけ補完候補にします。補完後も認証主体・操作・モデルとAppGWの照合を行い、一致しない場合や欠測は保留します。時刻だけの結合は行いません。

必要な列は`_ResourceId`、`TimeGenerated`、`CorrelationId`、`ApiId`、`OperationId`、`ResponseCode`、`BackendResponseCode`、`TraceRecords`です。traceの認証方式・本人／tenant／client・modelを実要求と照合し、前段AppGWでは`TransactionId`等の既存相関を使います。[audit-usage.kql](../audit-usage.kql)は許可済み実行の利用量用であり、拒否要求やbackend呼出しゼロの証明には使いません。

拒否時にもtelemetryが`backendRequestId`を生成するため、そのフィールドの存在だけではbackend実行と判断できません。APIMの全要求行、backend状態、traceを合わせて評価します。ログ遅延・対象欠測は照合保留とし、呼出しゼロや成功を推測しません。WebSocket／MCPの正常接続、RESTの不正Bearer拒否、実際の監査照合は、それぞれの証拠を揃えてから工程全体を判断します。

strictなPII MCP受入では、正常終了した固定PII結果の非ゼロGUID `requestId`だけを `piiResultRequestId`へ記録します。外側HTTPの相関IDと分け、後続のnative REST監査でAPI・操作・主体を確認します。本文や任意のメタデータは保存しません。
