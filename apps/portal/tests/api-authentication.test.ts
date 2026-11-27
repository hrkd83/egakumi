import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";
import { test } from "node:test";
import { generateKeyPair, SignJWT } from "jose";
import { decodeApiClaims, PortalApiOAuth } from "../lib/api-auth/oauth.ts";
import { ApiAuthenticationError, readApiAuthenticationSettings, type ApiAuthenticationSettings } from "../lib/api-auth/settings.ts";
import { bindApiAuthSession } from "../lib/api-auth/store.ts";
import { resolveGatewayCredential } from "../lib/api-auth/gateway-credential.ts";
import { tryCredentialSchema } from "../lib/api-auth/try-credential.ts";
import { handleTextGeneration } from "../lib/model-gateway.ts";
import { chatModel } from "./helpers/model-fixtures.ts";

const tenantId = randomUUID();
const clientId = randomUUID();
const user = { tenantId, objectId: randomUUID(), sessionId: randomUUID() };
const scope = `api://${randomUUID()}/Gateway.Invoke`;
const portal = "https://portal.example.invalid";
const gateway = "https://gateway.example.invalid";
const entra = `https://login.microsoftonline.com/${tenantId}`;
const version = randomBytes(16).toString("hex");
const settings: ApiAuthenticationSettings = {
  auth: {
    baseURL: portal, secret: randomBytes(32).toString("base64url"), microsoftTenantId: tenantId,
    microsoftClientId: clientId, certificate: { keyId: `https://vault.example.invalid/keys/portal/${version}`,
      mode: "managed-identity", thumbprint: randomBytes(20).toString("hex"), identityClientId: randomUUID() },
  },
  callback: `${portal}/api/api-auth/callback`, entra, gatewayOrigin: gateway, scope,
  vaultSecret: "https://vault.example.invalid/secrets/api-auth",
  activeVersion: version, allowedVersions: new Set([version]), keyIdentity: randomUUID(),
};
const access = randomBytes(32).toString("base64url");
const refresh = randomBytes(32).toString("base64url");
const nonce = randomBytes(32).toString("base64url");
const verifier = randomBytes(32).toString("base64url");
const assertion = "test-only-client-assertion";
const state = randomBytes(32).toString("base64url");
const request = { nonce, verifier, returnTo: "/ja/models" };

test("API用の固定scopeとform_post、S256を使い、同じ本人のコードを一度交換する", async () => {
  const { publicKey, privateKey } = await generateKeyPair("RS256");
  const idToken = await new SignJWT({ nonce, tid: tenantId, oid: user.objectId })
    .setProtectedHeader({ alg: "RS256" }).setSubject("api-auth-test-user")
    .setIssuedAt().setExpirationTime("5m").setIssuer(`${entra}/v2.0`).setAudience(clientId).sign(privateKey);
  let requests = 0;
  const oauth = new PortalApiOAuth(settings, async (url, options) => {
    requests++;
    assert.equal(String(url), `${entra}/oauth2/v2.0/token`);
    assert.equal(options?.redirect, "error");
    const body = options?.body as URLSearchParams;
    assert.equal(body.get("code_verifier"), verifier);
    assert.equal(body.get("redirect_uri"), settings.callback);
    assert.equal(body.get("client_assertion"), assertion);
    assert.equal(body.get("client_secret"), null);
    return Response.json({ access_token: access, refresh_token: refresh, id_token: idToken,
      expires_in: 3600, token_type: "Bearer", scope: "Gateway.Invoke" });
  }, async () => publicKey, async () => assertion);
  const url = await oauth.authorizationURL(state, request);
  assert.equal(url.origin, "https://login.microsoftonline.com");
  assert.equal(url.searchParams.get("state"), state);
  assert.equal(url.searchParams.get("nonce"), nonce);
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
  assert.equal(url.searchParams.get("response_mode"), "form_post");
  assert.equal(url.searchParams.get("redirect_uri"), settings.callback);
  assert.ok(url.searchParams.get("scope")?.includes(scope));
  assert.ok(!url.href.includes(verifier));
  assert.deepEqual(await oauth.exchange("example-code", request, user), { access, refresh, expiresSeconds: 3600 });
  assert.equal(requests, 1);
  await assert.rejects(oauth.exchange("example-code", request, { ...user, objectId: randomUUID() }), ApiAuthenticationError);
  await assert.rejects(oauth.exchange("example-code", { ...request, nonce: state }, user), ApiAuthenticationError);
  assert.equal(requests, 3);
});

test("追加認証はアクセス用claimsだけを固定tenant・scopeのauthorizeへ追加する", async () => {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const claims = { access_token: { acrs: { essential: true, value: "c1" } } };
  const oauth = new PortalApiOAuth(settings, undefined, undefined, async () => assertion);
  const target = await oauth.authorizationURL(state, request, decodeApiClaims(encode(claims)));
  assert.equal(target.origin, new URL(entra).origin);
  assert.equal(target.searchParams.get("claims"), JSON.stringify(claims));
  assert.equal(target.searchParams.get("scope"), `openid profile offline_access ${scope}`);
  assert.equal(target.searchParams.get("state"), state);
  assert.equal(target.searchParams.get("code_challenge_method"), "S256");
  for (const invalid of [
    encode({ ...claims, id_token: { acrs: null } }),
    encode({ access_token: {} }),
    encode({ access_token: { acrs: { unknown: true } } }),
    encode({ access_token: { acrs: { values: ["a".repeat(512)] } } }) + "=",
  ]) {
    assert.throws(() => decodeApiClaims(invalid), { code: "ApiAuthorizationInvalid", status: 400 });
  }
});

test("更新要求は固定scopeと証明書を使い、invalid_grantを内部障害と区別する", async () => {
  const oauth = new PortalApiOAuth(settings, async (_url, options) => {
    const body = options?.body as URLSearchParams;
    assert.equal(body.get("grant_type"), "refresh_token");
    assert.equal(body.get("refresh_token"), refresh);
    assert.equal(body.get("client_assertion"), assertion);
    assert.match(body.get("scope") ?? "", /Gateway\.Invoke$/);
    return Response.json({ error: "invalid_grant", error_description: "internal response" }, { status: 400 });
  }, undefined, async () => assertion);
  await assert.rejects(oauth.refresh(refresh, user, ""), (error: unknown) =>
    error instanceof ApiAuthenticationError && error.code === "ApiAuthorizationDenied" && error.status === 401);
});

test("購読管理はTryのBearer設定から独立し、同じ本人・callbackでも用途が異なるトークンを受け付けない", async (testContext) => {
  const manageScope = scope.replace(/Gateway\.Invoke$/, "Subscription.Manage");
  const azureVault = new URL("https://vault.azure.net");
  azureVault.hostname = `synthetic-${randomUUID().slice(0, 8)}.${azureVault.hostname}`;
  const environment = {
    BETTER_AUTH_URL: portal,
    BETTER_AUTH_SECRET: settings.auth.secret,
    MICROSOFT_CLIENT_ID: clientId,
    MICROSOFT_CLIENT_SECRET: undefined,
    MICROSOFT_TENANT_ID: tenantId,
    PORTAL_AUTH_KEY_ID: `${azureVault.origin}/keys/portal/${version}`,
    PORTAL_AUTH_CERTIFICATE_THUMBPRINT: settings.auth.certificate.thumbprint,
    PORTAL_AUTH_SIGNING_MODE: "managed-identity",
    PORTAL_AUTH_IDENTITY_CLIENT_ID: settings.auth.certificate.identityClientId,
    PORTAL_GATEWAY_URL: gateway,
    PORTAL_API_AUTH_ENABLED: "false",
    PORTAL_API_AUTH_SCOPE: scope,
    PORTAL_API_AUTH_KEY_URL: `${azureVault.origin}/secrets/portal/${version}`,
    PORTAL_API_AUTH_KEY_CLIENT_ID: settings.keyIdentity,
    PORTAL_API_AUTH_PREVIOUS_KEY_VERSIONS: "",
    PORTAL_SUBSCRIPTION_MANAGER_URL: "https://internal.example.invalid",
    PORTAL_SUBSCRIPTION_MANAGEMENT_SCOPE: manageScope,
  };
  const previous = new Map(Object.keys(environment).map((name) => [name, process.env[name]]));
  testContext.after(() => {
    for (const [name, value] of previous) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });
  for (const [name, value] of Object.entries(environment)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
  assert.throws(() => readApiAuthenticationSettings(), { code: "ApiAuthenticationDisabled", status: 404 });
  const management = readApiAuthenticationSettings("subscription-management");
  assert.equal(management.scope, manageScope);
  assert.equal(management.callback, settings.callback);
  assert.notEqual(bindApiAuthSession(user, management).hash, bindApiAuthSession(user, settings).hash);
  let returned = "Gateway.Invoke";
  const oauth = new PortalApiOAuth(management, async (_url, options) => {
    const fields = options?.body as URLSearchParams;
    assert.equal(fields.get("scope"), `openid profile offline_access ${manageScope}`);
    return Response.json({ access_token: access, expires_in: 120, token_type: "Bearer", scope: returned });
  }, undefined, async () => assertion);
  const authorization = await oauth.authorizationURL(state, request);
  assert.equal(authorization.searchParams.get("scope"), `openid profile offline_access ${manageScope}`);
  await assert.rejects(oauth.refresh(refresh, user, ""), { code: "ApiAuthorizationDenied", status: 401 });
  returned = "Subscription.Manage";
  assert.equal((await oauth.refresh(refresh, user, "")).access, access);
  process.env.PORTAL_SUBSCRIPTION_MANAGEMENT_SCOPE = scope;
  assert.throws(() => readApiAuthenticationSettings("subscription-management"), { code: "ApiAuthenticationUnavailable", status: 503 });
});

test("APIキーとBearerを同時に受け付けず、本人のBearerだけを上流ヘッダーに使う", async () => {
  const keyId = `portal-${randomUUID()}`;
  assert.equal(tryCredentialSchema.safeParse({ apiKeyId: keyId, authentication: "bearer" }).success, false);
  assert.equal(tryCredentialSchema.safeParse({ authentication: "bearer", accessToken: access }).success, false);
  const bearer = { value: access, expiresAt: Date.now() + 600_000, accessExpiresAt: Date.now() + 700_000, generation: 0,
    binding: { ...user, hash: "a".repeat(64) } };
  const originRequest = (selection: object) => new Request(`${portal}/api/models/generate`, {
    method: "POST", headers: { Origin: portal, "Content-Type": "application/json" },
    body: JSON.stringify({ message: "Hello", modelId: chatModel.id, maxOutputTokens: 128, stream: false, ...selection }),
  });
  const dependencies = {
    readSession: async () => user, readPortalOrigin: () => portal,
    readModels: () => [chatModel], readGatewayEnvironment: async () => ({ origin: gateway }),
    resolveBearer: async () => bearer,
    resolveSubscriptionKey: async () => { throw new Error("Unexpected key lookup"); },
  };
  let sends = 0;
  const response = await handleTextGeneration(originRequest({ authentication: "bearer" }), {
    ...dependencies, fetch: async (url, options) => {
      sends++;
      assert.equal(String(url), `${gateway}${chatModel.path}`);
      const headers = new Headers(options?.headers);
      assert.equal(headers.get("Authorization"), `Bearer ${access}`);
      assert.equal(headers.get("api-key"), null);
      assert.ok(!String(options?.body).includes(access));
      return Response.json({ model: chatModel.id, choices: [{ message: { content: "Accepted" } }] });
    },
  });
  assert.equal(response.status, 200);
  assert.equal(sends, 1);
  assert.ok(!(await response.text()).includes(access));
  const denied = await handleTextGeneration(originRequest({ authentication: "bearer" }), {
    ...dependencies, resolveBearer: async () => { throw new ApiAuthenticationError("ApiAuthenticationRequired", 401); },
    fetch: async () => { throw new Error("Unexpected retry"); },
  });
  assert.equal(denied.status, 401);
  assert.equal((await denied.json()).error.code, "ApiAuthenticationRequired");
  await assert.rejects(resolveGatewayCredential(originRequest({}), { ...user, objectId: randomUUID() },
    { authentication: "bearer" }, chatModel.apiId, gateway, undefined, async () => bearer), ApiAuthenticationError);
});
