import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { Kysely, MssqlAdapter, MssqlIntrospector, MssqlQueryCompiler,
  type CompiledQuery, type DatabaseConnection, type Driver, type QueryResult } from "kysely";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import type { ManagedApiKeySubscription } from "../lib/api-keys/apim.ts";
import { apiKeySubscriptionImportHash, apiKeySubscriptionImportSchema, importApiKeySubscriptions,
  readApiKeySubscriptionImport, reserveApiKeySubscriptionImports, stableApiKeyImportId, type ApiKeySubscriptionImport } from "../scripts/api-key-subscription-import.ts";
import { bootstrapApiKeySubscriptions, readApiKeyBootstrapApimEnvironment, readApiKeyVerificationSubscriptions } from "../scripts/bootstrap-api-key-subscriptions.ts";
import { runLocalApiKeySubscriptionImport } from "../scripts/import-local-api-key-subscriptions.ts";

const tenantId = "11111111-1111-4111-8111-111111111111";
const platformGroupId = "22222222-2222-4222-8222-222222222222";
const otherGroupId = "33333333-3333-4333-8333-333333333333";
const runtimeClientId = "44444444-4444-4444-8444-444444444444";
const resource = `/subscriptions/${tenantId}/resourceGroups/rg-test/providers/Microsoft.ApiManagement/service/apim-test`;
const verification = { tenantId, platformGroupId, apimResourceId: resource,
  subscriptions: [{ subscriptionId: "existing-model-test", productId: "model-users", displayName: "基盤管理テスト" }] };
const manifest = (): ApiKeySubscriptionImport => apiKeySubscriptionImportSchema.parse({ ...verification,
  subscriptions: verification.subscriptions.map((record) => ({ ...record, state: "active", scope: `${resource}/products/${record.productId}` })) });
type Key = { id: string; tenantId: string; groupId: string; productId: string; displayName: string; state: string; apimSubscriptionId: string; creationSource: string };

/** 実際のMSSQLコンパイラーを使い、台帳・監査の書込とロールバックを観測する接続です。 */
function fixture() {
  const commands: CompiledQuery[] = [], keys = new Map<string, Key>(), audits: unknown[][] = [];
  let maximum = 5, approved = true, configuredGroupId = platformGroupId;
  let activeTransactions = 0;
  let snapshot: { keys: [string, Key][]; audits: unknown[][] } | undefined;
  const count = (product: string) => [...keys.values()].filter((key) => key.groupId === platformGroupId && key.productId === product && key.state !== "deleted").length;
  const connection: DatabaseConnection = {
    async executeQuery<R>(query: CompiledQuery): Promise<QueryResult<R>> {
      commands.push(query); const p = query.parameters; let rows: unknown[] = [];
      if (query.sql.includes("SELECT platformGroupId, groupNamePattern")) rows = [{ platformGroupId: configuredGroupId, groupNamePattern: "^ORG_" }];
      else if (query.sql.includes("SELECT id,groupId,productId,state,creationSource,apimSubscriptionId FROM dbo.portalApiKey")) {
        const key = keys.get(String(p[1]).toLowerCase()); rows = key ? [{ ...key }] : [];
      } else if (query.sql.includes("SELECT p.maxSubscriptions")) rows = approved
        ? [{ maxSubscriptions: maximum, amount: count(String(p.at(-1))) }] : [];
      else if (query.sql.includes("SELECT COUNT(*) AS amount")) rows = [{ amount: count(String(p.at(-1))) }];
      else if (/INSERT INTO dbo\.portalApiKey\(/.test(query.sql)) {
        assert.ok(query.sql.includes("'unlimited'")); assert.ok(query.sql.includes("NULL")); assert.ok(query.sql.includes("'deployment'"));
        const [id, tenant, groupId, productId, displayName, state, idempotencyKey, actual] = p.map(String);
        assert.equal(idempotencyKey, id.slice(7));
        keys.set(actual.toLowerCase(), { id, tenantId: tenant, groupId, productId, displayName, state, apimSubscriptionId: actual, creationSource: "deployment" });
      } else if (query.sql.includes("UPDATE dbo.portalApiKey SET state=")) {
        const key = [...keys.values()].find((entry) => entry.id === p[2]); if (key) key.state = String(p[0]);
      } else if (query.sql.includes("INSERT INTO dbo.portalApiKeyImportAudit")) audits.push([...p]);
      return { rows: rows as R[] };
    },
    async *streamQuery<R>(): AsyncIterableIterator<QueryResult<R>> { yield { rows: [] as R[] }; },
  };
  const driver: Driver = { async init() {}, async acquireConnection() { return connection; }, async releaseConnection() {}, async destroy() {},
    async beginTransaction() { activeTransactions++; snapshot = { keys: structuredClone([...keys]), audits: structuredClone(audits) }; },
    async commitTransaction() { activeTransactions--; snapshot = undefined; }, async rollbackTransaction() {
      activeTransactions--;
      keys.clear(); for (const [id, key] of snapshot?.keys ?? []) keys.set(id, key);
      audits.splice(0, audits.length, ...snapshot?.audits ?? []); snapshot = undefined;
    } };
  const database = new Kysely<Record<string, unknown>>({ dialect: { createAdapter: () => new MssqlAdapter(),
    createDriver: () => driver, createQueryCompiler: () => new MssqlQueryCompiler(), createIntrospector: (db) => new MssqlIntrospector(db) } });
  return { database, keys, audits, commands, maximum: (value: number) => { maximum = value; },
    denyProduct: () => { approved = false; }, differentPlatform: () => { configuredGroupId = otherGroupId; }, get activeTransactions() { return activeTransactions; } };
}

/** APIMメタデータだけを差し替え、秘密取得や状態変更の機能を持たせない依存です。 */
function apimFixture(initial: ManagedApiKeySubscription | null = null) {
  let current = initial;
  const calls: string[] = [];
  const apim = { scopeFor: (productId: string) => `${resource}/products/${productId}`,
    get: async (id: string) => { calls.push(`get:${id}`); return current; },
    create: async (id: string, productId: string, displayName: string) => {
      calls.push(`create:${id}`); current = { id, displayName, state: "active", scope: `${resource}/products/${productId}` }; return current;
    } };
  return { apim, calls };
}

test("登録マニフェストは明示ID・製品スコープに限定し、秘密・master・別環境を拒否する", () => {
  assert.equal(readApiKeySubscriptionImport({}), null);
  const value = manifest(), record = value.subscriptions[0]!;
  for (const candidate of [
    { ...value, primaryKey: "must-not-store" }, { ...value, subscriptions: [{ ...record, primaryKey: "must-not-store" }] },
    { ...value, subscriptions: [{ ...record, subscriptionId: "master" }] },
    { ...value, subscriptions: [{ ...record, scope: `${resource}/apis` }] },
    { ...value, subscriptions: [{ ...record, state: "submitted" }] },
    { ...value, apimResourceId: resource.replace("rg-test", "rg-test\\outside") },
    { ...value, subscriptions: [record, { ...record, subscriptionId: record.subscriptionId.toUpperCase() }] },
  ]) assert.equal(apiKeySubscriptionImportSchema.safeParse(candidate).success, false);
  const env = { PORTAL_API_KEY_SUBSCRIPTION_IMPORT: JSON.stringify(value), PORTAL_API_KEY_TENANT_ID: tenantId, PORTAL_APIM_RESOURCE_ID: resource };
  assert.ok(readApiKeySubscriptionImport(env));
  assert.throws(() => readApiKeySubscriptionImport({ ...env, PORTAL_APIM_RESOURCE_ID: resource.replace("apim-test", "apim-other") }));
  assert.throws(() => readApiKeySubscriptionImport({ ...env, PORTAL_API_KEY_TENANT_ID: otherGroupId }));
});

test("内部IDと監査hashは表記や順序で変わらず、テナント・APIM・購読の変更で区別する", () => {
  const value = manifest(), input = { ...value, subscriptionId: value.subscriptions[0]!.subscriptionId };
  const id = stableApiKeyImportId(input);
  assert.match(id, /^portal-[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(stableApiKeyImportId({ ...input, apimResourceId: resource.toUpperCase(), subscriptionId: input.subscriptionId.toUpperCase() }), id);
  for (const changed of [{ tenantId: otherGroupId }, { apimResourceId: resource.replace("apim-test", "apim-other") }, { subscriptionId: "another-test" }]) {
    assert.notEqual(stableApiKeyImportId({ ...input, ...changed }), id);
  }
  const additional = { ...value.subscriptions[0]!, subscriptionId: "second" };
  assert.equal(apiKeySubscriptionImportHash({ ...value, subscriptions: [additional, value.subscriptions[0]!] }),
    apiKeySubscriptionImportHash({ ...value, apimResourceId: resource.toUpperCase(), subscriptions: [value.subscriptions[0]!, additional] }));
});

test("初回だけ無制限・system由来で登録し、移管・削除後の再実行も既存状態と監査を保持する", async () => {
  const f = fixture();
  try {
    const first = await importApiKeySubscriptions(f.database, manifest(), resource);
    assert.equal(first.imported, 1); assert.equal(first.skipped, 0); assert.equal(f.audits.length, 1);
    assert.equal(f.audits[0]![1], first.runId); assert.equal(f.audits[0]![2], first.manifestHash);
    const key = f.keys.get("existing-model-test")!;
    key.groupId = otherGroupId; key.state = "suspended";
    const snapshot = structuredClone(key), audit = structuredClone(f.audits);
    const changed = manifest(); changed.subscriptions[0]!.displayName = "配備で上書きしない";
    assert.equal((await importApiKeySubscriptions(f.database, changed, resource)).skipped, 1);
    assert.deepEqual(key, snapshot); assert.deepEqual(f.audits, audit);
    key.state = "deleted";
    assert.equal((await importApiKeySubscriptions(f.database, manifest(), resource)).skipped, 1);
    assert.equal(key.state, "deleted"); assert.deepEqual(f.audits, audit);
    assert.equal(f.commands.some((command) => /UPDATE dbo.portalApiKey|portalApiKeyLimit\b|portalApiKeyAudit\b/.test(command.sql)), false);
  } finally { await f.database.destroy(); }
});

test("配備承認・管理グループ・発行枠を確認し、途中で上限超過した登録全体を戻す", async () => {
  for (const failure of ["product", "platform", "capacity"] as const) {
    const f = fixture();
    try {
      if (failure === "product") f.denyProduct(); else if (failure === "platform") f.differentPlatform(); else f.maximum(1);
      const value = manifest();
      if (failure === "capacity") value.subscriptions.push({ ...value.subscriptions[0]!, subscriptionId: "second" });
      await assert.rejects(importApiKeySubscriptions(f.database, value, resource));
      assert.equal(f.keys.size, 0); assert.equal(f.audits.length, 0);
    } finally { await f.database.destroy(); }
  }
});

test("構築用の明示設定だけを受け、APIM実行MIとSQL実行MIを分離する", () => {
  assert.equal(readApiKeyVerificationSubscriptions({}), null);
  const env = { PORTAL_API_KEY_TENANT_ID: tenantId, PORTAL_API_KEY_PLATFORM_GROUP_ID: platformGroupId,
    PORTAL_APIM_RESOURCE_ID: resource, PORTAL_API_KEY_VERIFICATION_SUBSCRIPTIONS: JSON.stringify(verification.subscriptions) };
  assert.deepEqual(readApiKeyVerificationSubscriptions(env), verification);
  assert.throws(() => readApiKeyVerificationSubscriptions({ ...env, PORTAL_API_KEY_VERIFICATION_SUBSCRIPTIONS: '[{"subscriptionId":"master","productId":"model-users","displayName":"x"}]' }));
  const actual = readApiKeyBootstrapApimEnvironment({ ...env, NODE_ENV: "production", PORTAL_API_KEY_APIM_CLIENT_ID: runtimeClientId, AZURE_CLIENT_ID: otherGroupId });
  assert.equal(actual.clientId, runtimeClientId); assert.equal(actual.mode, "managed-identity");
  assert.throws(() => readApiKeyBootstrapApimEnvironment({ ...env, NODE_ENV: "production", AZURE_CLIENT_ID: otherGroupId }));
});

test("台帳登録済みなら移管・削除を巻き戻さず、APIMへの照会や再作成も行わない", async () => {
  const f = fixture(), apim = apimFixture();
  try {
    f.keys.set("existing-model-test", { id: "existing", tenantId, groupId: otherGroupId, productId: "model-users",
      displayName: "旧名称", state: "deleted", apimSubscriptionId: "existing-model-test", creationSource: "deployment" });
    const result = await bootstrapApiKeySubscriptions(f.database, verification, apim.apim);
    assert.equal(result.skipped, 1); assert.equal(result.imported, 0); assert.equal(result.created, 0);
    assert.deepEqual(apim.calls, []); assert.equal(f.keys.get("existing-model-test")!.state, "deleted");
  } finally { await f.database.destroy(); }
});

test("APIM既存購読は更新せず登録し、表示名は配備正本の名称を使う", async () => {
  const f = fixture(), apim = apimFixture({ id: "existing-model-test", displayName: "", scope: `${resource}/products/model-users`, state: "suspended" });
  try {
    const result = await bootstrapApiKeySubscriptions(f.database, verification, apim.apim);
    assert.equal(result.imported, 1); assert.equal(result.created, 0); assert.deepEqual(apim.calls, ["get:existing-model-test", "get:existing-model-test"]);
    assert.equal(f.keys.get("existing-model-test")!.displayName, "基盤管理テスト");
    assert.equal(f.keys.get("existing-model-test")!.state, "suspended");
  } finally { await f.database.destroy(); }
});

test("404の購読だけを一度作り、再実行では台帳対応によりAPIMも監査も増やさない", async () => {
  const f = fixture(), apim = apimFixture();
  try {
    const first = await bootstrapApiKeySubscriptions(f.database, verification, apim.apim);
    assert.equal(first.imported, 1); assert.equal(first.created, 1);
    assert.deepEqual(apim.calls, ["get:existing-model-test", "get:existing-model-test", "create:existing-model-test"]);
    const second = await bootstrapApiKeySubscriptions(f.database, verification, apim.apim);
    assert.equal(second.skipped, 1); assert.equal(second.created, 0); assert.equal(f.audits.length, 1);
    assert.equal(apim.calls.length, 3);
  } finally { await f.database.destroy(); }
});

test("APIM通信失敗や製品不一致を404と混同せず、別購読の新規発行も開始しない", async () => {
  for (const failure of ["network", "scope", "state"] as const) {
    const f = fixture(); const calls: string[] = [];
    try {
      const input = { ...verification, subscriptions: [{ ...verification.subscriptions[0]!, subscriptionId: "missing-first" }, ...verification.subscriptions] };
      await assert.rejects(bootstrapApiKeySubscriptions(f.database, input, {
        scopeFor: (productId) => `${resource}/products/${productId}`,
        get: async (id) => {
          calls.push(`get:${id}`); if (failure === "network") throw new ApiKeyError("UpstreamUnavailable", 503);
          if (id === "missing-first") return null;
          return { id, displayName: "existing", scope: `${resource}/products/${failure === "scope" ? "pii-users" : "model-users"}`,
            state: failure === "state" ? "submitted" : "active" };
        }, create: async () => { throw new Error("must not create"); },
      }));
      assert.equal(f.keys.size, 0); assert.equal(f.audits.length, 0);
      assert.equal(calls.some((call) => call.startsWith("create:")), false);
    } finally { await f.database.destroy(); }
  }
});

test("If-None-Matchで競合した場合は既存購読を読み直し、無条件の上書きを行わない", async () => {
  const f = fixture(); let reads = 0, creates = 0;
  try {
    const result = await bootstrapApiKeySubscriptions(f.database, verification, {
      scopeFor: (productId) => `${resource}/products/${productId}`,
      get: async (id) => ++reads <= 2 ? null : { id, displayName: "old", scope: `${resource}/products/model-users`, state: "active" },
      create: async () => { creates++; throw new ApiKeyError("UpstreamConflict", 409); },
    });
    assert.equal(result.imported, 1); assert.equal(result.created, 0); assert.equal(reads, 3); assert.equal(creates, 1);
  } finally { await f.database.destroy(); }
});

test("APIM作成より先にSQL枠を予約し、応答喪失後もcreatingから同じ実IDで再開する", async () => {
  const f = fixture(); let remote: ManagedApiKeySubscription | null = null, creates = 0;
  const apim = { scopeFor: (product: string) => `${resource}/products/${product}`,
    get: async () => { assert.equal(f.activeTransactions, 0, "HTTP中はSQLトランザクションを保持しない"); return remote; },
    create: async (id: string, product: string, displayName: string) => {
      assert.equal(f.activeTransactions, 0);
      assert.equal(f.keys.get(id)?.state, "creating", "作成前に追跡可能な予約と発行枠を確定する");
      creates++; remote = { id, displayName, scope: `${resource}/products/${product}`, state: "active" };
      throw new ApiKeyError("UpstreamUnavailable", 503);
    } };
  try {
    await assert.rejects(bootstrapApiKeySubscriptions(f.database, verification, apim));
    assert.equal(f.keys.get("existing-model-test")?.state, "creating"); assert.equal(f.audits.length, 0);
    assert.ok(f.commands.some((command) => command.sql.includes("sp_releaseapplock")), "HTTP失敗でもキー排他を解放する");
    const result = await bootstrapApiKeySubscriptions(f.database, verification, apim);
    assert.equal(result.imported, 1); assert.equal(result.created, 0); assert.equal(creates, 1);
    assert.equal(f.keys.get("existing-model-test")?.state, "active"); assert.equal(f.audits.length, 1);
  } finally { await f.database.destroy(); }
});

test("事前照会中に枠が埋まるか削除済み台帳が現れても、APIMを新規作成しない", async () => {
  for (const race of ["capacity", "deleted"] as const) {
    const f = fixture(); let creates = 0;
    try {
      f.maximum(1);
      const bootstrap = bootstrapApiKeySubscriptions(f.database, verification, {
        scopeFor: (product) => `${resource}/products/${product}`,
        get: async () => {
          const actualId = race === "capacity" ? "concurrent-ui-key" : "existing-model-test";
          f.keys.set(actualId, { id: "portal-99999999-9999-4999-8999-999999999999", tenantId, groupId: platformGroupId,
            productId: "model-users", displayName: "concurrent", state: race === "capacity" ? "active" : "deleted",
            apimSubscriptionId: actualId, creationSource: "portal" });
          return null;
        },
        create: async () => { creates++; throw new Error("must not create"); },
      });
      if (race === "capacity") await assert.rejects(bootstrap);
      else assert.equal((await bootstrap).skipped, 1);
      assert.equal(creates, 0); assert.equal(f.audits.length, 0);
    } finally { await f.database.destroy(); }
  }
});

test("検証済みの直接登録もdeploymentのcreating予約だけを確定し、通常発行の予約を触らない", async () => {
  const f = fixture();
  try {
    const reservation = await reserveApiKeySubscriptionImports(f.database, manifest(), resource);
    assert.equal(reservation.reserved.length, 1); assert.equal(f.keys.get("existing-model-test")?.state, "creating");
    assert.equal((await importApiKeySubscriptions(f.database, manifest(), resource)).imported, 1);
    assert.equal(f.keys.get("existing-model-test")?.state, "active"); assert.equal(f.audits.length, 1);
    const key = f.keys.get("existing-model-test")!; key.state = "creating"; key.creationSource = "portal";
    assert.equal((await importApiKeySubscriptions(f.database, manifest(), resource)).skipped, 1);
    assert.equal(key.state, "creating"); assert.equal(f.audits.length, 1);
  } finally { await f.database.destroy(); }
});

test("ローカルCLIの既定planはSQL環境を要求せず、キーを取得せず変更を行わない", async () => {
  const folder = await mkdtemp(join(tmpdir(), "portal-subscription-plan-"));
  const output: string[] = [], originalLog = console.log;
  try {
    const path = join(folder, "manifest.json"); await writeFile(path, JSON.stringify(manifest()));
    console.log = (value: unknown) => { output.push(String(value)); };
    await runLocalApiKeySubscriptionImport(["--manifest", path], { PORTAL_APIM_RESOURCE_ID: resource });
    const plan = JSON.parse(output[0]!);
    assert.equal(plan.mode, "plan"); assert.equal(plan.sqlWrites, 0); assert.equal(plan.azureWrites, 0);
    assert.equal(plan.subscriptions[0].subscriptionId, "existing-model-test");
    assert.equal(JSON.stringify(plan).includes("primaryKey"), false);
  } finally {
    console.log = originalLog;
    assert.equal(dirname(resolve(folder)), resolve(tmpdir()));
    await rm(folder, { recursive: true, force: true });
  }
});
