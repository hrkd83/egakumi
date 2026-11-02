import "server-only";
import { createHash, createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { z } from "zod";
import { ApiKeyError, type ApiKeyActor } from "../api-keys/contracts.ts";
import { syncItemIdSchema } from "./contracts.ts";
import type { ManagedApiKeySubscription } from "../api-keys/apim.ts";
import type { CanonicalSubscription, SubscriptionDifference, SyncInventory, SyncPreview, SyncResult } from "./contracts.ts";
import type { SyncBinding } from "./environment.ts";
import type { SubscriptionSyncRepository } from "./repository.ts";
import { subscriptionDifferences } from "./differences.ts";

/** 同期に必要なメタデータ操作だけを公開し、キー取得操作は依存に含めません。 */
export type SubscriptionSyncApim = {
  scopeFor(id: string): string;
  listMetadata(): Promise<ManagedApiKeySubscription[]>;
  get(id: string): Promise<ManagedApiKeySubscription | null>;
  reconcileMetadata(id: string, desired: NonNullable<SubscriptionDifference["desired"]>, etag: string | null): Promise<void>;
  delete(id: string, etag: string): Promise<boolean>;
};
/** 現在の管理者所属・署名・固定DB・通信を独立して検証できる依存です。 */
export type SubscriptionSyncDependencies = {
  tenantId: string; signingSecret: string; binding: SyncBinding;
  isAdmin(actor: ApiKeyActor): Promise<boolean>; repository: SubscriptionSyncRepository; apim: SubscriptionSyncApim; now?: () => number;
};
const planSchema = z.object({ version: z.literal(1), id: z.uuid(), tenantId: z.uuid(), actorId: z.uuid(),
  binding: z.string().regex(/^[a-f0-9]{64}$/), expires: z.number().int(),
  items: z.array(z.object({ id: syncItemIdSchema, hash: z.string().regex(/^[a-f0-9]{64}$/) }).strict()).min(1).max(10),
}).strict();
/** 比較に必要な公開項目とETagだけをハッシュ化し、秘密のキーは読みません。 */
function fingerprint(value: unknown): string { return createHash("sha256").update(JSON.stringify(value)).digest("hex"); }
/** ユーザー入力を例外本文へ流さず、再照会が必要な状態を識別します。 */
function failure(error: unknown): string { return error instanceof ApiKeyError ? error.code : "ServiceUnavailable"; }

/** SQLの確定状態を、管理者が確認した購読だけAPIMへ反映します。 */
export class SubscriptionSyncService {
  private readonly dependencies: SubscriptionSyncDependencies;
  constructor(dependencies: SubscriptionSyncDependencies) { this.dependencies = dependencies; }
  private now(): number { return (this.dependencies.now ?? Date.now)(); }
  private async authorize(input: ApiKeyActor): Promise<ApiKeyActor> {
    const actor = z.object({ tenantId: z.uuid(), objectId: z.uuid() }).safeParse(input);
    if (!actor.success || actor.data.tenantId.toLowerCase() !== this.dependencies.tenantId.toLowerCase()) throw new ApiKeyError("AccessDenied", 403);
    const normalized = { tenantId: actor.data.tenantId.toLowerCase(), objectId: actor.data.objectId.toLowerCase() };
    if (!await this.dependencies.isAdmin(normalized)) throw new ApiKeyError("AccessDenied", 403);
    return normalized;
  }
  private async requireCanonical(): Promise<void> {
    const binding = this.dependencies.binding;
    if (!binding.writable) throw new ApiKeyError("CanonicalDatabaseRequired", 409);
    await this.dependencies.repository.assertCanonical(binding.server, binding.database);
  }
  private sign(payload: string): Buffer {
    if (this.dependencies.signingSecret.length < 32) throw new ApiKeyError("ConfigurationUnavailable", 503);
    return createHmac("sha256", this.dependencies.signingSecret).update(`portal-subscription-sync-v1:${payload}`).digest();
  }
  private row(sql: CanonicalSubscription | null, apim: ManagedApiKeySubscription | null): SubscriptionDifference {
    const row = subscriptionDifferences(sql ? [sql] : [], apim ? [apim] : [], (id) => this.dependencies.apim.scopeFor(id))[0];
    if (!row) throw new ApiKeyError("SyncConflict", 409);
    return row;
  }
  /** APIMのみの購読・削除済みSQL行も表示し、ローカル一覧は明示的に読み取り専用にします。 */
  async overview(input: ApiKeyActor): Promise<SyncInventory> {
    const actor = await this.authorize(input);
    const { repository, apim, binding } = this.dependencies;
    const [records, remote, audit] = await Promise.all([
      repository.list(actor.tenantId).catch(() => { throw new ApiKeyError("SyncRegistryUnavailable", 503); }),
      apim.listMetadata().catch(() => { throw new ApiKeyError("UpstreamUnavailable", 503); }),
      repository.history(actor.tenantId).catch(() => { throw new ApiKeyError("SyncAuditUnavailable", 503); }),
    ]);
    if (binding.writable) await this.requireCanonical();
    return { ...binding, rows: subscriptionDifferences(records, remote, (id) => apim.scopeFor(id)), audit };
  }
  /** 確認画面は5分有効です。各行を再取得し、反映値や任意のARM宛先はクライアントに指定させません。 */
  async preview(input: ApiKeyActor, ids: string[]): Promise<SyncPreview> {
    const actor = await this.authorize(input);
    await this.requireCanonical();
    const selected = z.array(syncItemIdSchema).min(1).max(10).refine((items) => new Set(items).size === items.length).safeParse(ids);
    if (!selected.success) throw new ApiKeyError("InvalidRequest", 400);
    const rows: SubscriptionDifference[] = [], items: { id: string; hash: string }[] = [];
    for (const id of selected.data) {
      await this.dependencies.repository.locked(actor, id, async ({ read }) => {
        const sql = await read(), apim = await this.dependencies.apim.get(sql?.apimSubscriptionId ?? id.slice(5));
        const row = this.row(sql, apim);
        if (!["update", "recreate", "delete", "delete-unregistered"].includes(row.action) || (apim && !apim.etag)) throw new ApiKeyError("SyncConflict", 409);
        rows.push(row); items.push({ id, hash: fingerprint({ sql, apim }) });
      });
    }
    const expires = this.now() + 300_000;
    const payload = Buffer.from(JSON.stringify({ version: 1, id: randomUUID(), tenantId: actor.tenantId, actorId: actor.objectId,
      binding: fingerprint(this.dependencies.binding), expires, items })).toString("base64url");
    return { rows, expiresAt: new Date(expires).toISOString(), token: `${payload}.${this.sign(payload).toString("base64url")}` };
  }
  /** 再照合・ETag・排他ロックで確認後の変更を検出し、各操作の開始をSQLに残してから送信します。 */
  async apply(input: ApiKeyActor, token: string): Promise<{ results: SyncResult[] }> {
    const actor = await this.authorize(input);
    await this.requireCanonical();
    let plan: z.infer<typeof planSchema>;
    try {
      if (token.length > 60_000) throw new Error();
      const parts = token.split(".");
      if (parts.length !== 2 || !parts.every((part) => /^[A-Za-z0-9_-]+$/.test(part))) throw new Error();
      const signature = Buffer.from(parts[1], "base64url"), expected = this.sign(parts[0]);
      if (signature.length !== expected.length || !timingSafeEqual(signature, expected)) throw new Error();
      plan = planSchema.parse(JSON.parse(Buffer.from(parts[0], "base64url").toString("utf8")));
      if (plan.tenantId !== actor.tenantId || plan.actorId !== actor.objectId || plan.binding !== fingerprint(this.dependencies.binding)
          || plan.expires <= this.now() || plan.expires > this.now() + 300_000) throw new Error();
    } catch { throw new ApiKeyError("SyncPlanExpired", 409); }
    const results: SyncResult[] = [];
    for (const item of plan.items) {
      try {
        // 複数件の反映途中に管理者から外れた場合も、それ以降を実行しません。
        await this.authorize(actor);
        const result = await this.dependencies.repository.locked(actor, item.id, async ({ read, audit }) => {
          const sql = await read(), subscriptionId = sql?.apimSubscriptionId ?? item.id.slice(5);
          const current = await this.dependencies.apim.get(subscriptionId);
          if (fingerprint({ sql, apim: current }) !== item.hash) throw new ApiKeyError("SyncConflict", 409);
          const row = this.row(sql, current);
          const action = row.action;
          if (action !== "update" && action !== "delete" && action !== "recreate" && action !== "delete-unregistered") throw new ApiKeyError("SyncConflict", 409);
          const entry = { planId: plan.id, apiKeyId: sql?.id ?? null, subscriptionId, action, snapshotHash: item.hash, code: null };
          // 同じ確認内容の再送は一意制約で拒否します。通信中断時は新しい一覧・確認からやり直します。
          await audit({ ...entry, phase: "attempted" });
          let outcome: SyncResult["outcome"] = "succeeded", code: string | null = null;
          try {
            if (action === "delete" || action === "delete-unregistered") {
              if (!current?.etag) throw new ApiKeyError("SyncConflict", 409);
              if (!await this.dependencies.apim.delete(current.id, current.etag)) outcome = "pending";
            } else {
              if (!row.desired || (current && !current.etag)) throw new ApiKeyError("SyncConflict", 409);
              await this.dependencies.apim.reconcileMetadata(subscriptionId, row.desired, current?.etag ?? null);
            }
            const verified = await this.dependencies.apim.get(subscriptionId);
            if (sql ? this.row(sql, verified).action !== "none" : verified !== null) {
              if ((action === "delete" || action === "delete-unregistered") && outcome === "pending") code = "DeletionPending";
              else throw new ApiKeyError("SyncVerificationFailed", 409);
            }
          } catch (error) { outcome = "failed"; code = failure(error); }
          await audit({ ...entry, phase: outcome, code });
          return { id: item.id, outcome, code };
        });
        results.push(result);
      } catch (error) { results.push({ id: item.id, outcome: "failed", code: failure(error) }); }
    }
    return { results };
  }
}
