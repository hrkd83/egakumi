import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { ApiKeyService } from "../../lib/api-keys/service.ts";
import { ApiKeyError, type ApiKeyActor, type ApiKeyGroup, type ApiKeyRecord, type CreateApiKeyInput, type ReserveApiKeyInput, type TransferApiKeyOwnershipInput } from "../../lib/api-keys/contracts.ts";
import type { DirectoryGroupObservation } from "../../lib/api-keys/group-policy.ts";
import type { ApiKeyApim, ManagedApiKeySubscription } from "../../lib/api-keys/apim.ts";
import type { ApiKeyRepository, ApiKeyLockedRecord, ApiKeyAuditAction } from "../../lib/api-keys/repository.ts";

export const tenantId = "11111111-1111-4111-8111-111111111111";
export const actor = { tenantId, objectId: "22222222-2222-4222-8222-222222222222" };
export const otherActor = { tenantId, objectId: "33333333-3333-4333-8333-333333333333" };
export const groupId = "41111111-1111-4111-8111-111111111111";
export const productId = "released-apis";
export const input: CreateApiKeyInput = { groupId, clientType:"custom-system", companyId:"cccccccc-cccc-4ccc-8ccc-cccccccccccc", contactEmail:"team@example.invalid", displayName: "人事システム", idempotencyKey: "a2222222-2222-4222-8222-222222222222" };
export const errorCode = (code: string) => (error: unknown) => error instanceof ApiKeyError && error.code === code;

/** サービスの認可判定を通して、外部副作用と秘密開示の有無を観測する台帳です。 */
class MemoryRepository implements ApiKeyRepository {
  groups: ApiKeyGroup[] = [{ id: groupId, displayName: "開発グループ", products: [{ id: productId, displayName: "Model Gateway", maxSubscriptions: 5 }] }];
  records = new Map<string, ApiKeyRecord>();
  requests = new Map<string, { input: ReserveApiKeyInput; id: string }>();
  auditEvents: { actorId: string; keyId: string; action: string }[] = [];
  companyName: string | null = "Test company";
  reserveCalls = 0;
  rejectAdditionalConnectionsWhileLocked = false;
  storedDirectoryStatuses = new Map<string, DirectoryGroupObservation["status"]>();
  private activeLocks = 0;

  async getGroups(): Promise<ApiKeyGroup[]> {
    assert.ok(!this.rejectAdditionalConnectionsWhileLocked || this.activeLocks === 0, "排他中に別のSQL接続で承認を読まない");
    return this.groups;
  }
  async ensureRegistry(): Promise<void> {}
  async observeGroups(_tenant: string, observations: readonly DirectoryGroupObservation[]): Promise<void> {
    assert.ok(!this.rejectAdditionalConnectionsWhileLocked || this.activeLocks === 0, "排他中に別のSQL接続で観測を書かない");
    for (const observed of observations) {
      const group = this.groups.find((entry) => entry.id === observed.id);
      if (group) { group.directoryStatus = this.storedDirectoryStatuses.get(group.id) ?? observed.status; group.lastCheckedAt = observed.checkedAt;
        if (observed.displayName) group.displayName = observed.displayName; }
    }
  }
  async transferOwnership(actor: ApiKeyActor, id: string, input: TransferApiKeyOwnershipInput, authorize: (record: ApiKeyRecord) => Promise<void>): Promise<ApiKeyRecord> {
    const current = this.records.get(id);
    if (!current) throw new ApiKeyError("NotFound", 404);
    if (current.groupId !== input.expectedSourceGroupId) throw new ApiKeyError("OwnershipConflict", 409);
    await authorize(current);
    const target = this.groups.find((group) => group.id === input.targetGroupId && group.directoryStatus === "active");
    const product = target?.products.find((entry) => entry.id === current.productId);
    if (!product) throw new ApiKeyError("TargetGroupNotAllowed", 403);
    if ([...this.records.values()].filter((record) => record.groupId === target!.id && record.productId === current.productId && record.state !== "deleted").length >= product.maxSubscriptions) {
      throw new ApiKeyError("TargetSubscriptionLimitReached", 409);
    }
    const updated = { ...current, groupId: input.targetGroupId };
    this.records.set(id, updated); this.auditEvents.push({ actorId: actor.objectId, keyId: id, action: "transfer" });
    return updated;
  }
  async listKeys(_tenant: string, groups: readonly string[]): Promise<ApiKeyRecord[]> {
    return [...this.records.values()].filter((record) => groups.includes(record.groupId) && record.state !== "deleted");
  }
  async getKey(_tenant: string, id: string): Promise<ApiKeyRecord | null> { return this.records.get(id) ?? null; }
  async reserve(actor: ApiKeyActor, value: ReserveApiKeyInput): Promise<{ record: ApiKeyRecord; created: boolean }> {
    this.reserveCalls++;
    const requestId = `${actor.objectId}:${value.idempotencyKey}`;
    const existing = this.requests.get(requestId);
    if (existing) {
      if (JSON.stringify(existing.input) !== JSON.stringify(value)) throw new ApiKeyError("IdempotencyConflict", 409);
      return { record: this.records.get(existing.id)!, created: false };
    }
    const id = `portal-${randomUUID()}`;
    const record: ApiKeyRecord = { id, apimSubscriptionId: id, groupId: value.groupId, productId: value.productId, displayName: value.displayName, state: "creating", createdAt: "2026-10-30T00:00:00Z", updatedAt: "2026-10-30T00:00:00Z", lastReviewedAt: null, budgetMode: "unlimited", clientType:value.clientType,systemName:value.systemName||null,teamName:value.teamName||null,companyId:value.companyId,companyName:this.companyName,contactEmail:value.contactEmail };
    this.records.set(id, record);
    this.requests.set(requestId, { input: value, id });
    return { record, created: true };
  }
  async withLockedKey<T>(actor: ApiKeyActor, id: string, callback: (context: ApiKeyLockedRecord) => Promise<T>): Promise<T> {
    let record = this.records.get(id);
    if (!record) throw new ApiKeyError("KeyNotFound", 404);
    this.activeLocks++;
    try { return await callback({
      record,
      confirmGroupApproval: async (observation) => {
        const group = this.groups.find((entry) => entry.id === record!.groupId);
        if (observation.id !== record!.groupId || observation.status !== "active"
            || !group?.products.some((product) => product.id === record!.productId)) throw new ApiKeyError("NotFound", 404);
        group.directoryStatus = observation.status; group.lastCheckedAt = observation.checkedAt;
        if (observation.displayName) group.displayName = observation.displayName;
      },
      updateState: async (state) => { record = { ...record!, state }; this.records.set(id, record); return record; },
      markReviewed: async () => { record = { ...record!, lastReviewedAt: "2026-10-30T01:00:00Z" }; this.records.set(id, record); return record; },
      audit: async (action: ApiKeyAuditAction) => { this.auditEvents.push({ actorId: actor.objectId, keyId: id, action }); },
    }); } finally { this.activeLocks--; }
  }
}

/** 実APIMの代わりに、操作順と保存状態を観測します。 */
export function fixture() {
  const repository = new MemoryRepository();
  const subscriptions = new Map<string, ManagedApiKeySubscription>();
  const members = new Set([actor.objectId, otherActor.objectId]);
  const excludedGroupMemberships = new Set<string>();
  const directoryStates = new Map<string, "active" | "deleted" | "ineligible" | "unknown">();
  const allowedApis = new Map<string, Set<string>>([[productId, new Set(["openai-inference-v1"])]]);
  const calls: string[] = [];
  const requestedSubscriptions: { operation: string; id: string }[] = [];
  let memberChecks = 0;
  let failGraph = false;
  let interruptedCreate = false;
  const apim: ApiKeyApim = {
    scopeFor: (product) => `/apim/products/${product}`,
    get: async (id) => { calls.push("get"); requestedSubscriptions.push({ operation: "get", id }); return subscriptions.get(id) ?? null; },
    create: async (id, product, displayName) => {
      calls.push("create");
      requestedSubscriptions.push({ operation: "create", id });
      const value = { id, displayName, scope: `/apim/products/${product}`, state: "active", etag: '"1"' };
      subscriptions.set(id, value);
      if (interruptedCreate) throw new ApiKeyError("UpstreamUnavailable", 503);
      return value;
    },
    productIncludesApi: async (product, apiId) => { calls.push(`product-api:${product}:${apiId}`); return allowedApis.get(product)?.has(apiId) ?? false; },
    setState: async (id, state) => { calls.push(state); requestedSubscriptions.push({ operation: state, id }); subscriptions.set(id, { ...subscriptions.get(id)!, state }); },
    delete: async (id) => { calls.push("delete"); requestedSubscriptions.push({ operation: "delete", id }); subscriptions.delete(id); return true; },
    secrets: async (id) => { calls.push("secrets"); requestedSubscriptions.push({ operation: "secrets", id }); return { primaryKey: "secret-primary", secondaryKey: "secret-secondary" }; },
  };
  const service = new ApiKeyService({
    tenantId, repository, apim,
    directory: {
      listEligibleGroups: async (member) => {
        if (failGraph) throw new ApiKeyError("AuthorizationUnavailable", 503);
        return members.has(member.objectId) ? repository.groups.filter((group) => !excludedGroupMemberships.has(group.id)
          && (!directoryStates.has(group.id) || directoryStates.get(group.id) === "active"))
          .map((group) => ({ id: group.id, displayName: group.displayName, securityEnabled: true })) : [];
      },
      listEligibleGroupPage: async () => ({ groups: repository.groups.map((group) => ({ id: group.id, displayName: group.displayName, securityEnabled: true })), nextCursor: null }),
      checkGroups: async (_member, ids) => {
        if (failGraph) throw new ApiKeyError("AuthorizationUnavailable", 503);
        return new Map(ids.map((id) => {
          const group = repository.groups.find((entry) => entry.id === id);
          return [id, { id, displayName: group?.displayName ?? null, securityEnabled: group ? true : null,
            status: group ? directoryStates.get(id) ?? "active" as const : "deleted" as const, checkedAt: new Date().toISOString(), errorCode: null }];
        }));
      },
    },
    membership: { check: async (member, groups) => {
      memberChecks++;
      if (failGraph) throw new ApiKeyError("AuthorizationUnavailable", 503);
      return new Set(members.has(member.objectId) ? groups.filter((id) => !excludedGroupMemberships.has(id)).map((id) => id.toLowerCase()) : []);
    } },
  });
  return { repository, service, subscriptions, members, calls, requestedSubscriptions, excludedGroupMemberships, directoryStates, allowedApis, get memberChecks() { return memberChecks; },
    set failGraph(value: boolean) { failGraph = value; }, set interruptedCreate(value: boolean) { interruptedCreate = value; } };
}
