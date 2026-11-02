import assert from "node:assert/strict";
import { test } from "node:test";
import { generateKeyPair, SignJWT } from "jose";
import { createIdentityGate, createMicrosoftProfileResolver, withoutOAuthTokens } from "../lib/auth-identity.ts";

const tenantId = "11111111-1111-4111-8111-111111111111";
const clientId = "22222222-2222-4222-8222-222222222222";
const objectId = "33333333-3333-4333-8333-333333333333";
const issuer = `https://login.microsoftonline.com/${tenantId}/v2.0`;
const keys = await generateKeyPair("RS256");
const resolveProfile = createMicrosoftProfileResolver({ tenantId, clientId }, async () => keys.publicKey);

/** 実在するユーザーを含まない、署名付きの検証用IDトークンを作ります。 */
function signedToken(overrides: Record<string, unknown> = {}) {
  return new SignJWT({ tid: tenantId, oid: objectId, name: "Test User", ...overrides })
    .setProtectedHeader({ alg: "RS256" }).setIssuer(issuer).setAudience(clientId)
    .setSubject("test-subject").setIssuedAt().setExpirationTime("5m").sign(keys.privateKey);
}

test("署名を検証してtid/oidを保持し、メールなしでも安定した本人を作る", async () => {
  const result = await resolveProfile({ idToken: await signedToken() });
  assert.equal(result.user.entraTenantId, tenantId);
  assert.equal(result.user.entraObjectId, objectId);
  assert.equal(result.user.email, `${objectId}.${tenantId}@identity.invalid`);
  assert.equal(result.user.emailVerified, false);
  assert.equal(result.data.oid, objectId);
});

test("別テナント・宛先・署名・期限切れのトークンを拒否する", async () => {
  await assert.rejects(resolveProfile({ idToken: await signedToken({ tid: objectId }) }));
  const wrongAudience = await new SignJWT({ tid: tenantId, oid: objectId }).setProtectedHeader({ alg: "RS256" })
    .setIssuer(issuer).setAudience(objectId).setSubject("subject").setIssuedAt().setExpirationTime("5m").sign(keys.privateKey);
  await assert.rejects(resolveProfile({ idToken: wrongAudience }));
  const wrongIssuer = await new SignJWT({ tid: tenantId, oid: objectId }).setProtectedHeader({ alg: "RS256" })
    .setIssuer("https://untrusted.example.invalid").setAudience(clientId).setSubject("subject")
    .setIssuedAt().setExpirationTime("5m").sign(keys.privateKey);
  await assert.rejects(resolveProfile({ idToken: wrongIssuer }));
  const otherKeys = await generateKeyPair("RS256");
  const forged = await new SignJWT({ tid: tenantId, oid: objectId }).setProtectedHeader({ alg: "RS256" })
    .setIssuer(issuer).setAudience(clientId).setSubject("subject").setIssuedAt().setExpirationTime("5m").sign(otherKeys.privateKey);
  await assert.rejects(resolveProfile({ idToken: forged }));
  const expired = await new SignJWT({ tid: tenantId, oid: objectId }).setProtectedHeader({ alg: "RS256" })
    .setIssuer(issuer).setAudience(clientId).setSubject("subject").setIssuedAt(1).setExpirationTime(2).sign(keys.privateKey);
  await assert.rejects(resolveProfile({ idToken: expired }));
  await assert.rejects(resolveProfile({}));
});

test("本人識別列は検証済みMicrosoftプロフィールとの一致を必要とする", async () => {
  const gate = createIdentityGate(tenantId);
  const data = {
    user: { entraTenantId: tenantId, entraObjectId: objectId },
    source: { method: "oauth", action: "create-user" as const, oauth: {
      providerId: "microsoft", profile: { tid: tenantId, oid: objectId, sub: "subject" },
    } },
  };
  type Context = Parameters<typeof gate>[1];
  const unusedContext = {} as Context;
  assert.equal(await gate(data, unusedContext), undefined);
  assert.equal((await gate({ ...data, user: { ...data.user, entraObjectId: clientId } }, unusedContext))?.error, "identity_not_allowed");
  assert.equal((await gate({ ...data, source: { method: "email-password", action: "create-user" } }, unusedContext))?.error, "identity_not_allowed");
});

test("新規登録・更新に含まれるOAuthトークン値を除去し、アカウント対応を残す", () => {
  const result = withoutOAuthTokens({ userId: "user", accountId: objectId, providerId: "microsoft", accessToken: "secret", refreshToken: "secret", idToken: "secret", scope: "openid profile email" });
  assert.equal(result.userId, "user");
  assert.equal(result.accountId, objectId);
  assert.equal(result.scope, "openid profile email");
  for (const field of ["accessToken", "refreshToken", "idToken", "accessTokenExpiresAt", "refreshTokenExpiresAt"] as const) assert.equal(result[field], null);
});
