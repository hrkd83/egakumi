import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { apiKeyErrorCode, findApiKeyOwnerGroups, transferApiKeyOwner } from "../components/api-keys/client.ts";
import { safeReturnTo } from "../lib/auth-navigation.ts";
import { parsePortalSignIn } from "../lib/auth-routes.ts";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const id = "portal-00000000-0000-4000-8000-000000000001";
const source = "00000000-0000-4000-8000-000000000002";
const target = "00000000-0000-4000-8000-000000000003";
const group = { id: target, displayName: "開発チーム", directoryStatus: "active", lastCheckedAt: "2026-10-30T00:00:00Z", products: [], isMember: false };

test("移管先検索は接頭辞を付けず入力を送り、所属と続きのカーソルを明示検証する", async () => {
  globalThis.fetch = async (input, init) => {
    const url = new URL(String(input), "https://portal.invalid");
    assert.equal(url.pathname, `/api/api-keys/${id}/owner`);
    assert.equal(url.searchParams.get("search"), "開発");
    assert.equal(url.searchParams.get("cursor"), "opaque+/=&cursor");
    assert.equal(init?.method, "GET");
    assert.equal(init?.cache, "no-store");
    assert.equal(init?.credentials, "same-origin");
    return Response.json({ groups: [group], nextCursor: "next-page" });
  };
  const result = await findApiKeyOwnerGroups(id, "開発", "opaque+/=&cursor");
  assert.equal(result.groups[0].isMember, false);
  assert.equal(result.nextCursor, "next-page");
});

test("未確認の所属を未所属と推測せず、壊れた検索結果を拒否する", async () => {
  const { isMember: ignored, ...withoutMembership } = group;
  void ignored;
  globalThis.fetch = async () => Response.json({ groups: [withoutMembership], nextCursor: null });
  await assert.rejects(findApiKeyOwnerGroups(id, "開発"), (error) => apiKeyErrorCode(error) === "ServiceUnavailable");
});

test("環境の名前条件で候補0件でも続きの検索位置を捨てず、長いカーソルを検証する", async () => {
  const cursor = "x".repeat(22000);
  globalThis.fetch = async (input) => {
    const url = new URL(String(input), "https://portal.invalid");
    assert.equal(url.searchParams.get("cursor"), cursor);
    return Response.json({ groups: [], nextCursor: cursor });
  };
  const result = await findApiKeyOwnerGroups(id, "開発", cursor);
  assert.deepEqual(result.groups, []);
  assert.equal(result.nextCursor, cursor);
});

test("所有権の移管は元所有者IDを添え、秘密値を送らず応答にも残さない", async () => {
  globalThis.fetch = async (input, init) => {
    assert.equal(input, `/api/api-keys/${id}/owner`);
    assert.equal(init?.method, "POST");
    assert.deepEqual(JSON.parse(String(init?.body)), { targetGroupId: target, expectedSourceGroupId: source });
    return Response.json({ id, apimSubscriptionId: "existing-model-subscription", groupId: target, productId: "model-users", displayName: "test", state: "active", budgetMode: "unlimited",
      clientType: null, systemName: null, teamName: null, companyId: null, companyName: null, contactEmail: null,
      createdAt: "2026-10-30T00:00:00Z", updatedAt: "2026-10-30T00:00:00Z", lastReviewedAt: null, primaryKey: "must-not-remain" });
  };
  const result = await transferApiKeyOwner(id, { targetGroupId: target, expectedSourceGroupId: source });
  assert.equal(result.groupId, target);
  assert.equal(result.id, id);
  assert.equal(result.apimSubscriptionId, "existing-model-subscription");
  assert.equal("primaryKey" in result, false);
});

test("短すぎる検索と管理外の購読は通信前に拒否する", async () => {
  globalThis.fetch = async () => { assert.fail("must not make a request"); };
  await assert.rejects(findApiKeyOwnerGroups(id, "O"));
  await assert.rejects(transferApiKeyOwner("master", { targetGroupId: target, expectedSourceGroupId: source }));
});

test("管理画面への戻り先だけを許可し、外部や別言語への転送は拒否する", () => {
  assert.equal(safeReturnTo("/ja/api-key-administration", "ja"), "/ja/api-key-administration");
  assert.equal(safeReturnTo("/en/api-key-administration", "ja"), "/ja/models");
  assert.ok(parsePortalSignIn({ provider: "microsoft", callbackURL: "/ja/api-key-administration", errorCallbackURL: "/ja/login?error=authentication_failed&next=%2Fja%2Fapi-key-administration" }));
});
