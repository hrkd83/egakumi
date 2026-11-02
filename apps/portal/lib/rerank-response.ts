import { z } from "zod";
import { readEmbeddingBody } from "./embedding-response.ts";
import { ModelResponseError } from "./model-response.ts";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";
import { rerankResultSchema, type RerankInput, type RerankResult } from "./rerank-request.ts";
import type { RerankModel } from "./rerank-types.ts";

const responseSchema = z.object({
  results: z.array(z.object({ index: z.number().int().nonnegative().safe(), relevance_score: z.number().finite().min(0).max(1) })).min(1),
  meta: z.object({ billed_units: z.object({ search_units: z.number().int().nonnegative().safe().optional() }).optional() }).optional(),
});
const publicErrors = new Set<ModelGatewayErrorCode>(["InvalidRequest", "AuthenticationRequired", "RequestNotAllowed", "ModelUnavailable", "GatewayNotConfigured", "GatewayUnauthorized", "RateLimitExceeded", "GatewayTimeout", "GenerationFailed", "AuthenticationUnavailable", "RequestCancelled", "ApiKeyUnavailable", "ApiKeyAccessUnavailable"]);

/** 上流の順位を検証し、indexで元の文書を対応付けます。本文内容を推測して補完しません。 */
export function parseRerankResult(value: unknown, model: RerankModel, input: RerankInput, durationMs: number, requestId?: string): RerankResult {
  if (!value || typeof value !== "object" || Array.isArray(value) || ("error" in value && value.error != null)) throw new TypeError("Invalid rerank response");
  const data = responseSchema.parse(value);
  if (data.results.length !== input.topN || data.results.length > model.maxTopN || data.results.some((entry) => entry.index >= input.documents.length)) throw new TypeError("Unexpected rerank result count or index");
  return rerankResultSchema.parse({
    model: model.id,
    ranking: data.results.map((entry) => ({ index: entry.index, relevanceScore: entry.relevance_score, document: input.documents[entry.index] })),
    ...(data.meta?.billed_units?.search_units === undefined ? {} : { searchUnits: data.meta.billed_units.search_units }),
    durationMs, ...(requestId ? { requestId } : {}), rawResponse: value,
  });
}

/** BFFから返る固定エラーだけを公開し、上流の自由文を画面へ表示しません。 */
function publicFailure(value: unknown): TextGenerationFailure {
  const data = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const error = data.error && typeof data.error === "object" ? data.error as Record<string, unknown> : {};
  const code = typeof error.code === "string" && publicErrors.has(error.code as ModelGatewayErrorCode) ? error.code as ModelGatewayErrorCode : "GenerationFailed";
  return { error: { code }, ...(typeof data.requestId === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(data.requestId) ? { requestId: data.requestId } : {}), ...(Number.isInteger(data.retryAfterSeconds) && Number(data.retryAfterSeconds) > 0 && Number(data.retryAfterSeconds) <= 3_600 ? { retryAfterSeconds: Number(data.retryAfterSeconds) } : {}) };
}

/** ブラウザーでも応答のサイズ・形式・順位を確認してから表示します。 */
export async function readRerankResponse(response: Response, signal?: AbortSignal): Promise<RerankResult> {
  try {
    if (response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") throw new TypeError("Invalid content type");
    const value = JSON.parse(await readEmbeddingBody(response.body, 1_048_576, signal));
    if (!response.ok) throw new ModelResponseError(publicFailure(value));
    return rerankResultSchema.parse(value);
  } catch (error) {
    if (error instanceof ModelResponseError) throw error;
    throw new ModelResponseError({ error: { code: "GenerationFailed" } });
  }
}
