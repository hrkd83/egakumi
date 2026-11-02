import { z } from "zod";
import { embeddingResultSchema, type EmbeddingInput, type EmbeddingResult } from "./embedding-request.ts";
import { getEmbeddingDefaultDimensions } from "./embedding-integration.ts";
import { ModelResponseError } from "./model-response.ts";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";
import type { EmbeddingModel } from "./model-types.ts";

const maximumResponseBytes = 2_097_152;
const tokenCount = z.number().int().nonnegative().safe();
const vector = z.array(z.number().finite()).min(1).max(3_072);
const openaiResponse = z.object({
  model: z.string().min(1).max(200),
  data: z.array(z.object({ index: z.number().int().nonnegative(), embedding: vector })).min(1).max(16),
  usage: z.object({ prompt_tokens: tokenCount, total_tokens: tokenCount }).optional(),
});
const cohereResponse = z.object({
  embeddings: z.object({ float: z.array(vector).min(1).max(16) }),
  meta: z.object({ billed_units: z.object({ input_tokens: tokenCount }).optional() }).optional(),
});
const publicErrors = new Set<ModelGatewayErrorCode>(["InvalidRequest", "AuthenticationRequired", "RequestNotAllowed", "ModelUnavailable", "GatewayNotConfigured", "GatewayUnauthorized", "RateLimitExceeded", "GatewayTimeout", "GenerationFailed", "AuthenticationUnavailable", "RequestCancelled", "ApiKeyUnavailable", "ApiKeyAccessUnavailable"]);

/** 上流JSONを検証し、順序・件数・次元が要求と一致した実ベクトルだけを成功にします。 */
export function parseEmbeddingResult(value: unknown, model: EmbeddingModel, input: EmbeddingInput, durationMs: number, requestId?: string): EmbeddingResult {
  if (!value || typeof value !== "object" || Array.isArray(value) || ("error" in value && value.error !== null && value.error !== undefined)) throw new Error("Invalid embedding response");
  let vectors: number[][];
  let usage: EmbeddingResult["usage"];
  let returnedModel = model.id;
  if (model.providerId === "cohere") {
    const data = cohereResponse.parse(value);
    vectors = data.embeddings.float;
    const units = data.meta?.billed_units;
    if (units) usage = { inputTokens: units.input_tokens };
  } else {
    const data = openaiResponse.parse(value);
    const ordered = [...data.data].sort((left, right) => left.index - right.index);
    if (ordered.some((item, index) => item.index !== index)) throw new Error("Invalid embedding indexes");
    vectors = ordered.map((item) => item.embedding);
    returnedModel = data.model;
    if (data.usage) usage = { inputTokens: data.usage.prompt_tokens, totalTokens: data.usage.total_tokens };
  }
  const dimensions = input.dimensions ?? getEmbeddingDefaultDimensions(model);
  if (vectors.length !== input.texts.length || vectors.some((item) => item.length !== dimensions)) throw new Error("Unexpected embedding shape");
  return embeddingResultSchema.parse({ model: returnedModel, vectorCount: vectors.length, dimensions, vectors, durationMs, rawResponse: value, ...(usage ? { usage } : {}), ...(requestId ? { requestId } : {}) });
}

/** 本文の受信途中も中止・期限を監視し、Content-Lengthに依存せずUTF-8のバイト上限を守ります。 */
export async function readEmbeddingBody(body: ReadableStream<Uint8Array> | null, maximum: number, signal?: AbortSignal): Promise<string> {
  if (!body) throw new Error("Missing embedding body");
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  let listener: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    if (!signal) return;
    listener = () => reject(new Error("Embedding body aborted"));
    if (signal.aborted) listener(); else signal.addEventListener("abort", listener, { once: true });
  });
  try {
    while (true) {
      const part = await Promise.race([reader.read(), aborted]);
      if (part.done) break;
      length += part.value.byteLength;
      if (length > maximum) throw new Error("Embedding body exceeds limit");
      chunks.push(part.value);
    }
  } finally {
    if (listener) signal?.removeEventListener("abort", listener);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

/** BFFの固定された失敗種別だけを採用し、上流のエラーメッセージを画面へ出しません。 */
function publicFailure(value: unknown): TextGenerationFailure {
  const data = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const error = data.error && typeof data.error === "object" ? data.error as Record<string, unknown> : {};
  const code = typeof error.code === "string" && publicErrors.has(error.code as ModelGatewayErrorCode) ? error.code as ModelGatewayErrorCode : "GenerationFailed";
  return { error: { code }, ...(typeof data.requestId === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(data.requestId) ? { requestId: data.requestId } : {}), ...(Number.isInteger(data.retryAfterSeconds) && Number(data.retryAfterSeconds) > 0 && Number(data.retryAfterSeconds) <= 3_600 ? { retryAfterSeconds: Number(data.retryAfterSeconds) } : {}) };
}

/** ブラウザーでもBFFの形とサイズを確認し、壊れた応答を埋め込み成功として扱いません。 */
export async function readEmbeddingResponse(response: Response, signal?: AbortSignal): Promise<EmbeddingResult> {
  try {
    if (response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") throw new Error("Invalid embedding content type");
    const value = JSON.parse(await readEmbeddingBody(response.body, maximumResponseBytes, signal));
    if (!response.ok) throw new ModelResponseError(publicFailure(value));
    return embeddingResultSchema.parse(value);
  } catch (error) {
    if (error instanceof ModelResponseError) throw error;
    throw new ModelResponseError({ error: { code: "GenerationFailed" } });
  }
}
