import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleApiKeys } from "@/lib/api-keys/http";
import { getApiKeyService } from "@/lib/api-keys/service";

/** キー表示・停止・再開・棚卸・削除は、毎回本人とグループ所属を確認します。 */
export async function POST(request: Request, context: { params: Promise<{ id: string }> }): Promise<Response> {
  return handleApiKeys(request, {
    readActor: readPortalPrincipal,
    readOrigin: () => getAuthEnvironment().baseURL,
    getService: getApiKeyService,
  }, (await context.params).id);
}
