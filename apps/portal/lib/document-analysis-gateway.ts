import "server-only";
import { z } from "zod";
import { containsModelCredential } from "./model-credential.ts";
import { ApiKeyError, apiKeyIdSchema, type ApiKeyActor } from "./api-keys/contracts.ts";
import { getApiKeyService } from "./api-keys/service.ts";
import { getPublicModelGatewayOrigin } from "./model-public-config.ts";
import { MAX_BUFFERED_BODY_BYTES, modelDeadline } from "./model-runtime-limits.ts";
import { openDocumentOperation, sealDocumentOperation, DocumentOperationExpiredError, documentOperationStateSchema, type DocumentOperationState } from "./document-operation-handle.ts";
import { DOCUMENT_ANALYSIS_API_ID, DOCUMENT_MEDIA_TYPES, MAX_DOCUMENT_BYTES, MAX_DOCUMENT_RESULT_BYTES, documentAnalysisInputSchema, getDocumentAnalysisEndpoint, inspectDocument, parseDocumentAnalysisResult, readDocumentBody, type DocumentAnalysisErrorCode, type DocumentAnalysisFailure } from "./document-analysis-contracts.ts";

const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };
const pollInputSchema = z.object({ apiKeyId: apiKeyIdSchema, handle: z.string().regex(/^[A-Za-z0-9_-]{40,16000}$/) }).strict();
const acceptedSchema = z.object(documentOperationStateSchema.shape).omit({ startedAt: true }).extend({ status: z.literal("running"), requestId: z.string().regex(/^[A-Za-z0-9_-]{1,128}$/).optional() });
const gatewayErrors: Record<string, { code: DocumentAnalysisErrorCode; status: number }> = {
  DocumentInvalidInput: { code: "InvalidRequest", status: 400 }, DocumentInputTooLarge: { code: "DocumentTooLarge", status: 413 },
  DocumentUnsupportedMediaType: { code: "UnsupportedDocument", status: 415 }, DocumentRejected: { code: "DocumentRejected", status: 422 },
  DocumentAnalysisThrottled: { code: "RateLimitExceeded", status: 429 }, DocumentAnalysisTimeout: { code: "DocumentAnalysisTimeout", status: 504 },
  DocumentAnalysisUnavailable: { code: "DocumentAnalysisUnavailable", status: 503 },
};

/** 文書解析の認証・購読確認・上流通信と、本人に束縛する暗号化鍵の境界です。 */
export type DocumentAnalysisDependencies = {
  readSession: () => Promise<ApiKeyActor | null>; readPortalOrigin: () => string;
  readGatewayEnvironment?: () => Promise<{ origin: string }>; readHandleSecret?: () => string;
  resolveSubscriptionKey?: (actor: ApiKeyActor, id: string, apiId: string) => Promise<string>;
  fetch?: typeof globalThis.fetch; timeoutMs?: number;
};

/** 元のファイル・秘密・任意の上流本文を含めず、固定コードを返します。 */
function failure(code: DocumentAnalysisErrorCode, status: number, details: Omit<DocumentAnalysisFailure, "error"> = {}): Response {
  return Response.json({ error: { code }, ...details }, { status, headers: { ...headers, ...(details.retryAfterSeconds ? { "Retry-After": String(details.retryAfterSeconds) } : {}) } });
}

/** 提供元の指示が有効なときだけ採用し、再確認の間隔を返します。 */
function retryAfter(response: Response): number {
  const seconds = Number(response.headers.get("retry-after"));
  return Number.isInteger(seconds) && seconds > 0 && seconds <= 86_400 ? seconds : 2;
}

/** 同一オリジンと現在のPortal本人を、送信と各状態確認のたびに検査します。 */
async function authenticated(request: Request, dependencies: DocumentAnalysisDependencies, allowQuery = false): Promise<ApiKeyActor | Response> {
  let origin: string;
  try { origin = new URL(dependencies.readPortalOrigin()).origin; } catch { return failure("GatewayNotConfigured", 503); }
  const site = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== origin || site && site !== "same-origin") return failure("RequestNotAllowed", 403);
  if (!allowQuery && new URL(request.url).search) return failure("InvalidRequest", 400);
  try { return await dependencies.readSession() ?? failure("AuthenticationRequired", 401); } catch { return failure("AuthenticationUnavailable", 503); }
}

/** 接続先は構成済みHTTPS originに限り、ジョブの入力値で変更させません。 */
async function gatewayOrigin(dependencies: DocumentAnalysisDependencies): Promise<string> {
  const value = dependencies.readGatewayEnvironment ? (await dependencies.readGatewayEnvironment()).origin : getPublicModelGatewayOrigin();
  const url = new URL(value ?? "");
  if (url.protocol !== "https:" || url.origin !== value || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error("Invalid gateway origin");
  return url.origin;
}

/** 現在の本人に選択購読を実行する権限があることを毎回確認します。 */
async function executionKey(actor: ApiKeyActor, id: string, dependencies: DocumentAnalysisDependencies): Promise<string | Response> {
  try {
    const key = await (dependencies.resolveSubscriptionKey ?? ((owner, apiKeyId, apiId) => getApiKeyService().executionKey(owner, apiKeyId, apiId)))(actor, id, DOCUMENT_ANALYSIS_API_ID);
    if (!/^[\x21-\x7e]{16,512}$/.test(key)) throw new Error("Invalid key");
    return key;
  } catch (error) { return error instanceof ApiKeyError && error.status < 500 ? failure("ApiKeyUnavailable", 403) : failure("ApiKeyAccessUnavailable", 503); }
}

/** Gatewayの受付と完了結果を検証し、操作チケットを本人専用handleへ封印します。 */
async function gatewayResponse(response: Response, context: { actor: ApiKeyActor; apiKeyId: string; model: "prebuilt-read" | "prebuilt-layout"; startedAt: number; key: string; signal: AbortSignal; dependencies: DocumentAnalysisDependencies; state?: DocumentOperationState; handle?: string }): Promise<Response> {
  const id = response.headers.get("apim-request-id"); const requestId = id && /^[A-Za-z0-9_-]{1,128}$/.test(id) ? id : undefined;
  const details = requestId ? { requestId } : {};
  if (!response.ok) {
    let code = "";
    try { const body = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await readDocumentBody(response.body, 8192, context.signal))); code = typeof body?.error?.code === "string" ? body.error.code : ""; } catch { /* 任意の上流本文を利用者へ返しません。 */ }
    const mapped = Object.hasOwn(gatewayErrors, code) ? gatewayErrors[code] : undefined;
    if (response.status === 429 || mapped?.code === "RateLimitExceeded") return failure("RateLimitExceeded", 429, { ...details, retryAfterSeconds: retryAfter(response) });
    if (mapped) return failure(mapped.code, mapped.status, details);
    if (response.status === 410) return failure("DocumentOperationExpired", 410, details);
    if (response.status === 404) return failure("DocumentOperationUnavailable", 404, details);
    if (response.status === 422) return failure("DocumentRejected", 422, details);
    if (response.status === 504) return failure("DocumentAnalysisTimeout", 504, details);
    return failure([401, 403].includes(response.status) ? "GatewayUnauthorized" : "DocumentAnalysisFailed", 502, details);
  }
  if (response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") { await response.body?.cancel(); return failure("DocumentAnalysisFailed", 502, details); }
  if (Number(response.headers.get("content-length") ?? 0) > MAX_DOCUMENT_RESULT_BYTES) { await response.body?.cancel(); return failure("DocumentResultTooLarge", 502, details); }
  const raw = new TextDecoder("utf-8", { fatal: true }).decode(await readDocumentBody(response.body, MAX_DOCUMENT_RESULT_BYTES, context.signal));
  const value: unknown = JSON.parse(raw);
  if (containsModelCredential(value, context.key, raw)) throw new Error("Credential reflection");
  if (response.status === 202) {
    let state = context.state; let handle = context.handle;
    if (!state) {
      const accepted = acceptedSchema.parse(value);
      if (accepted.model !== context.model || Date.parse(accepted.expiresAt) <= Date.now()) throw new Error("Invalid document operation");
      state = documentOperationStateSchema.parse({ operationId: accepted.operationId, model: accepted.model, statusPath: accepted.statusPath, ticket: accepted.ticket, expiresAt: accepted.expiresAt, startedAt: context.startedAt });
      handle = sealDocumentOperation(state, context.actor, context.apiKeyId, context.dependencies.readHandleSecret?.() ?? "");
    } else {
      const active = z.object({ operationId: z.uuid(), model: z.enum(["prebuilt-read", "prebuilt-layout"]), status: z.literal("running"), requestId: z.string().optional() }).strict().parse(value);
      if (active.operationId !== state.operationId || active.model !== state.model) throw new Error("Mismatched document operation");
    }
    const seconds = retryAfter(response);
    return Response.json({ operationId: state.operationId, model: state.model, status: "running", handle, expiresAt: state.expiresAt, retryAfterSeconds: seconds, ...details }, { status: 202, headers: { ...headers, "Retry-After": String(seconds) } });
  }
  const result = parseDocumentAnalysisResult(value, context.model, Math.max(0, Date.now() - context.startedAt), requestId);
  return Response.json({ model: result.model, durationMs: result.durationMs, rawResponse: result.rawResponse, ...(result.requestId ? { requestId: result.requestId } : {}) }, { headers });
}

/** 本文全体を保持せず、形式を判別する先頭と受信バイト数だけを確認して転送します。 */
function documentUploadStream(body: ReadableStream<Uint8Array>, mediaType: string, signal: AbortSignal) {
  const reader = body.getReader(); let total = 0; let validated = false; let prefix = new Uint8Array(0); let released = false;
  const state: { failure?: "DocumentTooLarge" | "UnsupportedDocument" } = {};
  const pending: Uint8Array[] = [];
  const required = mediaType === "application/pdf" ? 5 : mediaType === "image/jpeg" ? 3 : mediaType === "image/png" ? 8 : mediaType === "image/bmp" ? 2 : ["image/heif", "image/heic"].includes(mediaType) ? 12 : mediaType === "text/html" ? 1 : 4;
  const release = () => { if (!released) { released = true; reader.releaseLock(); } };
  const cancel = async (reason?: unknown) => { if (!released) { await reader.cancel(reason).catch(() => undefined); release(); } };
  const stream = new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        signal.throwIfAborted();
        if (pending.length) { controller.enqueue(pending.shift()!); return; }
        while (true) {
          const next = await reader.read(); signal.throwIfAborted();
          if (next.done) {
            if (!validated) { state.failure = "UnsupportedDocument"; throw new Error("Incomplete document signature"); }
            release(); controller.close(); return;
          }
          total += next.value.byteLength;
          if (total > MAX_DOCUMENT_BYTES) { state.failure = "DocumentTooLarge"; throw new RangeError("Document upload too large"); }
          if (validated) { controller.enqueue(next.value); return; }
          pending.push(next.value);
          const joined = new Uint8Array(Math.min(required, prefix.length + next.value.length));
          joined.set(prefix); joined.set(next.value.subarray(0, joined.length - prefix.length), prefix.length); prefix = joined;
          if (prefix.length < required) continue;
          try { inspectDocument(prefix, mediaType); } catch { state.failure = "UnsupportedDocument"; throw new Error("Unsupported document signature"); }
          validated = true; controller.enqueue(pending.shift()!); return;
        }
      } catch (error) { await cancel(error); controller.error(error); }
    },
    cancel,
  }, { highWaterMark: 0 });
  return { stream, state, cancel };
}

/** raw fileの本文を、本人の現在の購読で一度だけ転送します。元文書をメモリーへ集約しません。 */
async function handleRawDocument(request: Request, dependencies: DocumentAnalysisDependencies, actor: ApiKeyActor, mediaType: string): Promise<Response> {
  const url = new URL(request.url); let input: ReturnType<typeof documentAnalysisInputSchema.parse>;
  try {
    if ([...url.searchParams.keys()].length !== 2 || url.searchParams.getAll("model").length !== 1 || url.searchParams.getAll("apiKeyId").length !== 1) throw new Error("Invalid document query");
    input = documentAnalysisInputSchema.parse(Object.fromEntries(url.searchParams));
  } catch { return failure("InvalidRequest", 400); }
  if (!DOCUMENT_MEDIA_TYPES.some((allowed) => allowed === mediaType) || !request.body) return failure("UnsupportedDocument", 415);
  const length = request.headers.get("content-length");
  if (length !== null && (!Number.isSafeInteger(Number(length)) || Number(length) <= 0)) return failure("InvalidRequest", 400);
  if (Number(length ?? 0) > MAX_DOCUMENT_BYTES) return failure("DocumentTooLarge", 413);
  let endpoint: string; try { endpoint = getDocumentAnalysisEndpoint(await gatewayOrigin(dependencies), input.model); } catch { return failure("GatewayNotConfigured", 503); }
  const key = await executionKey(actor, input.apiKeyId, dependencies); if (key instanceof Response) return key;
  const timeout = modelDeadline(dependencies.timeoutMs); const signal = AbortSignal.any([request.signal, timeout]);
  const upload = documentUploadStream(request.body, mediaType, signal);
  try {
    const startedAt = Date.now();
    const init: RequestInit & { duplex: "half" } = { method: "POST", redirect: "error", cache: "no-store", signal, body: upload.stream, duplex: "half", headers: { "Content-Type": mediaType, Accept: "application/json", "api-key": key } };
    const response = await (dependencies.fetch ?? globalThis.fetch)(endpoint, init);
    if (upload.state.failure) return failure(upload.state.failure, upload.state.failure === "DocumentTooLarge" ? 413 : 400);
    return await gatewayResponse(response, { actor, apiKeyId: input.apiKeyId, model: input.model, startedAt, key, signal, dependencies });
  } catch {
    if (upload.state.failure) return failure(upload.state.failure, upload.state.failure === "DocumentTooLarge" ? 413 : 400);
    return failure(request.signal.aborted ? "RequestCancelled" : timeout.aborted ? "DocumentAnalysisTimeout" : "DocumentAnalysisFailed", request.signal.aborted ? 499 : timeout.aborted ? 504 : 502);
  } finally { await upload.cancel(); }
}

/** ファイルを一度だけ解析へ送り、受付後は本人専用の状態参照を返します。元ファイルを保存しません。 */
export async function handleDocumentAnalysis(request: Request, dependencies: DocumentAnalysisDependencies): Promise<Response> {
  const actor = await authenticated(request, dependencies, true); if (actor instanceof Response) return actor;
  if (request.signal.aborted) return failure("RequestCancelled", 499);
  const contentType = request.headers.get("content-type") ?? "";
  const mediaTypeInput = contentType.split(";", 1)[0].trim().toLowerCase();
  if (mediaTypeInput !== "multipart/form-data") return handleRawDocument(request, dependencies, actor, mediaTypeInput);
  if (new URL(request.url).search) return failure("InvalidRequest", 400);
  if (Number(request.headers.get("content-length") ?? 0) > MAX_BUFFERED_BODY_BYTES) return failure("DocumentTooLarge", 413);
  let input: ReturnType<typeof documentAnalysisInputSchema.parse>; let bytes: Uint8Array; let mediaType: string;
  try {
    const upload = await readDocumentBody(request.body, MAX_BUFFERED_BODY_BYTES, request.signal);
    const form = await new Request("https://multipart.invalid", { method: "POST", headers: { "Content-Type": contentType }, body: new Uint8Array(upload).buffer }).formData();
    if ([...form.keys()].length !== 2 || form.getAll("input").length !== 1 || form.getAll("file").length !== 1) return failure("InvalidRequest", 400);
    const metadata = form.get("input"); const file = form.get("file");
    if (typeof metadata !== "string" || metadata.length > 4096 || !(file instanceof File)) return failure("InvalidRequest", 400);
    input = documentAnalysisInputSchema.parse(JSON.parse(metadata));
    if (file.size > MAX_DOCUMENT_BYTES) return failure("DocumentTooLarge", 413);
    bytes = new Uint8Array(await file.arrayBuffer());
    try { mediaType = inspectDocument(bytes, file.type); } catch { return failure("UnsupportedDocument", 400); }
  } catch (error) { return request.signal.aborted ? failure("RequestCancelled", 499) : failure(error instanceof RangeError ? "DocumentTooLarge" : "InvalidRequest", error instanceof RangeError ? 413 : 400); }
  let endpoint: string;
  try { endpoint = getDocumentAnalysisEndpoint(await gatewayOrigin(dependencies), input.model); } catch { return failure("GatewayNotConfigured", 503); }
  const key = await executionKey(actor, input.apiKeyId, dependencies); if (key instanceof Response) return key;
  const timeout = modelDeadline(dependencies.timeoutMs); const signal = AbortSignal.any([request.signal, timeout]);
  try {
    const startedAt = Date.now();
    const response = await (dependencies.fetch ?? globalThis.fetch)(endpoint, { method: "POST", redirect: "error", cache: "no-store", signal, body: new Uint8Array(bytes).buffer, headers: { "Content-Type": mediaType, Accept: "application/json", "api-key": key } });
    return await gatewayResponse(response, { actor, apiKeyId: input.apiKeyId, model: input.model, startedAt, key, signal, dependencies });
  } catch (error) { return failure(request.signal.aborted ? "RequestCancelled" : timeout.aborted ? "DocumentAnalysisTimeout" : error instanceof RangeError ? "DocumentResultTooLarge" : "DocumentAnalysisFailed", request.signal.aborted ? 499 : timeout.aborted ? 504 : 502); }
}

/** 各状態確認で本人と購読権限を再確認し、封印した固定パスだけをGatewayへ照会します。 */
export async function handleDocumentAnalysisStatus(request: Request, dependencies: DocumentAnalysisDependencies): Promise<Response> {
  const actor = await authenticated(request, dependencies); if (actor instanceof Response) return actor;
  if (request.signal.aborted) return failure("RequestCancelled", 499);
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") return failure("InvalidRequest", 415);
  let input: z.infer<typeof pollInputSchema>; let state: DocumentOperationState;
  try {
    input = pollInputSchema.parse(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await readDocumentBody(request.body, 32_768, request.signal))));
    state = openDocumentOperation(input.handle, actor, input.apiKeyId, dependencies.readHandleSecret?.() ?? "");
  } catch (error) { return failure(error instanceof DocumentOperationExpiredError ? "DocumentOperationExpired" : "DocumentOperationUnavailable", error instanceof DocumentOperationExpiredError ? 410 : 403); }
  const key = await executionKey(actor, input.apiKeyId, dependencies); if (key instanceof Response) return key;
  let origin: string; try { origin = await gatewayOrigin(dependencies); } catch { return failure("GatewayNotConfigured", 503); }
  const timeout = modelDeadline(dependencies.timeoutMs); const signal = AbortSignal.any([request.signal, timeout]);
  try {
    const response = await (dependencies.fetch ?? globalThis.fetch)(new URL(state.statusPath, origin), { method: "GET", redirect: "error", cache: "no-store", signal, headers: { Accept: "application/json", "api-key": key, "X-Core-Document-Ticket": state.ticket } });
    return await gatewayResponse(response, { actor, apiKeyId: input.apiKeyId, model: state.model, startedAt: state.startedAt, key, signal, dependencies, state, handle: input.handle });
  } catch { return failure(request.signal.aborted ? "RequestCancelled" : timeout.aborted ? "DocumentAnalysisTimeout" : "DocumentAnalysisFailed", request.signal.aborted ? 499 : timeout.aborted ? 504 : 502); }
}
