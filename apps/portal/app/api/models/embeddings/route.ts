import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleEmbeddings } from "@/lib/embedding-gateway";

/** 固定サンプルの埋め込み要求を、ログイン本人が利用できる購読キーでAPIMへ送ります。 */
export async function POST(request: Request): Promise<Response> {
  return handleEmbeddings(request, { readSession: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL });
}
