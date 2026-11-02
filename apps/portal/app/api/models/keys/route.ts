import { readPortalPrincipal } from "@/lib/auth-session";
import { getApiKeyService } from "@/lib/api-keys/service";
import { handleModelKeyChoices } from "@/lib/model-key-access";

/** 本人の所属と対象APIに応じた購読候補だけを返し、キー値をブラウザーへ渡しません。 */
export async function GET(request: Request): Promise<Response> {
  return handleModelKeyChoices(request, {
    readActor: readPortalPrincipal,
    readChoices: (actor, apiId) => getApiKeyService().executionChoices(actor, apiId),
  });
}
