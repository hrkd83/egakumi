import { readEmbeddingBody } from "./embedding-response.ts";
import { piiMcpResultSchema, type PiiMcpResult } from "./pii-mcp-contract.ts";
import { readPiiResponse } from "./pii-response.ts";

/** ブラウザーへ渡るMCP結果にも上限と型検査を適用します。秘密のヘッダーは受け取りません。 */
export async function readPiiMcpResponse(response: Response, signal?: AbortSignal): Promise<PiiMcpResult> {
  if (!response.ok) { await readPiiResponse(response, signal); throw new Error("Unreachable"); }
  if (response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") throw new Error("Invalid response type");
  return piiMcpResultSchema.parse(JSON.parse(await readEmbeddingBody(response.body, 524288, signal)));
}
