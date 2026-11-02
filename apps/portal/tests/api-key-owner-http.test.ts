import assert from "node:assert/strict";
import { test } from "node:test";
import { handleApiKeyOwner, type ApiKeyOwnerHttpDependencies } from "../lib/api-keys/owner-http.ts";
import { ApiKeyError, type ApiKeyRecord } from "../lib/api-keys/contracts.ts";

const origin = "https://portal.example.invalid";
const actor = { tenantId: "11111111-1111-4111-8111-111111111111", objectId: "22222222-2222-4222-8222-222222222222" };
const record: ApiKeyRecord = { id: "portal-33333333-3333-4333-8333-333333333333", groupId: "44444444-4444-4444-8444-444444444444",
  apimSubscriptionId: "portal-33333333-3333-4333-8333-333333333333",
  productId: "model-users", displayName: "Example application", state: "active", budgetMode: "unlimited", clientType:null,systemName:null,teamName:null,companyId:null,companyName:null,contactEmail:null,
  createdAt: "2026-10-30T00:00:00.000Z", updatedAt: "2026-10-30T00:00:00.000Z", lastReviewedAt: null };
const target = "55555555-5555-4555-8555-555555555555";
const input = { targetGroupId: target, expectedSourceGroupId: record.groupId };

/** 実際の入口を通して認証前の操作がないことと、渡す本人識別子を検証します。 */
function fixture() {
  const calls: unknown[][] = [];
  const dependencies: ApiKeyOwnerHttpDependencies = { readActor: async () => actor, readOrigin: () => origin,
    getService: () => ({ ownerTargets: async (...args) => { calls.push(["targets", ...args]); return { groups: [{ id: target,
      displayName: "ORG_Target", products: [], isMember: false }], nextCursor: "next" }; },
    transfer: async (...args) => { calls.push(["transfer", ...args]); return { ...record, groupId: target }; } }) };
  return { dependencies, calls };
}
/** Originを含む、実画面と同じ所有者移管要求です。 */
function post(body: unknown = input, headers: Record<string, string> = {}): Request {
  return new Request(`${origin}/api/api-keys/${record.id}/owner`, { method: "POST", headers: {
    Origin: origin, "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) });
}

test("移管候補の未所属とページ続きを明示し、サーバーで確認した本人だけを操作へ渡す", async () => {
  const { dependencies, calls } = fixture();
  const listed = await handleApiKeyOwner(new Request(`${origin}/api/api-keys/${record.id}/owner?search=ORG&cursor=current`), dependencies, record.id);
  assert.equal(listed.status, 200);
  const payload = await listed.json();
  assert.equal(payload.groups[0].isMember, false);
  assert.equal(payload.nextCursor, "next");
  const updated = await handleApiKeyOwner(post(), dependencies, record.id);
  assert.equal(updated.status, 200);
  assert.equal((await updated.json()).groupId, target);
  assert.deepEqual(calls, [["targets", actor, record.id, "ORG", "current"], ["transfer", actor, record.id, input]]);
  assert.equal(updated.headers.get("cache-control"), "no-store");
});

test("未認証・CSRF・不正な検索/本文を移管操作へ到達させない", async () => {
  const { dependencies, calls } = fixture(); dependencies.readActor = async () => null;
  assert.equal((await handleApiKeyOwner(post(), dependencies, record.id)).status, 401);
  dependencies.readActor = async () => actor;
  for (const request of [post(input, { Origin: "" }), post(input, { "Sec-Fetch-Site": "cross-site" })]) {
    assert.equal((await handleApiKeyOwner(request, dependencies, record.id)).status, 403);
  }
  for (const request of [post({ ...input, ownerId: actor.objectId }), post({ ...input, note: "x".repeat(5000) }),
    new Request(`${origin}/api/api-keys/${record.id}/owner?search=A`),
    new Request(`${origin}/api/api-keys/${record.id}/owner?search=ORG&search=Other`),
    new Request(`${origin}/api/api-keys/${record.id}/owner?search=ORG&tenantId=foreign`),
  ]) assert.equal((await handleApiKeyOwner(request, dependencies, record.id)).status, 400);
  assert.equal(calls.length, 0);
});

test("移管競合を409として返し、内部エラーの秘密値は返さない", async () => {
  const { dependencies } = fixture();
  dependencies.getService = () => { throw new ApiKeyError("OwnershipConflict", 409); };
  const conflict = await handleApiKeyOwner(post(), dependencies, record.id);
  assert.equal(conflict.status, 409);
  dependencies.getService = () => { throw new Error("token=private-value"); };
  const failed = await handleApiKeyOwner(post(), dependencies, record.id);
  assert.equal(failed.status, 503);
  assert.equal((await failed.text()).includes("private-value"), false);
});
