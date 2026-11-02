import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import type { TestContext } from "node:test";
import { Kysely, sql } from "kysely";
import { createAuthDatabase } from "../../lib/auth-database.ts";
import { getSqlEnvironment } from "../../lib/auth-environment.ts";
import { migrateAuth } from "../../scripts/auth-migrations.ts";
import { grantRuntimeProcedureAccess, portalRuntimePermissions } from "../../scripts/auth-runtime-grants.ts";

/** 明示的に有効にしたローカルSQLだけで統合テストを実行します。 */
export const localSqlTest = { skip: process.env.PORTAL_SQL_INTEGRATION_TESTS !== "1" };

/**
 * テスト専用DBと必要に応じて最小権限の接続を作り、失敗時も専用DBだけを破棄します。
 * @remarks 実行用ログインは事前に作成済みのローカルSQLアカウントを使います。
 */
export async function createLocalSqlTestDatabase(context: TestContext, withRuntime = true) {
  const settings = getSqlEnvironment();
  assert.equal(settings.mode, "local-sql", "SQL統合テストはローカル専用です");
  const name = `portal_test_${randomUUID().replaceAll("-", "")}`;
  assert.notEqual(name, settings.database);
  const master = new Kysely<Record<string, unknown>>(createAuthDatabase({ ...settings, database: "master" }));
  const admin = new Kysely<Record<string, unknown>>(createAuthDatabase({ ...settings, database: name }));
  let runtime: Kysely<Record<string, unknown>> | undefined;
  let created = false;
  context.after(async () => {
    try {
      await Promise.all([runtime?.destroy(), admin.destroy()]);
    } finally {
      try {
        if (created) await sql.raw(`ALTER DATABASE [${name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [${name}]`).execute(master);
      } finally {
        await master.destroy();
      }
    }
  });
  await sql.raw(`CREATE DATABASE [${name}] COLLATE Latin1_General_100_BIN2`).execute(master);
  created = true;
  await migrateAuth(admin);
  if (withRuntime) {
    const user = process.env.PORTAL_SQL_RUNTIME_USER;
    const password = process.env.PORTAL_SQL_RUNTIME_PASSWORD;
    assert.ok(user && /^[A-Za-z][A-Za-z0-9_-]{0,127}$/.test(user), "ローカルSQL実行用ログイン名が必要です");
    assert.ok(password, "ローカルSQL実行用パスワードが必要です");
    await sql.raw(`CREATE USER [${user}] FOR LOGIN [${user}]`).execute(admin);
    for (const [table, permissions] of Object.entries(portalRuntimePermissions)) {
      await sql.raw(`GRANT ${permissions} ON OBJECT::dbo.[${table}] TO [${user}]`).execute(admin);
    }
    await grantRuntimeProcedureAccess(admin, user);
    runtime = new Kysely<Record<string, unknown>>(createAuthDatabase({ ...settings, database: name, user, password }));
  }
  return { admin, runtime: runtime ?? admin, name };
}
