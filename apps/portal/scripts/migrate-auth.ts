import { Kysely } from "kysely";
import { getAuthDatabase } from "../lib/auth-database.ts";
import { getSqlEnvironment } from "../lib/auth-environment.ts";
import { migrateAuth, sqlFailure } from "./auth-migrations.ts";
import { grantRuntimeAccess, parseRuntimeIdentity } from "./auth-runtime-grants.ts";
import { configureApiKeyGroups, readApiKeyGroupConfiguration } from "./api-key-group-config.ts";
import { configureApiKeyAdministration, readApiKeyAdministrationConfiguration } from "./api-key-administration-config.ts";
import { importApiKeySubscriptions, readApiKeySubscriptionImport } from "./api-key-subscription-import.ts";
import { bootstrapApiKeySubscriptions, readApiKeyBootstrapApimEnvironment, readApiKeyVerificationSubscriptions } from "./bootstrap-api-key-subscriptions.ts";
import { AzureApiKeyApim } from "../lib/api-keys/apim.ts";

/** 配備用の管理IDで事前実行するDDLです。アプリ起動時には呼び出しません。 */
async function main() {
  const runtimeIdentity = parseRuntimeIdentity(process.env);
  const keyGroups = readApiKeyGroupConfiguration(process.env);
  const administration = readApiKeyAdministrationConfiguration(process.env);
  const subscriptionImport = readApiKeySubscriptionImport(process.env);
  const verificationSubscriptions = readApiKeyVerificationSubscriptions(process.env);
  if (getSqlEnvironment().mode !== "local-sql" && !runtimeIdentity) {
    throw new Error("Portal SQL runtime identity is required for Azure schema deployment");
  }
  const database = new Kysely<Record<string, unknown>>(getAuthDatabase());
  try {
    const files = await migrateAuth(database);
    if (keyGroups) await configureApiKeyGroups(database, keyGroups.tenantId, keyGroups.groups);
    if (administration) await configureApiKeyAdministration(database, administration);
    const importedSubscriptions = subscriptionImport ? await importApiKeySubscriptions(database,
      subscriptionImport.manifest, subscriptionImport.expectedApimResourceId) : null;
    const verification = verificationSubscriptions?.subscriptions.length ? await bootstrapApiKeySubscriptions(database,
      verificationSubscriptions, new AzureApiKeyApim(readApiKeyBootstrapApimEnvironment(process.env))) : null;
    if (runtimeIdentity) await grantRuntimeAccess(database, runtimeIdentity);
    console.log(JSON.stringify({ status: "complete", migrationFiles: files, runtimeAccessGranted: Boolean(runtimeIdentity),
      keyGroupsConfigured: keyGroups?.groups.length ?? 0, importedSubscriptions, verificationSubscriptions: verification }));
  } finally {
    await database.destroy();
  }
}
main().catch((error: unknown) => {
  console.error(sqlFailure(error));
  process.exitCode = 1;
});
