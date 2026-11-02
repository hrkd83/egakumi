import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { loadApiKeyAdministration, mergeAdministrationPages, type ApiKeyAdministrationOverview } from "../components/api-key-administration/client.ts";
import { apiKeyErrorCode } from "../components/api-keys/client.ts";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const cursor = "00000000-0000-4000-8000-000000000001";
const summary = { totalGroups: 2000, activeGroups: 1800, deletedGroups: 100, ineligibleGroups: 50, unknownGroups: 50, activeSubscriptions: 5000, affectedSubscriptions: 300 };

test("確認不能と削除済みを区別し、キーの秘密情報を採用しない", async () => {
  globalThis.fetch = async (input, init) => {
    assert.equal(input, "/api/api-key-administration");
    assert.equal(init?.cache, "no-store");
    assert.equal(init?.credentials, "same-origin");
    return Response.json({ groups: [{ id: cursor, displayName: "Business Team", directoryStatus: "unknown", lastCheckedAt: null, lastErrorCode: "GraphUnavailable", activeSubscriptions: 2, affectedSubscriptions: 2, primaryKey: "never-keep" }], checkedAt: null, syncErrors: 1, nextCursor: cursor, summary });
  };
  const result = await loadApiKeyAdministration();
  assert.equal(result.groups[0].directoryStatus, "unknown");
  assert.equal(result.groups[0].lastCheckedAt, null);
  assert.equal("primaryKey" in result.groups[0], false);
  assert.equal(result.summary.totalGroups, 2000);
  assert.equal(result.nextCursor, cursor);
});

test("手動再確認は専用POSTでだけ要求する", async () => {
  globalThis.fetch = async (_input, init) => {
    assert.equal(init?.method, "POST");
    assert.deepEqual(JSON.parse(String(init?.body)), { action: "refresh" });
    return Response.json({ groups: [], checkedAt: null, syncErrors: 0, nextCursor: null, summary });
  };
  assert.deepEqual(await loadApiKeyAdministration(true), { groups: [], checkedAt: null, syncErrors: 0, nextCursor: null, summary });
});

test("非管理者への拒否や壊れた結果を正常な空一覧に変換しない", async () => {
  globalThis.fetch = async () => Response.json({ error: { code: "AccessDenied" }, privateDetails: "do-not-show" }, { status: 403 });
  await assert.rejects(loadApiKeyAdministration(), (error) => apiKeyErrorCode(error) === "AccessDenied" && !String(error).includes("do-not-show"));
  globalThis.fetch = async () => Response.json({ groups: [] });
  await assert.rejects(loadApiKeyAdministration(), (error) => apiKeyErrorCode(error) === "ServiceUnavailable");
});

test("続きの取得は管理者APIへカーソルを送り、集計のない不完全なページを受け入れない", async () => {
  globalThis.fetch = async (input, init) => {
    assert.equal(input, `/api/api-key-administration?cursor=${cursor}`);
    assert.equal(init?.method, "GET");
    return Response.json({ groups: [], checkedAt: null, syncErrors: 0, nextCursor: null, summary });
  };
  assert.equal((await loadApiKeyAdministration(false, undefined, cursor)).nextCursor, null);
  globalThis.fetch = async () => Response.json({ groups: [], checkedAt: null, syncErrors: 0, nextCursor: null });
  await assert.rejects(loadApiKeyAdministration(false, undefined, cursor), (error) => apiKeyErrorCode(error) === "ServiceUnavailable");
});

test("追加ページでは重複グループを最新観測へ置き換え、全体集計を二重加算しない", () => {
  const group = { id: "abcdef00-0000-4000-8000-000000000001", displayName: "Old Team", directoryStatus: "unknown" as const, lastCheckedAt: null, lastErrorCode: null, activeSubscriptions: 2, affectedSubscriptions: 2 };
  const previous: ApiKeyAdministrationOverview = { groups: [group], checkedAt: null, syncErrors: 1, nextCursor: cursor, summary };
  const page: ApiKeyAdministrationOverview = {
    groups: [{ ...group, id: group.id.toUpperCase(), displayName: "Current Team", directoryStatus: "active", affectedSubscriptions: 0 }, { ...group, id: "00000000-0000-4000-8000-000000000002" }],
    checkedAt: "2026-10-30T00:00:00Z", syncErrors: 0, nextCursor: null, summary: { ...summary, affectedSubscriptions: 298 },
  };
  const result = mergeAdministrationPages(previous, page);
  assert.equal(result.groups.length, 2);
  assert.equal(result.groups[0].displayName, "Current Team");
  assert.equal(result.groups[0].directoryStatus, "active");
  assert.equal(result.summary.totalGroups, 2000);
  assert.equal(result.summary.affectedSubscriptions, 298);
  assert.equal(result.syncErrors, 0);
  assert.equal(result.nextCursor, null);
  assert.equal(previous.groups[0].displayName, "Old Team");
});

test("不正なカーソルや再確認へのカーソル指定は通信前に拒否する", async () => {
  globalThis.fetch = async () => { assert.fail("must not request invalid pagination"); };
  await assert.rejects(loadApiKeyAdministration(false, undefined, "not-a-group-id"));
  await assert.rejects(loadApiKeyAdministration(true, undefined, cursor));
});
