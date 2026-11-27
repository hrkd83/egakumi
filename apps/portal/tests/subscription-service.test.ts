import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { generateKeyPair, SignJWT, type JWTPayload } from "jose";
import { handleSubscriptionService } from "../lib/subscription-service/handler.ts";
import { createSubscriptionActorVerifier } from "../lib/subscription-service/identity.ts";

const actor = { tenantId: randomUUID(), objectId: randomUUID() };
const host = "https://service.example.invalid";
const managementUrl = host + "/api/internal/subscriptions";
const profileUrl = host + "/api/internal/subscription-profile";

test("購読管理は専用scopeを持つPortal向けの署名済み本人トークンを照合する", async () => {
  const settings = { tenant: actor.tenantId, audience: randomUUID(), client: randomUUID() };
  const issuer = `https://login.microsoftonline.com/${settings.tenant}/v2.0`;
  const { privateKey, publicKey } = await generateKeyPair("RS256");
  const claims = { iss: issuer, aud: settings.audience, azp: settings.client, tid: settings.tenant,
    oid: actor.objectId, ver: "2.0", scp: "Subscription.Manage" };
  const signed = (patch: JWTPayload = {}) => new SignJWT({ ...claims, ...patch })
    .setProtectedHeader({ alg: "RS256" }).setIssuedAt().setNotBefore("0s").setExpirationTime("5m").sign(privateKey);
  const verify = createSubscriptionActorVerifier(settings, async () => publicKey);
  const request = (token: string) => new Request(managementUrl, {
    headers: { Authorization: `Bearer ${token}`, "X-Entra-Object-Id": randomUUID(), Cookie: "session=synthetic" },
  });
  assert.deepEqual(await verify(request(await signed())), actor);
  for (const patch of [{ scp: "Gateway.Invoke" }, { scp: "Subscription.Manage Gateway.Invoke" },
    { scp: "", roles: ["Subscription.Manage"] }, { azp: randomUUID() }, { tid: randomUUID() },
    { aud: randomUUID() }, { iss: "https://issuer.example.invalid" }, { oid: "absent" }]) {
    await assert.rejects(verify(request(await signed(patch))), { status: 401 });
  }
  await assert.rejects(verify(new Request(managementUrl, { headers: { Cookie: "session=synthetic" } })), { status: 401 });
});

test("購読管理と利用枠照会の入口は互いの操作を受け付けない", async () => {
  const secret = "a".repeat(128);
  let reads = 0;
  let authorizations = 0;
  const dependencies = {
    readActor: async () => { authorizations++; return actor; },
    profile: {
      settings: () => ({ secret, tenantId: actor.tenantId }),
      readProfile: async () => { reads++; return null; },
    },
  };
  const management = (body: object) => new Request(managementUrl, { method: "POST", body: JSON.stringify(body) });
  const profile = () => new Request(profileUrl, { headers: {
    "X-Core-Profile-Key": secret, "X-Core-Profile-Subscription": "synthetic",
  } });
  assert.equal((await handleSubscriptionService(management({ operation: "keyOverview", input: null }), "profile", dependencies)).status, 404);
  assert.equal((await handleSubscriptionService(profile(), "management", dependencies)).status, 404);
  assert.equal((await handleSubscriptionService(new Request(profileUrl), "profile", dependencies)).status, 403);
  assert.equal(reads, 0);
  assert.equal(authorizations, 0);
  assert.equal((await handleSubscriptionService(management({ operation: "constructor", input: null }), "management", dependencies)).status, 404);
  assert.equal((await handleSubscriptionService(management({ operation: "keyOverview", input: { actor } }), "management", dependencies)).status, 400);
  const success = await handleSubscriptionService(profile(), "profile", dependencies);
  assert.equal(success.status, 200);
  assert.equal(success.headers.get("Cache-Control"), "no-store");
  assert.equal((await success.json()).subscriptionId, "synthetic");
  assert.equal(reads, 1);
  assert.equal(authorizations, 2);
});
