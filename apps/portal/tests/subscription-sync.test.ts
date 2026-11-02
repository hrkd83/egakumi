import assert from "node:assert/strict";
import { test } from "node:test";
import { randomUUID } from "node:crypto";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { AzureApiKeyApim, type ManagedApiKeySubscription } from "../lib/api-keys/apim.ts";
import { SubscriptionSyncService, type SubscriptionSyncDependencies } from "../lib/subscription-sync/service.ts";
import { subscriptionDifferences } from "../lib/subscription-sync/differences.ts";
import { syncBinding } from "../lib/subscription-sync/environment.ts";
import { handleSubscriptionSync } from "../lib/subscription-sync/http.ts";
import type { CanonicalSubscription } from "../lib/subscription-sync/contracts.ts";
import type { SyncAuditInput } from "../lib/subscription-sync/repository.ts";

const tenantId = randomUUID(), actor = { tenantId, objectId: randomUUID() };
const resource = `/subscriptions/${randomUUID()}/resourceGroups/rg-sync/providers/Microsoft.ApiManagement/service/apim-sync`;
const id = `portal-${randomUUID()}`;
const scopeFor = (product: string) => `${resource}/products/${product}`;
const source: CanonicalSubscription = { id, apimSubscriptionId: id, productId: "released-apis", purpose: "Purpose", groupName: "ORG_Owner", companyName: "Company", state: "active", creationSource: "portal" };
const remote: ManagedApiKeySubscription = { id, displayName: "Old company", scope: scopeFor("model-users"), state: "suspended", etag: '"v1"' };
const errorCode = (code: string) => (error: unknown) => error instanceof ApiKeyError && error.code === code;

/** 実Azureへ接続せず、状態競合と監査の永続化順序を検証する実行環境です。 */
function fixture() {
  const sql = new Map<string, CanonicalSubscription>([[id, { ...source }]]);
  const apim = new Map<string, ManagedApiKeySubscription>([[id, { ...remote }]]);
  const events: SyncAuditInput[] = [], mutations: unknown[] = [];
  let admin = true, time = 10_000, canonical = true, writeFailure = false, auditFailure = false, pendingDelete = false;
  const deps: SubscriptionSyncDependencies = {
    tenantId, signingSecret: "test-only-strong-plan-signing-secret-32-chars",
    binding: { source: "azure", writable: true, server: "sql-sync.database.windows.net", database: "sqldb-sync-portal", apimResourceId: resource },
    isAdmin: async () => admin, now: () => time,
    repository: {
      list: async () => structuredClone([...sql.values()]), history: async () => [],
      assertCanonical: async () => { if (!canonical) throw new ApiKeyError("CanonicalDatabaseRequired", 409); },
      locked: async (_actor, key, callback) => callback({
        read: async () => {
          if (key.startsWith("apim:")) {
            if ([...sql.values()].some((row) => row.apimSubscriptionId === key.slice(5))) throw new ApiKeyError("SyncConflict", 409);
            return null;
          }
          return structuredClone(sql.get(key) ?? null);
        },
        audit: async (entry) => {
          if (auditFailure || events.some((row) => row.planId === entry.planId && row.subscriptionId === entry.subscriptionId && row.phase === entry.phase)) throw new ApiKeyError("SyncAuditUnavailable", 409);
          events.push(entry);
        },
      }),
    },
    apim: {
      scopeFor, listMetadata: async () => structuredClone([...apim.values()]), get: async (key) => structuredClone(apim.get(key) ?? null),
      reconcileMetadata: async (key, desired, etag) => {
        assert.equal(events.at(-1)?.phase, "attempted", "APIMへ書く前に監査が永続化される");
        if (writeFailure) throw new ApiKeyError("UpstreamUnavailable", 503);
        assert.equal(apim.get(key)?.etag ?? null, etag);
        mutations.push({ key, desired, etag }); apim.set(key, { id: key, ...desired, scope: scopeFor(desired.productId), etag: '"v2"' });
      },
      delete: async (key, etag) => {
        assert.equal(events.at(-1)?.phase, "attempted"); assert.equal(apim.get(key)?.etag, etag);
        mutations.push({ deleted: key }); if (!pendingDelete) apim.delete(key); return !pendingDelete;
      },
    },
  };
  return { deps, sql, apim, events, mutations, service: new SubscriptionSyncService(deps),
    change: (values: { admin?: boolean; time?: number; canonical?: boolean; writeFailure?: boolean; auditFailure?: boolean; pendingDelete?: boolean }) => {
      admin = values.admin ?? admin; time = values.time ?? time; canonical = values.canonical ?? canonical;
      writeFailure = values.writeFailure ?? writeFailure; auditFailure = values.auditFailure ?? auditFailure; pendingDelete = values.pendingDelete ?? pendingDelete;
    } };
}

test("SQLを正として会社名・製品・停止状態を照合し、APIMのみの購読を削除対象にする", () => {
  const rows = subscriptionDifferences([source], [remote, { ...remote, id: "orphan" }, { ...remote, id: "master" }], scopeFor);
  assert.equal(rows[0].action, "update"); assert.equal(rows[0].reactivates, true);
  assert.equal(rows[0].desired?.displayName, "Company");
  assert.equal(rows[1].action, "delete-unregistered"); assert.equal(rows[2].action, "blocked");
  assert.equal(subscriptionDifferences([{ ...source, state: "deleted" }], [remote], scopeFor)[0].action, "delete");
  assert.equal(subscriptionDifferences([{ ...source, state: "creating" }], [remote], scopeFor)[0].action, "blocked");
  assert.equal(subscriptionDifferences([{ ...source, companyName: null }], [remote], scopeFor)[0].action, "blocked");
  assert.equal(subscriptionDifferences([{ ...source, creationSource: "deployment", companyName: null }], [], scopeFor)[0].desired?.displayName, "Purpose");
});

test("管理者の確認・再照合後に更新し、キー値を持たず開始と完了を残す", async () => {
  const f = fixture(), preview = await f.service.preview(actor, [id]);
  assert.equal(f.mutations.length, 0); assert.equal(preview.rows[0].reactivates, true);
  const result = await f.service.apply(actor, preview.token);
  assert.equal(result.results[0].outcome, "succeeded");
  assert.equal(f.apim.get(id)?.displayName, "Company"); assert.equal(f.apim.get(id)?.state, "active");
  assert.deepEqual(f.events.map((event) => event.phase), ["attempted", "succeeded"]);
});

test("SQL未登録の購読も同期で削除し、masterはプレビューでも操作しない", async () => {
  const f = fixture(); f.sql.clear(); f.apim.set("master", { ...remote, id: "master" });
  const inventory = await f.service.overview(actor);
  assert.equal(inventory.rows.find((row) => row.id === `apim:${id}`)?.action, "delete-unregistered");
  const preview = await f.service.preview(actor, [`apim:${id}`]);
  assert.equal((await f.service.apply(actor, preview.token)).results[0].outcome, "succeeded");
  assert.equal(f.events[0].apiKeyId, null); assert.equal(f.apim.has(id), false); assert.equal(f.apim.has("master"), true);
  await assert.rejects(f.service.preview(actor, ["apim:master"]));
});

test("欠落購読の再作成は新キーになることを表示し、SQLの停止状態を直接反映する", async () => {
  const f = fixture(); f.apim.clear(); f.sql.set(id, { ...source, state: "suspended" });
  const preview = await f.service.preview(actor, [id]); assert.equal(preview.rows[0].action, "recreate");
  await f.service.apply(actor, preview.token);
  assert.deepEqual(f.mutations, [{ key: id, desired: { displayName: "Company", productId: "released-apis", state: "suspended" }, etag: null }]);
});

test("確認後のSQL変更・APIM ETag変更・所属喪失・期限切れ・署名改変を拒否する", async () => {
  for (const scenario of ["sql", "apim", "admin", "expired", "forged", "actor", "database"] as const) {
    const f = fixture(), preview = await f.service.preview(actor, [id]);
    if (scenario === "sql") f.sql.set(id, { ...source, state: "suspended" });
    if (scenario === "apim") f.apim.set(id, { ...remote, etag: '"changed"' });
    if (scenario === "admin") f.change({ admin: false });
    if (scenario === "expired") f.change({ time: 400_000 });
    if (scenario === "database") f.change({ canonical: false });
    const token = scenario === "forged" ? `${preview.token}x` : preview.token;
    try { const result = await f.service.apply(scenario === "actor" ? { ...actor, objectId: randomUUID() } : actor, token); assert.equal(result.results[0].outcome, "failed"); }
    catch (error) { assert.ok(error instanceof ApiKeyError); }
    assert.equal(f.mutations.length, 0, scenario);
  }
});

test("SQL未登録として確認した後に台帳へ登録された購読を削除しない", async () => {
  const f = fixture(); f.sql.clear(); const preview = await f.service.preview(actor, [`apim:${id}`]);
  f.sql.set(id, { ...source });
  assert.equal((await f.service.apply(actor, preview.token)).results[0].code, "SyncConflict");
  assert.equal(f.mutations.length, 0);
});

test("失敗は個別結果と永続監査に残し、同じ確認内容で上流更新を再送しない", async () => {
  const f = fixture(), preview = await f.service.preview(actor, [id]); f.change({ writeFailure: true });
  assert.equal((await f.service.apply(actor, preview.token)).results[0].code, "UpstreamUnavailable");
  assert.deepEqual(f.events.map((event) => event.phase), ["attempted", "failed"]);
  f.change({ writeFailure: false });
  assert.equal((await f.service.apply(actor, preview.token)).results[0].code, "SyncAuditUnavailable");
  assert.equal(f.mutations.length, 0);
  const another = fixture(); another.change({ auditFailure: true });
  const plan = await another.service.preview(actor, [id]); await another.service.apply(actor, plan.token);
  assert.equal(another.mutations.length, 0, "開始監査を記録できなければ上流を変更しない");
});

test("非同期削除の未完了を成功と表示しない", async () => {
  const f = fixture(); f.sql.set(id, { ...source, state: "deleted" }); f.change({ pendingDelete: true });
  const preview = await f.service.preview(actor, [id]); const result = await f.service.apply(actor, preview.token);
  assert.equal(result.results[0].outcome, "pending"); assert.equal(f.events.at(-1)?.phase, "pending");
});

test("ローカルSQLの偽装フラグでは反映できず、サーバー設定と実DB照合が必要", async () => {
  const f = fixture(); f.deps.binding.writable = false;
  assert.equal((await f.service.overview(actor)).writable, false);
  await assert.rejects(f.service.preview(actor, [id]), errorCode("CanonicalDatabaseRequired"));
  await assert.rejects(f.service.apply(actor, "anything"), errorCode("CanonicalDatabaseRequired"));
  const sql = { mode: "local-sql" as const, server: "localhost", database: "portal", host: "localhost", port: 14330, trustLocalCertificate: true };
  const apim = { mode: "managed-identity" as const, apimResourceId: resource };
  assert.equal(syncBinding(sql, apim, { NODE_ENV: "production", PORTAL_SUBSCRIPTION_SYNC_ENABLED: "true", PORTAL_CANONICAL_SQL_SERVER: "localhost", PORTAL_CANONICAL_SQL_DATABASE: "portal" }).writable, false);
  const azure = { ...sql, server: "sql-test.database.windows.net", mode: "managed-identity" as const, trustLocalCertificate: false };
  const env = { NODE_ENV: "production", PORTAL_SUBSCRIPTION_SYNC_ENABLED: "true", PORTAL_CANONICAL_SQL_SERVER: azure.server, PORTAL_CANONICAL_SQL_DATABASE: "portal" };
  assert.equal(syncBinding(azure, apim, env).writable, true);
  assert.equal(syncBinding({ ...azure, database: "other" }, apim, env).writable, false);
});

test("HTTPは未認証・CSRF・余分な反映値・大きな本文でサービスを起動しない", async () => {
  const origin = "https://portal.example.invalid"; let calls = 0;
  const deps = { readActor: async () => actor as typeof actor | null, readOrigin: () => origin,
    getService: () => { calls++; return fixture().service; } };
  const request = (value: unknown, headers: Record<string, string> = {}) => new Request(`${origin}/api/subscription-sync`, {
    method: "POST", headers: { Origin: origin, "Content-Type": "application/json", ...headers }, body: JSON.stringify(value) });
  assert.equal((await handleSubscriptionSync(request({ action: "preview", ids: [id] }), { ...deps, readActor: async () => null })).status, 401);
  assert.equal((await handleSubscriptionSync(request({ action: "preview", ids: [id] }, { Origin: "https://evil.invalid" }), deps)).status, 403);
  for (const body of [{ action: "preview", ids: [id], state: "active" }, { action: "preview", ids: [id, id] }, { action: "apply", token: "a", confirmed: false }, { action: "apply", token: "x".repeat(70_000), confirmed: true }]) {
    assert.equal((await handleSubscriptionSync(request(body), deps)).status, 400);
  }
  assert.equal(calls, 0);
});

test("APIM一覧は秘密を捨ててページを完走し、別宛先・不完全な応答を拒否する", async () => {
  let calls = 0;
  const settings = { apimResourceId: resource, mode: "azure-cli" as const, tenantId };
  const api = new AzureApiKeyApim(settings, { getAccessToken: async () => "test-token", fetch: async () => {
    calls++;
    return Response.json({ value: [{ id: `${resource}/subscriptions/${calls === 1 ? id : "orphan"}`, properties: { ...remote, primaryKey: "MUST_NOT_LEAK" } }],
      ...(calls === 1 ? { nextLink: `https://management.azure.com${resource}/subscriptions?api-version=2024-05-01&$skip=100` } : {}) });
  } });
  const rows = await api.listMetadata(); assert.equal(rows.length, 2); assert.equal(JSON.stringify(rows).includes("MUST_NOT_LEAK"), false);
  for (const nextLink of ["https://evil.invalid/page", `https://management.azure.com${resource}/apis?api-version=2024-05-01`]) {
    const bad = new AzureApiKeyApim(settings, { getAccessToken: async () => "test-token", fetch: async () => Response.json({ value: [], nextLink }) });
    await assert.rejects(bad.listMetadata(), errorCode("UpstreamUnavailable"));
  }
});

test("APIM更新はPATCHとETag、再作成はIf-None-Matchを使い停止状態を保持する", async () => {
  const calls: { method: string; headers: Headers; body: unknown }[] = [];
  const api = new AzureApiKeyApim({ apimResourceId: resource, mode: "azure-cli", tenantId }, { getAccessToken: async () => "test-token",
    fetch: async (_url, init) => { calls.push({ method: init!.method!, headers: new Headers(init?.headers), body: JSON.parse(String(init?.body)) }); return new Response(null, { status: init?.method === "PUT" ? 201 : 204 }); } });
  const desired = { displayName: "Company", productId: "released-apis", state: "suspended" as const };
  await api.reconcileMetadata(id, desired, '"v1"'); await api.reconcileMetadata(id, desired, null);
  assert.equal(calls[0].method, "PATCH"); assert.equal(calls[0].headers.get("If-Match"), '"v1"');
  assert.equal(calls[1].method, "PUT"); assert.equal(calls[1].headers.get("If-None-Match"), "*");
  for (const call of calls) { assert.equal((call.body as { properties: { state: string } }).properties.state, "suspended"); assert.equal(JSON.stringify(call.body).includes("primaryKey"), false); }
  await assert.rejects(api.reconcileMetadata("master", desired, null));
});
