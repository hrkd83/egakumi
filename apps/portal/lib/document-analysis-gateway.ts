import "server-only";
import { containsModelCredential } from "./model-credential.ts";
import { ApiKeyError, type ApiKeyActor } from "./api-keys/contracts.ts";
import { getApiKeyService } from "./api-keys/service.ts";
import { getPublicModelGatewayOrigin } from "./model-public-config.ts";
import { DOCUMENT_ANALYSIS_API_ID, MAX_DOCUMENT_BYTES, MAX_DOCUMENT_RESULT_BYTES, documentAnalysisInputSchema, getDocumentAnalysisEndpoint, inspectDocument, parseDocumentAnalysisResult, readDocumentBody, type DocumentAnalysisErrorCode, type DocumentAnalysisFailure } from "./document-analysis-contracts.ts";

const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };
const maximumUploadBytes = MAX_DOCUMENT_BYTES + 32_768;
const gatewayErrors: Record<string, { code: DocumentAnalysisErrorCode; status: number }> = {
  DocumentInvalidInput: { code: "InvalidRequest", status: 400 }, DocumentInputTooLarge: { code: "DocumentTooLarge", status: 413 },
  DocumentUnsupportedMediaType: { code: "UnsupportedDocument", status: 415 }, DocumentRejected: { code: "DocumentRejected", status: 422 },
  DocumentAnalysisThrottled: { code: "RateLimitExceeded", status: 429 }, DocumentAnalysisTimeout: { code: "DocumentAnalysisTimeout", status: 504 },
  DocumentAnalysisUnavailable: { code: "DocumentAnalysisUnavailable", status: 503 },
};

/** 文書解析の認証・購読確認・上流通信を検証するための境界です。 */
export type DocumentAnalysisDependencies = {
  readSession: () => Promise<ApiKeyActor | null>; readPortalOrigin: () => string;
  readGatewayEnvironment?: () => Promise<{ origin: string }>;
  resolveSubscriptionKey?: (actor: ApiKeyActor, id: string, apiId: string) => Promise<string>;
  fetch?: typeof globalThis.fetch; timeoutMs?: number;
};

/** ファイル内容、秘密、上流例外を含めず固定コードを返します。 */
function failure(code: DocumentAnalysisErrorCode, status: number, details: Omit<DocumentAnalysisFailure, "error"> = {}): Response {
  return Response.json({ error: { code }, ...details }, { status, headers });
}

/**
 * 現在の本人・所属グループ・購読を確認して、選択した文書を固定Gatewayへ一度だけ送信します。
 * @remarks 元ファイルは保存せず、Azureの解析ジョブURLはブラウザーへ渡しません。
 */
export async function handleDocumentAnalysis(request: Request, dependencies: DocumentAnalysisDependencies): Promise<Response> {
  let origin: string;
  try { origin = new URL(dependencies.readPortalOrigin()).origin; } catch { return failure("GatewayNotConfigured", 503); }
  const site = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== origin || site && site !== "same-origin") return failure("RequestNotAllowed", 403);
  if (new URL(request.url).search) return failure("InvalidRequest", 400);
  let actor: ApiKeyActor | null;
  try { actor = await dependencies.readSession(); if (!actor) return failure("AuthenticationRequired", 401); } catch { return failure("AuthenticationUnavailable", 503); }
  if (request.signal.aborted) return failure("RequestCancelled", 499);
  const contentType = request.headers.get("content-type") ?? "";
  if (contentType.split(";", 1)[0].trim().toLowerCase() !== "multipart/form-data") return failure("InvalidRequest", 415);
  if (Number(request.headers.get("content-length") ?? 0) > maximumUploadBytes) return failure("DocumentTooLarge", 413);
  let input: ReturnType<typeof documentAnalysisInputSchema.parse>; let bytes: Uint8Array; let mediaType: string;
  try {
    const upload = await readDocumentBody(request.body, maximumUploadBytes, AbortSignal.any([request.signal, AbortSignal.timeout(10_000)]));
    const form = await new Request("https://multipart.invalid", { method: "POST", headers: { "Content-Type": contentType }, body: new Uint8Array(upload).buffer }).formData();
    if ([...form.keys()].length !== 2 || form.getAll("input").length !== 1 || form.getAll("file").length !== 1) return failure("InvalidRequest", 400);
    const metadata = form.get("input"); const file = form.get("file");
    if (typeof metadata !== "string" || metadata.length > 4096 || !(file instanceof File)) return failure("InvalidRequest", 400);
    input = documentAnalysisInputSchema.parse(JSON.parse(metadata));
    if (file.size > MAX_DOCUMENT_BYTES) return failure("DocumentTooLarge", 413);
    bytes = new Uint8Array(await file.arrayBuffer());
    try { mediaType = inspectDocument(bytes, file.type); } catch { return failure("UnsupportedDocument", 400); }
  } catch (error) { return request.signal.aborted ? failure("RequestCancelled", 499) : error instanceof RangeError ? failure("DocumentTooLarge", 413) : failure("InvalidRequest", 400); }
  let endpoint: string;
  try {
    const gateway = dependencies.readGatewayEnvironment ? await dependencies.readGatewayEnvironment() : { origin: getPublicModelGatewayOrigin() };
    if (!gateway.origin) throw new Error("Missing gateway origin");
    endpoint = getDocumentAnalysisEndpoint(gateway.origin, input.model);
  } catch { return failure("GatewayNotConfigured", 503); }
  let key: string;
  try {
    key = await (dependencies.resolveSubscriptionKey ?? ((owner, id, apiId) => getApiKeyService().executionKey(owner, id, apiId)))(actor, input.apiKeyId, DOCUMENT_ANALYSIS_API_ID);
    if (!/^[\x21-\x7e]{16,512}$/.test(key)) throw new Error("Invalid key");
  } catch (error) { return error instanceof ApiKeyError && error.status < 500 ? failure("ApiKeyUnavailable", 403) : failure("ApiKeyAccessUnavailable", 503); }
  const timeout = AbortSignal.timeout(dependencies.timeoutMs ?? 95_000);
  const signal = AbortSignal.any([request.signal, timeout]);
  if (signal.aborted) return failure("RequestCancelled", 499);
  const started = performance.now(); let requestId: string | undefined;
  try {
    const response = await (dependencies.fetch ?? globalThis.fetch)(endpoint, { method: "POST", redirect: "error", cache: "no-store", signal,
      body: new Uint8Array(bytes).buffer, headers: { "Content-Type": mediaType, Accept: "application/json", "api-key": key } });
    const id = response.headers.get("apim-request-id"); if (id && /^[A-Za-z0-9_-]{1,128}$/.test(id)) requestId = id;
    const details = requestId ? { requestId } : {};
    if (!response.ok) {
      let upstreamCode = "";
      try {
        const body: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await readDocumentBody(response.body, 8192, signal)));
        if (body && typeof body === "object" && "error" in body && body.error && typeof body.error === "object" && "code" in body.error && typeof body.error.code === "string") upstreamCode = body.error.code;
      } catch { /* 詳細本文は公開せず、HTTP状態または既知のコードだけで案内します。 */ }
      signal.throwIfAborted();
      const mapped = Object.hasOwn(gatewayErrors, upstreamCode) ? gatewayErrors[upstreamCode] : undefined;
      if (mapped?.code === "RateLimitExceeded" || response.status === 429) { const retry = Number(response.headers.get("retry-after")); return failure("RateLimitExceeded", 429, { ...details, ...(Number.isInteger(retry) && retry > 0 && retry <= 3600 ? { retryAfterSeconds: retry } : {}) }); }
      if (mapped) return failure(mapped.code, mapped.status, details);
      if (response.status === 504) return failure("DocumentAnalysisTimeout", 504, details);
      return failure([401, 403].includes(response.status) ? "GatewayUnauthorized" : "DocumentAnalysisFailed", 502, details);
    }
    if (response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") { await response.body?.cancel(); return failure("DocumentAnalysisFailed", 502, details); }
    if (Number(response.headers.get("content-length") ?? 0) > MAX_DOCUMENT_RESULT_BYTES) { await response.body?.cancel(); return failure("DocumentResultTooLarge", 502, details); }
    const rawBytes = await readDocumentBody(response.body, MAX_DOCUMENT_RESULT_BYTES, signal);
    const raw = new TextDecoder("utf-8", { fatal: true }).decode(rawBytes); const value: unknown = JSON.parse(raw);
    if (containsModelCredential(value, key, raw)) throw new Error("Credential reflection");
    const result = parseDocumentAnalysisResult(value, input.model, Math.max(0, Math.round(performance.now() - started)), requestId);
    const serialized = JSON.stringify(result);
    if (Buffer.byteLength(serialized) > MAX_DOCUMENT_RESULT_BYTES) return failure("DocumentResultTooLarge", 502, details);
    return new Response(serialized, { headers: { ...headers, "Content-Type": "application/json" } });
  } catch (error) {
    const details = requestId ? { requestId } : {};
    if (request.signal.aborted) return failure("RequestCancelled", 499, details);
    if (timeout.aborted) return failure("DocumentAnalysisTimeout", 504, details);
    return failure(error instanceof RangeError ? "DocumentResultTooLarge" : "DocumentAnalysisFailed", 502, details);
  }
}
