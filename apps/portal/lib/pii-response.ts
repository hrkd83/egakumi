import { readEmbeddingBody } from "./embedding-response.ts";
import { ModelResponseError } from "./model-response.ts";
import type { ModelGatewayErrorCode, TextGenerationFailure } from "./model-request.ts";
import { piiResultSchema, type PiiResult } from "./pii-contract.ts";

const errorCodes = new Set<ModelGatewayErrorCode>(["InvalidRequest", "AuthenticationRequired", "RequestNotAllowed", "ModelUnavailable", "GatewayNotConfigured", "GatewayUnauthorized", "RateLimitExceeded", "GatewayTimeout", "GenerationFailed", "AuthenticationUnavailable", "RequestCancelled", "ApiKeyUnavailable", "ApiKeyAccessUnavailable"]);

/** 固定エラー以外を破棄し、元テキストや上流の診断文を表示しません。 */
function failure(value: unknown): TextGenerationFailure {
  const data = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const error = data.error && typeof data.error === "object" ? data.error as Record<string, unknown> : {};
  const code = typeof error.code === "string" && errorCodes.has(error.code as ModelGatewayErrorCode) ? error.code as ModelGatewayErrorCode : "GenerationFailed";
  return { error: { code }, ...(typeof data.requestId === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(data.requestId) ? { requestId: data.requestId } : {}), ...(Number.isInteger(data.retryAfterSeconds) && Number(data.retryAfterSeconds) > 0 && Number(data.retryAfterSeconds) <= 3600 ? { retryAfterSeconds: Number(data.retryAfterSeconds) } : {}) };
}

/** サイズと固定形式を検証し、マスキング済みの結果だけを画面へ返します。 */
export async function readPiiResponse(response: Response, signal?: AbortSignal): Promise<PiiResult> {
  try {
    if (response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") throw new TypeError("Invalid response type");
    const value = JSON.parse(await readEmbeddingBody(response.body, 131_072, signal));
    if (!response.ok) throw new ModelResponseError(failure(value));
    return piiResultSchema.parse(value);
  } catch (error) { if (error instanceof ModelResponseError) throw error; throw new ModelResponseError({ error: { code: "GenerationFailed" } }); }
}
