import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleApiKeys } from "@/lib/api-keys/http";
import { getPortalApiKeyService } from "@/lib/subscription-service/client";

const dependencies = { readActor: readPortalPrincipal, readOrigin: () => getAuthEnvironment().baseURL, getService: getPortalApiKeyService };

/** 所属が確認されたグループの購読台帳だけを返します。 */
export async function GET(request: Request): Promise<Response> {
  return handleApiKeys(request, dependencies);
}

/** 承認済み製品の購読をグループ所有で発行します。 */
export async function POST(request: Request): Promise<Response> {
  return handleApiKeys(request, dependencies);
}
