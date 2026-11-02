import "server-only";
import { z } from "zod";
import { readEmbeddingBody } from "./embedding-response.ts";

/** メッセージ本文や秘密値を外へ渡さないMCP通信の失敗です。 */
export class PiiMcpTransportError extends Error {
  readonly status: number;
  readonly retryAfterSeconds?: number;
  constructor(status: number = 502, retryAfterSeconds?: number) { super("MCP transport failed"); this.name = "PiiMcpTransportError"; this.status = status; this.retryAfterSeconds = retryAfterSeconds; }
}

/** JSON-RPCの要求と、そのIDに対応した成功応答です。 */
export type PiiMcpMessage = { jsonrpc: "2.0"; id?: number; method: string; params?: Record<string, unknown> };
const envelopeSchema = z.object({ jsonrpc: z.literal("2.0"), id: z.union([z.string(), z.number().int()]).optional(), method: z.string().optional(), result: z.unknown().optional(), error: z.unknown().optional() });

/** JSON/SSE両方を処理し、要求IDが一致した結果だけを返します。 */
export async function readPiiMcpMessage(response: Response, expectedId: number | string, signal: AbortSignal): Promise<unknown> {
  const kind = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  const maximum = 262144;
  /** 他要求の応答、JSON-RPCエラー、未宣言のサーバー要求は採用しません。 */
  function inspect(value: unknown): { found: boolean; result?: unknown } {
    const entry = envelopeSchema.parse(value);
    if (entry.method !== undefined) {
      if (entry.id !== undefined || entry.result !== undefined || entry.error !== undefined) throw new PiiMcpTransportError();
      return { found: false };
    }
    if (entry.id !== expectedId || entry.error !== undefined || !Object.hasOwn(entry, "result")) throw new PiiMcpTransportError();
    return { found: true, result: entry.result };
  }
  if (kind === "application/json") {
    const reply = inspect(JSON.parse(await readEmbeddingBody(response.body, maximum, signal)));
    if (!reply.found) throw new PiiMcpTransportError();
    return reply.result;
  }
  if (kind !== "text/event-stream" || !response.body) { await response.body?.cancel(); throw new PiiMcpTransportError(); }
  const reader = response.body.getReader(), decoder = new TextDecoder("utf-8", { fatal: true });
  let bytes = 0, buffer = "", data: string[] = [], messages = 0;
  const cancel = () => { void reader.cancel().catch(() => undefined); };
  signal.addEventListener("abort", cancel, { once: true });
  /** SSEのdata行を結合し、通知の後に来る応答を待ちます。 */
  function dispatch(): { found: boolean; result?: unknown } {
    if (!data.length) return { found: false };
    if (++messages > 100) throw new PiiMcpTransportError();
    const payload = data.join("\n"); data = [];
    return inspect(JSON.parse(payload));
  }
  try {
    signal.throwIfAborted();
    while (true) {
      const part = await reader.read(); signal.throwIfAborted();
      if (part.done) { buffer += decoder.decode(); break; }
      bytes += part.value.byteLength; if (bytes > maximum) throw new PiiMcpTransportError();
      buffer += decoder.decode(part.value, { stream: true });
      let index = 0;
      while (index < buffer.length) {
        const end = buffer.slice(index).search(/[\r\n]/); if (end < 0) break;
        const at = index + end; if (buffer[at] === "\r" && at === buffer.length - 1) break;
        const line = buffer.slice(index, at); index = at + (buffer[at] === "\r" && buffer[at + 1] === "\n" ? 2 : 1);
        if (!line) { const found = dispatch(); if (found.found) return found.result; }
        else if (line === "data") data.push("");
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      buffer = buffer.slice(index);
    }
    // CR単独改行は、次のLFとの結合を待っていた末尾CRもEOFで確定します。
    if (buffer === "\r") { const found = dispatch(); if (found.found) return found.result; }
    // 終端の空行がない途中切断は成功として扱いません。
    throw new PiiMcpTransportError();
  } finally { signal.removeEventListener("abort", cancel); await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}
