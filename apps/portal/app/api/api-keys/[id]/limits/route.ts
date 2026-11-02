import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleApiKeyLimits } from "@/lib/api-keys/limit-http";
import { getApiKeyLimitService } from "@/lib/api-keys/limit-service";

/** 各購読の制限設定を、現在のグループ所属を確認して読み取ります。 */
export async function GET(request: Request, context: { params: Promise<{ id: string }> }): Promise<Response> {
  return handleApiKeyLimits(request, { readActor: readPortalPrincipal,
    readOrigin: () => getAuthEnvironment().baseURL, getService: getApiKeyLimitService }, (await context.params).id);
}

/** 共通XMLには触れず、競合検知を伴うプロファイル更新を受け付けます。 */
export async function PUT(request: Request, context: { params: Promise<{ id: string }> }): Promise<Response> {
  return handleApiKeyLimits(request, { readActor: readPortalPrincipal,
    readOrigin: () => getAuthEnvironment().baseURL, getService: getApiKeyLimitService }, (await context.params).id);
}
