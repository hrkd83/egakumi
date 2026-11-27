# 初期APIとポリシーの対応表

初期構築の[API定義](../../apis/)から、[API公開資材](../../api-publication.bicep)が次のスコープへ展開します。この表のAPI IDは宣言の`name`、公開prefixは`path`です。ポータルで別途追加するAPIは[手動追加のひな型](README.md)を使います。

| スコープ | 正本 | Azureポータルで読む場所 |
| --- | --- | --- |
| 全API | [service.xml](../service.xml) | **All APIs**の各section。入口確認、キー・Entra認証、監査の要求情報、共通エラー |
| 再利用する部品 | [fragments](../fragments/)のXMLとC# | **Policy fragments**の同名ID。API・操作から参照 |
| HTTP API | [rest.xml](../api-templates/rest/rest.xml)とAPI定義JSON | **APIs → 対象API → All operations**。操作設定・回数制限・固定転送・監査 |
| WebSocket | [ws.xml](../api-templates/ws/ws.xml)とAPI定義JSON | **APIs → 対象API → onHandshake**。接続時の認可と転送先 |
| 生成MCP | [generated.xml](../api-templates/mcp/generated.xml)と元RESTの`mcp`設定 | **APIs → MCP server → All operations**。公開ツールから元のREST操作へ接続 |
| 外部MCP | [external.xml](../api-templates/mcp/external.xml)とAPIの`mcpProxy`設定 | **APIs → 対象API → All operations**。登録した外部サーバー、転送方式、クエリ・セッションを確認 |
| WebSocket・外部MCPの共通処理 | [stream.xml](../api-templates/shared/stream.xml) | 上記の実効ポリシーで、入口処理・固定転送・監査の組み合わせを確認 |
| 個別操作 | [api-publication.bicep](../../api-publication.bicep)の`operationPolicyValue`とAPI定義JSON。固有の処理は[operation-policies](../operation-policies/)のXMLとC# | **APIs → 対象API → 対象Operation**。全HTTP操作で`base`を継承し、宣言に応じてURIや固定応答を構成。固有のテンプレートは下表 |
| 差し込み | [snippets](../snippets/) | 個別のポータル登録はありません。対応するAPI・操作の実効ポリシーで確認 |

## 標準19 APIと生成MCP

HTTP行はAll operationsに、WebSocket行はonHandshakeに共通テンプレートが入ります。下表の操作IDはJSONの`operations[].name`です。WebSocketではJSON側の`invoke`に対してAzureポータルの`onHandshake`を開きます。JSONが指定する購読Productと、公開済みAPI用の`released-apis`、構築時に照合するBearer用の`core-entra-model-users`は、APIのポリシーと別スコープです。

| 分類 | API正本 | 公開prefix | 操作ID | スコープ・操作別資材 |
| --- | --- | --- | --- | --- |
| テキスト生成 | [openai-inference-v1](../../apis/openai-inference-v1.json) | `models/openai/v1` | `chat-completions`、`responses-create` | HTTP。`chat-completions`、`responses-create`をAPIスコープで選択 |
| 埋め込み | [openai-embeddings-v1](../../apis/openai-embeddings-v1.json) | `models/openai-embeddings/v1` | `embeddings` | HTTP |
| 埋め込み・リランク | [cohere-inference-v1](../../apis/cohere-inference-v1.json) | `models/cohere/v1` | `cohere-embeddings`、`rerank` | HTTP。`cohere-embeddings`と`rerank`は同じAPIの別操作 |
| 画像生成・編集 | [openai-images-v1](../../apis/openai-images-v1.json) | `models/openai-images/v1` | `image-generate`、`image-edit` | HTTP |
| 画像生成・編集 | [blackforest-images-v1](../../apis/blackforest-images-v1.json) | `models/blackforestlabs/v1` | `flux2-generate`、`flux2-edit` | HTTP |
| 音声STT／TTS | [azure-speech-v1](../../apis/azure-speech-v1.json) | `models/azure-speech/v1` | `tts`、`stt` | HTTP。TTSのみ[SSML部品](../fragments/request/core-speech-ssml-guard/) |
| 音声STT | [openai-audio-v1](../../apis/openai-audio-v1.json) | `models/openai-audio/v1` | `audio-transcriptions` | HTTP |
| リアルタイム音声 | [openai-realtime-control-v1](../../apis/openai-realtime-control-v1.json) | `models/openai-realtime/v1` | `realtime-create`、`realtime-hangup` | HTTP。[realtime-create](../operation-policies/realtime/realtime-create.xml)と[realtime-hangup](../operation-policies/realtime/realtime-hangup.xml)は同名の操作に適用 |
| リアルタイム音声 | [openai-realtime-ws-v1](../../apis/openai-realtime-ws-v1.json) | `models/openai-realtime/v1/ws` | `invoke` | WebSocket |
| リアルタイム音声 | [openai-realtime-observer-v1](../../apis/openai-realtime-observer-v1.json) | `models/openai-realtime/v1/observer` | `invoke` | WebSocket。[監視接続の本人確認](../fragments/authentication/core-realtime-observer/) |
| リアルタイム音声 | [openai-live-control-v1](../../apis/openai-live-control-v1.json) | `models/openai-live/v1` | `live-create` | HTTP。[live-create](../operation-policies/live/live-create.xml)は同名の操作に適用 |
| リアルタイム音声 | [openai-live-ws-v1](../../apis/openai-live-ws-v1.json) | `models/openai-live/v1/ws` | `invoke` | WebSocket |
| リアルタイム音声 | [openai-transcription-ws-v1](../../apis/openai-transcription-ws-v1.json) | `models/openai-transcription/v1/ws` | `invoke` | WebSocket |
| リアルタイム音声 | [azure-voice-live-ws-v1](../../apis/azure-voice-live-ws-v1.json) | `models/voice-live/v1/ws` | `invoke` | WebSocket |
| リアルタイム音声 | [azure-voice-live-webrtc-v1](../../apis/azure-voice-live-webrtc-v1.json) | `models/voice-live/v1/webrtc` | `invoke` | WebRTCのWebSocket制御接続 |
| 意思決定 | [microsoft-decision-v1](../../apis/microsoft-decision-v1.json) | `models/microsoft-decision/v1` | `systemone` | HTTP |
| REST API | [document-analysis-v1](../../apis/document-analysis-v1.json) | `tools/documents/v1` | `analyze_document`、`get_document_operation` | HTTP。`analyze_document`は[開始用XML](../operation-policies/document-analysis/document-analysis.xml)、`get_document_operation`は[状態取得用XML](../operation-policies/document-analysis/document-analysis-status.xml) |
| REST API | [graph-profile-v1](../../apis/graph-profile-v1.json) | `tools/graph/v1` | `graph_profile_self` | HTTP。APIスコープでBearerの[OBO](../snippets/authentication/graph-obo/)と購読キーのCMを分ける |
| REST API | [pii-v1](../../apis/pii-v1.json) | `tools/pii/v1` | `pii_redact_text` | HTTP。`pii_redact_text`は[PIIの個別操作XML](../operation-policies/pii/pii.xml) |
| MCP | `pii-v1-mcp` | `tools/pii-v1-mcp` | `pii_redact_text` | [PII正本](../../apis/pii-v1.json)から生成。`pii_redact_text`を公開 |

本人接続用の`credential-manager-login`と`credential-manager-browser`はCM設定から生成します。[認証の個別操作](../operation-policies/credential-login/)と[生成資材](../../api-publication.bicep)で対象スコープを確認できます。標準19 APIのJSONと生成MCPの一覧とは別です。

## 追加APIへ流用する範囲

テキスト、埋め込み、画像、音声、リランク、意思決定、一般RESTには、同じREST・SSEひな型で認証・回数枠・固定接続先・監査を適用できます。本文形式と上限、HTTP操作と提供元のURIは公開する契約に合わせます。音声のSSML、状態を持つRealtimeの本人確認、Documentの非同期取得の所有者、PIIの入出力加工は保護目的がある個別処理です。追加するAPIで同じ保護を必要とする場合に適用範囲を決めます。

共通ひな型で記録する監査は要求IDと認証主体、操作・結果などのメタデータです。本文の保管は、初期構築でChat Completions・Responsesを専用中継へ送る構成で有効にします。 [本文監査の対象と保存](../../../../docs/reference/request-body-audit.md)

WebSocketとMCPは、接続・セッション・終了・公開ツールの契約を先に決めます。MCPで既存RESTを公開する場合は公開対象のOperationと引数を絞り、元の認可を実際に受け入れます。外部MCPは固定接続先、SSEまたはStreamable HTTP、セッション所有者を扱うプロトコル用設定を使います。[通常HTTPのひな型](rest.xml)はこれらのプロトコル固有の処理を含みません。
