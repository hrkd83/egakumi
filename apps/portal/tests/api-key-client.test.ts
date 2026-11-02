import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { actOnApiKey, apiKeyErrorCode, issueApiKey, loadApiKeyCompanies, loadApiKeys } from "../components/api-keys/client.ts";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

const record = {
  id: "portal-00000000-0000-4000-8000-000000000001", groupId: "00000000-0000-4000-8000-000000000002",
  apimSubscriptionId: "existing-model-subscription",
  productId: "model-users", displayName: "Test app", state: "active", budgetMode: "unlimited", createdAt: "2026-10-30T00:00:00Z",
  updatedAt: "2026-10-30T00:00:00Z", lastReviewedAt: null,
  clientType: null, systemName: null, teamName: null, companyId: null, companyName: null, contactEmail: null,
};
const issueInput = { groupId: record.groupId, displayName: record.displayName, idempotencyKey: "00000000-0000-4000-8000-000000000003",
  clientType: "custom-system" as const, companyId: "00000000-0000-4000-8000-000000000004", contactEmail: "platform@example.com", systemName: "Helpdesk", teamName: "Platform" };

test("一覧には公開台帳だけを採用し、予期しない秘密フィールドを保持しない", async () => {
  globalThis.fetch = async (url, init) => {
    assert.equal(url, "/api/api-keys");
    assert.equal(init?.cache, "no-store");
    assert.equal(init?.credentials, "same-origin");
    return Response.json({ groups: [], keys: [{ ...record, primaryKey: "must-not-be-stored" }] });
  };
  const result = await loadApiKeys();
  assert.equal(result.keys.length, 1);
  assert.equal(result.keys[0].id, record.id);
  assert.equal(result.keys[0].apimSubscriptionId, record.apimSubscriptionId);
  assert.equal("primaryKey" in result.keys[0], false);
});

test("実APIM購読IDがない応答や危険な形式は、内部IDで補完せず拒否する", async () => {
  const { apimSubscriptionId: ignored, ...withoutMapping } = record;
  void ignored;
  for (const key of [withoutMapping, { ...record, apimSubscriptionId: "../../other-subscription" }]) {
    globalThis.fetch = async () => Response.json({ groups: [], keys: [key] });
    await assert.rejects(loadApiKeys(), (error) => apiKeyErrorCode(error) === "ServiceUnavailable");
  }
});

test("形式が壊れた台帳は一部だけの成功として表示しない", async () => {
  globalThis.fetch = async () => Response.json({ groups: [], keys: [{ ...record, state: "unknown" }] });
  await assert.rejects(loadApiKeys(), (error) => apiKeyErrorCode(error) === "ServiceUnavailable");
});

test("発行は同じ冪等キーを本文に送り、戻り値から秘密値を除く", async () => {
  const input = issueInput;
  globalThis.fetch = async (url, init) => {
    assert.equal(url, "/api/api-keys");
    assert.equal(init?.method, "POST");
    assert.deepEqual(JSON.parse(String(init?.body)), input);
    return Response.json({ ...record, primaryKey: "must-not-be-stored" });
  };
  assert.equal("primaryKey" in await issueApiKey(input), false);
});

test("発行入力は製品指定や不正な連絡先を通信前に拒否する", async () => {
  globalThis.fetch = async () => { assert.fail("must not make a request"); };
  await assert.rejects(issueApiKey({ ...issueInput, productId: "model-users" } as typeof issueInput), (error) => apiKeyErrorCode(error) === "InvalidRequest");
  await assert.rejects(issueApiKey({ ...issueInput, contactEmail: "not-an-email" }), (error) => apiKeyErrorCode(error) === "InvalidRequest");
});

test("会社選択は登録済みの有効会社だけを取得し、予期しない情報を保持しない", async () => {
  const company = { id: issueInput.companyId, name: "Sample company", isActive: true, createdAt: record.createdAt, updatedAt: record.updatedAt };
  globalThis.fetch = async (url, init) => {
    assert.equal(url, "/api/companies");
    assert.equal(init?.method, "GET");
    assert.equal(init?.cache, "no-store");
    return Response.json({ companies: [{ ...company, internalNote: "must-not-remain" }, { ...company, id: "00000000-0000-4000-8000-000000000005", isActive: false }] });
  };
  const result = await loadApiKeyCompanies();
  assert.deepEqual(result, [company]);
});

test("会社一覧の通信失敗を会社未登録と表示せず、壊れた応答も拒否する", async () => {
  globalThis.fetch = async () => Response.json({ error: { code: "KeyStoreUnavailable" } }, { status: 503 });
  await assert.rejects(loadApiKeyCompanies(), (error) => apiKeyErrorCode(error) === "KeyStoreUnavailable");
  globalThis.fetch = async () => Response.json({ companies: [{ id: "unsafe", name: "Broken", isActive: true }] });
  await assert.rejects(loadApiKeyCompanies(), (error) => apiKeyErrorCode(error) === "ServiceUnavailable");
});

test("表示要求だけがキーの値を返し、URLや本文へキーを送らない", async () => {
  globalThis.fetch = async (url, init) => {
    assert.equal(url, `/api/api-keys/${record.id}`);
    assert.deepEqual(JSON.parse(String(init?.body)), { action: "reveal" });
    return Response.json({ primaryKey: "test-primary", secondaryKey: "test-secondary" });
  };
  assert.deepEqual(await actOnApiKey(record.id, "reveal"), { primaryKey: "test-primary", secondaryKey: "test-secondary" });
});

test("既存購読の管理操作は内部台帳IDへ送り、実APIM購読IDを返す", async () => {
  globalThis.fetch = async (url, init) => {
    assert.equal(url, `/api/api-keys/${record.id}`);
    assert.deepEqual(JSON.parse(String(init?.body)), { action: "review" });
    return Response.json({ ...record, lastReviewedAt: "2026-10-31T00:00:00Z", secondaryKey: "must-not-be-stored" });
  };
  const result = await actOnApiKey(record.id, "review");
  assert.equal(result.id, record.id);
  assert.equal(result.apimSubscriptionId, record.apimSubscriptionId);
  assert.equal("secondaryKey" in result, false);
});

test("サービス障害の本文を画面エラーへ流さない", async () => {
  globalThis.fetch = async () => Response.json({ error: { code: "AccessDenied" }, sensitiveDetail: "must-not-be-shown" }, { status: 403 });
  await assert.rejects(loadApiKeys(), (error) => {
    assert.equal(apiKeyErrorCode(error), "AccessDenied");
    assert.equal(String(error).includes("must-not-be-shown"), false);
    return true;
  });
});

test("ユーザーによるダイアログ終了の中断をサービス障害へ変換しない", async () => {
  const controller = new AbortController();
  controller.abort();
  const aborted = new DOMException("Aborted", "AbortError");
  globalThis.fetch = async () => { throw aborted; };
  await assert.rejects(actOnApiKey(record.id, "reveal", controller.signal), (error) => error === aborted);
});
