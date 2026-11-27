import assert from "node:assert/strict";
import { test } from "node:test";
import { randomUUID } from "node:crypto";
import { ApiAuthenticationError } from "../lib/api-auth/settings.ts";
import { getGraphToolEnvironment, handleGraphToolProfile, type GraphToolDependencies } from "../lib/graph-tool-server.ts";
import { trialBearer } from "./helpers/trial-bearer.ts";

const portal = "https://portal.example.invalid", origin = "https://gateway.example.invalid";
const owner = { tenantId: randomUUID(), objectId: randomUUID() };
const profile = { id: owner.objectId, displayName: "Example Graph User", companyName: null, accountEnabled: false };
const token = "synthetic-graph-trial-bearer";

/** Graphの上流と認可状態だけを合成データへ差し替えます。 */
function setup(responses: Response[] = []) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const dependencies: GraphToolDependencies = {
    readActor: async () => owner, readPortalOrigin: () => portal,
    readGateway: () => ({ origin }), resolveBearer: async () => trialBearer(token, owner),
    authorizeBearer: async () => undefined,
    fetch: async (url, init) => { calls.push({ url: String(url), init }); return responses.shift() ?? Response.json(profile); },
  };
  return { dependencies, calls };
}

function request(input: object = { authentication: "bearer" }, requestOrigin = portal): Request {
  return new Request(`${portal}/api/tools/graph/profile`, {
    method: "POST", headers: { Origin: requestOrigin, "Content-Type": "application/json" }, body: JSON.stringify(input),
  });
}
const body = (response: Response) => response.json();

test("本人のBearerは固定Graph APIへ1度だけ送られ、本人属性のnullとfalseは保持される", async () => {
  const app = setup();
  const success = await handleGraphToolProfile(request(), app.dependencies);
  assert.equal(success.status, 200);
  const result = await body(success);
  assert.deepEqual(result.profile, profile);
  assert.ok(!JSON.stringify(result).includes(token));
  assert.equal(app.calls.length, 1);
  const [{ url, init }] = app.calls;
  const headers = new Headers(init?.headers);
  assert.equal(url, `${origin}/tools/graph/v1/me`);
  assert.equal(init?.method, "GET");
  assert.equal(init.redirect, "error");
  assert.equal(headers.get("authorization"), `Bearer ${token}`);
  assert.equal(headers.has("api-key"), false);
});

test("Graphの実行中にPortalのBearerが失効したら本人情報を返さず再送しない", async () => {
  const app = setup();
  let authorized = true;
  let calls = 0;
  const response = await handleGraphToolProfile(request(), {
    ...app.dependencies,
    resolveBearer: async () => ({ value: "fixture." + "z".repeat(20), expiresAt: Date.now() + 60_000, accessExpiresAt: Date.now() + 700_000,
      generation: 1, binding: { ...owner, sessionId: "test-portal-session", hash: "c".repeat(64) } }),
    authorizeBearer: async () => {
      if (!authorized) throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
    },
    fetch: async () => { calls++; authorized = false; return Response.json(profile); },
  });
  assert.equal(response.status, 401);
  assert.deepEqual(await body(response), { error: { code: "ApiAuthenticationRequired" } });
  assert.equal(calls, 1);
});

test("同じテナントのGraph追加認証だけを返し、CMを利用しない", async () => {
  const expected = Buffer.from('{"access_token":{"acrs":{"essential":true,"value":"c1"}}}');
  const invalid = Buffer.from('{"id_token":{"nonce":{"essential":true}}}');
  const baseChallenge = `Bearer authorization_uri="https://login.microsoftonline.com/${owner.tenantId}/oauth2/v2.0/authorize", error="insufficient_claims", claims="`;
  for (const [challenge, forwarded] of [
    [baseChallenge + expected.toString("base64") + '"', expected.toString("base64url")],
    [baseChallenge + invalid.toString("base64") + '"', undefined],
    [baseChallenge.replace(owner.tenantId, "another-tenant") + expected.toString("base64") + '"', undefined],
    ['Bearer error="insufficient_claims", claims="synthetic-claims"', undefined],
  ] as const) {
    const app = setup([Response.json({ error: { code: "GRAPH_REAUTHENTICATION_REQUIRED" } }, {
      status: 401, headers: { "www-authenticate": challenge },
    })]);
    const response = await handleGraphToolProfile(request(), {
      ...app.dependencies,
      resolveBearer: async () => ({ value: "fixture." + "b".repeat(20), expiresAt: Date.now() + 60_000, accessExpiresAt: Date.now() + 700_000,
        generation: 1, binding: { ...owner, sessionId: "test-portal-session", hash: "c".repeat(64) } }),
      authorizeBearer: async () => undefined,
    });
    assert.equal(response.status, 403);
    assert.deepEqual(await body(response), { error: { code: "GraphReauthenticationRequired" }, ...(forwarded ? { claims: forwarded } : {}) });
    assert.equal(response.headers.has("www-authenticate"), false);
    assert.equal(app.calls.length, 1);
  }
});

test("ブラウザー提供の資格情報や別オリジンの要求を上流への通信前に拒否する", async () => {
  const app = setup();
  for (const supplied of [{ apiKeyId: "fixture" }, { authentication: "bearer", accessToken: token }, { authentication: "bearer", url: origin }]) {
    const result = await handleGraphToolProfile(request(supplied), app.dependencies);
    assert.equal(result.status, 400);
  }
  assert.equal((await handleGraphToolProfile(request(undefined, origin), app.dependencies)).status, 403);
  assert.equal(app.calls.length, 0);
});

test("Graphが別の本人を返したりBearerを反射したときは本文を公開しない", async () => {
  for (const [response, code] of [
    [Response.json({ ...profile, id: randomUUID() }), "ConnectionIdentityMismatch"],
    [Response.json({ ...profile, displayName: token }), "GatewayResponseInvalid"],
  ] as const) {
    const result = await handleGraphToolProfile(request(), setup([response]).dependencies);
    assert.deepEqual(await body(result), { error: { code } });
  }
});

test("Graphの接続先は公開HTTPSのオリジンに限定する", () => {
  assert.deepEqual(getGraphToolEnvironment({ PORTAL_GATEWAY_URL: origin }), { origin });
  for (const url of ["http://gateway.example.invalid", origin + "/other", origin + "?query=value"]) {
    assert.throws(() => getGraphToolEnvironment({ PORTAL_GATEWAY_URL: url }));
  }
});
