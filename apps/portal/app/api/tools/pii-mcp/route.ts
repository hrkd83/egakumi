import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handlePiiMcp } from "@/lib/pii-mcp-gateway";

/** 一回のMCP接続で初期化からPIIツール実行までを行います。 */
export async function POST(request: Request): Promise<Response> {
  return handlePiiMcp(request, { readSession: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL });
}
