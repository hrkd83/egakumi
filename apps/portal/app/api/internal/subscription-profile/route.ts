import { getGatewayProfileSettings, handleGatewayProfile } from "@/lib/api-keys/gateway-profile";
import { getApiKeyLimitRepository } from "@/lib/api-keys/limit-repository";


/** APIM専用の読み取り口です。通常のPortal Cookieでは利用できません。 */
export async function GET(request: Request): Promise<Response> {
  return handleGatewayProfile(request, {
    settings: getGatewayProfileSettings,
    readProfile: (tenantId, subscriptionId) => getApiKeyLimitRepository().getGatewayProfile(tenantId, subscriptionId),
  });
}
