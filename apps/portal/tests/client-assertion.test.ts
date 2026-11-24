import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { createClientAssertion, validateCertificateAuthentication } from "../lib/client-assertion.ts";
import { GraphApiKeyMembership } from "../lib/api-keys/graph.ts";

// 実環境から独立した合成値。RSA秘密鍵は試験中のメモリーだけで保持します。
const tenant = "11111111-1111-4111-8111-111111111111";
const client = "22222222-2222-4222-8222-222222222222";
const certificate = { keyId: "https://kv-synthetic.vault.azure.net/keys/portal-auth/" + "a".repeat(32),
  thumbprint: "a".repeat(40), mode: "managed-identity" as const, identityClientId: client };

test("client assertionは固定宛先・証明書thumbprint・短い期限・一意jtiを使い、SHA256だけを署名依存へ渡す", async () => {
  const digests: Uint8Array[] = [];
  const factory = createClientAssertion(tenant, client, certificate, async (digest) => {
    digests.push(digest);
    return new Uint8Array(384).fill(7);
  }, () => 1_800_000_000_000);
  const first = await factory();
  const second = await factory();
  const [header, payload] = first.split(".").slice(0, 2).map((item) => JSON.parse(Buffer.from(item, "base64url").toString()));
  assert.equal(header.alg, "RS256");
  assert.equal(header.x5t, Buffer.from(certificate.thumbprint, "hex").toString("base64url"));
  assert.equal(payload.aud, `https://login.microsoftonline.com/${tenant}/oauth2/v2.0/token`);
  assert.equal(payload.iss, client);
  assert.equal(payload.sub, client);
  assert.equal(payload.exp - payload.iat, 300);
  assert.equal(payload.iat - payload.nbf, 30);
  assert.notEqual(payload.jti, JSON.parse(Buffer.from(second.split(".")[1], "base64url").toString()).jti);
  assert.deepEqual(Buffer.from(digests[0]), createHash("sha256").update(first.split(".").slice(0, 2).join(".")).digest());
  assert.equal(digests[0].length, 32);
});

test("署名失敗と不完全な設定は入力値を例外へ含めず停止する", async () => {
  const secretMarker = "synthetic-private-marker";
  const factory = createClientAssertion(tenant, client, certificate, async () => { throw new Error(secretMarker); });
  await assert.rejects(factory(), (error: Error) => error.message === "Portal certificate signing unavailable");
  for (const changed of [{ keyId: certificate.keyId.slice(0, -33) }, { thumbprint: "bad" }, { identityClientId: "bad" }]) {
    assert.throws(() => validateCertificateAuthentication({ ...certificate, ...changed }), /configuration is invalid/);
  }
});

test("Graph管理は証明書方式のApplication tokenを使用し、取得失敗でsecret方式へ切り替えない", async () => {
  let tokenCalls = 0;
  const urls: string[] = [];
  const graph = new GraphApiKeyMembership({ microsoftClientId: client, microsoftTenantId: tenant, certificate }, {
    getToken: async () => { tokenCalls++; return { token: "synthetic-graph-token", expiresOnTimestamp: Date.now() + 600_000 }; },
    fetch: async (input, init) => {
      urls.push(String(input));
      assert.ok(String(input).startsWith("https://graph.microsoft.com/"));
      assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer synthetic-graph-token");
      return Response.json({ value: [client] });
    },
  });
  const actor = { tenantId: tenant, objectId: client, name: "Synthetic user" };
  assert.deepEqual(await graph.check(actor, [client]), new Set([client]));
  await graph.check(actor, [client]);
  assert.equal(tokenCalls, 1);
  const failing = new GraphApiKeyMembership({ microsoftClientId: client, microsoftTenantId: tenant, certificate }, {
    getToken: async () => { throw new Error("synthetic-token-error"); },
    fetch: async () => { throw new Error("must not contact a token or Graph endpoint"); },
  });
  await assert.rejects(failing.check(actor, [client]), (error: Error) => !error.message.includes("synthetic-token-error"));
  assert.equal(urls.length, 2);
});
