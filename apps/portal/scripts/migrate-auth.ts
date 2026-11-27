import { Kysely } from "kysely";
import { createAuthDatabase, isCanonicalAzureSqlDatabase } from "../lib/auth-database.ts";
import { getSqlEnvironment, isCanonicalManagedSqlConfiguration } from "../lib/auth-environment.ts";
import { migrateAuth, observeAuthSchemaBeforeMigration, sqlFailure } from "./auth-migrations.ts";
import { grantRuntimeAccess, parseMigrationSqlIdentities } from "./auth-runtime-grants.ts";
import { configureApiKeyGroups, readApiKeyGroupConfiguration } from "./api-key-group-config.ts";
import { configureApiKeyAdministration, readApiKeyAdministrationConfiguration } from "./api-key-administration-config.ts";
import { importApiKeySubscriptions, readApiKeySubscriptionImport } from "./api-key-subscription-import.ts";
import { bootstrapApiKeySubscriptions, readApiKeyBootstrapApimEnvironment, readApiKeyVerificationSubscriptions } from "./bootstrap-api-key-subscriptions.ts";
import { AzureApiKeyApim } from "../lib/api-keys/apim.ts";

/** 配備用の管理IDで事前実行するDDLです。アプリ起動時には呼び出しません。 */
async function main() {
  const settings = getSqlEnvironment();
  const identities = settings.mode === "local-sql" ? null : parseMigrationSqlIdentities(process.env);
  if (identities && !isCanonicalManagedSqlConfiguration(settings)) throw new Error("Canonical Portal database is required");
  const keyGroups = readApiKeyGroupConfiguration(process.env);
  const administration = readApiKeyAdministrationConfiguration(process.env);
  const subscriptionImport = readApiKeySubscriptionImport(process.env);
  const verificationSubscriptions = readApiKeyVerificationSubscriptions(process.env);
  let verificationApim: AzureApiKeyApim | undefined;
  if (verificationSubscriptions?.subscriptions.length) {
    if (!identities) throw new Error("Verification subscriptions require the dedicated management identity");
    verificationApim = new AzureApiKeyApim(readApiKeyBootstrapApimEnvironment(process.env, identities.management.clientId));
  }
  const database = new Kysely<Record<string, unknown>>(createAuthDatabase(settings));
  const management = identities
    ? new Kysely<Record<string, unknown>>(createAuthDatabase({ ...settings, clientId: identities.management.clientId }))
    : database;
  try {
    if (identities && !await isCanonicalAzureSqlDatabase(database, settings.server, settings.database)) {
      throw new Error("Canonical Portal database is required");
    }
    console.log(JSON.stringify(await observeAuthSchemaBeforeMigration(database)));
    const files = await migrateAuth(database);
    if (keyGroups) await configureApiKeyGroups(database, keyGroups.tenantId, keyGroups.groups);
    if (administration) await configureApiKeyAdministration(database, administration);
    if (identities) {
      await grantRuntimeAccess(database, { management: identities.management, profile: identities.profile });
      if (!await isCanonicalAzureSqlDatabase(management, settings.server, settings.database)) {
        throw new Error("Canonical Portal database is required");
      }
    }
    const importedSubscriptions = subscriptionImport ? await importApiKeySubscriptions(management,
      subscriptionImport.manifest, subscriptionImport.expectedApimResourceId) : null;
    const verification = verificationSubscriptions && verificationApim
      ? await bootstrapApiKeySubscriptions(management, verificationSubscriptions, verificationApim) : null;
    if (identities) await grantRuntimeAccess(database, { runtime: identities.runtime });
    console.log(JSON.stringify({ status: "complete", migrationFiles: files, runtimeAccessGranted: Boolean(identities),
      keyGroupsConfigured: keyGroups?.groups.length ?? 0, importedSubscriptions, verificationSubscriptions: verification }));
  } finally {
    await Promise.all([database.destroy(), management === database ? undefined : management.destroy()]);
  }
}
main().catch((error: unknown) => {
  console.error(sqlFailure(error));
  process.exitCode = 1;
});
