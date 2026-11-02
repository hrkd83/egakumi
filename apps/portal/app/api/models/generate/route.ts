import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleTextGeneration } from "@/lib/model-gateway";

/** 認証済みポータルからのテキスト生成要求をAPIMへ渡します。秘密キーはサーバー内に留めます。 */
export async function POST(request: Request): Promise<Response> {
  return handleTextGeneration(request, {
    readSession: readPortalPrincipal,
    readPortalOrigin: () => getAuthEnvironment().baseURL,
  });
}
