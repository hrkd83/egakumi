import { readPortalPrincipal } from "@/lib/auth-session";
import { getAuthEnvironment } from "@/lib/auth-environment";
import { handleGraphToolConnection } from "@/lib/graph-tool-server";

/** 保存した本人別認証取引の完了状態だけを返します。Graphは呼び直しません。 */
export async function POST(request: Request): Promise<Response> {
  return handleGraphToolConnection(request, { readActor: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL });
}
