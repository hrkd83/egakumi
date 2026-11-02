import assert from "node:assert/strict";
import { test } from "node:test";
import { AzureApiKeyApim } from "../lib/api-keys/apim.ts";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { getApiKeyEnvironment } from "../lib/api-keys/environment.ts";
import { GraphApiKeyMembership } from "../lib/api-keys/graph.ts";

const tenantId = "11111111-1111-4111-8111-111111111111";
const objectId = "22222222-2222-4222-8222-222222222222";
const clientId = "33333333-3333-4333-8333-333333333333";
const apimResourceId = "/subscriptions/44444444-4444-4444-8444-444444444444/resourceGroups/rg-test/providers/Microsoft.ApiManagement/service/apim-test";
const id = "portal-c40ec37d-9af4-4f44-8ca7-a3f4625e29a3";
const groupIds = Array.from({ length: 43 }, (_, index) => `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`);
const actor = { tenantId, objectId };
const graphSettings = { microsoftTenantId: tenantId, microsoftClientId: clientId, microsoftClientSecret: "test-only-client-secret" };
const keySettings = { apimResourceId, mode: "azure-cli" as const, tenantId };
const errorCode = (code: string) => (error: unknown) => error instanceof ApiKeyError && error.code === code;

test("APIMは設定済みの単一リソースと明示した実行IDだけを使う", () => {
  assert.deepEqual(getApiKeyEnvironment({ PORTAL_APIM_RESOURCE_ID: apimResourceId, AZURE_TENANT_ID: tenantId }), { ...keySettings, clientId: undefined });
  assert.equal(getApiKeyEnvironment({ NODE_ENV: "production", PORTAL_APIM_RESOURCE_ID: apimResourceId, AZURE_CLIENT_ID: clientId }).mode, "managed-identity");
  for (const resource of [`https://evil.invalid${apimResourceId}`, `${apimResourceId}?next=evil`, `${apimResourceId}/subscriptions/any`, `${apimResourceId}/../other`]) {
    assert.throws(() => getApiKeyEnvironment({ PORTAL_APIM_RESOURCE_ID: resource, AZURE_TENANT_ID: tenantId }), errorCode("ConfigurationUnavailable"));
  }
  assert.throws(() => getApiKeyEnvironment({ NODE_ENV: "production", PORTAL_APIM_AUTH: "azure-cli", PORTAL_APIM_RESOURCE_ID: apimResourceId, AZURE_TENANT_ID: tenantId }), errorCode("ConfigurationUnavailable"));
  assert.throws(() => getApiKeyEnvironment({ PORTAL_APIM_RESOURCE_ID: apimResourceId }), errorCode("ConfigurationUnavailable"));
});

test("Graphは20件ずつ入れ子対応の所属を照会し、所属結果を使い回さない", async () => {
  const membershipRequests: string[][] = [];
  let tokens = 0;
  let removed = false;
  let clock = 1_000;
  const graph = new GraphApiKeyMembership(graphSettings, {
    now: () => clock,
    fetch: async (input, init) => {
      assert.equal(init?.redirect, "error");
      assert.equal(init?.cache, "no-store");
      if (String(input).startsWith("https://login.microsoftonline.com/")) {
        tokens++;
        assert.equal(String(input), `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/token`);
        const body = new URLSearchParams(String(init?.body));
        assert.equal(body.get("grant_type"), "client_credentials");
        assert.equal(body.get("scope"), "https://graph.microsoft.com/.default");
        return Response.json({ token_type: "Bearer", access_token: "test-token", expires_in: 3600 });
      }
      assert.equal(String(input), `https://graph.microsoft.com/v1.0/users/${objectId}/checkMemberGroups`);
      assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer test-token");
      const batch = JSON.parse(String(init?.body)).groupIds as string[];
      membershipRequests.push(batch);
      return Response.json({ value: removed ? [] : batch });
    },
  });
  assert.equal((await graph.check(actor, groupIds)).size, 43);
  assert.deepEqual(membershipRequests.map((request) => request.length), [20, 20, 3]);
  assert.equal(tokens, 1);
  removed = true;
  assert.equal((await graph.check(actor, [groupIds[0]])).size, 0);
  assert.equal(membershipRequests.length, 4);
  assert.equal(tokens, 1);
  clock += 3_600_000;
  await graph.check(actor, [groupIds[0]]);
  assert.equal(tokens, 2);
});

test("Graphが障害・不正な所属・別テナントを返しても利用を許可しない", async () => {
  for (const response of [() => new Response("secret server message", { status: 403 }), () => Response.json({ value: [groupIds[1]] }), () => Response.json({ value: ["not-a-guid"] })]) {
    const graph = new GraphApiKeyMembership(graphSettings, { fetch: async (input) => String(input).includes("oauth2")
      ? Response.json({ token_type: "Bearer", access_token: "token", expires_in: 3600 }) : response() });
    await assert.rejects(graph.check(actor, [groupIds[0]]), errorCode("AuthorizationUnavailable"));
  }
  let calls = 0;
  const graph = new GraphApiKeyMembership(graphSettings, { fetch: async () => { calls++; throw new Error("must not call"); } });
  await assert.rejects(graph.check({ ...actor, tenantId: clientId }, [groupIds[0]]), errorCode("AccessDenied"));
  assert.equal(calls, 0);
});

test("Graphの本文読み取りも制限し、エラーメッセージへ秘密値を混入しない", async () => {
  const graph = new GraphApiKeyMembership(graphSettings, { maximumBytes: 50, fetch: async () => new Response("secret".repeat(1000)) });
  await assert.rejects(graph.check(actor, [groupIds[0]]), (error: unknown) => {
    assert.ok(error instanceof ApiKeyError);
    assert.equal(error.message.includes("secret"), false);
    return error.code === "AuthorizationUnavailable";
  });
});

test("APIM作成は製品に限定し、読み取りでキーを返さず個人所有者を設定しない", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const client = new AzureApiKeyApim(keySettings, {
    getAccessToken: async () => "test-arm-token",
    fetch: async (input, init) => {
      calls.push({ url: String(input), init });
      return Response.json({
        id: `${apimResourceId}/subscriptions/${id}`,
        properties: { displayName: "社内システム", scope: `${apimResourceId}/products/model-users`, state: "active", primaryKey: "never-return-from-list", secondaryKey: "nor-this" },
      }, { headers: { etag: '"etag-1"' } });
    },
  });
  const record = await client.create(id, "model-users", "社内システム");
  assert.equal(JSON.stringify(record).includes("never-return"), false);
  const request = calls[0];
  assert.equal(request.url, `https://management.azure.com${apimResourceId}/subscriptions/${id}?api-version=2024-05-01`);
  assert.equal(request.init?.redirect, "error");
  assert.equal(new Headers(request.init?.headers).get("If-None-Match"), "*");
  assert.equal(new Headers(request.init?.headers).get("Authorization"), "Bearer test-arm-token");
  assert.deepEqual(JSON.parse(String(request.init?.body)), { properties: { displayName: "社内システム", scope: `${apimResourceId}/products/model-users`, state: "active", allowTracing: false } });
  assert.equal(record.etag, '"etag-1"');
  await assert.rejects(client.get("master"), errorCode("NotFound"));
  await assert.rejects(client.get(`${id}/../../master`), errorCode("NotFound"));
  assert.equal(calls.length, 1);
});

test("APIM発行の400・404後に製品の404を確認した場合だけ未配備を案内する", async () => {
  for (const status of [400, 404]) {
    const calls: { url: string; method: string }[] = [];
    const client = new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch: async (input, init) => {
      calls.push({ url: String(input), method: init?.method ?? "GET" });
      return new Response("private ARM error", { status: init?.method === "PUT" ? status : 404 });
    } });
    await assert.rejects(client.create(id, "released-apis", "社内システム"), (error: unknown) => {
      assert.ok(error instanceof ApiKeyError);
      assert.equal(error.code, "ProductNotDeployed");
      assert.equal(error.status, 503);
      assert.equal(error.message.includes("private ARM error"), false);
      return true;
    });
    assert.deepEqual(calls, [
      { url: `https://management.azure.com${apimResourceId}/subscriptions/${id}?api-version=2024-05-01`, method: "PUT" },
      { url: `https://management.azure.com${apimResourceId}/products/released-apis?api-version=2024-05-01`, method: "GET" },
    ]);
  }
});

test("製品が存在する場合や確認に失敗した場合は未配備と断定しない", async () => {
  for (const status of [400, 404]) {
    for (const productStatus of [200, 403, 500, "network"] as const) {
      let calls = 0;
      const client = new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch: async (_input, init) => {
        calls++;
        if (init?.method === "PUT") return new Response(null, { status });
        if (productStatus === "network") throw new Error("private connection failure");
        return productStatus === 200 ? Response.json({ id: "existing-product" }) : new Response(null, { status: productStatus });
      } });
      await assert.rejects(client.create(id, "released-apis", "社内システム"), errorCode("UpstreamUnavailable"));
      assert.equal(calls, 2);
    }
  }
});

test("発行の権限不足・上流障害では製品を追加照会しない", async () => {
  for (const status of [403, 500]) {
    let calls = 0;
    const client = new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch: async () => {
      calls++;
      return new Response(null, { status });
    } });
    await assert.rejects(client.create(id, "released-apis", "社内システム"), errorCode("UpstreamUnavailable"));
    assert.equal(calls, 1);
  }
});

test("APIM停止と削除はETagを必須にし、再生成ではなく明示的なキー取得APIを呼ぶ", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const client = new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch: async (input, init) => {
    calls.push({ url: String(input), init });
    if (String(input).includes("listSecrets")) return Response.json({ primaryKey: "primary-test-key-0000", secondaryKey: "secondary-test-key-0000" });
    return new Response(null, { status: 204 });
  } });
  await client.setState(id, "suspended", '"etag-2"');
  assert.equal(new Headers(calls[0].init?.headers).get("If-Match"), '"etag-2"');
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { properties: { state: "suspended" } });
  assert.equal(await client.delete(id, '"etag-3"'), true);
  assert.equal(new Headers(calls[1].init?.headers).get("If-Match"), '"etag-3"');
  assert.equal((await client.secrets(id)).primaryKey, "primary-test-key-0000");
  assert.ok(calls[2].url.includes(`/subscriptions/${id}/listSecrets?`));
  assert.equal(calls[2].init?.method, "POST");
  await assert.rejects(client.delete(id, "*"), errorCode("UpstreamUnavailable"));
  assert.equal(calls.length, 3);
});

test("APIMの別ID応答・競合・大きすぎる本文を安全なエラーとして扱う", async () => {
  const client = (fetch: typeof globalThis.fetch) => new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch, maximumBytes: 1024 });
  await assert.rejects(client(async () => Response.json({ id: `${apimResourceId}/subscriptions/foreign`, properties: { displayName: "other", scope: "all", state: "active" } })).get(id), errorCode("UpstreamUnavailable"));
  await assert.rejects(client(async () => new Response("secret", { status: 412 })).setState(id, "active", '"etag"'), errorCode("UpstreamConflict"));
  await assert.rejects(client(async () => new Response("x".repeat(2048))).get(id), errorCode("UpstreamUnavailable"));
  await assert.rejects(client(async () => { throw new Error("secret upstream error"); }).get(id), errorCode("UpstreamUnavailable"));
});

test("安全な既存購読名を扱い、表示名がnullでもキーを含めずIDを代替表示する", async () => {
  const actualId = "verification.model-users", calls: { url: string; method: string }[] = [];
  const client = new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch: async (input, init) => {
    const method = init?.method ?? "GET"; calls.push({ url: String(input), method });
    if (String(input).includes("/listSecrets?")) return Response.json({ primaryKey: "primary-test-key-0000", secondaryKey: "secondary-test-key-0000" });
    if (method === "PATCH" || method === "DELETE") return new Response(null, { status: 204 });
    if (method === "PUT") assert.equal(new Headers(init?.headers).get("If-None-Match"), "*");
    return Response.json({ id: `${apimResourceId}/subscriptions/${actualId}`,
      properties: { displayName: method === "PUT" ? "Verification" : null, scope: `${apimResourceId}/products/model-users`, state: "active", primaryKey: "never-return" } },
    { headers: { etag: '"existing"' } });
  } });
  assert.equal((await client.get(actualId))?.displayName, actualId);
  assert.equal((await client.create(actualId, "model-users", "Verification")).id, actualId);
  await client.setState(actualId, "suspended", '"existing"');
  await client.secrets(actualId); await client.delete(actualId, '"existing"');
  assert.ok(calls.every((call) => call.url.includes(`/subscriptions/${actualId}`)));
  const before = calls.length;
  for (const unsafe of ["master", "MASTER", "../other", "valid/other", "valid?query", "valid#hash", "valid%2fother", "valid\nheader", "x".repeat(257)]) {
    await assert.rejects(client.get(unsafe), errorCode("NotFound"));
    await assert.rejects(client.create(unsafe, "model-users", "Verification"), errorCode("NotFound"));
  }
  assert.equal(calls.length, before);
});

test("実行先APIの製品所属は通常の製品IDと要求API配下のIDを厳密に照合する", async () => {
  const apiId = "openai-inference-v1";
  for (const productResource of [`${apimResourceId}/products/released-apis`, `${apimResourceId}/apis/${apiId}/products/released-apis`]) {
    const calls: { url: string; method?: string }[] = [];
    const client = new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch: async (input, init) => {
      calls.push({ url: String(input), method: init?.method });
      return Response.json({ value: [{ id: productResource }] });
    } });
    assert.equal(await client.productIncludesApi("released-apis", apiId), true);
    assert.deepEqual(calls, [{ url: `https://management.azure.com${apimResourceId}/apis/${apiId}/products?api-version=2024-05-01`, method: "GET" }]);
    assert.equal(await client.productIncludesApi("pii-users", apiId), false);
  }
});

test("API所属のページ送りは同一APIMの同じAPIだけを参照し、全ページで不在ならfalseにする", async () => {
  const apiId = "openai-inference-v1";
  const first = `https://management.azure.com${apimResourceId}/apis/${apiId}/products?api-version=2024-05-01`;
  const second = first + "&$skip=1";
  const calls: string[] = [];
  const client = new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch: async (input, init) => {
    calls.push(String(input));
    assert.equal(init?.redirect, "error"); assert.equal(init?.cache, "no-store");
    return String(input) === first
      ? Response.json({ value: [{ id: `${apimResourceId}/products/pii-users` }], nextLink: second })
      : Response.json({ value: [{ id: `${apimResourceId}/products/released-apis` }] });
  } });
  assert.equal(await client.productIncludesApi("released-apis", apiId), true);
  assert.deepEqual(calls, [first, second]);
  assert.equal(await client.productIncludesApi("model-users", apiId), false);
  const missing = new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch: async () => new Response(null, { status: 404 }) });
  assert.equal(await missing.productIncludesApi("released-apis", apiId), false);
});

test("API所属照会の入力から任意のURLやクエリーを持ち込めない", async () => {
  let calls = 0;
  const client = new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch: async () => { calls++; return Response.json({ value: [] }); } });
  for (const unsafe of ["../other", "valid/other", "valid?query", "valid#hash", "valid%2fother", "valid;rev=2", "valid\nheader", "x".repeat(257)]) {
    await assert.rejects(client.productIncludesApi("released-apis", unsafe), errorCode("NotFound"));
    await assert.rejects(client.productIncludesApi(unsafe, "openai-inference-v1"), errorCode("NotFound"));
  }
  assert.equal(calls, 0);
});

test("API所属の不正な次ページ・循環・別APIの製品IDを信用せず、認証情報を転送しない", async () => {
  const apiId = "openai-inference-v1";
  const first = `https://management.azure.com${apimResourceId}/apis/${apiId}/products?api-version=2024-05-01`;
  const unsafeLinks = [first, first.replace("management.azure.com", "other.invalid"), first.replace("https:", "http:"),
    first.replace("apis/openai-inference-v1", "apis/pii-v1"), first.replace("2024-05-01", "2025-09-01-preview"),
    first.replace("44444444-4444-4444-8444-444444444444", objectId), first + "#fragment", first + "&api-version=2024-05-01",
    first.replace("https://", "https://userinfo@"), "/relative/products"];
  for (const nextLink of unsafeLinks) {
    let calls = 0;
    const client = new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch: async () => {
      calls++; return Response.json({ value: [], nextLink });
    } });
    await assert.rejects(client.productIncludesApi("released-apis", apiId), errorCode("UpstreamUnavailable"));
    assert.equal(calls, 1, "未検証の送り先へARMトークンを送らない");
  }
  for (const body of [{ value: "not-an-array" }, { value: [{ id: `${apimResourceId}/apis/pii-v1/products/released-apis` }] },
    { value: [{ id: `${apimResourceId}/products/released-apis?key=private` }] }, { value: [{ name: "released-apis" }] }]) {
    const client = new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch: async () => Response.json(body) });
    await assert.rejects(client.productIncludesApi("released-apis", apiId), errorCode("UpstreamUnavailable"));
  }
});

test("API所属の読取権限不足や上流障害は未所属と誤認せず503にする", async () => {
  for (const status of [403, 500]) {
    const client = new AzureApiKeyApim(keySettings, { getAccessToken: async () => "token", fetch: async () => new Response("private", { status }) });
    await assert.rejects(client.productIncludesApi("released-apis", "openai-inference-v1"), errorCode("UpstreamUnavailable"));
  }
});
