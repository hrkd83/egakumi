import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { Kysely, MssqlAdapter, MssqlIntrospector, MssqlQueryCompiler,
  type CompiledQuery, type DatabaseConnection, type Driver, type QueryResult } from "kysely";
import { AzureApiKeyApim } from "../lib/api-keys/apim.ts";
import { SqlApiKeyRepository } from "../lib/api-keys/repository.ts";
import { ApiKeyService } from "../lib/api-keys/service.ts";
import { actor, tenantId, groupId, productId, input, errorCode, fixture } from "./helpers/api-key-service-fixture.ts";

// 実環境から独立した、依存注入先だけで使用する合成ARM識別子です。
const apimResourceId = `/subscriptions/${randomUUID()}/resourceGroups/rg-synthetic/providers/Microsoft.ApiManagement/service/apim-synthetic`;
const settings = { apimResourceId, mode: "azure-cli" as const, tenantId };
const targetRemoteId = "synthetic-pending-target";
const targetUrl = `https://management.azure.com${apimResourceId}/subscriptions/${targetRemoteId}?api-version=2024-05-01`;

test("通常削除の202をdeletingとして保持し、明示的な再確認でだけ確定して別キーを変更しない", async () => {
  const app = fixture();
  const target = await app.service.create(actor, input);
  const other = await app.service.create(actor, { ...input,
    idempotencyKey: randomUUID(), displayName: "独立した対照キー" });
  app.repository.records.set(target.id, { ...target, apimSubscriptionId: targetRemoteId });
  const otherBefore = structuredClone(app.repository.records.get(other.id));
  const otherRemoteBefore = structuredClone(app.subscriptions.get(other.id));
  const initialAudit = structuredClone(app.repository.auditEvents);
  const requests: { method: string; url: string; etag: string | null }[] = [];
  let remotePresent = true, revision = 1;
  const apim = new AzureApiKeyApim(settings, {
    getAccessToken: async () => "synthetic-arm-token",
    fetch: async (url, init) => {
      const request = { method: init?.method ?? "GET", url: String(url), etag: new Headers(init?.headers).get("If-Match") };
      requests.push(request);
      assert.equal(init?.redirect, "error");
      if (request.url === `https://management.azure.com${apimResourceId}/apis/openai-inference-v1/products?api-version=2024-05-01`) {
        assert.equal(request.method, "GET");
        return Response.json({ value: [{ id: `${apimResourceId}/products/${productId}` }] });
      }
      assert.equal(request.url, targetUrl, "台帳内部IDや別購読へ要求しない");
      if (request.method === "GET") {
        return remotePresent ? Response.json({ id: `${apimResourceId}/subscriptions/${targetRemoteId}`,
          properties: { displayName: target.displayName, state: "active", scope: `${apimResourceId}/products/${productId}` } },
        { headers: { etag: `"synthetic-${revision}"` } }) : new Response(null, { status: 404 });
      }
      assert.equal(request.method, "DELETE", "秘密取得・停止・再生成に進まない");
      assert.equal(request.etag, `"synthetic-${revision}"`, "直前GETのETagを使用する");
      revision++;
      return new Response(null, { status: 202 });
    },
  });
  const service = new ApiKeyService({ tenantId, repository: app.repository, apim,
    membership: { check: async (_member, ids) => new Set(ids) },
    directory: {
      listEligibleGroups: async () => [{ id: groupId, displayName: "合成グループ", securityEnabled: true }],
      listEligibleGroupPage: async () => ({ groups: [], nextCursor: null }),
      checkGroups: async (_member, ids) => new Map(ids.map((id) => [id, {
        id, displayName: "合成グループ", securityEnabled: true, status: "active" as const,
        checkedAt: "2026-01-01T00:00:00Z", errorCode: null,
      }])),
    },
  });

  const pending = await service.act(actor, target.id, "delete");
  assert.equal("state" in pending && pending.state, "deleting");
  assert.deepEqual(requests.map((entry) => entry.method), ["GET", "DELETE"], "202を自動再送しない");
  assert.deepEqual(app.repository.auditEvents, initialAudit, "未完了の削除を監査上の完了にしない");
  for (const action of ["reveal", "suspend", "resume", "review"]) {
    await assert.rejects(service.act(actor, target.id, action), errorCode("SubscriptionNotReady"));
  }
  await assert.rejects(service.executionCredential(actor, target.id, "openai-inference-v1"), errorCode("NotFound"));
  await assert.rejects(service.ownerTargets(actor, target.id, "合成"), errorCode("SubscriptionNotReady"));
  await assert.rejects(service.create(actor, input), errorCode("IdempotencyConflict"));
  assert.equal(requests.length, 2, "削除待ちの拒否では上流や秘密に到達しない");
  const overview = await service.overview(actor);
  assert.equal(overview.keys.find((key) => key.id === target.id)?.state, "deleting");
  assert.deepEqual((await service.executionChoices(actor, "openai-inference-v1")).keys.map((key) => key.id), [other.id]);

  const second = await service.act(actor, target.id, "delete");
  assert.equal("state" in second && second.state, "deleting");
  const targetRequests = () => requests.filter((entry) => entry.url === targetUrl);
  assert.deepEqual(targetRequests().map((entry) => [entry.method, entry.etag]), [
    ["GET", null], ["DELETE", '"synthetic-1"'], ["GET", null], ["DELETE", '"synthetic-2"'],
  ]);
  assert.deepEqual(app.repository.auditEvents, initialAudit);
  remotePresent = false; // 非同期上流の完了を、独立した次回GETの404として与える。
  const deleted = await service.act(actor, target.id, "delete");
  assert.equal("state" in deleted && deleted.state, "deleted");
  assert.deepEqual(targetRequests().map((entry) => entry.method), ["GET", "DELETE", "GET", "DELETE", "GET"]);
  const afterConfirmed = structuredClone(requests);
  const repeated = await service.act(actor, target.id, "delete");
  assert.equal("state" in repeated && repeated.state, "deleted");
  await assert.rejects(service.act(actor, target.id, "reveal"), errorCode("NotFound"));
  assert.deepEqual(requests, afterConfirmed, "確定後の再削除はGETもDELETEも再送しない");
  assert.deepEqual((await service.overview(actor)).keys.map((key) => key.id), [other.id]);
  assert.deepEqual(app.repository.auditEvents, [...initialAudit, { actorId: actor.objectId, keyId: target.id, action: "delete" }]);
  assert.deepEqual(app.repository.records.get(other.id), otherBefore);
  assert.deepEqual(app.subscriptions.get(other.id), otherRemoteBefore);
});

/** 本番MSSQLコンパイラーへ状態行だけを返し、SQLの早期拒否を独立に観測します。 */
function stateRepository(state: "creating" | "deleting" | "deleted", id: string) {
  const commands: CompiledQuery[] = [];
  let rollbacks = 0;
  const connection: DatabaseConnection = {
    async executeQuery<R>(query: CompiledQuery): Promise<QueryResult<R>> {
      commands.push(query);
      if (query.sql.includes("sp_getapplock")) return { rows: [] };
      assert.match(query.sql, /SELECT[\s\S]*FROM dbo\.portalApiKey WHERE tenantId=/);
      assert.deepEqual(query.parameters, [tenantId, id]);
      return { rows: [{ id, groupId, state }] as R[] };
    },
    async *streamQuery<R>(): AsyncIterableIterator<QueryResult<R>> { throw new Error("合成試験でストリームを使用しない"); },
  };
  const driver: Driver = {
    async init() {}, async acquireConnection() { return connection; }, async beginTransaction() {},
    async commitTransaction() { assert.fail("拒否した移管をcommitしない"); },
    async rollbackTransaction() { rollbacks++; }, async releaseConnection() {}, async destroy() {},
  };
  const database = new Kysely<Record<string, unknown>>({ dialect: {
    createAdapter: () => new MssqlAdapter(), createDriver: () => driver,
    createQueryCompiler: () => new MssqlQueryCompiler(), createIntrospector: (db) => new MssqlIntrospector(db),
  } });
  return { repository: new SqlApiKeyRepository(database), database, commands, get rollbacks() { return rollbacks; } };
}

test("本番所有権リポジトリは作成中・削除待ち・削除済みの行を移管前に拒否する", async () => {
  const id = `portal-${randomUUID()}`;
  const targetGroupId = randomUUID();
  for (const state of ["creating", "deleting", "deleted"] as const) {
    const f = stateRepository(state, id);
    let authorizeCalls = 0;
    try {
      await assert.rejects(f.repository.transferOwnership(actor, id, { expectedSourceGroupId: groupId, targetGroupId },
        async () => { authorizeCalls++; }), errorCode(state === "creating" ? "SubscriptionNotReady" : "NotFound"));
      assert.equal(authorizeCalls, 0);
      assert.equal(f.commands.length, 2, "キー排他と対象行SELECTだけで停止する");
      assert.deepEqual(f.commands[0].parameters, [`portal-api-key:${tenantId}:${id}`]);
      assert.equal(f.rollbacks, 1);
      assert.ok(f.commands.every((query) => !/\b(?:UPDATE|INSERT|DELETE)\b/i.test(query.sql)), "台帳・所有監査・別キーを変更しない");
    } finally { await f.database.destroy(); }
  }
});

test("APIM削除は202だけ未完了とし、正確なIDとETagで一度だけ要求する", async () => {
  for (const status of [202, 200, 204, 404, 412, 500]) {
    const requests: { url: string; method?: string; etag: string | null }[] = [];
    let tokenCalls = 0;
    const apim = new AzureApiKeyApim(settings, {
      getAccessToken: async () => { tokenCalls++; return "synthetic-arm-token"; },
      fetch: async (url, init) => {
        requests.push({ url: String(url), method: init?.method, etag: new Headers(init?.headers).get("If-Match") });
        assert.equal(init?.redirect, "error");
        assert.equal(init?.body, undefined);
        return new Response(null, { status });
      },
    });
    if (status === 412 || status === 500) {
      await assert.rejects(apim.delete(targetRemoteId, '"synthetic-etag"'), errorCode(status === 412 ? "UpstreamConflict" : "UpstreamUnavailable"));
    } else {
      assert.equal(await apim.delete(targetRemoteId, '"synthetic-etag"'), status !== 202);
    }
    assert.deepEqual(requests, [{ url: targetUrl, method: "DELETE", etag: '"synthetic-etag"' }]);
    assert.equal(tokenCalls, 1, "失敗や202を自動再試行しない");
    await assert.rejects(apim.delete(targetRemoteId, "*"), errorCode("UpstreamUnavailable"));
    assert.equal(requests.length, 1);
    assert.equal(tokenCalls, 1);
  }
});
