import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { ApiKeyError, type ApiKeyActor, type ApiKeyOverview, type ApiKeyRecord } from "../lib/api-keys/contracts.ts";
import { handleModelKeyChoices, type ModelKeyChoiceDependencies } from "../lib/model-key-access.ts";
import { loadModelKeyChoices, type ModelKeyChoice } from "../lib/model-key-choices.ts";
import { ModelResponseError } from "../lib/model-response.ts";
import { chatModel as model } from "./helpers/model-fixtures.ts";

const origin = "https://portal.example.invalid";
const actor: ApiKeyActor = { tenantId: "11111111-1111-4111-8111-111111111111", objectId: "22222222-2222-4222-8222-222222222222" };
const groupId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const otherGroupId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const privateValue = "SYNTHETIC_UPSTREAM_SECRET_DO_NOT_DISPLAY";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

/** 台帳の必須項目を備え、状態や所有グループだけを変えられる合成データです。 */
function record(index: number, overrides: Partial<ApiKeyRecord> = {}): ApiKeyRecord {
  return {
    id: `portal-00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    apimSubscriptionId: `test-subscription-${index}`, groupId, productId: "released-apis", displayName: `Test key ${index}`,
    state: "active", createdAt: "2026-10-30T00:00:00Z", updatedAt: "2026-10-30T00:00:00Z", lastReviewedAt: null,
    budgetMode: "unlimited", clientType: "custom-system", systemName: "Example system", teamName: null,
    companyId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", companyName: "Example company", contactEmail: "team@example.invalid",
    ...overrides,
  };
}

/** 認証・所属確認・モデルカタログを、外部通信しない境界の依存へ置き換えます。 */
function dependencies(overrides: Partial<ModelKeyChoiceDependencies> = {}): ModelKeyChoiceDependencies {
  return {
    readActor: async () => actor,
    readModels: () => [model],
    readChoices: async () => ({ groups: [{ id: groupId, displayName: "ORG_Test team", products: [] }], keys: [record(1)] }),
    ...overrides,
  };
}

/** ブラウザー入力で本人や接続先を差し替えられないことを確認する要求を組み立てます。 */
function request(query = `apiId=${model.apiId}`, method = "GET", headers: HeadersInit = {}): Request {
  return new Request(`${origin}/api/models/keys${query ? `?${query}` : ""}`, { method, headers });
}

/** 公開エラーは固定コードだけを返し、キャッシュや生の上流情報を残さないことを確認します。 */
async function expectServerFailure(response: Response, status: number, code: string) {
  assert.equal(response.status, status);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  assert.deepEqual(await response.json(), { error: { code } });
}

test("候補取得はGETだけを受け付け、別メソッドやcross-siteでは本人・所属を問い合わせない", async () => {
  let actorReads = 0, choiceReads = 0;
  const boundary = dependencies({
    readActor: async () => { actorReads++; return actor; },
    readChoices: async () => { choiceReads++; throw new Error(privateValue); },
  });
  for (const method of ["POST", "PUT", "DELETE", "HEAD", "OPTIONS"]) {
    const result = await handleModelKeyChoices(request(undefined, method), boundary);
    assert.equal(result.headers.get("allow"), "GET");
    await expectServerFailure(result, 405, "RequestNotAllowed");
  }
  await expectServerFailure(await handleModelKeyChoices(request(undefined, "GET", { "sec-fetch-site": "cross-site" }), boundary), 403, "RequestNotAllowed");
  assert.equal(actorReads, 0);
  assert.equal(choiceReads, 0);
});

test("未認証や認証障害では候補を読まず、安全な失敗だけを返す", async () => {
  let choiceReads = 0, catalogReads = 0;
  const boundary = dependencies({
    readModels: () => { catalogReads++; return [model]; },
    readChoices: async () => { choiceReads++; throw new Error(privateValue); },
  });
  await expectServerFailure(await handleModelKeyChoices(request(), { ...boundary, readActor: async () => null }), 401, "AuthenticationRequired");
  await expectServerFailure(await handleModelKeyChoices(request(), { ...boundary, readActor: async () => { throw new Error(privateValue); } }), 503, "ApiKeyAccessUnavailable");
  assert.equal(choiceReads, 0);
  assert.equal(catalogReads, 0);
});

test("未登録・空・重複したAPI識別子では候補取得へ進まない", async () => {
  let choiceReads = 0;
  const boundary = dependencies({ readChoices: async () => { choiceReads++; throw new Error(privateValue); } });
  for (const query of ["", "apiId=", "apiId=unknown-api", "apiId=https%3A%2F%2Fother.invalid", `apiId=${model.apiId}&apiId=${model.apiId}`, `apiId=${model.apiId}&apiId=unknown-api`]) {
    await expectServerFailure(await handleModelKeyChoices(request(query), boundary), 400, "InvalidRequest");
  }
  assert.equal(choiceReads, 0);
});

test("標準のカタログは埋め込み・画像・音声APIでも本人所属の購読だけを照会する", async () => {
  const calls: string[] = [];
  const boundary = dependencies({ readChoices: async (_actor, apiId) => { calls.push(apiId); return { groups: [], keys: [] }; } });
  delete boundary.readModels;
  const apiIds = ["openai-embeddings-v1", "cohere-inference-v1", "openai-images-v1", "blackforest-images-v1", "azure-speech-v1", "openai-audio-v1", "microsoft-decision-v1", "pii-v1", "pii-v1-mcp", "document-analysis-v1"];
  for (const apiId of apiIds) {
    const result = await handleModelKeyChoices(request(`apiId=${apiId}`), boundary);
    assert.equal(result.status, 200);
    assert.deepEqual(await result.json(), { keys: [] });
  }
  assert.deepEqual(calls, apiIds);
  await expectServerFailure(await handleModelKeyChoices(request("apiId=unpublished-api-v1"), boundary), 400, "InvalidRequest");
  assert.equal(calls.length, apiIds.length);
});

test("本人は認証依存だけから取得し、サーバーカタログのAPI識別子だけを所属サービスへ渡す", async () => {
  const calls: unknown[][] = [];
  const result = await handleModelKeyChoices(request(`apiId=${model.apiId}&tenantId=forged-tenant&objectId=forged-user`, "GET", {
    "X-Entra-Object-Id": "forged-user", "X-Entra-Tenant-Id": "forged-tenant",
  }), dependencies({ readChoices: async (...input) => { calls.push(input); return { groups: [], keys: [] }; } }));
  assert.deepEqual(calls, [[actor, model.apiId]]);
  assert.equal(calls[0][0], actor);
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), { keys: [] });
});

test("候補は所属グループの有効なキーだけで、余分な台帳属性や上流秘密値を公開しない", async () => {
  const selected = { ...record(1), primaryKey: privateValue, secondaryKey: privateValue, accessToken: privateValue, apimSubscriptionId: privateValue };
  const overview: ApiKeyOverview & { upstreamToken: string } = {
    groups: [{ id: groupId.toUpperCase(), displayName: "ORG_Test team", products: [] }],
    keys: [selected, record(2, { companyName: null }), record(3, { groupId: otherGroupId }),
      ...(["creating", "suspended", "deleting", "deleted"] as const).map((state, index) => record(index + 4, { state }))],
    upstreamToken: privateValue,
  };
  const result = await handleModelKeyChoices(request(), dependencies({ readChoices: async () => overview }));
  assert.equal(result.status, 200);
  assert.equal(result.headers.get("cache-control"), "no-store");
  const body = await result.json();
  assert.deepEqual(body, { keys: [
    { id: selected.id, displayName: selected.displayName, companyName: "Example company", groupName: "ORG_Test team" },
    { id: record(2).id, displayName: record(2).displayName, companyName: null, groupName: "ORG_Test team" },
  ] });
  assert.equal(JSON.stringify(body).includes(privateValue), false);
});

test("所属拒否・Graph障害・壊れた公開候補は詳細を伏せたエラーへ変換する", async () => {
  for (const failure of [new ApiKeyError(privateValue, 403), new ApiKeyError(privateValue, 404)]) {
    await expectServerFailure(await handleModelKeyChoices(request(), dependencies({ readChoices: async () => { throw failure; } })), 403, "ApiKeyUnavailable");
  }
  for (const failure of [new ApiKeyError(privateValue, 503), new Error(privateValue)]) {
    await expectServerFailure(await handleModelKeyChoices(request(), dependencies({ readChoices: async () => { throw failure; } })), 503, "ApiKeyAccessUnavailable");
  }
  await expectServerFailure(await handleModelKeyChoices(request(), dependencies({ readModels: () => { throw new Error(privateValue); } })), 503, "ApiKeyAccessUnavailable");
  await expectServerFailure(await handleModelKeyChoices(request(), dependencies({ readChoices: async () => ({
    groups: [{ id: groupId, displayName: "ORG_Test team", products: [] }], keys: [record(1, { id: "unmanaged-subscription" })],
  }) })), 503, "ApiKeyAccessUnavailable");
});

test("候補ローダーは同一オリジンの限定GETをキャッシュせず、API識別子をクエリへエンコードする", async () => {
  const choice: ModelKeyChoice = { id: record(1).id, displayName: "Test key", companyName: null, groupName: "ORG_Test team" };
  const source = new AbortController();
  const apiId = "api/with?query&fragment#value";
  let calls = 0;
  globalThis.fetch = async (url, options) => {
    calls++;
    assert.equal(url, `/api/models/keys?${new URLSearchParams({ apiId })}`);
    assert.equal(options?.method ?? "GET", "GET");
    assert.equal(options?.credentials, "same-origin");
    assert.equal(options?.cache, "no-store");
    assert.equal(options?.body, undefined);
    assert.equal(new Headers(options?.headers).get("api-key"), null);
    assert.ok(options?.signal instanceof AbortSignal);
    assert.equal(options.signal.aborted, false);
    return Response.json({ keys: [choice] });
  };
  assert.deepEqual(await loadModelKeyChoices(apiId, source.signal), [choice]);
  assert.equal(calls, 1);
});

test("候補ローダーはHTTP失敗の本文を読まず、固定コードへ変換する", async () => {
  for (const status of [401, 403, 429, 500, 503]) {
    let bodyReads = 0;
    globalThis.fetch = async () => {
      const response = new Response(privateValue, { status });
      response.json = async () => { bodyReads++; throw new Error(privateValue); };
      return response;
    };
    await assert.rejects(loadModelKeyChoices(model.apiId, new AbortController().signal), (error) => {
      assert.ok(error instanceof ModelResponseError);
      const code = status === 401 ? "AuthenticationRequired" : "ApiKeyAccessUnavailable";
      assert.deepEqual(error.failure, { error: { code } });
      assert.equal(error.message, code);
      return true;
    });
    assert.equal(bodyReads, 0);
  }
});

test("候補ローダーは壊れたJSON・余分な秘密属性・通信例外の詳細を返さない", async () => {
  const choice = { id: record(1).id, displayName: "Test key", companyName: null, groupName: "ORG_Test team" };
  for (const fetcher of [
    async () => new Response(privateValue),
    async () => Response.json({ keys: [{ ...choice, primaryKey: privateValue }] }),
    async () => Response.json({ keys: [record(1)] }),
    async () => { throw new Error(privateValue); },
  ]) {
    globalThis.fetch = fetcher;
    await assert.rejects(loadModelKeyChoices(model.apiId, new AbortController().signal), (error) => {
      assert.ok(error instanceof ModelResponseError);
      assert.deepEqual(error.failure, { error: { code: "ApiKeyAccessUnavailable" } });
      assert.equal(error.message.includes(privateValue), false);
      return true;
    });
  }
});

test("候補の読み込み中断を上流障害に変換せず、合成signalへ伝える", async () => {
  const controller = new AbortController();
  const cancelled = new DOMException("Cancelled", "AbortError");
  globalThis.fetch = async (_url, options) => {
    assert.ok(options?.signal instanceof AbortSignal);
    controller.abort();
    assert.equal(options.signal.aborted, true);
    throw cancelled;
  };
  await assert.rejects(loadModelKeyChoices(model.apiId, controller.signal), (error) => error === cancelled);
});
