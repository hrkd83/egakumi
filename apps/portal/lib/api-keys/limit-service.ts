import "server-only";

import { z } from "zod";
import { getAuthEnvironment } from "../auth-environment.ts";
import { ApiKeyError, apiKeyIdSchema, type ApiKeyActor, type ApiKeyRecord } from "./contracts.ts";
import { GraphApiKeyMembership, type ApiKeyMembership } from "./graph.ts";
import { updateApiKeyLimitsSchema, type ApiKeyLimitProfile, type UpdateApiKeyLimits } from "./limit-contracts.ts";
import { getApiKeyLimitRepository, type ApiKeyLimitRepository } from "./limit-repository.ts";
import { getApiKeyRepository, type ApiKeyRepository } from "./repository.ts";
import { getApiKeyAdministrationRepository } from "../api-key-administration/repository.ts";

/** 既存の所有者台帳・所属確認を共有し、制限の保存先だけを分離した依存です。 */
export type ApiKeyLimitDependencies = {
  tenantId: string;
  keys: Pick<ApiKeyRepository, "getKey" | "getGroups" | "observeGroups">;
  membership: ApiKeyMembership;
  directory: Pick<GraphApiKeyMembership, "checkGroups">;
  limits: ApiKeyLimitRepository;
};

/** すべての読取・更新で、現在の所有グループへの所属と製品の承認を確認します。 */
export class ApiKeyLimitService {
  private readonly dependencies: ApiKeyLimitDependencies;

  constructor(dependencies: ApiKeyLimitDependencies) {
    if (!z.uuid().safeParse(dependencies.tenantId).success) throw new ApiKeyError("ConfigurationUnavailable", 503);
    this.dependencies = dependencies;
  }

  /** 画面の自己申告ではなく、サーバーで検証した本人だけを受け付けます。 */
  private actor(actor: ApiKeyActor): ApiKeyActor {
    if (!z.uuid().safeParse(actor.tenantId).success || !z.uuid().safeParse(actor.objectId).success
        || actor.tenantId.toLowerCase() !== this.dependencies.tenantId.toLowerCase()) {
      throw new ApiKeyError("AccessDenied", 403);
    }
    return { tenantId: actor.tenantId.toLowerCase(), objectId: actor.objectId.toLowerCase() };
  }

  /** 存在しないキーと、参照権限のないキーを同じ応答にして台帳情報を漏らしません。 */
  private async authorize(actor: ApiKeyActor, id: string): Promise<ApiKeyRecord> {
    if (!apiKeyIdSchema.safeParse(id).success) throw new ApiKeyError("NotFound", 404);
    const record = await this.dependencies.keys.getKey(actor.tenantId, id);
    if (!record) throw new ApiKeyError("NotFound", 404);
    const observations = await this.dependencies.directory.checkGroups(actor, [record.groupId]);
    await this.dependencies.keys.observeGroups(actor.tenantId, [...observations.values()]);
    const observation = observations.get(record.groupId.toLowerCase());
    if (!observation || observation.status === "unknown") throw new ApiKeyError("AuthorizationUnavailable", 503);
    if (observation.status !== "active") throw new ApiKeyError("NotFound", 404);
    const groups = await this.dependencies.keys.getGroups(actor.tenantId);
    const group = groups.find((entry) => entry.id.toLowerCase() === record.groupId.toLowerCase()
      && entry.directoryStatus === "active" && entry.products.some((product) => product.id === record.productId));
    if (!group) throw new ApiKeyError("NotFound", 404);
    const membership = await this.dependencies.membership.check(actor, [group.id]);
    if (!membership.has(group.id.toLowerCase())) throw new ApiKeyError("NotFound", 404);
    if (record.state === "deleted" || record.state === "deleting") throw new ApiKeyError("NotFound", 404);
    if (record.state !== "active" && record.state !== "suspended") throw new ApiKeyError("SubscriptionNotReady", 409);
    return record;
  }

  /** 所属確認に成功した場合だけ、未設定を含むプロファイルを読み取ります。 */
  async get(inputActor: ApiKeyActor, id: string): Promise<ApiKeyLimitProfile> {
    const actor = this.actor(inputActor);
    await this.authorize(actor, id);
    return this.dependencies.limits.get(actor.tenantId, id);
  }

  /**
   * 固定のAPIMポリシーや購読キーを書き換えず、SQLの設定データだけを更新します。
   * @remarks expectedVersionが古い場合は競合を返し、他の管理者の変更を保持します。
   */
  async update(inputActor: ApiKeyActor, id: string, values: UpdateApiKeyLimits): Promise<ApiKeyLimitProfile> {
    const actor = this.actor(inputActor);
    const parsed = updateApiKeyLimitsSchema.safeParse(values);
    if (!parsed.success) throw new ApiKeyError("InvalidRequest", 400);
    const record = await this.authorize(actor, id);
    return this.dependencies.limits.update(actor, record, parsed.data);
  }
}

let service: ApiKeyLimitService | undefined;
/** 認証後に必要な依存を作り、ビルドや公開ヘルスチェックでは秘密を要求しません。 */
export function getApiKeyLimitService(): ApiKeyLimitService {
  if (service) return service;
  try {
    const auth = getAuthEnvironment();
    const administration = getApiKeyAdministrationRepository();
    const directory = new GraphApiKeyMembership(auth, { readPolicy: () => administration.getSettings(auth.microsoftTenantId) });
    service = new ApiKeyLimitService({ tenantId: auth.microsoftTenantId, keys: getApiKeyRepository(),
      membership: directory, directory, limits: getApiKeyLimitRepository() });
    return service;
  } catch (error) {
    if (error instanceof ApiKeyError) throw error;
    throw new ApiKeyError("ConfigurationUnavailable", 503);
  }
}
