import "server-only";
import { getAuthEnvironment, getSqlEnvironment } from "../auth-environment.ts";
import { getApiKeyEnvironment } from "../api-keys/environment.ts";
import { AzureApiKeyApim } from "../api-keys/apim.ts";
import { getApiKeyAdministrationService } from "../api-key-administration/service.ts";
import { getSubscriptionSyncRepository } from "./repository.ts";
import { syncBinding } from "./environment.ts";
import { SubscriptionSyncService } from "./service.ts";

/** 配備資材で指定したSQL・APIM・実行IDだけから同期サービスを作ります。 */
export function getSubscriptionSyncService(): SubscriptionSyncService {
  const auth = getAuthEnvironment(), apim = getApiKeyEnvironment();
  return new SubscriptionSyncService({ tenantId: auth.microsoftTenantId, signingSecret: auth.secret,
    binding: syncBinding(getSqlEnvironment(), apim), repository: getSubscriptionSyncRepository(), apim: new AzureApiKeyApim(apim),
    isAdmin: (actor) => getApiKeyAdministrationService().isAdmin(actor) });
}
