import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleDecision } from "@/lib/decision-gateway";

/** 本人が選んだ購読で、公開済みの意思決定APIを呼び出します。 */
export async function POST(request: Request): Promise<Response> {
  return handleDecision(request, { readSession: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL });
}
