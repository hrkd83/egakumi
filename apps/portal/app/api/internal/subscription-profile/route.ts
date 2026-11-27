import { getGatewayProfileSettings, handleGatewayProfile } from "@/lib/api-keys/gateway-profile";
import { getApiKeyLimitRepository } from "@/lib/api-keys/limit-repository";


/** ローカル検証用プロファイル口です。Azureでは専用読み取りACAが担当します。 */
export async function GET(request: Request): Promise<Response> {
  if (process.env.NODE_ENV === "production") {
    return Response.json({ error: { code: "NotFound" } }, { status: 404, headers: { "Cache-Control": "no-store" } });
  }
  return handleGatewayProfile(request, {
    settings: getGatewayProfileSettings,
    readProfile: (tenantId, subscriptionId) => getApiKeyLimitRepository().getGatewayProfile(tenantId, subscriptionId),
  });
}
