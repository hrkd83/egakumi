import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleApiKeyAdministration } from "@/lib/api-key-administration/http";
import { getApiKeyAdministrationService } from "@/lib/api-key-administration/service";

/** メニューの表示可否は現在の所属だけで判定し、ダッシュボードのSQL照会はしません。 */
export async function GET(request: Request): Promise<Response> {
  return handleApiKeyAdministration(request, { readActor: readPortalPrincipal,
    readOrigin: () => getAuthEnvironment().baseURL, getService: getApiKeyAdministrationService }, true);
}
