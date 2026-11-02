import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import {
  limitDraftFromProfile, limitsFromDraft, loadApiKeyLimits, saveApiKeyLimits,
} from "../components/api-keys/limit-client.ts";
import { apiKeyErrorCode } from "../components/api-keys/client.ts";
import { unlimitedApiKeyLimits } from "../lib/api-keys/limit-contracts.ts";

const originalFetch = globalThis.fetch;
const id = "portal-00000000-0000-4000-8000-000000000001";
afterEach(() => { globalThis.fetch = originalFetch; });

test("無制限の初期値は空欄とnullを往復し、暗黙の60回制限を補わない", () => {
  const draft = limitDraftFromProfile(unlimitedApiKeyLimits());
  assert.equal(draft.calls, "");
  assert.equal(draft.tokensPerMinute, "");
  assert.deepEqual(limitsFromDraft(draft), { requestRate: null, tokensPerMinute: null, tokenQuota: null });
});

test("入力した保護上限を独立して扱い、有効な項目だけ数値にする", () => {
  const draft = limitDraftFromProfile(unlimitedApiKeyLimits());
  const values = limitsFromDraft({ ...draft, requestEnabled: true, calls: "12", seconds: "30", quotaEnabled: true, quotaTokens: "1000000", quotaPeriod: "Monthly" });
  assert.deepEqual(values, { requestRate: { calls: 12, renewalPeriodSeconds: 30 }, tokensPerMinute: null, tokenQuota: { tokens: 1_000_000, period: "Monthly" } });
  assert.deepEqual(limitsFromDraft({ ...draft, tokenRateEnabled: true, tokensPerMinute: "5000", calls: "invalid-but-disabled" }), { requestRate: null, tokensPerMinute: 5000, tokenQuota: null });
});

test("有効な項目の空欄・小数・上限超過を送信前に拒否する", () => {
  const draft = limitDraftFromProfile(unlimitedApiKeyLimits());
  for (const calls of ["", "0", "1.5", "1e2", "1000001", "-1", " "]) {
    assert.throws(() => limitsFromDraft({ ...draft, requestEnabled: true, calls, seconds: "30" }), (error) => apiKeyErrorCode(error) === "InvalidLimitSettings");
  }
  assert.throws(() => limitsFromDraft({ ...draft, requestEnabled: true, calls: "12", seconds: "301" }));
  assert.throws(() => limitsFromDraft({ ...draft, tokenRateEnabled: true, tokensPerMinute: "2147483648" }));
  assert.throws(() => limitsFromDraft({ ...draft, quotaEnabled: true, quotaTokens: "" }));
});

test("設定読込は同一オリジンでキャッシュせず、無制限も検証済み応答だけを使う", async () => {
  globalThis.fetch = async (url, init) => {
    assert.equal(url, `/api/api-keys/${id}/limits`);
    assert.equal(init?.method, "GET");
    assert.equal(init?.cache, "no-store");
    assert.equal(init?.credentials, "same-origin");
    assert.equal(init?.body, undefined);
    return Response.json(unlimitedApiKeyLimits());
  };
  assert.deepEqual(await loadApiKeyLimits(id), unlimitedApiKeyLimits());
});

test("PUTは読み込んだ版を必須にし、設定以外の情報やキーを送らない", async () => {
  const input = { expectedVersion: 3, requestRate: null, tokensPerMinute: 1200, tokenQuota: null };
  globalThis.fetch = async (url, init) => {
    assert.equal(url, `/api/api-keys/${id}/limits`);
    assert.equal(init?.method, "PUT");
    assert.deepEqual(JSON.parse(String(init?.body)), input);
    return Response.json({ version: 4, requestRate: null, tokensPerMinute: 1200, tokenQuota: null, updatedAt: "2026-10-30T05:00:00Z" });
  };
  assert.equal((await saveApiKeyLimits(id, input)).version, 4);
});

test("版の競合は自動上書きせず、再読み込み用のエラーとして返す", async () => {
  let requests = 0;
  globalThis.fetch = async () => { requests++; return Response.json({ error: { code: "LimitVersionConflict" }, unsafeDetail: "do-not-display" }, { status: 409 }); };
  await assert.rejects(saveApiKeyLimits(id, { ...limitsFromDraft(limitDraftFromProfile(unlimitedApiKeyLimits())), expectedVersion: 1 }), (error) => {
    assert.equal(apiKeyErrorCode(error), "LimitVersionConflict");
    assert.equal(String(error).includes("do-not-display"), false);
    return true;
  });
  assert.equal(requests, 1);
});

test("壊れた応答を無制限として扱わず、管理対象外のIDは通信しない", async () => {
  globalThis.fetch = async () => Response.json({ version: 2, requestRate: null });
  await assert.rejects(loadApiKeyLimits(id), (error) => apiKeyErrorCode(error) === "ServiceUnavailable");
  globalThis.fetch = async () => { assert.fail("must not request arbitrary subscriptions"); };
  await assert.rejects(loadApiKeyLimits("master"), (error) => apiKeyErrorCode(error) === "NotFound");
});

test("画面を閉じた中断はサービス障害へ変換しない", async () => {
  const controller = new AbortController();
  controller.abort();
  const failure = new DOMException("Aborted", "AbortError");
  globalThis.fetch = async () => { throw failure; };
  await assert.rejects(loadApiKeyLimits(id, controller.signal), (error) => error === failure);
});
