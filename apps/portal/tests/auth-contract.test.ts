import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { getSchema, parseAdditionalUserInputFromProviderProfile } from "better-auth/db";
import { createPortalAuthOptions } from "../lib/auth-options.ts";
import { isAllowedAuthRoute, parsePortalSignIn } from "../lib/auth-routes.ts";
import { safeReturnTo } from "../lib/auth-navigation.ts";
import { portalAuthModels } from "../lib/auth-schema.ts";

const environment = {
  baseURL: "https://portal.example.invalid", secret: "test-only-32-characters-long-secret",
  microsoftTenantId: "11111111-1111-4111-8111-111111111111",
  microsoftClientId: "22222222-2222-4222-8222-222222222222", microsoftClientSecret: "test-only",
};
const options = createPortalAuthOptions(environment, undefined);

test("PortalはOIDCのみを要求し、Graph・リフレッシュトークン・メールパスワード認証を使わない", () => {
  assert.deepEqual(options.socialProviders.microsoft.scope, ["openid", "profile", "email"]);
  assert.equal(options.socialProviders.microsoft.disableDefaultScope, true);
  assert.equal(options.socialProviders.microsoft.disableProfilePhoto, true);
  assert.equal(options.socialProviders.microsoft.disableIdTokenSignIn, true);
  assert.equal(options.emailAndPassword.enabled, false);
  assert.equal(options.account.accountLinking.enabled, false);
  assert.equal(options.account.updateAccountOnSignIn, false);
  assert.equal(options.session.cookieCache.enabled, false);
  assert.equal(options.advanced.useSecureCookies, true);
});

test("Better Authの実際のOAuth取り込みが必須のtid/oidを失わない", () => {
  const profile = { entraTenantId: environment.microsoftTenantId, entraObjectId: environment.microsoftClientId };
  assert.deepEqual({ ...parseAdditionalUserInputFromProviderProfile(options, profile, "create") }, profile);
});

test("本人情報編集と任意の認証APIを公開せず、既定の操作だけを通す", () => {
  assert.equal(isAllowedAuthRoute("/api/auth/sign-in/social", "POST"), true);
  assert.equal(isAllowedAuthRoute("/api/auth/callback/microsoft", "GET"), true);
  assert.equal(isAllowedAuthRoute("/api/auth/get-session", "GET"), true);
  assert.equal(isAllowedAuthRoute("/api/auth/sign-out", "POST"), true);
  for (const route of ["update-user", "link-social", "get-access-token", "delete-user", "sign-up/email", "%75pdate-user"])
    assert.equal(isAllowedAuthRoute(`/api/auth/${route}`, "POST"), false);
  assert.equal(isAllowedAuthRoute("/api/auth/sign-out", "GET"), false);
});

test("認証開始は画面のMicrosoftログインだけを受け付け、追加スコープや直接トークンを拒否する", () => {
  const request = {
    provider: "microsoft",
    callbackURL: "/ja/tools",
    errorCallbackURL: "/ja/login?error=authentication_failed&next=%2Fja%2Ftools",
  };
  assert.deepEqual(parsePortalSignIn(request), request);
  for (const extra of [
    { scopes: ["User.Read", "offline_access"] }, { scopes: [] },
    { idToken: { token: "untrusted" } }, { additionalParams: { prompt: "consent" } },
    { additionalData: { userId: "untrusted" } }, { newUserCallbackURL: "/ja/agents" },
  ]) assert.equal(parsePortalSignIn({ ...request, ...extra }), null);
  assert.equal(parsePortalSignIn({ ...request, provider: "google" }), null);
});

test("認証開始の戻り先を同じ言語のカタログとログイン画面に限定する", () => {
  const request = {
    provider: "microsoft", callbackURL: "/en/models",
    errorCallbackURL: "/en/login?error=authentication_failed&next=%2Fen%2Fmodels",
  };
  assert.ok(parsePortalSignIn(request));
  for (const callbackURL of ["https://untrusted.invalid", "//untrusted.invalid", "/api/auth/get-session", "/en/models?next=/api/auth", "/en/../api/auth"])
    assert.equal(parsePortalSignIn({ ...request, callbackURL }), null);
  for (const errorCallbackURL of [
    "https://untrusted.invalid", "/ja/login?error=authentication_failed&next=%2Fen%2Fmodels",
    "/en/login?error=authentication_failed&next=https%3A%2F%2Funtrusted.invalid",
    "/en/login?error=authentication_failed&next=%2Fen%2Fmodels&scope=User.Read",
    "/en/login?error=authentication_failed&next=%2Fen%2Fmodels&next=%2Fen%2Ftools",
    "/en/login?error=authentication_failed&next=%2Fen%2Fmodels#external",
  ]) assert.equal(parsePortalSignIn({ ...request, errorCallbackURL }), null);
});

test("ログイン後に外部URLやAPIパスへ転送しない", () => {
  for (const path of ["https://untrusted.example.invalid", "//untrusted.example.invalid", "/api/auth/sign-out", "/ja/../api/auth", "/en/models"])
    assert.equal(safeReturnTo(path, "ja"), "/ja/models");
  assert.equal(safeReturnTo("/ja/tools", "ja"), "/ja/tools");
  for (const locale of ["ja", "en"] as const) {
    const callbackURL = `/${locale}/company-administration`;
    assert.equal(safeReturnTo(callbackURL, locale), callbackURL);
    assert.ok(parsePortalSignIn({ provider: "microsoft", callbackURL,
      errorCallbackURL: `/${locale}/login?error=authentication_failed&next=${encodeURIComponent(callbackURL)}` }));
  }
});

test("APIの詳細ガイドとシステム管理画面へ認証後も同じ言語で復帰する", () => {
  for (const locale of ["ja", "en"] as const) {
    for (const suffix of [
      "models/text/openai", "models/embeddings/cohere", "models/image/black-forest-labs",
      "models/speech/azure-ai-speech", "models/realtime/openai", "models/rerank/cohere",
      "models/decision/microsoft", "tools/identity/graph-profile", "tools/privacy/pii-redact",
      "tools/document/document-analysis", "agents/assistant/knowledge-agent",
      "agents/research/research-agent", "agents/workflow/workflow-agent", "subscription-sync",
    ]) {
      const callbackURL = `/${locale}/${suffix}`;
      assert.equal(safeReturnTo(callbackURL, locale), callbackURL);
      assert.ok(parsePortalSignIn({ provider: "microsoft", callbackURL,
        errorCallbackURL: `/${locale}/login?error=authentication_failed&next=${encodeURIComponent(callbackURL)}` }), callbackURL);
    }
  }
});

test("詳細ガイドの戻り先でもURL変形・別言語・余分な階層を許可しない", () => {
  for (const callbackURL of [
    "/en/models/text/openai", "/ja/models/text/openai?next=https://untrusted.invalid",
    "/ja/models/text/openai#fragment", "/ja/models/text/openai/extra", "/ja/models/text/../openai",
    "/ja/models/text/%2e%2e", "/ja/models/text/%2F%2Funtrusted.invalid", "/ja/models/text/\\untrusted.invalid",
    "/ja/models//text/openai", "/ja/models/login/openai", "/ja/tools/text/openai",
    "/ja/agents/identity/graph-profile", "/ja/models/text/openai\n", "/ja/models/text/" + "a".repeat(300),
  ]) {
    assert.equal(safeReturnTo(callbackURL, "ja"), "/ja/models", callbackURL);
    // 別言語自体はログインAPIで利用可能なため、他の不正なURLだけを共通拒否します。
    if (!callbackURL.startsWith("/en/")) assert.equal(parsePortalSignIn({ provider: "microsoft", callbackURL,
      errorCallbackURL: `/ja/login?error=authentication_failed&next=${encodeURIComponent(callbackURL)}` }), null, callbackURL);
  }
});

test("バージョン管理SQLはBetter Authの実スキーマを満たし、本人とトークン保持を制約する", async () => {
  const sql = await readFile(new URL("../sql/001_initial.sql", import.meta.url), "utf8");
  const schema = getSchema(portalAuthModels);
  for (const [table, value] of Object.entries(schema)) {
    const block = sql.match(new RegExp(`CREATE TABLE dbo\\.${table} \\(([\\s\\S]+?)\\n    \\);`))?.[1];
    assert.ok(block, `${table} must exist`);
    for (const [field, config] of Object.entries(value.fields)) {
      assert.match(block, new RegExp(`\\[${field}\\] [^\\n]+ ${config.required === false ? "NULL" : "NOT NULL"}`), `${table}.${field}`);
    }
    assert.match(block, /\[id\] varchar\(36\) NOT NULL/);
  }
  assert.match(sql, /UNIQUE NONCLUSTERED \(\[entraTenantId\], \[entraObjectId\]\)/);
  assert.match(sql, /UNIQUE NONCLUSTERED \(\[providerId\], \[accountId\]\)/);
  assert.match(sql, /CHECK \([\s\S]*\[accessToken\] IS NULL[\s\S]*\[refreshToken\] IS NULL[\s\S]*\[idToken\] IS NULL/);
  assert.doesNotMatch(sql, /\bDROP\b|\bTRUNCATE\b/i);
  assert.equal((sql.match(/ON DELETE CASCADE/g) ?? []).length, 2);
});
