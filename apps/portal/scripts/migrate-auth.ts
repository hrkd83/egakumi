import { Kysely } from "kysely";
import { createAuthDatabase, isCanonicalAzureSqlDatabase } from "../lib/auth-database.ts";
import { getSqlEnvironment, isCanonicalManagedSqlConfiguration } from "../lib/auth-environment.ts";
import { migrateAuth, observeAuthSchemaBeforeMigration, sqlFailure } from "./auth-migrations.ts";
import { grantRuntimeAccess, parseMigrationSqlIdentities } from "./auth-runtime-grants.ts";

/** 配備用の管理IDで事前実行するDDLです。アプリ起動時には呼び出しません。 */
async function main() {
  const settings = getSqlEnvironment();
  const identities = settings.mode === "local-sql" ? null : parseMigrationSqlIdentities(process.env);
  if (identities && !isCanonicalManagedSqlConfiguration(settings)) throw new Error("Canonical Portal database is required");
  const database = new Kysely<Record<string, unknown>>(createAuthDatabase(settings));
  try {
    if (identities && !await isCanonicalAzureSqlDatabase(database, settings.server, settings.database)) {
      throw new Error("Canonical Portal database is required");
    }
    console.log(JSON.stringify(await observeAuthSchemaBeforeMigration(database)));
    const files = await migrateAuth(database);
    if (identities) await grantRuntimeAccess(database, identities.runtime);
    console.log(JSON.stringify({ status: "complete", migrationFiles: files, runtimeAccessGranted: Boolean(identities) }));
  } finally {
    await database.destroy();
  }
}
main().catch((error: unknown) => {
  console.error(sqlFailure(error));
  process.exitCode = 1;
});
