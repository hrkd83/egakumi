import { z } from "zod";
import { piiResponseSchema, piiResultSchema } from "./pii-contract.ts";

/** 既存REST操作をAPIMが公開する、PII専用MCPの固定接続先です。 */
export const PII_MCP = { apiId: "pii-v1-mcp", path: "/tools/pii-v1-mcp/mcp", toolName: "pii_redact_text", protocolVersion: "2025-03-26" } as const;
/** このクライアントが実装するStreamable HTTPのプロトコル版です。 */
export const PII_MCP_PROTOCOLS = ["2025-06-18", "2025-03-26"] as const;

/** MCPのPII結果から、文章以外の添付や任意メタデータを除いた表示用契約です。 */
export const piiMcpResultSchema = piiResultSchema.extend({
  mcp: z.object({
    protocolVersion: z.enum(PII_MCP_PROTOCOLS),
    toolName: z.literal(PII_MCP.toolName),
    response: z.object({ jsonrpc: z.literal("2.0"), id: z.number().int().positive().safe(), result: z.object({
      content: z.array(z.object({ type: z.literal("text"), text: z.string().max(131072) }).strict()).max(4),
      structuredContent: piiResponseSchema.optional(), isError: z.literal(false).optional(),
    }).strict() }).strict(),
  }).strict(),
}).strict();

/** 秘密のHTTPヘッダーを含まない、PIIとJSON-RPCの実行結果です。 */
export type PiiMcpResult = z.infer<typeof piiMcpResultSchema>;

/** ユーザーの入力ではなく固定のMCPパスを組み立てます。 */
export function getPiiMcpEndpoint(origin: string): string {
  const url = new URL(origin);
  if (url.protocol !== "https:" || url.origin !== origin || url.username || url.password) throw new TypeError("Invalid MCP origin");
  return new URL(PII_MCP.path, url).href;
}
