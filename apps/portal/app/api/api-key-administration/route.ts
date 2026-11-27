import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleApiKeyAdministration } from "@/lib/api-key-administration/http";
import { getPortalApiKeyAdministrationService } from "@/lib/subscription-service/client";

const dependencies = { readActor: readPortalPrincipal, readOrigin: () => getAuthEnvironment().baseURL, getService: getPortalApiKeyAdministrationService };

/** 管理グループの現在メンバーだけに、登録済み所有グループの状態を返します。 */
export async function GET(request: Request): Promise<Response> { return handleApiKeyAdministration(request, dependencies); }

/** 登録済みIDの観測だけを更新し、APIM購読の停止や削除は行いません。 */
export async function POST(request: Request): Promise<Response> { return handleApiKeyAdministration(request, dependencies); }
