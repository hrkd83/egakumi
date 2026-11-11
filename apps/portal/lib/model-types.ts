/** 実際のModel Gateway APIを、利用者が選ぶ機能ごとに分類する識別子です。 */
export type ModelCategoryId = "text" | "embeddings" | "image" | "speech" | "realtime" | "rerank" | "decision";

/** 表示名の変更で提供元ごとの画面グループが変わらないための識別子です。 */
export type TextModelProviderId = "openai" | "deepseek" | "mistral" | "xai" | "azure-ai-foundry";

/** テキスト以外のAPIも含め、提供元のカードを安定して識別する値です。 */
export type ModelProviderId = TextModelProviderId | "cohere" | "black-forest-labs" | "azure-ai-speech" | "microsoft";

/** API定義から取り出した、公開してよい操作とモデル名だけの情報です。 */
export type ModelOperation = {
  apiId: string;
  operationId: string;
  name: string;
  providerId: ModelProviderId;
  provider: string;
  method: string;
  path: string;
  models: string[];
};

/** タブ内の操作一覧です。APIキーやバックエンド接続先は含みません。 */
export type ModelCategory = {
  id: ModelCategoryId;
  apiIds: string[];
  operations: ModelOperation[];
};

/** テキスト生成に使用するモデルと、そのAPIが要求する入力制約です。 */
export type TextGenerationModel = {
  id: string;
  label: string;
  provider: string;
  providerId: TextModelProviderId;
  apiId: string;
  operationId: "chat-completions" | "responses-create";
  path: string;
  profileId: string;
  maxRequestBytes: number;
  maxOutputTokens: number | null;
  minOutputTokens?: number;
  outputTokenLimitKind?: "documented" | "dynamic" | "unsupported" | "unverified" | "configured";
  outputTokenParameter: "max_completion_tokens" | "max_tokens" | "max_output_tokens";
};

/** 埋め込みの実行先と、API正本が許可する入力の範囲です。 */
export type EmbeddingModel = {
  id: string;
  label: string;
  providerId: "openai" | "cohere";
  provider: string;
  apiId: "openai-embeddings-v1" | "cohere-inference-v1";
  operationId: "embeddings" | "cohere-embeddings";
  path: string;
  maxRequestBytes: number;
  maxInputs: number;
  inputTypes: string[];
  embeddingDimensions: { default: number; maximum: number; allowed?: number[] };
};

/** 画像生成で選べる設定です。編集用操作は生成モデルの一覧へ含めません。 */
export type ImageGenerationModel = {
  id: string;
  label: string;
  providerId: "openai" | "black-forest-labs";
  provider: string;
  apiId: "openai-images-v1" | "blackforest-images-v1";
  operationId: "image-generate" | "flux2-generate";
  path: string;
  profileId?: string;
  maxRequestBytes: number;
  sizes: string[];
  qualities: string[];
  formats: ("png" | "jpeg" | "webp")[];
  minImages?: number;
  maxImages?: number;
  minWidth: number;
  maxWidth: number;
  minHeight: number;
  maxHeight: number;
};

/** 音声ファイルの操作です。音声モデルを持たないAzure Speechも、実際の操作単位で選択します。 */
export type SpeechModel = {
  id: string;
  label: string;
  providerId: "azure-ai-speech" | "openai";
  provider: string;
  apiId: "azure-speech-v1" | "openai-audio-v1";
  operationId: "tts" | "stt" | "audio-transcriptions";
  kind: "tts" | "stt";
  path: string;
  maxRequestBytes: number;
  modelId?: string;
  language: "ja-JP";
};

/** リアルタイム音声のモデルと接続方式です。開始・観測・終了のAPIを取り違えないように束縛します。 */
export type RealtimeModel = {
  id: string;
  label: string;
  providerId: "openai" | "azure-ai-speech";
  provider: string;
  family: "realtime" | "live" | "transcription" | "voice-live";
  transport: "websocket" | "webrtc";
  apiId: string;
  operationId: string;
  path: string;
  modelId: string;
  voice: string;
  /** 同じ会話音声の入力字幕に使う、カタログで束縛された文字起こしモデルです。 */
  inputTranscriptionModel?: string;
  maxRequestBytes: number;
  maxOutputTokens: number | null;
  outputTokenLimitKind?: "documented" | "dynamic" | "unsupported" | "unverified" | "configured";
  numericMaxOutputTokens?: number;
  supportsMaximumOutput?: boolean;
  observer?: { apiId: string; path: string };
  hangupPath?: string;
};
