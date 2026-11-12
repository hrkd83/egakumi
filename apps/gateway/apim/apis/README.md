# 標準APIの配備定義

このフォルダーのJSONは、Gatewayへ実際に配備する標準APIの定義です。`<API名>.json`の1 API 1ファイルで管理し、ファイル名とJSON内の`name`を一致させます。

`scripts/deploy_gateway.py`は、paramsの`apiDefinitions: []`なら19ファイルをまとめて読み込みます。非空の配列なら、その配列に明記したAPIだけを対象にします。ディレクトリ内のJSONはAPIオブジェクトだけを持ち、`apiDefinitions`等の包みは付けません。

| ファイル | 主な機能 |
| --- | --- |
| [openai-inference-v1.json](openai-inference-v1.json) | Chat Completions・Responses |
| [openai-embeddings-v1.json](openai-embeddings-v1.json) | OpenAI埋め込み |
| [cohere-inference-v1.json](cohere-inference-v1.json) | Cohere埋め込み・リランキング |
| [openai-images-v1.json](openai-images-v1.json) | OpenAI画像生成・編集 |
| [blackforest-images-v1.json](blackforest-images-v1.json) | FLUX画像生成・編集 |
| [azure-speech-v1.json](azure-speech-v1.json) | 音声合成・音声認識 |
| [openai-audio-v1.json](openai-audio-v1.json) | 音声ファイルの文字起こし |
| [microsoft-decision-v1.json](microsoft-decision-v1.json) | Microsoft Decision |
| [openai-realtime-control-v1.json](openai-realtime-control-v1.json) | Realtimeの接続作成・終了 |
| [openai-realtime-ws-v1.json](openai-realtime-ws-v1.json) | RealtimeのWebSocket接続 |
| [openai-realtime-observer-v1.json](openai-realtime-observer-v1.json) | Realtime通話のサーバー側監視接続 |
| [azure-voice-live-ws-v1.json](azure-voice-live-ws-v1.json) | Voice LiveのWebSocket接続 |
| [azure-voice-live-webrtc-v1.json](azure-voice-live-webrtc-v1.json) | Voice LiveのWebRTC制御接続 |
| [openai-live-control-v1.json](openai-live-control-v1.json) | Liveのセッション作成 |
| [openai-live-ws-v1.json](openai-live-ws-v1.json) | LiveのWebSocket接続 |
| [openai-transcription-ws-v1.json](openai-transcription-ws-v1.json) | リアルタイム文字起こしのWebSocket接続 |
| [pii-v1.json](pii-v1.json) | 個人情報の検出・マスキング |
| [graph-profile-v1.json](graph-profile-v1.json) | CMの本人接続によるMicrosoft Graph /meの12属性取得 |
| [document-analysis-v1.json](document-analysis-v1.json) | PDF・画像の文字認識と文書レイアウト・表の解析 |

## 埋め込みAPIの入口

OpenAIの`POST /models/openai-embeddings/v1/embeddings`とCohereの`POST /models/cohere/v1/embed`は、単一の`Content-Type: application/json`を必要とします。`charset`等のMIMEパラメーターは利用できます。WAFの引数値除外は同名queryにも作用するため、APIMは本文を読む前にqueryを検査します。

OpenAIはqueryなし、または完全一致名の`api-version`を1個だけ指定した`v1`／`preview`を受け付け、元の値を上流へ転送します。Cohere Embedはqueryを受け付けず、既存の`/providers/cohere/v2/embed`へ転送します。同名の入力query、未知名、重複値、不正なAPI版は400です。同じAPIのCohere Rerankはこの入口制約の対象に含めません。[OpenAI Embeddings v1のAPI版](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/embeddings)

本文は既存のモデル・入力型・次元・受信byte制約を維持します。OpenAIの`input`は文字列、文字列配列、非負整数のtoken配列、token配列の配列を扱い、Cohereの`texts`は文字列配列を扱います。許可されたnative optionの扱いと、本文を変更せず転送する動作は維持します。

## 変更する場所

- APIの公開パス、操作、許可モデル、要求上限、下流接続、製品・購読との関連は、対象APIのJSONで変更します。
- `released: true`のAPIと生成MCPは、通常キーで使う`released-apis`製品へ関連付けます。省略・`false`は検証用製品のみで、標準19 APIはすべて`true`を明示しています。リリースを取り消す場合は`false`へ変更してGatewayを配備します。`productId`は既存の検証用製品を指定したままにします。
- モデルの配備名・版・SKU・容量は、`config/test.json`または`config/production.json`で管理します。APIファイルへモデルの配備設定を重複させません。
- CMプロバイダー、ログイン設定、環境固有のID・公開ホスト名はparamsで管理します。APIファイルには、そのAPIが必要とする本人接続の条件だけを持たせます。
- 共通ポリシーは[../policies/](../policies/)、APIMへ登録する入出力スキーマは[../schemas/](../schemas/)で管理します。

APIを追加する場合は、重複しない`name`で1ファイルを追加し、公開パス・バックエンド・認可条件・製品との関連をそろえます。APIを変更した後は、ローカルの登録計画を確認してからGateway配備を実行します。ファイルを編集・削除するだけで、既存Azureリソースが自動更新・削除されるわけではありません。

PIIから生成するMCPと、CMのサインイン・ブラウザー用の補助APIは登録処理が生成します。この19ファイルには含めません。
