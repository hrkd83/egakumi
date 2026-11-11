import { MAX_IMAGE_RESPONSE_BYTES, type GeneratedImage, type ImageGenerationResult } from "./image-request.ts";
import { ModelResponseError } from "./model-response.ts";
import type { ModelGatewayErrorCode } from "./model-request.ts";

/** JSONオブジェクトだけを画像応答として扱います。 */
function record(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === "object" && !Array.isArray(value); }

/** 外部画像はHTTPSの公開ホストに限り、資格情報・IPアドレス・内部名を認めません。 */
export function safeImageUrl(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 8192 || /[\s\\]/.test(value)) return null;
  try {
    const url = new URL(value);
    const hostname = url.hostname.toLowerCase();
    if (url.protocol !== "https:" || url.username || url.password || url.port && url.port !== "443" || url.hash
      || !hostname.includes(".") || /^[\d.]+$/.test(hostname) || hostname.includes(":")
      || /\.(localhost|local|internal|invalid|test|example)$/.test(hostname)) return null;
    return url.href;
  } catch { return null; }
}

/** Base64の文字列とPNG/JPEGの先頭バイトを検査し、SVGやHTMLを表示しません。 */
export function rasterBase64Mime(value: unknown): "image/png" | "image/jpeg" | "image/webp" | null {
  if (typeof value !== "string" || value.length < 16 || value.length > MAX_IMAGE_RESPONSE_BYTES || value.length % 4 !== 0) return null;
  // 繰り返しグループの正規表現は大きな画像でスタックを消費するため、文字種と末尾を別に検査します。
  const padding = value.endsWith("==") ? 2 : value.endsWith("=") ? 1 : 0;
  if (!/^[A-Za-z0-9+/]+$/.test(value.slice(0, value.length - padding))) return null;
  const prefix = atob(value.slice(0, 16));
  if (prefix.startsWith("\x89PNG\r\n\x1a\n")) return "image/png";
  if (prefix.startsWith("\xff\xd8\xff")) return "image/jpeg";
  if (prefix.startsWith("RIFF") && prefix.slice(8, 12) === "WEBP") return "image/webp";
  return null;
}

/** 同期応答に含まれる各画像を検証し、実際のJSONは改変せず保持します。 */
export function parseImageGenerationResult(value: unknown, model: string, durationMs: number, requestId?: string): ImageGenerationResult {
  if (!record(value) || value.error || !Array.isArray(value.data) || value.data.length < 1) throw new Error("Invalid image generation response");
  const images: GeneratedImage[] = value.data.map((item, index) => {
    if (!record(item)) throw new Error("Invalid image item");
    if (item.b64_json !== undefined) {
      const mimeType = rasterBase64Mime(item.b64_json);
      if (!mimeType) throw new Error("Invalid raster image");
      return { index, kind: "base64", mimeType };
    }
    if (!safeImageUrl(item.url)) throw new Error("Invalid image URL");
    return { index, kind: "url" };
  });
  return { model, images, rawResponse: value, durationMs, ...(requestId ? { requestId } : {}) };
}

/** 検証済みのPNG/JPEGだけを、画像最適化サーバーを経由せずブラウザーで表示します。 */
export function imagePreviewSource(result: ImageGenerationResult, image: GeneratedImage): string | null {
  const data = result.rawResponse.data;
  if (!Array.isArray(data) || !record(data[image.index])) return null;
  const entry = data[image.index];
  return image.kind === "base64" && rasterBase64Mime(entry.b64_json) === image.mimeType ? `data:${image.mimeType};base64,${entry.b64_json}` : null;
}

/** 一時URLは再検証した外部リンクだけに使い、自動プレビューへ渡しません。 */
export function imageExternalUrl(result: ImageGenerationResult, image: GeneratedImage): string | null {
  const data = result.rawResponse.data;
  if (image.kind !== "url" || !Array.isArray(data) || !record(data[image.index])) return null;
  return safeImageUrl(data[image.index].url);
}

/** 大きなBase64文字列は表示用だけ省略し、コピー用の完全JSONは別に保持します。 */
export function imageResponsePreview(raw: Record<string, unknown>): { text: string; truncated: boolean } {
  let truncated = false;
  const text = JSON.stringify(raw, (_key, value: unknown) => {
    if (typeof value === "string" && value.length > 1200) { truncated = true; return `${value.slice(0, 160)}… [${value.length} characters; full value available via Copy]`; }
    return value;
  }, 2);
  return { text, truncated };
}

const errors = new Set<ModelGatewayErrorCode>(["InvalidRequest", "AuthenticationRequired", "RequestNotAllowed", "ModelUnavailable", "GatewayNotConfigured", "GatewayUnauthorized", "RateLimitExceeded", "GatewayTimeout", "GenerationFailed", "AuthenticationUnavailable", "RequestCancelled", "ApiKeyUnavailable", "ApiKeyAccessUnavailable"]);

/** 応答をサイズ制限内で読み、公開した結果の形をもう一度検証します。 */
export async function readImageResponse(response: Response, signal?: AbortSignal): Promise<ImageGenerationResult> {
  const reader = response.body?.getReader();
  if (!reader) throw new ModelResponseError({ error: { code: "GenerationFailed" } });
  const chunks: Uint8Array[] = [];
  let length = 0;
  let listener: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    if (!signal) return;
    listener = () => reject(new Error("Read aborted"));
    if (signal.aborted) listener(); else signal.addEventListener("abort", listener, { once: true });
  });
  try {
    while (true) {
      signal?.throwIfAborted();
      const part = await Promise.race([reader.read(), aborted]);
      if (part.done) break;
      length += part.value.byteLength;
      if (length > MAX_IMAGE_RESPONSE_BYTES + 8192) throw new Error("Response limit exceeded");
      chunks.push(part.value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (!response.ok) {
      const code = record(value) && record(value.error) && errors.has(value.error.code as ModelGatewayErrorCode) ? value.error.code as ModelGatewayErrorCode : "GenerationFailed";
      const requestId = record(value) && typeof value.requestId === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(value.requestId) ? value.requestId : undefined;
      const retryAfterSeconds = record(value) && Number.isInteger(value.retryAfterSeconds) && Number(value.retryAfterSeconds) > 0 && Number(value.retryAfterSeconds) <= 3600 ? Number(value.retryAfterSeconds) : undefined;
      throw new ModelResponseError({ error: { code }, ...(requestId ? { requestId } : {}), ...(retryAfterSeconds ? { retryAfterSeconds } : {}) });
    }
    if (!record(value) || typeof value.model !== "string" || !Number.isFinite(value.durationMs) || Number(value.durationMs) < 0) throw new Error("Invalid response");
    const requestId = typeof value.requestId === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(value.requestId) ? value.requestId : undefined;
    return parseImageGenerationResult(value.rawResponse, value.model, Number(value.durationMs), requestId);
  } catch (error) {
    if (error instanceof ModelResponseError || signal?.aborted) throw error;
    throw new ModelResponseError({ error: { code: "GenerationFailed" } });
  } finally { if (listener) signal?.removeEventListener("abort", listener); void reader.cancel().catch(() => undefined); reader.releaseLock(); }
}
