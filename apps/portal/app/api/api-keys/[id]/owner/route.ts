import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleApiKeyOwner } from "@/lib/api-keys/owner-http";
import { getPortalApiKeyService } from "@/lib/subscription-service/client";

/** 移管元を管理できる本人にだけ、所属有無を明示した候補を返します。 */
export async function GET(request: Request, context: { params: Promise<{ id: string }> }): Promise<Response> {
  return handleApiKeyOwner(request, { readActor: readPortalPrincipal, readOrigin: () => getAuthEnvironment().baseURL,
    getService: getPortalApiKeyService }, (await context.params).id);
}
/** 所有元メンバーから、選択した適格グループへの直接移管を受け付けます。 */
export async function POST(request: Request, context: { params: Promise<{ id: string }> }): Promise<Response> {
  return handleApiKeyOwner(request, { readActor: readPortalPrincipal, readOrigin: () => getAuthEnvironment().baseURL,
    getService: getPortalApiKeyService }, (await context.params).id);
}
