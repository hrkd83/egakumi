import "server-only";
import { MAX_RUNTIME_ARRAY_LENGTH } from "./model-runtime-limits.ts";
import { z } from "zod";
import snapshot from "./generated/model-api-definitions.json" with { type: "json" };
import type { EmbeddingModel, ImageGenerationModel, ModelCategory, ModelCategoryId, ModelOperation, ModelProviderId, RealtimeModel, SpeechModel, TextGenerationModel, TextModelProviderId } from "./model-types.ts";
import type { RerankModel } from "./rerank-types.ts";
import type { DecisionModel } from "./decision-types.ts";

const outputTokenParameterSchema = z.enum(["max_completion_tokens", "max_tokens", "max_output_tokens"]);
const integerLimitSchema = z.object({ minimum: z.number().int(), maximum: z.number().int(), required: z.boolean().optional() });
const outputLimitFields = {
  maxOutputTokens: z.number().int().positive().nullable().optional(),
  maxOutputTokensByModel: z.record(z.string(), z.number().int().positive().nullable()).optional(),
  outputTokenLimitKinds: z.record(z.string(), z.enum(["documented", "dynamic", "unsupported", "unverified"])).optional(),
};
const operationLimits = {
  arrayLimits: z.record(z.string(), z.number().int().positive()).optional(),
  integerLimits: z.record(z.string(), integerLimitSchema).optional(),
  stringChoices: z.record(z.string(), z.array(z.string()).min(1)).optional(),
};
const profileSchema = z.object({
  profileId: z.string(),
  allowedModels: z.array(z.string()).optional(),
  modalities: z.array(z.string()).optional(),
  maxRequestBytes: z.number().int().positive().optional(),
  ...outputLimitFields,
  outputTokenParameter: outputTokenParameterSchema.optional(),
  ...operationLimits,
});
/** 次元の既定値・上限・列挙をAPI正本から読み、モデル名による推測をしません。 */
const embeddingDimensionsSchema = z.object({
  default: z.number().int().positive(),
  maximum: z.number().int().positive(),
  allowed: z.array(z.number().int().positive()).min(1).optional(),
}).strict().refine((value) => value.default <= value.maximum && (!value.allowed ||
  (new Set(value.allowed).size === value.allowed.length && value.allowed.includes(value.default) &&
   value.allowed.includes(value.maximum) && value.allowed.every((item) => item <= value.maximum))));

const constraintSchema = z.object({
  kind: z.string().optional(),
  embeddingDimensionsByModel: z.record(z.string(), embeddingDimensionsSchema).optional(),
  bodyFormat: z.string().optional(),
  contentTypes: z.array(z.string()).optional(),
  allowedModels: z.array(z.string()).optional(),
  model: z.string().optional(),
  defaultProfile: z.string().optional(),
  profiles: z.array(profileSchema).optional(),
  maxRequestBytes: z.number().int().positive().optional(),
  ...outputLimitFields,
  outputTokenParameter: outputTokenParameterSchema.optional(),
  ...operationLimits,
});
const definitionSchema = z.object({
  name: z.string(),
  path: z.string(),
  operations: z.array(z.object({
    name: z.string(), displayName: z.string(), method: z.string(), urlTemplate: z.string(),
    backendUrlTemplate: z.string().optional(),
  })),
  authorization: z.object({ operationConstraints: z.record(z.string(), constraintSchema) }),
  websocket: z.object({
    allowedModels: z.array(z.string()).optional(),
    session: z.object({ models: z.array(z.string()) }).optional(),
    queryParameters: z.record(z.string(), z.string()).optional(),
  }).optional(),
});

/** 正本から自動生成したビルド用データだけを読みます。許可モデルは最新世代を先頭にする公開順を維持します。 */
const definitions = z.object({ schemaVersion: z.literal(1), definitions: z.array(definitionSchema).min(1) }).parse(snapshot).definitions;
const capabilities = z.object({ schemaVersion: z.literal(1), entries: z.array(z.object({
  apiIds: z.array(z.string()), operationIds: z.array(z.string()), modelId: z.string(),
  maxOutputTokens: z.number().int().positive().nullable(), numericMaxOutputTokens: z.number().int().positive().optional(), supportsMaximumOutput: z.boolean().optional(),
})) }).optional().parse((snapshot as Record<string, unknown>).modelCapabilities);

/** nullも有効な「固定上限なし」の値として保持し、外側の数値へ誤って戻しません。 */
function outputLimitFor(constraint: z.infer<typeof constraintSchema> | z.infer<typeof profileSchema>, modelId: string): number | null | undefined {
  if (constraint.maxOutputTokensByModel) {
    if (!Object.hasOwn(constraint.maxOutputTokensByModel, modelId)) throw new Error("Model output limit is missing");
    return constraint.maxOutputTokensByModel[modelId];
  }
  return constraint.maxOutputTokens;
}

/** JSONで宣言されたAPIの機能をタブへ割り当てます。Cohereだけ操作単位で分けます。 */
function categoryFor(apiId: string, operationId: string): ModelCategoryId {
  if (apiId === "openai-inference-v1") return "text";
  if (apiId === "openai-embeddings-v1") return "embeddings";
  if (apiId === "cohere-inference-v1") return operationId === "rerank" ? "rerank" : "embeddings";
  if (apiId === "openai-images-v1" || apiId === "blackforest-images-v1") return "image";
  if (apiId === "azure-speech-v1" || apiId === "openai-audio-v1") return "speech";
  if (apiId === "microsoft-decision-v1") return "decision";
  return "realtime";
}

/** API定義がモデル名として公開している値を集め、未解決の配備用変数は除きます。 */
function operationModels(definition: z.infer<typeof definitionSchema>, operationId: string): string[] {
  const constraint = definition.authorization.operationConstraints[operationId];
  const values = [
    ...(constraint?.allowedModels ?? []), ...(constraint?.model ? [constraint.model] : []),
    ...(definition.websocket?.allowedModels ?? []), ...(definition.websocket?.session?.models ?? []),
    ...(definition.websocket?.queryParameters?.model ? [definition.websocket.queryParameters.model] : []),
  ];
  return [...new Set(values.filter((model) => !model.startsWith("__")))];
}

/** APIが提供するサービスの供給元を特定し、各タブで同じ提供元の操作をまとめます。 */
function apiProviderFor(apiId: string): { providerId: ModelProviderId; provider: string } {
  if (apiId === "cohere-inference-v1") return { providerId: "cohere", provider: "Cohere" };
  if (apiId === "blackforest-images-v1") return { providerId: "black-forest-labs", provider: "Black Forest Labs" };
  if (apiId === "azure-speech-v1" || apiId.startsWith("azure-voice-live-")) {
    return { providerId: "azure-ai-speech", provider: "Azure AI Speech" };
  }
  if (apiId === "microsoft-decision-v1") return { providerId: "microsoft", provider: "Microsoft" };
  return { providerId: "openai", provider: "OpenAI" };
}

/**
 * 公開用の操作一覧を作ります。音声ファイルのSTT/TTSと、継続接続するリアルタイムAPIを分離します。
 * @returns バックエンド情報や秘密値を含まない、画面へ渡せる7種類のタブ情報。
 */
export function getModelCatalog(): ModelCategory[] {
  const ids: ModelCategoryId[] = ["text", "embeddings", "image", "speech", "realtime", "rerank", "decision"];
  const categories = ids.map((id): ModelCategory => ({ id, apiIds: [], operations: [] }));
  for (const definition of definitions) {
    for (const operation of definition.operations) {
      const category = categories.find((item) => item.id === categoryFor(definition.name, operation.name))!;
      const item: ModelOperation = {
        apiId: definition.name, operationId: operation.name, name: operation.displayName,
        ...apiProviderFor(definition.name),
        method: operation.method, path: `/${definition.path}${operation.urlTemplate}`,
        models: operationModels(definition, operation.name),
      };
      category.operations.push(item);
      if (!category.apiIds.includes(definition.name)) category.apiIds.push(definition.name);
    }
  }
  return categories;
}

/** モデルIDの供給元を表示します。動作特性や配備状態を推測して付け加えません。 */
function providerFor(model: string): { providerId: TextModelProviderId; provider: string } {
  if (model.startsWith("DeepSeek")) return { providerId: "deepseek", provider: "DeepSeek" };
  if (model.startsWith("Mistral")) return { providerId: "mistral", provider: "Mistral AI" };
  if (model.startsWith("grok")) return { providerId: "xai", provider: "xAI" };
  if (model === "model-router") return { providerId: "azure-ai-foundry", provider: "Azure AI Foundry" };
  return { providerId: "openai", provider: "OpenAI" };
}

/**
 * テキスト生成モデルを、API側のプロファイルとトークン上限に結び付けます。
 * @remarks 両形式を使えるモデルは、同じ実モデル名のまま操作ごとに一覧へ含めます。
 * 選択時はモデル名とoperationIdの組合せで識別し、片方のAPIへ暗黙に置き換えません。
 * 画像・PDF用プロファイルは選ばないため、テキスト画面から不適切な制約で実行しません。
 */
export function getTextGenerationModels(): TextGenerationModel[] {
  const definition = definitions.find((item) => item.name === "openai-inference-v1")!;
  const models: TextGenerationModel[] = [];
  for (const operationId of ["chat-completions", "responses-create"] as const) {
    const operation = definition.operations.find((item) => item.name === operationId);
    const constraint = definition.authorization.operationConstraints[operationId];
    if (!operation || !constraint) throw new Error("Text generation API definition is incomplete");
    for (const id of constraint.allowedModels ?? []) {
      const textProfiles = (constraint.profiles ?? []).filter((profile) =>
        profile.allowedModels?.includes(id) &&
        profile.modalities?.includes("text") && profile.modalities.every((modality) => modality === "text"));
      const profile = textProfiles.find((item) => item.profileId === constraint.defaultProfile) ?? textProfiles[0];
      const maxRequestBytes = profile?.maxRequestBytes ?? constraint.maxRequestBytes;
      const maxOutputTokens = outputLimitFor(profile ?? constraint, id);
      const outputTokenParameter = profile?.outputTokenParameter ?? constraint.outputTokenParameter;
      if (!profile || !maxRequestBytes || maxOutputTokens === undefined || !outputTokenParameter) {
        throw new Error("Text generation model has no complete text profile");
      }
      models.push({
        id, label: id, ...providerFor(id), apiId: definition.name, operationId,
        path: `/${definition.path}${operation.urlTemplate}`, profileId: profile.profileId,
        maxRequestBytes, maxOutputTokens, minOutputTokens: (profile.integerLimits ?? constraint.integerLimits)?.[outputTokenParameter]?.minimum ?? (operationId === "responses-create" ? 16 : 1),
        outputTokenParameter, outputTokenLimitKind: profile.outputTokenLimitKinds?.[id] ?? "configured",
      });
    }
  }
  return models;
}

/** 埋め込み用のAPIとモデルを正本から取得し、Cohereの入力用途もそのまま引き継ぎます。 */
export function getEmbeddingModels(): EmbeddingModel[] {
  const models: EmbeddingModel[] = [];
  for (const [apiId, operationId, providerId, provider] of [
    ["openai-embeddings-v1", "embeddings", "openai", "OpenAI"],
    ["cohere-inference-v1", "cohere-embeddings", "cohere", "Cohere"],
  ] as const) {
    const definition = definitions.find((item) => item.name === apiId);
    const operation = definition?.operations.find((item) => item.name === operationId);
    const constraint = definition?.authorization.operationConstraints[operationId];
    const maxInputs = constraint?.arrayLimits?.[providerId === "openai" ? "input" : "texts"] ?? MAX_RUNTIME_ARRAY_LENGTH;
    if (!definition || !operation || !constraint?.maxRequestBytes || !maxInputs || constraint.kind !== "textEmbeddings" || !constraint.embeddingDimensionsByModel) throw new Error("Embedding API definition is incomplete");
    for (const id of constraint.allowedModels ?? []) {
      const embeddingDimensions = constraint.embeddingDimensionsByModel[id];
      if (!embeddingDimensions || (providerId === "cohere" && !embeddingDimensions.allowed)) throw new Error("Embedding dimension contract is missing");
      models.push({ id, label: id, providerId, provider, apiId, operationId,
        path: `/${definition.path}${operation.urlTemplate}`, maxRequestBytes: constraint.maxRequestBytes,
        maxInputs, inputTypes: constraint.stringChoices?.input_type ?? [],
        embeddingDimensions: { ...embeddingDimensions, ...(embeddingDimensions.allowed ? { allowed: [...embeddingDimensions.allowed] } : {}) } });
    }
  }
  return models;
}

/** リランクの公開モデルと文書数・返却件数の制約をAPI定義から取得します。 */
export function getRerankModels(): RerankModel[] {
  const definition = definitions.find((item) => item.name === "cohere-inference-v1");
  const operation = definition?.operations.find((item) => item.name === "rerank");
  const constraint = definition?.authorization.operationConstraints.rerank;
  const topN = constraint?.integerLimits?.top_n ?? { minimum: 1, maximum: Number.MAX_SAFE_INTEGER };
  const maxDocuments = constraint?.arrayLimits?.documents ?? MAX_RUNTIME_ARRAY_LENGTH;
  if (!definition || !operation || !constraint?.maxRequestBytes || !maxDocuments || !topN || topN.minimum < 1 || topN.maximum < topN.minimum) throw new Error("Rerank API definition is incomplete");
  return (constraint.allowedModels ?? []).map((id) => ({
    id, label: id, providerId: "cohere", provider: "Cohere", apiId: "cohere-inference-v1", operationId: "rerank",
    path: `/${definition.path}${operation.urlTemplate}`, maxRequestBytes: constraint.maxRequestBytes!,
    maxDocuments, minTopN: topN.minimum, maxTopN: topN.maximum,
  }));
}

/** 意思決定の公開配備名と本文上限を取得し、上流応答の基礎モデル名と区別します。 */
export function getDecisionModels(): DecisionModel[] {
  const definition = definitions.find((item) => item.name === "microsoft-decision-v1");
  const operation = definition?.operations.find((item) => item.name === "systemone");
  const constraint = definition?.authorization.operationConstraints.systemone;
  if (!definition || !operation || !constraint?.maxRequestBytes) throw new Error("Decision API definition is incomplete");
  return (constraint.allowedModels ?? []).map((id) => ({
    id, label: id, providerId: "microsoft", provider: "Microsoft", apiId: "microsoft-decision-v1", operationId: "systemone",
    path: `/${definition.path}${operation.urlTemplate}`, maxRequestBytes: constraint.maxRequestBytes!, responseModel: "Microsoft-Decision-1",
  }));
}

/** 画像生成のサイズ・品質・画像形式を、モデルに適用されるプロファイルから取得します。 */
export function getImageGenerationModels(): ImageGenerationModel[] {
  const models: ImageGenerationModel[] = [];
  for (const [apiId, operationId, providerId, provider] of [
    ["openai-images-v1", "image-generate", "openai", "OpenAI"],
    ["blackforest-images-v1", "flux2-generate", "black-forest-labs", "Black Forest Labs"],
  ] as const) {
    const definition = definitions.find((item) => item.name === apiId);
    const operation = definition?.operations.find((item) => item.name === operationId);
    const constraint = definition?.authorization.operationConstraints[operationId];
    if (!definition || !operation || !constraint?.maxRequestBytes) throw new Error("Image generation API definition is incomplete");
    for (const id of constraint.allowedModels ?? []) {
      const profile = constraint.profiles?.find((item) => item.allowedModels?.includes(id) && item.modalities?.includes("text") && item.modalities.every((modality) => modality === "text"));
      if (providerId === "black-forest-labs" && !profile) throw new Error("Image generation model has no text profile");
      const choices = profile?.stringChoices ?? constraint.stringChoices;
      const limits = profile?.integerLimits ?? constraint.integerLimits;
      const width = limits?.width;
      const height = limits?.height;
      const formats = (choices?.output_format ?? (providerId === "openai" ? ["png", "jpeg", "webp"] : ["png", "jpeg"])).filter((format): format is "png" | "jpeg" | "webp" => format === "png" || format === "jpeg" || format === "webp");
      models.push({ id, label: id, providerId, provider, apiId, operationId,
        path: `/${definition.path}${operation.urlTemplate}`,
        ...(profile ? { profileId: profile.profileId } : {}),
        maxRequestBytes: profile?.maxRequestBytes ?? constraint.maxRequestBytes,
        sizes: choices?.size ?? (providerId === "openai" ? ["1024x1024", "1536x1024", "1024x1536", "auto"] : []),
        qualities: choices?.quality ?? (providerId === "openai" ? (id.startsWith("gpt-image-2.5") ? ["auto", "low", "medium", "high", "xhigh", "max"] : ["low", "medium", "high"]) : []), formats,
        minImages: limits?.[providerId === "openai" ? "n" : "num_images"]?.minimum ?? 1,
        maxImages: limits?.[providerId === "openai" ? "n" : "num_images"]?.maximum ?? (providerId === "openai" ? 10 : 1),
        minWidth: width?.minimum ?? 1, maxWidth: width?.maximum ?? Number.MAX_SAFE_INTEGER,
        minHeight: height?.minimum ?? 1, maxHeight: height?.maximum ?? Number.MAX_SAFE_INTEGER });
    }
  }
  return models;
}

/** キーの選択画面が認可を照会できる、実装済みのモデルAPIだけを返します。 */
export function getExecutableModelTargets(): { apiId: string }[] {
  return [...new Set([...getTextGenerationModels(), ...getEmbeddingModels(), ...getImageGenerationModels(), ...getSpeechModels(), ...getRealtimeModels(), ...getRerankModels(), ...getDecisionModels()].map((model) => model.apiId))].map((apiId) => ({ apiId }));
}

/**
 * 公開済みのSTT/TTSを、固定された言語・形式・本文上限に結び付けます。
 * @remarks Azureの認識言語はAPI定義から読み取り、接続先や認証設定はブラウザーへ渡しません。
 */
export function getSpeechModels(): SpeechModel[] {
  const speech = definitions.find((item) => item.name === "azure-speech-v1");
  const recognition = speech?.operations.find((item) => item.name === "stt");
  const language = recognition?.backendUrlTemplate ? new URL(recognition.backendUrlTemplate, "https://definition.invalid").searchParams.get("language") : null;
  if (!speech || language !== "ja-JP") throw new Error("Speech language is not supported by the fixed sample");
  const models: SpeechModel[] = [];
  for (const operationId of ["stt", "tts"] as const) {
    const operation = speech.operations.find((item) => item.name === operationId);
    const constraint = speech.authorization.operationConstraints[operationId];
    const mime = operationId === "tts" ? "application/ssml+xml" : "audio/wav";
    if (!operation || operation.method !== "POST" || constraint?.kind !== "registeredOperation" ||
      constraint.bodyFormat !== (operationId === "tts" ? "text" : "binary") || !constraint.contentTypes?.includes(mime) || !constraint.maxRequestBytes) {
      throw new Error("Azure Speech API definition is incomplete");
    }
    models.push({ id: `azure-speech-${operationId}`, label: operationId === "tts" ? "Text to speech (TTS)" : "Speech to text (STT)",
      providerId: "azure-ai-speech", provider: "Azure AI Speech", apiId: "azure-speech-v1", operationId, kind: operationId,
      path: `/${speech.path}${operation.urlTemplate}`, maxRequestBytes: constraint.maxRequestBytes, language });
  }
  const openai = definitions.find((item) => item.name === "openai-audio-v1");
  const operation = openai?.operations.find((item) => item.name === "audio-transcriptions");
  const constraint = openai?.authorization.operationConstraints["audio-transcriptions"];
  if (!openai || !operation || operation.method !== "POST" || constraint?.kind !== "pathModel" || constraint.bodyFormat !== "multipart" ||
    !constraint.contentTypes?.includes("multipart/form-data") || !constraint.maxRequestBytes || !constraint.allowedModels?.length ||
    !operation.urlTemplate.includes("{deployment}")) throw new Error("Audio transcription API definition is incomplete");
  for (const modelId of constraint.allowedModels) {
    if (!/^[a-zA-Z0-9._-]{1,160}$/.test(modelId)) throw new Error("Audio model path is invalid");
    models.push({ id: modelId, label: modelId, providerId: "openai", provider: "OpenAI", apiId: "openai-audio-v1",
      operationId: "audio-transcriptions", kind: "stt", path: `/${openai.path}${operation.urlTemplate.replace("{deployment}", encodeURIComponent(modelId))}`,
      maxRequestBytes: constraint.maxRequestBytes, modelId, language });
  }
  return models;
}

/**
 * リアルタイム音声の公開契約を、モデル・方式ごとの実行先へ整理します。
 * @remarks 観測APIは通話を開始するモデルとして表示せず、WebRTCの認可対象に結び付けます。
 * 対応するシリーズはWebRTCを先頭に置き、初回の接続方式として選びます。
 * WebSocketの出力上限はPortalの短い実行確認用です。APIMが接続後のフレームを検査するという意味ではありません。
 */
export function getRealtimeModels(): RealtimeModel[] {
  const models: RealtimeModel[] = [];
  // 既存の配備済み登録を使い、文字起こしモデル名を会話用コードへ埋め込みません。
  const transcriptionModels = definitions.find((item) => item.name === "openai-transcription-ws-v1")?.websocket?.session?.models;
  if (!transcriptionModels || transcriptionModels.length !== 1 || !/^[a-zA-Z0-9._-]{1,120}$/.test(transcriptionModels[0]) || transcriptionModels[0].startsWith("__")) throw new Error("Realtime transcription model binding is incomplete");
  const inputTranscriptionModel = transcriptionModels[0];
  const registrations = [
    ["openai-realtime-control-v1", "realtime-create", "realtime", "webrtc"],
    ["openai-realtime-ws-v1", "invoke", "realtime", "websocket"],
    ["openai-live-control-v1", "live-create", "live", "webrtc"],
    ["openai-live-ws-v1", "invoke", "live", "websocket"],
    ["openai-transcription-ws-v1", "invoke", "transcription", "websocket"],
    ["azure-voice-live-webrtc-v1", "invoke", "voice-live", "webrtc"],
    ["azure-voice-live-ws-v1", "invoke", "voice-live", "websocket"],
  ] as const;
  for (const [apiId, operationId, family, transport] of registrations) {
    const definition = definitions.find((item) => item.name === apiId);
    const operation = definition?.operations.find((item) => item.name === operationId);
    const constraint = definition?.authorization.operationConstraints[operationId];
    if (!definition || !operation || !constraint?.maxRequestBytes || operation.method !== (transport === "websocket" || family === "voice-live" ? "GET" : "POST")) {
      throw new Error("Realtime API definition is incomplete");
    }
    const ids = definition.websocket?.allowedModels ?? definition.websocket?.session?.models ??
      (definition.websocket?.queryParameters?.model ? [definition.websocket.queryParameters.model] : constraint.model ? [constraint.model] : []);
    if (!ids.length || ids.some((id) => !/^[a-zA-Z0-9._-]{1,120}$/.test(id) || id.startsWith("__"))) throw new Error("Realtime model binding is incomplete");
    const observer = definitions.find((item) => item.name === "openai-realtime-observer-v1");
    const observerOperation = observer?.operations.find((item) => item.name === "invoke");
    const hangup = definition.operations.find((item) => item.name === "realtime-hangup");
    if (family === "realtime" && transport === "webrtc" && (!observer || !observerOperation || !hangup)) throw new Error("Realtime WebRTC cleanup definitions are incomplete");
    for (const modelId of ids) {
      const maxOutputTokens = outputLimitFor(constraint, modelId);
      if (maxOutputTokens === undefined) throw new Error("Realtime model output capability is missing");
      const capability = capabilities?.entries.find((entry) => entry.modelId === modelId && entry.apiIds.includes(apiId) && entry.operationIds.includes(operationId));
      models.push({
      id: `${apiId}:${modelId}`, label: modelId, modelId, family, transport, apiId, operationId,
      providerId: family === "voice-live" ? "azure-ai-speech" : "openai", provider: family === "voice-live" ? "Azure AI Speech" : "OpenAI",
      path: `/${definition.path}${operation.urlTemplate}`, voice: family === "voice-live" ? "ja-JP-NanamiNeural" : "marin",
      ...(family === "realtime" ? { inputTranscriptionModel } : {}),
      maxRequestBytes: constraint.maxRequestBytes, maxOutputTokens, outputTokenLimitKind: constraint.outputTokenLimitKinds?.[modelId] ?? "configured",
      ...(capability?.numericMaxOutputTokens ? { numericMaxOutputTokens: capability.numericMaxOutputTokens } : {}),
      ...(capability?.supportsMaximumOutput !== undefined ? { supportsMaximumOutput: capability.supportsMaximumOutput } : {}),
      ...(family === "realtime" && transport === "webrtc" ? {
        observer: { apiId: observer!.name, path: `/${observer!.path}${observerOperation!.urlTemplate}` },
        hangupPath: `/${definition.path}${hangup!.urlTemplate}`,
      } : {}),
      });
    }
  }
  return models;
}
