import "server-only";

import { z } from "zod";
import { getAuthEnvironment } from "../auth-environment.ts";
import {
  ApiKeyError, RELEASED_API_PRODUCT_ID, apiKeyActionSchema, apiKeyIdSchema, apimApiIdSchema, createApiKeySchema,
  transferApiKeyOwnershipSchema,
  type ApiKeyActor, type ApiKeyGroup, type ApiKeyOverview, type ApiKeyRecord, type ApiKeySecrets, type CreateApiKeyInput,
  type ApiKeyOwnerTarget, type TransferApiKeyOwnershipInput,
} from "./contracts.ts";
import { AzureApiKeyApim, type ApiKeyApim, type ManagedApiKeySubscription } from "./apim.ts";
import { getApiKeyEnvironment } from "./environment.ts";
import { GraphApiKeyMembership, type ApiKeyMembership } from "./graph.ts";
import { getApiKeyRepository, type ApiKeyLockedRecord, type ApiKeyRepository } from "./repository.ts";
import { getApiKeyAdministrationRepository } from "../api-key-administration/repository.ts";

/** 操作ごとに実環境の認可を確認しつつ、通信と台帳を単体検証できる構成です。 */
export type ApiKeyServiceDependencies = {
  tenantId: string;
  repository: ApiKeyRepository;
  membership: ApiKeyMembership;
  apim: ApiKeyApim;
  directory: Pick<GraphApiKeyMembership, "listEligibleGroups" | "listEligibleGroupPage" | "checkGroups">;
};

/** APIMの表示名上限100文字に合わせ、会社マスタの名称を文字の途中で分断せず使います。 */
function companySubscriptionName(record: ApiKeyRecord): string {
  if (!record.companyId || !record.companyName?.trim()) throw new ApiKeyError("CompanyNotAvailable", 409);
  let name = record.companyName.slice(0, 100);
  if (/[\uD800-\uDBFF]$/u.test(name)) name = name.slice(0, -1);
  return name;
}

/** 許可済みの所有グループに限定して、APIM購読のライフサイクルを管理します。 */
export class ApiKeyService {
  private dependencies: ApiKeyServiceDependencies;

  constructor(dependencies: ApiKeyServiceDependencies) {
    if (!z.uuid().safeParse(dependencies.tenantId).success) throw new ApiKeyError("ConfigurationUnavailable", 503);
    this.dependencies = dependencies;
  }

  /** ブラウザーから受け取った名前ではなく、署名検証済みのテナントと本人IDを使います。 */
  private actor(actor: ApiKeyActor): ApiKeyActor {
    if (!z.uuid().safeParse(actor.tenantId).success || !z.uuid().safeParse(actor.objectId).success
        || actor.tenantId.toLowerCase() !== this.dependencies.tenantId.toLowerCase()) {
      throw new ApiKeyError("AccessDenied", 403);
    }
    return { tenantId: actor.tenantId.toLowerCase(), objectId: actor.objectId.toLowerCase() };
  }

  /** 名前変更の影響を受けないグループIDと製品IDで認可し、現在の所属を必ず再確認します。 */
  private async authorize(actor: ApiKeyActor, groupId: string, productId: string, notFound = true): Promise<ApiKeyGroup> {
    const observations = await this.dependencies.directory.checkGroups(actor, [groupId]);
    await this.dependencies.repository.observeGroups(actor.tenantId, [...observations.values()]);
    const observed = observations.get(groupId.toLowerCase());
    if (!observed || observed.status === "unknown") throw new ApiKeyError("AuthorizationUnavailable", 503);
    if (observed.status !== "active") throw new ApiKeyError(notFound ? "NotFound" : "AccessDenied", notFound ? 404 : 403);
    const groups = await this.dependencies.repository.getGroups(actor.tenantId);
    const group = groups.find((item) => item.id.toLowerCase() === groupId.toLowerCase()
      && item.directoryStatus === "active" && item.products.some((product) => product.id === productId));
    if (!group) throw new ApiKeyError(notFound ? "NotFound" : "AccessDenied", notFound ? 404 : 403);
    const memberOf = await this.dependencies.membership.check(actor, [group.id]);
    if (!memberOf.has(group.id.toLowerCase())) throw new ApiKeyError(notFound ? "NotFound" : "AccessDenied", notFound ? 404 : 403);
    return group;
  }

  /**
   * 排他取得後は現在のGraph所属を再確認し、SQL照合を保持中の接続だけで完結させます。
   * @remarks 別トランザクションで観測を書き込むと、並列数が接続プールを埋めた際に自己待ちになります。
   */
  private async authorizeLocked(actor: ApiKeyActor, context: ApiKeyLockedRecord): Promise<void> {
    const groupId = context.record.groupId;
    const observations = await this.dependencies.directory.checkGroups(actor, [groupId]);
    const observed = observations.get(groupId.toLowerCase());
    if (!observed || observed.status === "unknown") throw new ApiKeyError("AuthorizationUnavailable", 503);
    if (observed.status !== "active") throw new ApiKeyError("NotFound", 404);
    if (!(await this.dependencies.membership.check(actor, [groupId])).has(groupId.toLowerCase())) {
      throw new ApiKeyError("NotFound", 404);
    }
    await context.confirmGroupApproval(observed);
  }

  /** 他の製品へ移された購読を、キー表示や更新前に拒否します。 */
  private verifyScope(record: ApiKeyRecord, subscription: ManagedApiKeySubscription): void {
    if (subscription.id !== record.apimSubscriptionId || subscription.scope.toLowerCase() !== this.dependencies.apim.scopeFor(record.productId).toLowerCase()) {
      throw new ApiKeyError("NotFound", 404);
    }
  }

  /** 表示可能なグループとその台帳だけを返します。APIMのキー一覧は取得しません。 */
  async overview(inputActor: ApiKeyActor): Promise<ApiKeyOverview> {
    const actor = this.actor(inputActor);
    const discovered = await this.dependencies.directory.listEligibleGroups(actor);
    await this.dependencies.repository.ensureRegistry(actor.tenantId, discovered);
    await this.dependencies.repository.observeGroups(actor.tenantId, discovered.map((group) => ({ ...group,
      status: "active" as const, checkedAt: new Date().toISOString(), errorCode: null })));
    const groups = await this.dependencies.repository.getGroups(actor.tenantId);
    const discoveredIds = new Set(discovered.map((group) => group.id.toLowerCase()));
    const visible = groups.filter((group) => discoveredIds.has(group.id.toLowerCase()) && group.directoryStatus === "active");
    const records = visible.length ? await this.dependencies.repository.listKeys(actor.tenantId, visible.map((group) => group.id)) : [];
    const keys = records.filter((record) => record.state !== "deleted"
      && visible.some((group) => group.id.toLowerCase() === record.groupId.toLowerCase()
        && group.products.some((product) => product.id === record.productId)));
    return { groups: visible, keys };
  }

  /** 実行候補だけを返します。秘密は取得せず、同じ製品のAPI所属確認を一度にまとめます。 */
  async executionChoices(inputActor: ApiKeyActor, apiId: string): Promise<ApiKeyOverview> {
    if (!apimApiIdSchema.safeParse(apiId).success) throw new ApiKeyError("NotFound", 404);
    const overview = await this.overview(inputActor);
    const active = overview.keys.filter((record) => record.state === "active");
    const permitted = new Map(await Promise.all([...new Set(active.map((record) => record.productId))]
      .map(async (productId) => [productId, await this.dependencies.apim.productIncludesApi(productId, apiId)] as const)));
    const keys = active.filter((record) => permitted.get(record.productId));
    const groups = overview.groups.filter((group) => keys.some((record) => record.groupId.toLowerCase() === group.id.toLowerCase()));
    return { keys, groups };
  }

  /**
   * サーバー内部のAPI実行に限り、現在の所属・状態・API所属を確認して主キーを返します。
   * @remarks モデル実行前に排他を解放します。返した値をHTTP応答・ログ・永続化へ渡してはいけません。
   */
  async executionKey(inputActor: ApiKeyActor, id: string, apiId: string): Promise<string> {
    return (await this.executionCredential(inputActor, id, apiId)).key;
  }

  /**
   * 本人接続の保存先を実APIM購読へ結び付けるため、現在の認可と一緒に実購読IDを解決します。
   * @remarks 台帳IDを購読IDと仮定しません。秘密キーと実購読IDはサーバー内だけで利用します。
   */
  async executionCredential(inputActor: ApiKeyActor, id: string, apiId: string): Promise<{ subscriptionId: string; key: string }> {
    const actor = this.actor(inputActor);
    if (!apiKeyIdSchema.safeParse(id).success || !apimApiIdSchema.safeParse(apiId).success) throw new ApiKeyError("NotFound", 404);
    return this.dependencies.repository.withLockedKey(actor, id, async (context) => {
      const record = context.record;
      await this.authorizeLocked(actor, context);
      if (record.state !== "active") throw new ApiKeyError("NotFound", 404);
      const subscription = await this.dependencies.apim.get(record.apimSubscriptionId);
      if (!subscription) throw new ApiKeyError("NotFound", 404);
      this.verifyScope(record, subscription);
      if (subscription.state !== "active" || !await this.dependencies.apim.productIncludesApi(record.productId, apiId)) {
        throw new ApiKeyError("NotFound", 404);
      }
      return { subscriptionId: record.apimSubscriptionId, key: (await this.dependencies.apim.secrets(record.apimSubscriptionId)).primaryKey };
    }).catch((error: unknown) => {
      if (error instanceof ApiKeyError && error.status === 404) throw new ApiKeyError("NotFound", 404);
      throw error;
    });
  }

  /**
   * 発行予約を先に保存し、通信失敗後も同じIDで再開します。
   * @remarks 発行の応答に秘密値は含めません。表示は別の明示操作です。
   */
  async create(inputActor: ApiKeyActor, input: CreateApiKeyInput): Promise<ApiKeyRecord> {
    const actor = this.actor(inputActor);
    const parsed = createApiKeySchema.safeParse(input);
    if (!parsed.success) throw new ApiKeyError("InvalidInput", 400);
    // HTTPの任意IDをそのまま登録せず、現在の本人所属から発見した適格グループだけを初期化します。
    const discovered = (await this.dependencies.directory.listEligibleGroups(actor))
      .filter((group) => group.id.toLowerCase() === parsed.data.groupId.toLowerCase());
    if (!discovered.length) throw new ApiKeyError("AccessDenied", 403);
    await this.dependencies.repository.ensureRegistry(actor.tenantId, discovered);
    const group = await this.authorize(actor, parsed.data.groupId, RELEASED_API_PRODUCT_ID, false);
    const { record } = await this.dependencies.repository.reserve(actor, { ...parsed.data, groupId: group.id, productId: RELEASED_API_PRODUCT_ID });
    return this.dependencies.repository.withLockedKey(actor, record.id, async (context) => {
      const current = context.record;
      await this.authorizeLocked(actor, context);
      if (current.state === "deleted" || current.state === "deleting") throw new ApiKeyError("IdempotencyConflict", 409);
      if (current.state === "creating" && current.apimSubscriptionId !== current.id) throw new ApiKeyError("UpstreamConflict", 409);
      let subscription = await this.dependencies.apim.get(current.apimSubscriptionId);
      if (current.state !== "creating") {
        if (!subscription) throw new ApiKeyError("NotFound", 404);
        this.verifyScope(current, subscription);
        return current;
      }
      const companyName = companySubscriptionName(current);
      const existing = subscription !== null;
      subscription ??= await this.dependencies.apim.create(current.apimSubscriptionId, current.productId, companyName);
      this.verifyScope(current, subscription);
      // 旧版で発行済みの同じ予約は用途名の一致も許可し、再作成・既存購読の上書きは行いません。
      if ((subscription.displayName !== companyName && !(existing && subscription.displayName === current.displayName))
          || subscription.state !== "active") throw new ApiKeyError("UpstreamConflict", 409);
      const active = await context.updateState("active");
      await context.audit("create");
      return active;
    });
  }

  /**
   * 表示・停止・再開・棚卸・削除のたびに所属とAPIMの製品スコープを照合します。
   * @remarks 削除済みIDや無関係な購読を推測されても、秘密値や存在情報を返しません。
   */
  async act(inputActor: ApiKeyActor, id: string, action: string): Promise<ApiKeyRecord | ApiKeySecrets> {
    const actor = this.actor(inputActor);
    if (!apiKeyIdSchema.safeParse(id).success) throw new ApiKeyError("NotFound", 404);
    const parsed = apiKeyActionSchema.safeParse({ action });
    if (!parsed.success) throw new ApiKeyError("InvalidInput", 400);
    return this.dependencies.repository.withLockedKey(actor, id, async (context) => {
      const record = context.record;
      await this.authorizeLocked(actor, context);
      if (record.state === "deleted") {
        if (parsed.data.action === "delete") return record;
        throw new ApiKeyError("NotFound", 404);
      }
      if ((record.state === "creating" || record.state === "deleting") && parsed.data.action !== "delete") {
        throw new ApiKeyError("SubscriptionNotReady", 409);
      }
      const subscription = await this.dependencies.apim.get(record.apimSubscriptionId);
      if (subscription) this.verifyScope(record, subscription);
      if (parsed.data.action === "delete") {
        const deleting = record.state === "deleting" ? record : await context.updateState("deleting");
        if (subscription && !await this.dependencies.apim.delete(record.apimSubscriptionId, this.etag(subscription))) {
          return deleting;
        }
        const deleted = await context.updateState("deleted");
        await context.audit("delete");
        return deleted;
      }
      if (!subscription) throw new ApiKeyError("NotFound", 404);
      if (!["active", "suspended"].includes(subscription.state)) throw new ApiKeyError("SubscriptionNotReady", 409);
      if (parsed.data.action === "reveal") {
        const secrets = await this.dependencies.apim.secrets(record.apimSubscriptionId);
        await context.audit("reveal");
        return secrets;
      }
      if (parsed.data.action === "review") {
        const reviewed = await context.markReviewed();
        await context.audit("review");
        return reviewed;
      }
      const target = parsed.data.action === "suspend" ? "suspended" : "active";
      if (subscription.state !== target) await this.dependencies.apim.setState(record.apimSubscriptionId, target, this.etag(subscription));
      const updated = await context.updateState(target);
      await context.audit(parsed.data.action);
      return updated;
    }).catch((error: unknown) => {
      // 他グループのIDと存在しないIDを、SQL層のエラー名の差で区別させません。
      if (error instanceof ApiKeyError && error.status === 404) throw new ApiKeyError("NotFound", 404);
      throw error;
    });
  }

  /** 同時変更を検出できない取得結果では、破壊的な操作を行いません。 */
  private etag(subscription: ManagedApiKeySubscription): string {
    if (!subscription.etag || subscription.etag === "*" || /[\r\n]/.test(subscription.etag)) throw new ApiKeyError("UpstreamUnavailable", 503);
    return subscription.etag;
  }

  /** 所有元の現メンバーにだけ、未所属を含む適格な移管先を返します。 */
  async ownerTargets(inputActor: ApiKeyActor, id: string, search: string, cursor?: string): Promise<{ groups: ApiKeyOwnerTarget[]; nextCursor: string | null }> {
    const actor = this.actor(inputActor);
    if (!apiKeyIdSchema.safeParse(id).success) throw new ApiKeyError("NotFound", 404);
    const record = await this.dependencies.repository.getKey(actor.tenantId, id);
    if (!record) throw new ApiKeyError("NotFound", 404);
    await this.authorize(actor, record.groupId, record.productId);
    if (!["active", "suspended"].includes(record.state)) throw new ApiKeyError("SubscriptionNotReady", 409);
    const query = z.string().trim().min(2).max(80).safeParse(search);
    if (!query.success) throw new ApiKeyError("InvalidRequest", 400);
    const page = await this.dependencies.directory.listEligibleGroupPage(actor, { search: query.data, cursor });
    const candidates = page.groups;
    await this.dependencies.repository.ensureRegistry(actor.tenantId, candidates);
    await this.dependencies.repository.observeGroups(actor.tenantId, candidates.map((group) => ({ ...group,
      status: "active" as const, checkedAt: new Date().toISOString(), errorCode: null })));
    const ids = new Set(candidates.map((group) => group.id.toLowerCase()));
    const approved = (await this.dependencies.repository.getGroups(actor.tenantId)).filter((group) =>
      group.id.toLowerCase() !== record.groupId.toLowerCase() && ids.has(group.id.toLowerCase())
      && group.directoryStatus === "active" && group.products.some((product) => product.id === record.productId));
    const members = await this.dependencies.membership.check(actor, approved.map((group) => group.id));
    return { groups: approved.map((group) => ({ ...group, isMember: members.has(group.id.toLowerCase()) })), nextCursor: page.nextCursor };
  }

  /** 移管元の現メンバーが、移管先への所属を問わず所有権を直接渡します。 */
  async transfer(inputActor: ApiKeyActor, id: string, values: TransferApiKeyOwnershipInput): Promise<ApiKeyRecord> {
    const actor = this.actor(inputActor);
    if (!apiKeyIdSchema.safeParse(id).success) throw new ApiKeyError("NotFound", 404);
    const parsed = transferApiKeyOwnershipSchema.safeParse(values);
    if (!parsed.success) throw new ApiKeyError("InvalidRequest", 400);
    const record = await this.dependencies.repository.getKey(actor.tenantId, id);
    if (!record) throw new ApiKeyError("NotFound", 404);
    await this.authorize(actor, record.groupId, record.productId);
    const targetId = parsed.data.targetGroupId.toLowerCase();
    const targets = await this.dependencies.directory.checkGroups(actor, [targetId]);
    const target = targets.get(targetId);
    if (!target || target.status === "unknown") throw new ApiKeyError("AuthorizationUnavailable", 503);
    if (target.status !== "active" || !target.displayName || target.securityEnabled !== true) throw new ApiKeyError("TargetGroupNotAllowed", 403);
    await this.dependencies.repository.ensureRegistry(actor.tenantId, [{ id: targetId, displayName: target.displayName, securityEnabled: true }]);
    await this.dependencies.repository.observeGroups(actor.tenantId, [...targets.values()]);
    return this.dependencies.repository.transferOwnership(actor, id, parsed.data, async (current) => {
      // 並行変更の排他を取得した後にも、元の所属と両グループの適格性を再確認します。
      const checked = await this.dependencies.directory.checkGroups(actor, [current.groupId, targetId]);
      for (const groupId of [current.groupId, targetId]) {
        const value = checked.get(groupId.toLowerCase());
        if (!value || value.status === "unknown") throw new ApiKeyError("AuthorizationUnavailable", 503);
        if (value.status !== "active") throw new ApiKeyError(groupId === current.groupId ? "NotFound" : "TargetGroupNotAllowed", groupId === current.groupId ? 404 : 403);
      }
      if (!(await this.dependencies.membership.check(actor, [current.groupId])).has(current.groupId.toLowerCase())) {
        throw new ApiKeyError("NotFound", 404);
      }
    });
  }
}

let service: ApiKeyService | undefined;

/** ビルド時に秘密を読まず、最初の認証済み操作でサービスを組み立てます。 */
export function getApiKeyService(): ApiKeyService {
  if (service) return service;
  try {
    const auth = getAuthEnvironment();
    const administration = getApiKeyAdministrationRepository();
    const directory = new GraphApiKeyMembership(auth, { readPolicy: () => administration.getSettings(auth.microsoftTenantId) });
    service = new ApiKeyService({
      tenantId: auth.microsoftTenantId,
      repository: getApiKeyRepository(),
      membership: directory, directory,
      apim: new AzureApiKeyApim(getApiKeyEnvironment()),
    });
    return service;
  } catch (error) {
    if (error instanceof ApiKeyError) throw error;
    throw new ApiKeyError("ConfigurationUnavailable", 503);
  }
}
