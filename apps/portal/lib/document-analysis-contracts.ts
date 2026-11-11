import { z } from "zod";
import { MAX_BUFFERED_BODY_BYTES, MAX_RUNTIME_STRING_CHARACTERS } from "./model-runtime-limits.ts";
import { apiKeyIdSchema } from "./api-keys/contracts.ts";

/** 文書ファイルを解析する公開済みのGateway APIです。 */
export const DOCUMENT_ANALYSIS_API_ID = "document-analysis-v1";
export const DOCUMENT_ANALYSIS_PATH = "/tools/documents/v1/analyze";
/** Gatewayが受け付けるファイル上限です。Azureのサービス上限ではありません。 */
export const MAX_DOCUMENT_BYTES = 500_000_000;
/** 解析結果をブラウザーへ返す際のPortalの上限です。 */
export const MAX_DOCUMENT_RESULT_BYTES = MAX_BUFFERED_BODY_BYTES;
export const DOCUMENT_MODELS = ["prebuilt-read", "prebuilt-layout"] as const;
export const DOCUMENT_MEDIA_TYPES = ["application/pdf", "image/jpeg", "image/png", "image/tiff", "image/bmp", "image/heif", "image/heic", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "text/html"] as const;

/** ブラウザーから受け付ける選択値です。URL・任意ヘッダーは指定できません。 */
export const documentAnalysisInputSchema = z.object({ model: z.enum(DOCUMENT_MODELS), apiKeyId: apiKeyIdSchema }).strict();
export type DocumentAnalysisModel = typeof DOCUMENT_MODELS[number];
export type DocumentAnalysisMediaType = typeof DOCUMENT_MEDIA_TYPES[number];

/** 解析失敗時は固定コードだけを公開し、Azureの例外や秘密情報を返しません。 */
export const documentAnalysisErrorSchema = z.object({
  error: z.object({ code: z.enum(["AuthenticationRequired", "AuthenticationUnavailable", "RequestNotAllowed", "InvalidRequest", "DocumentTooLarge", "UnsupportedDocument", "DocumentRejected", "ApiKeyUnavailable", "ApiKeyAccessUnavailable", "GatewayNotConfigured", "GatewayUnauthorized", "RateLimitExceeded", "DocumentAnalysisTimeout", "DocumentAnalysisUnavailable", "DocumentAnalysisFailed", "DocumentResultTooLarge", "RequestCancelled", "DocumentOperationExpired", "DocumentOperationUnavailable"]) }).strict(),
  requestId: z.string().regex(/^[A-Za-z0-9_-]{1,128}$/).optional(),
  retryAfterSeconds: z.number().int().positive().max(86_400).optional(),
}).strict();
export type DocumentAnalysisFailure = z.infer<typeof documentAnalysisErrorSchema>;
export type DocumentAnalysisErrorCode = DocumentAnalysisFailure["error"]["code"];

/** 表示に利用するページ・表の概要です。完全な解析結果はrawResponseで保持します。 */
export type DocumentAnalysisResult = {
  model: DocumentAnalysisModel; content: string; pageNumbers: number[];
  tables: { rowCount: number; columnCount: number; cells: { rowIndex: number; columnIndex: number; content: string }[] }[];
  durationMs: number; requestId?: string; rawResponse: Record<string, unknown>;
};

/** 選択モデルを固定パスのクエリーに変換し、外部URLへの差し替えを防ぎます。 */
export function getDocumentAnalysisEndpoint(origin: string, model: DocumentAnalysisModel): string {
  const parsed = new URL(origin);
  if (parsed.protocol !== "https:" || parsed.origin !== origin || parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash || !DOCUMENT_MODELS.includes(model)) throw new Error("Invalid document endpoint");
  const target = new URL(DOCUMENT_ANALYSIS_PATH, origin);
  target.searchParams.set("model", model);
  return target.toString();
}

/** 拡張子に頼らず、許可MIMEとファイル先頭のシグネチャが一致するかを確認します。 */
export function inspectDocument(bytes: Uint8Array, mediaType: string): DocumentAnalysisMediaType {
  if (bytes.length === 0 || bytes.length > MAX_DOCUMENT_BYTES) throw new RangeError("Invalid document size");
  if (!DOCUMENT_MEDIA_TYPES.some((value) => value === mediaType)) throw new TypeError("Unsupported document");
  const starts = (...signature: number[]) => signature.every((value, index) => bytes[index] === value);
  const valid = mediaType === "application/pdf" ? starts(0x25, 0x50, 0x44, 0x46, 0x2d)
    : mediaType === "image/jpeg" ? starts(0xff, 0xd8, 0xff)
      : mediaType === "image/png" ? starts(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a)
        : mediaType === "image/tiff" ? starts(0x49, 0x49, 0x2a, 0) || starts(0x4d, 0x4d, 0, 0x2a)
          : mediaType === "image/bmp" ? starts(0x42, 0x4d)
            : ["image/heif", "image/heic"].includes(mediaType) ? bytes.length >= 12 && new TextDecoder().decode(bytes.subarray(4, 8)) === "ftyp"
              : mediaType.startsWith("application/vnd.openxmlformats-officedocument.") ? starts(0x50, 0x4b, 3, 4)
                : mediaType === "text/html";
  if (!valid) throw new TypeError("Unsupported document");
  return mediaType as DocumentAnalysisMediaType;
}

/** 大きさを実測しながら読み、キャンセル時は停止します。Content-Lengthを信用しません。 */
export async function readDocumentBody(body: ReadableStream<Uint8Array> | null, maximum: number, signal: AbortSignal): Promise<Uint8Array> {
  if (!body) throw new Error("Missing body");
  const reader = body.getReader(); const chunks: Uint8Array[] = []; let size = 0;
  const cancel = () => { void reader.cancel().catch(() => undefined); };
  signal.addEventListener("abort", cancel, { once: true });
  try {
    signal.throwIfAborted();
    while (true) {
      const next = await reader.read(); signal.throwIfAborted();
      if (next.done) break;
      size += next.value.length;
      if (size > maximum) throw new RangeError("Document body too large");
      chunks.push(next.value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    return bytes;
  } finally { signal.removeEventListener("abort", cancel); await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}

/** JSONに過剰な入れ子・項目数・非JSON値がないことを検査します。 */
function validateJson(value: unknown): void {
  const stack = [{ value, depth: 0 }]; const visited = new Set<object>();
  while (stack.length) {
    const next = stack.pop()!;
    if (next.value === null || typeof next.value === "string" || typeof next.value === "boolean") continue;
    if (typeof next.value === "number" && Number.isFinite(next.value)) continue;
    if (typeof next.value !== "object" || visited.has(next.value)) throw new Error("Invalid JSON");
    visited.add(next.value);
    for (const entry of Object.values(next.value)) stack.push({ value: entry, depth: next.depth + 1 });
  }
}

const pageSchema = z.object({ pageNumber: z.number().int().positive() }).passthrough();
const tableSchema = z.object({ rowCount: z.number().int().nonnegative(), columnCount: z.number().int().nonnegative(), cells: z.array(z.object({ rowIndex: z.number().int().nonnegative(), columnIndex: z.number().int().nonnegative(), content: z.string() }).passthrough()) }).passthrough();
const nativeResultSchema = z.object({ apiVersion: z.literal("2024-11-30"), modelId: z.enum(DOCUMENT_MODELS), content: z.string(), pages: z.array(pageSchema).min(1), tables: z.array(tableSchema).optional() }).passthrough();
const gatewayResultSchema = z.object({ analyzeResult: nativeResultSchema, requestId: z.string().regex(/^[A-Za-z0-9_-]{1,128}$/).optional() }).strict();

/** Native解析結果をそのまま残し、表示に必要な属性だけを型検査して取り出します。 */
export function parseDocumentAnalysisResult(value: unknown, model: DocumentAnalysisModel, durationMs: number, requestId?: string): DocumentAnalysisResult {
  validateJson(value);
  const parsed = gatewayResultSchema.parse(value);
  if (parsed.analyzeResult.modelId !== model || !Number.isFinite(durationMs) || durationMs < 0) throw new Error("Unexpected document result");
  const pageNumbers = parsed.analyzeResult.pages.map((page) => page.pageNumber);
  if (new Set(pageNumbers).size !== pageNumbers.length) throw new Error("Duplicate document pages");
  const tables = (parsed.analyzeResult.tables ?? []).map((table) => {
    if (table.cells.some((cell) => cell.rowIndex >= table.rowCount || cell.columnIndex >= table.columnCount)) throw new Error("Invalid table coordinates");
    return { rowCount: table.rowCount, columnCount: table.columnCount, cells: table.cells.map((cell) => ({ rowIndex: cell.rowIndex, columnIndex: cell.columnIndex, content: cell.content })) };
  });
  return { model, content: parsed.analyzeResult.content, pageNumbers, tables, durationMs, ...(requestId ?? parsed.requestId ? { requestId: requestId ?? parsed.requestId } : {}), rawResponse: value as Record<string, unknown> };
}

/** ブラウザーが受け取る本文と固定エラーを確認します。 */
export async function readDocumentAnalysisResponse(response: Response, model: DocumentAnalysisModel, signal: AbortSignal): Promise<DocumentAnalysisResult> {
  const bytes = await readDocumentBody(response.body, MAX_RUNTIME_STRING_CHARACTERS, signal);
  const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  if (!response.ok) {
    const parsed = documentAnalysisErrorSchema.safeParse(value);
    throw new DocumentAnalysisResponseError(parsed.success ? parsed.data : { error: { code: "DocumentAnalysisFailed" } });
  }
  const output = z.object({ model: z.enum(DOCUMENT_MODELS), durationMs: z.number().finite().nonnegative(), rawResponse: z.record(z.string(), z.unknown()), requestId: z.string().regex(/^[A-Za-z0-9_-]{1,128}$/).optional() }).passthrough().parse(value);
  if (output.model !== model) throw new Error("Unexpected result model");
  return parseDocumentAnalysisResult(output.rawResponse, model, output.durationMs, output.requestId);
}

/** 上流の本文をエラーメッセージへ混ぜず、表示コードだけを運びます。 */
export class DocumentAnalysisResponseError extends Error {
  readonly failure: DocumentAnalysisFailure;
  constructor(failure: DocumentAnalysisFailure) { super(failure.error.code); this.failure = failure; this.name = "DocumentAnalysisResponseError"; }
}
