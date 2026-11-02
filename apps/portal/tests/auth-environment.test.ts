import assert from "node:assert/strict";
import test from "node:test";
import { getAuthEnvironment, getSqlEnvironment } from "../lib/auth-environment.ts";

/** 環境変数を一時的に変更し、他のケースや実行元へ残さず復元します。 */
function environment(values: Record<string, string | undefined>, run: () => void) {
  const original = { ...process.env };
  try {
    for (const [key, value] of Object.entries(values)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    run();
  } finally {
    process.env = original;
  }
}

test("本番は平文HTTPを許容せず、エラーへ秘密値を出さない", () => {
  environment({ NODE_ENV: "production", BETTER_AUTH_URL: "http://localhost:3000", BETTER_AUTH_SECRET: "a".repeat(40),
    MICROSOFT_CLIENT_ID: "11111111-1111-4111-8111-111111111111", MICROSOFT_TENANT_ID: "22222222-2222-4222-8222-222222222222",
    MICROSOFT_CLIENT_SECRET: "secret-sentinel" }, () => {
    assert.throws(getAuthEnvironment, /HTTPS origin/);
    process.env.BETTER_AUTH_SECRET = "secret-sentinel";
    assert.throws(getAuthEnvironment, (error: unknown) => {
      assert.ok(error instanceof Error);
      assert.ok(error.message.includes("secret"));
      assert.ok(!error.message.includes("secret-sentinel"));
      return true;
    });
  });
});

test("ローカルSQLのパスワード認証はループバックの開発環境だけで使える", () => {
  environment({ NODE_ENV: "development", PORTAL_SQL_AUTH: "local-sql", PORTAL_SQL_SERVER: "127.0.0.1",
    PORTAL_SQL_HOST: "127.0.0.1", PORTAL_SQL_PORT: "14330", PORTAL_SQL_DATABASE: "egakumi_portal",
    PORTAL_SQL_USER: "portal_app", PORTAL_SQL_PASSWORD: "local-secret", PORTAL_SQL_TRUST_LOCAL_CERTIFICATE: "true",
    AZURE_TENANT_ID: undefined, AZURE_CLIENT_ID: undefined }, () => {
    assert.equal(getSqlEnvironment().mode, "local-sql");
    process.env.PORTAL_SQL_HOST = "192.0.2.10";
    assert.throws(getSqlEnvironment, /loopback/);
    process.env.PORTAL_SQL_HOST = "127.0.0.1";
    Object.assign(process.env, { NODE_ENV: "production" });
    assert.throws(getSqlEnvironment, /loopback/);
  });
});

test("Azure SQLでは自己署名許容とSQLパスワードを拒否する", () => {
  environment({ NODE_ENV: "production", PORTAL_SQL_AUTH: "managed-identity", PORTAL_SQL_SERVER: "sql-example.database.windows.net",
    PORTAL_SQL_HOST: undefined, PORTAL_SQL_DATABASE: "portal", PORTAL_SQL_USER: undefined, PORTAL_SQL_PASSWORD: undefined,
    PORTAL_SQL_TRUST_LOCAL_CERTIFICATE: "false", AZURE_CLIENT_ID: "33333333-3333-4333-8333-333333333333",
    AZURE_TENANT_ID: undefined }, () => {
    assert.equal(getSqlEnvironment().mode, "managed-identity");
    process.env.PORTAL_SQL_TRUST_LOCAL_CERTIFICATE = "true";
    assert.throws(getSqlEnvironment, /verified TLS/);
    process.env.PORTAL_SQL_TRUST_LOCAL_CERTIFICATE = "false";
    process.env.PORTAL_SQL_PASSWORD = "secret-sentinel";
    assert.throws(getSqlEnvironment, /explicit Entra identity/);
  });
});
