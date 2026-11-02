import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleRerank } from "@/lib/rerank-gateway";

/** 本人が選んだ購読キーで、編集した質問と候補文書をリランクAPIへ送ります。 */
export async function POST(request: Request): Promise<Response> {
  return handleRerank(request, { readSession: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL });
}
