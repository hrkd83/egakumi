import "server-only";

import { z } from "zod";
import { getAuthEnvironment } from "../auth-environment.ts";
import { ApiKeyError, type ApiKeyActor } from "../api-keys/contracts.ts";
import { GraphApiKeyMembership } from "../api-keys/graph.ts";
import type { DirectoryGroupObservation } from "../api-keys/group-policy.ts";
import { getApiKeyRepository } from "../api-keys/repository.ts";
import { getApiKeyAdministrationRepository, type ApiKeyAdministrationRepository } from "./repository.ts";
import type { ApiKeyAdministrationOverview, ApiKeyAdministrationPageRequest, ApiKeyGroupReconciliation } from "./contracts.ts";

/** GraphとSQLだけを照合します。APIMを変更・停止・削除する依存は持ちません。 */
export type ApiKeyAdministrationDependencies = {
  tenantId: string;
  repository: ApiKeyAdministrationRepository;
  isPlatformAdmin(actor: ApiKeyActor): Promise<boolean>;
  readGroups(ids: readonly string[]): Promise<Map<string, DirectoryGroupObservation>>;
  saveGroups(tenantId: string, observations: readonly DirectoryGroupObservation[]): Promise<void>;
  now?: () => number;
};

/** 管理画面の情報は、毎回の現在所属を確認してから読み取ります。 */
export class ApiKeyAdministrationService {
  private readonly dependencies: ApiKeyAdministrationDependencies;
  constructor(dependencies: ApiKeyAdministrationDependencies) {
    if (!z.uuid().safeParse(dependencies.tenantId).success) throw new ApiKeyError("ConfigurationUnavailable", 503);
    this.dependencies = dependencies;
  }

  /** 署名検証済みの本人と固定テナントが一致する場合だけ、管理者判定へ進みます。 */
  private actor(actor: ApiKeyActor): ApiKeyActor {
    if (!z.uuid().safeParse(actor.tenantId).success || !z.uuid().safeParse(actor.objectId).success
        || actor.tenantId.toLowerCase() !== this.dependencies.tenantId.toLowerCase()) throw new ApiKeyError("AccessDenied", 403);
    return { tenantId: actor.tenantId.toLowerCase(), objectId: actor.objectId.toLowerCase() };
  }

  /** サイドメニュー表示用でも所属判定を省略せず、Graph障害はfalseへ変換しません。 */
  async isAdmin(input: ApiKeyActor): Promise<boolean> { return this.dependencies.isPlatformAdmin(this.actor(input)); }

  /** 管理情報のDB照会は、管理グループの現在所属を確認した後だけ実行します。 */
  private async authorize(input: ApiKeyActor): Promise<ApiKeyActor> {
    const actor = this.actor(input);
    if (!await this.dependencies.isPlatformAdmin(actor)) throw new ApiKeyError("AccessDenied", 403);
    return actor;
  }

  /** 最新の観測時刻を添え、未確認・削除・対象外・通信障害を各行で区別します。 */
  private async readOverview(input: ApiKeyAdministrationPageRequest = {}): Promise<ApiKeyAdministrationOverview> {
    const [page, totals] = await Promise.all([
      this.dependencies.repository.listTrackedGroups(this.dependencies.tenantId, input.cursor),
      this.dependencies.repository.getSummary(this.dependencies.tenantId),
    ]);
    const { checkedAt, syncErrors, ...summary } = totals;
    return { groups: page.groups, nextCursor: page.nextCursor, summary, checkedAt, syncErrors };
  }

  /** 基盤管理グループのメンバーだけに、追跡しているグループ状態を返します。 */
  async overview(actor: ApiKeyActor, input: ApiKeyAdministrationPageRequest = {}): Promise<ApiKeyAdministrationOverview> {
    await this.authorize(actor);
    const parsed = z.object({ cursor: z.uuid().transform((id) => id.toLowerCase()).optional() }).strict().safeParse(input);
    if (!parsed.success) throw new ApiKeyError("InvalidRequest", 400);
    return this.readOverview(parsed.data);
  }

  /** 認証済み利用者による更新要求では、現在の管理グループ所属を再確認します。 */
  async refresh(actor: ApiKeyActor): Promise<ApiKeyAdministrationOverview> {
    await this.authorize(actor);
    await this.reconcileTrackedGroups();
    return this.readOverview();
  }

  /**
   * 定期実行向けのサーバー内操作です。古い登録済みIDを最大100件だけ再確認します。
   * @remarks HTTPから直接公開しません。購読の自動停止・削除・メール通知は行いません。
   */
  async reconcileTrackedGroups(): Promise<ApiKeyGroupReconciliation> {
    await this.dependencies.repository.getSettings(this.dependencies.tenantId);
    const ids = await this.dependencies.repository.listGroupsToRefresh(this.dependencies.tenantId, 100);
    const observed = ids.length ? await this.dependencies.readGroups(ids) : new Map<string, DirectoryGroupObservation>();
    // 応答にないIDも不明として残し、確認漏れを削除や確認成功へ変換しません。
    const completedAt = new Date((this.dependencies.now ?? Date.now)()).toISOString();
    const observations = ids.map((id) => observed.get(id) ?? { id, displayName: null, securityEnabled: null,
      status: "unknown" as const, checkedAt: completedAt, errorCode: "DirectoryUnavailable" });
    if (observations.length) await this.dependencies.saveGroups(this.dependencies.tenantId, observations);
    return { checkedGroups: observations.length, unknownGroups: observations.filter((group) => group.status === "unknown").length, completedAt };
  }
}

let service: ApiKeyAdministrationService | undefined;
/** 認証済み要求で初めて、同じテナントのGraph・SQL依存を組み立てます。 */
export function getApiKeyAdministrationService(): ApiKeyAdministrationService {
  if (service) return service;
  const auth = getAuthEnvironment();
  const repository = getApiKeyAdministrationRepository();
  const directory = new GraphApiKeyMembership(auth, { readPolicy: () => repository.getSettings(auth.microsoftTenantId) });
  service = new ApiKeyAdministrationService({ tenantId: auth.microsoftTenantId, repository,
    isPlatformAdmin: (actor) => directory.isPlatformAdmin(actor), readGroups: (ids) => directory.observeGroups(ids),
    saveGroups: (tenantId, observations) => getApiKeyRepository().observeGroups(tenantId, observations) });
  return service;
}
