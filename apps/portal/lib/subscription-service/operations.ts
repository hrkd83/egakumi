import "server-only";

import { z } from "zod";
import { apiKeyActionSchema, ApiKeyError, apiKeyIdSchema, apimApiIdSchema, createApiKeySchema,
  transferApiKeyOwnershipSchema, type ApiKeyActor } from "../api-keys/contracts.ts";
import { updateApiKeyLimitsSchema } from "../api-keys/limit-contracts.ts";
import { getApiKeyService } from "../api-keys/service.ts";
import { getApiKeyLimitService } from "../api-keys/limit-service.ts";
import { getApiKeyAdministrationService } from "../api-key-administration/service.ts";
import { createCompanySchema, updateCompanySchema } from "../company-administration/contracts.ts";
import { getCompanyAdministrationService } from "../company-administration/service.ts";
import { applyRequestSchema, previewRequestSchema } from "../subscription-sync/contracts.ts";
import { getSubscriptionSyncService } from "../subscription-sync/factory.ts";

/** 公開Portalから受ける操作ごとに入力を検証し、本人は別途検証したトークンから渡します。 */
function operation<S extends z.ZodType, R>(schema: S, run: (actor: ApiKeyActor, input: z.output<S>) => Promise<R>) {
  return async (actor: ApiKeyActor, raw: unknown): Promise<R> => {
    const parsed = schema.safeParse(raw);
    if (!parsed.success) throw new ApiKeyError("InvalidRequest", 400);
    return run(actor, parsed.data);
  };
}

const keyOperation = z.object({ id: apiKeyIdSchema }).strict();
const executionOperation = keyOperation.extend({ apiId: apimApiIdSchema });
const noInput = z.null();
const cursor = z.string().min(1).max(2048).optional();
const companyOperation = z.object({ id: z.uuid(), values: updateCompanySchema }).strict();

/** 許可した業務操作を固定し、汎用のSQL・ARMリクエストを受け付けません。 */
export const subscriptionOperations = {
  keyOverview: operation(noInput, (actor) => getApiKeyService().overview(actor)),
  keyChoices: operation(z.object({ apiId: apimApiIdSchema }).strict(),
    (actor, input) => getApiKeyService().executionChoices(actor, input.apiId)),
  keyExecution: operation(executionOperation, (actor, input) => getApiKeyService().executionCredential(actor, input.id, input.apiId)),
  keyCreate: operation(createApiKeySchema, (actor, input) => getApiKeyService().create(actor, input)),
  keyAction: operation(keyOperation.extend(apiKeyActionSchema.shape), (actor, input) => getApiKeyService().act(actor, input.id, input.action)),
  ownerTargets: operation(keyOperation.extend({ search: z.string().max(120), cursor }),
    (actor, input) => getApiKeyService().ownerTargets(actor, input.id, input.search, input.cursor)),
  ownerTransfer: operation(keyOperation.extend({ values: transferApiKeyOwnershipSchema }),
    (actor, input) => getApiKeyService().transfer(actor, input.id, input.values)),
  limitGet: operation(keyOperation, (actor, input) => getApiKeyLimitService().get(actor, input.id)),
  limitUpdate: operation(keyOperation.extend({ values: updateApiKeyLimitsSchema }),
    (actor, input) => getApiKeyLimitService().update(actor, input.id, input.values)),
  adminAccess: operation(noInput, (actor) => getApiKeyAdministrationService().isAdmin(actor)),
  adminOverview: operation(z.object({ cursor }).strict(), (actor, input) => getApiKeyAdministrationService().overview(actor, input)),
  adminRefresh: operation(noInput, (actor) => getApiKeyAdministrationService().refresh(actor)),
  companyAvailable: operation(noInput, (actor) => getCompanyAdministrationService().available(actor)),
  companyOverview: operation(noInput, (actor) => getCompanyAdministrationService().overview(actor)),
  companyCreate: operation(createCompanySchema, (actor, input) => getCompanyAdministrationService().create(actor, input)),
  companyUpdate: operation(companyOperation, (actor, input) => getCompanyAdministrationService().update(actor, input.id, input.values)),
  syncOverview: operation(noInput, (actor) => getSubscriptionSyncService().overview(actor)),
  syncPreview: operation(previewRequestSchema, (actor, input) => getSubscriptionSyncService().preview(actor, input.ids)),
  syncApply: operation(applyRequestSchema, (actor, input) => getSubscriptionSyncService().apply(actor, input.token)),
};

export type SubscriptionOperation = keyof typeof subscriptionOperations;
export type SubscriptionResult<K extends SubscriptionOperation> = Awaited<ReturnType<typeof subscriptionOperations[K]>>;
