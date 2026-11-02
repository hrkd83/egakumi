import { readPortalPrincipal } from "@/lib/auth-session";
import { getAuthEnvironment } from "@/lib/auth-environment";
import { handleGraphToolProfile } from "@/lib/graph-tool-server";

/** 選択購読でGraphを実行し、本人接続がなければ認証案内だけを返します。 */
export async function POST(request: Request): Promise<Response> {
  return handleGraphToolProfile(request, { readActor: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL });
}
