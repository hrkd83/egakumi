import { Kysely, sql } from "kysely";
import { z } from "zod";
import { createAuthDatabase } from "../lib/auth-database.ts";
import { getSqlEnvironment } from "../lib/auth-environment.ts";
import { migrateAuth, sqlFailure } from "./auth-migrations.ts";
import { portalRuntimePermissions, grantRuntimeProcedureAccess } from "./auth-runtime-grants.ts";
import { configureApiKeyGroups, readApiKeyGroupConfiguration } from "./api-key-group-config.ts";
import { configureApiKeyAdministration, readApiKeyAdministrationConfiguration } from "./api-key-administration-config.ts";

/** ローカルSQLだけに、空DB・テーブル・DML専用アプリ利用者を準備します。 */
async function main() {
  const settings = getSqlEnvironment();
  const keyGroups = readApiKeyGroupConfiguration(process.env);
  const administration = readApiKeyAdministrationConfiguration(process.env);
  if (settings.mode !== "local-sql") throw new Error("Local SQL mode required");
  const runtime = z.object({ user: z.literal("portal_app"), password: z.string().min(32) }).parse({
    user: process.env.PORTAL_SQL_RUNTIME_USER, password: process.env.PORTAL_SQL_RUNTIME_PASSWORD,
  });
  const databaseName = `[${settings.database}]`;
  const admin = new Kysely<Record<string, unknown>>(createAuthDatabase({ ...settings, database: "master" }));
  try {
    const found = await sql`SELECT name FROM sys.databases WHERE name=${settings.database}`.execute(admin);
    if (!found.rows.length) {
      await sql.raw(`CREATE DATABASE ${databaseName} COLLATE Latin1_General_100_BIN2`).execute(admin);
    }
    const login = await sql`SELECT name FROM sys.sql_logins WHERE name=${runtime.user}`.execute(admin);
    if (!login.rows.length) {
      const password = runtime.password.replaceAll("'", "''");
      await sql.raw(`CREATE LOGIN [portal_app] WITH PASSWORD=N'${password}', CHECK_POLICY=ON`).execute(admin);
    }
  } finally {
    await admin.destroy();
  }
  const database = new Kysely<Record<string, unknown>>(createAuthDatabase(settings));
  try {
    await migrateAuth(database);
    if (keyGroups) await configureApiKeyGroups(database, keyGroups.tenantId, keyGroups.groups);
    if (administration) await configureApiKeyAdministration(database, administration);
    await sql`IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name=N'portal_app')
      CREATE USER [portal_app] FOR LOGIN [portal_app];`.execute(database);
    for (const [table, permissions] of Object.entries(portalRuntimePermissions)) {
      await sql.raw(`GRANT ${permissions} ON OBJECT::dbo.[${table}] TO [portal_app]`).execute(database);
    }
    await grantRuntimeProcedureAccess(database, runtime.user);
  } finally {
    await database.destroy();
  }
  const application = new Kysely<Record<string, unknown>>(createAuthDatabase({ ...settings, user: runtime.user, password: runtime.password }));
  try {
    await sql`SELECT TOP(0) id FROM dbo.portalUser`.execute(application);
    const permission = await sql<{ canCreate: number }>`SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE TABLE') AS canCreate`.execute(application);
    if (permission.rows[0]?.canCreate !== 0) throw new Error("Runtime must not have DDL permission");
    console.log(JSON.stringify({ status: "complete", databaseReady: true, runtimeDdlAllowed: false }));
  } finally {
    await application.destroy();
  }
}
main().catch((error: unknown) => {
  console.error(sqlFailure(error));
  process.exitCode = 1;
});
