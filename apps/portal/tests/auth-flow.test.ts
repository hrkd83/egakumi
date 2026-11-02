import assert from "node:assert/strict";
import { test } from "node:test";
import { betterAuth } from "better-auth";
import { memoryAdapter } from "better-auth/adapters/memory";
import { generateKeyPair, SignJWT } from "jose";
import { createMicrosoftProfileResolver } from "../lib/auth-identity.ts";
import { createPortalAuthOptions } from "../lib/auth-options.ts";

/** 検証用レスポンスのCookieから、次のリクエストに送る値だけを作ります。 */
function responseCookies(response: Response): string {
  return response.headers.getSetCookie().map((cookie) => cookie.split(";", 1)[0]).join("; ");
}

test("認証開始から本人登録・再利用・ログアウトまでをBetter Auth本体で確認する", async () => {
  const tenantId = "11111111-1111-4111-8111-111111111111";
  const clientId = "22222222-2222-4222-8222-222222222222";
  const objectId = "33333333-3333-4333-8333-333333333333";
  const baseURL = "https://portal.example.invalid";
  const database: Record<string, Record<string, unknown>[]> = {
    portalUser: [], portalAccount: [], portalSession: [], portalVerification: [],
  };
  const configuration = createPortalAuthOptions({
    baseURL, secret: "integration-test-only-32-character-secret", microsoftTenantId: tenantId,
    microsoftClientId: clientId, microsoftClientSecret: "integration-test-only",
  }, memoryAdapter(database));
  const auth = betterAuth({ ...configuration, logger: { disabled: true } });
  const context = await auth.$context;
  const provider = context.socialProviders.find((item) => item.id === "microsoft");
  assert.ok(provider);
  const keys = await generateKeyPair("RS256");
  const idToken = await new SignJWT({ tid: tenantId, oid: objectId, name: "Test User" })
    .setProtectedHeader({ alg: "RS256" }).setIssuer(`https://login.microsoftonline.com/${tenantId}/v2.0`)
    .setAudience(clientId).setSubject("test-subject").setIssuedAt().setExpirationTime("5m").sign(keys.privateKey);
  // Entraのコード交換だけを固定応答に置き換え、状態照合・保存・セッションは本体を通します。
  provider.validateAuthorizationCode = async () => ({ idToken, accessToken: "transient-test-token", scopes: ["openid", "profile", "email"] });
  provider.getUserInfo = createMicrosoftProfileResolver({ tenantId, clientId }, async () => keys.publicKey);

  const start = await auth.handler(new Request(`${baseURL}/api/auth/sign-in/social`, {
    method: "POST", headers: { "Content-Type": "application/json", Origin: baseURL },
    body: JSON.stringify({ provider: "microsoft", callbackURL: "/ja/models", errorCallbackURL: "/ja/login?error=test" }),
  }));
  assert.equal(start.status, 200);
  const startData = await start.json() as { url: string };
  const loginURL = new URL(startData.url);
  assert.deepEqual(loginURL.searchParams.get("scope")?.split(" ").sort(), ["email", "openid", "profile"]);
  assert.equal(loginURL.searchParams.get("code_challenge_method"), "S256");
  const state = loginURL.searchParams.get("state");
  assert.ok(state);
  assert.equal(database.portalVerification.length, 1);

  const callbackURL = `${baseURL}/api/auth/callback/microsoft?code=test-code&state=${encodeURIComponent(state)}`;
  const callback = await auth.handler(new Request(callbackURL, { headers: { Cookie: responseCookies(start) } }));
  assert.equal(callback.status, 302);
  assert.equal(callback.headers.get("location"), "/ja/models");
  assert.equal(database.portalUser.length, 1);
  assert.equal(database.portalUser[0].entraTenantId, tenantId);
  assert.equal(database.portalUser[0].entraObjectId, objectId);
  assert.equal(database.portalAccount.length, 1);
  assert.equal(database.portalAccount[0].accountId, objectId);
  for (const key of ["accessToken", "refreshToken", "idToken"]) assert.equal(database.portalAccount[0][key], null);
  assert.ok(callback.headers.getSetCookie().some((cookie) => cookie.includes("HttpOnly") && cookie.includes("Secure")));

  const cookie = responseCookies(callback);
  const sessionRequest = () => new Request(`${baseURL}/api/auth/get-session`, { headers: { Cookie: cookie } });
  const session = await auth.handler(sessionRequest());
  assert.equal(session.status, 200);
  const current = await session.json() as { user: { entraObjectId: string } };
  assert.equal(current.user.entraObjectId, objectId);

  // 同じコールバックを再使用しても、新規セッションや本人を増やせません。
  const sessionCount = database.portalSession.length;
  await auth.handler(new Request(callbackURL, { headers: { Cookie: responseCookies(start) } }));
  assert.equal(database.portalSession.length, sessionCount);
  assert.equal(database.portalUser.length, 1);

  const logout = await auth.handler(new Request(`${baseURL}/api/auth/sign-out`, {
    method: "POST", headers: { Cookie: cookie, Origin: baseURL, "Content-Type": "application/json" }, body: "{}",
  }));
  assert.equal(logout.status, 200);
  assert.equal(await (await auth.handler(sessionRequest())).json(), null);
});
