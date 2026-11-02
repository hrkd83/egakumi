import assert from "node:assert/strict";
import { test } from "node:test";
import { betterAuth } from "better-auth";
import { generateKeyPair, SignJWT } from "jose";
import { sql } from "kysely";
import { createPortalAuthOptions } from "../lib/auth-options.ts";
import { createMicrosoftProfileResolver } from "../lib/auth-identity.ts";
import { createLocalSqlTestDatabase, localSqlTest } from "./helpers/local-sql.ts";

/** 検証用Cookieをメモリー内だけで次の要求へ引き継ぎます。 */
function cookies(response: Response): string {
  return response.headers.getSetCookie().map((value) => value.split(";", 1)[0]).join("; ");
}

test("実SQLのPortalセッションは8時間で作成され、期限切れCookieを本人接続とは別に拒否する", localSqlTest, async (context) => {
  const { admin, runtime } = await createLocalSqlTestDatabase(context);
  const tenantId = "11111111-1111-4111-8111-111111111111";
  const clientId = "22222222-2222-4222-8222-222222222222";
  const objectId = "33333333-3333-4333-8333-333333333333";
  const baseURL = "https://portal.example.invalid";
  const options = createPortalAuthOptions({ baseURL, secret: "isolated-sql-auth-expiry-only-secret-32",
    microsoftTenantId: tenantId, microsoftClientId: clientId, microsoftClientSecret: "test-only" },
  { db: runtime, type: "mssql" });
  const auth = betterAuth({ ...options, logger: { disabled: true } });
  const provider = (await auth.$context).socialProviders.find((entry) => entry.id === "microsoft");
  assert.ok(provider);
  const keys = await generateKeyPair("RS256");
  const idToken = await new SignJWT({ tid: tenantId, oid: objectId, name: "Expiry Test" })
    .setProtectedHeader({ alg: "RS256" }).setIssuer(`https://login.microsoftonline.com/${tenantId}/v2.0`)
    .setAudience(clientId).setSubject("expiry-test").setIssuedAt().setExpirationTime("5m").sign(keys.privateKey);
  // Microsoftのコード交換だけを置き換え、署名検証・Cookie・SQL期限判定は本体を通します。
  provider.validateAuthorizationCode = async () => ({ idToken, accessToken: "transient-test-token", scopes: ["openid", "profile", "email"] });
  provider.getUserInfo = createMicrosoftProfileResolver({ tenantId, clientId }, async () => keys.publicKey);
  const start = await auth.handler(new Request(`${baseURL}/api/auth/sign-in/social`, {
    method: "POST", headers: { Origin: baseURL, "Content-Type": "application/json" },
    body: JSON.stringify({ provider: "microsoft", callbackURL: "/ja/models", errorCallbackURL: "/ja/login" }),
  }));
  assert.equal(start.status, 200);
  const login = new URL((await start.json() as { url: string }).url);
  const callback = await auth.handler(new Request(`${baseURL}/api/auth/callback/microsoft?code=test-code&state=${encodeURIComponent(login.searchParams.get("state")!)}`,
    { headers: { Cookie: cookies(start) } }));
  assert.equal(callback.status, 302);
  assert.ok(callback.headers.getSetCookie().some((value) => value.includes("Secure") && value.includes("HttpOnly") && /SameSite=Lax/i.test(value)));
  const cookie = cookies(callback);
  const read = () => auth.handler(new Request(`${baseURL}/api/auth/get-session`, { headers: { Cookie: cookie } }));
  const before = await (await read()).json() as { user: { entraObjectId: string } };
  assert.equal(before.user.entraObjectId, objectId);

  // Nextの要求コンテキストと構成取得だけを接続し、実際のセッション検証と本人抽出を実行します。
  // Better Auth本体・署名Cookie・SQL・readPortalPrincipalの本人判定は置き換えません。
  const bridgeId = "__egakumi_auth_sql_principal_fixture";
  const bridge = { auth, headers: new Headers(), tenantId };
  const globals = globalThis as typeof globalThis & Record<string, unknown>;
  globals[bridgeId] = bridge;
  type Resolution = { url: string; shortCircuit?: boolean };
  type ModuleHooks = { registerHooks: (hooks: { resolve: (
    specifier: string, context: { parentURL?: string }, next: (specifier: string, context: { parentURL?: string }) => Resolution,
  ) => Resolution }) => { deregister(): void } };
  const { registerHooks } = await import("node:module") as unknown as ModuleHooks;
  const sessionModule = new URL("../lib/auth-session.ts", import.meta.url).href;
  const access = `globalThis[${JSON.stringify(bridgeId)}]`;
  const adapters: Record<string, string> = {
    "next/headers": `export const headers = async () => ${access}.headers`,
    "next/server": "export const connection = async () => {}",
    "next/navigation": "export const redirect = () => { throw new Error('Unexpected redirect') }",
    "@/lib/auth": `export const getAuth = () => ${access}.auth`,
    "@/lib/auth-environment": `export const getAuthEnvironment = () => ({ microsoftTenantId: ${access}.tenantId })`,
    "@/lib/auth-navigation": "export const safeReturnTo = (value) => value",
  };
  const hooks = registerHooks({ resolve(specifier, context, next) {
    if (context.parentURL === sessionModule && adapters[specifier]) {
      return { url: `data:text/javascript,${encodeURIComponent(adapters[specifier])}`, shortCircuit: true };
    }
    return next(specifier, context);
  } });
  let principalReader: (() => Promise<{ tenantId: string; objectId: string } | null>) | undefined;
  try {
    const principal = await import(sessionModule) as {
      readPortalPrincipal: () => Promise<{ tenantId: string; objectId: string } | null>;
      readPortalSession: () => Promise<{ name: string; email: string | null } | null>;
    };
    principalReader = principal.readPortalPrincipal;
    const spoofed = { "X-Core-User-UPN": "different-person@example.invalid",
      "X-Entra-UPN": "different-person@example.invalid", "X-Entra-Tenant-ID": "44444444-4444-4444-8444-444444444444",
      "X-Entra-Object-ID": "55555555-5555-4555-8555-555555555555",
      "X-MS-CLIENT-PRINCIPAL-ID": "55555555-5555-4555-8555-555555555555" };
    bridge.headers = new Headers(spoofed);
    assert.equal(await principal.readPortalPrincipal(), null, "本人申告ヘッダーだけではログインにならない");
    bridge.headers = new Headers({ Cookie: cookie, ...spoofed });
    const verified = await principal.readPortalPrincipal();
    assert.equal(verified?.tenantId, tenantId);
    assert.equal(verified?.objectId, objectId, "別人の申告で署名済みCookieの本人を切り替えない");
    assert.ok(await principal.readPortalSession());
    const spoofedSession = await auth.api.getSession({ headers: bridge.headers, query: { disableCookieCache: true, disableRefresh: true } });
    assert.equal(spoofedSession?.user.entraObjectId, objectId, "Better Auth本体でも本人はSQLへ保存した署名検証済みID");
  } finally {
    hooks.deregister();
  }
  const rows = (await sql<{ id: string; lifetimeMs: number }>`SELECT id,DATEDIFF_BIG(millisecond,createdAt,expiresAt) AS lifetimeMs FROM dbo.portalSession`.execute(admin)).rows;
  assert.equal(rows.length, 1);
  assert.ok(Math.abs(Number(rows[0].lifetimeMs) - 8 * 60 * 60 * 1000) < 1000);
  await sql`UPDATE dbo.portalSession SET expiresAt=DATEADD(second,-1,SYSUTCDATETIME()) WHERE id=${rows[0].id}`.execute(admin);
  assert.equal(await (await read()).json(), null);
  assert.equal(await principalReader?.(), null, "実Portal本人抽出も、期限切れCookieと申告ヘッダーを本人にしない");
  delete globals[bridgeId];
  const remaining = (await sql<{ total: number }>`SELECT COUNT(*) AS total FROM dbo.portalSession WHERE expiresAt>SYSUTCDATETIME()`.execute(admin)).rows[0].total;
  assert.equal(remaining, 0, "期限切れ要求で有効なPortalセッションを作り直さない");
});
