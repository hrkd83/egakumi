import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { Kysely } from "kysely";
import { createAuthDatabase } from "../lib/auth-database.ts";
import { getSqlEnvironment } from "../lib/auth-environment.ts";
import { apiKeySubscriptionImportSchema, importApiKeySubscriptions, readApiKeySubscriptionImport,
  stableApiKeyImportId } from "./api-key-subscription-import.ts";

/**
 * 検証済みメタデータをローカルSQLへ登録します。通常実行では計画だけを表示します。
 * @remarks APIMへの通信やキー新規発行は行いません。SQLスキーマは事前に配備済みである必要があります。
 */
export async function runLocalApiKeySubscriptionImport(args: readonly string[], environment: Readonly<Record<string, string | undefined>> = process.env): Promise<void> {
  const { values } = parseArgs({ args: [...args], options: { manifest: { type: "string" },
    "apim-resource-id": { type: "string" }, execute: { type: "boolean", default: false } } });
  if (!values.manifest) throw new Error("--manifest must name verified APIM subscription metadata");
  const content = await readFile(values.manifest, "utf8");
  const parsed = apiKeySubscriptionImportSchema.parse(JSON.parse(content));
  const configuration = readApiKeySubscriptionImport({ ...environment,
    PORTAL_API_KEY_SUBSCRIPTION_IMPORT: content,
    PORTAL_API_KEY_TENANT_ID: environment.PORTAL_API_KEY_TENANT_ID ?? environment.MICROSOFT_TENANT_ID ?? parsed.tenantId,
    PORTAL_APIM_RESOURCE_ID: values["apim-resource-id"] ?? environment.PORTAL_APIM_RESOURCE_ID,
  });
  if (!configuration) throw new Error("Explicit APIM subscription import is required");
  if (!values.execute) {
    console.log(JSON.stringify({ mode: "plan", platformGroupId: configuration.manifest.platformGroupId,
      subscriptions: configuration.manifest.subscriptions.map((record) => ({ subscriptionId: record.subscriptionId,
        productId: record.productId, state: record.state,
        internalId: stableApiKeyImportId({ ...configuration.manifest, subscriptionId: record.subscriptionId }) })),
      sqlWrites: 0, azureWrites: 0 }));
    return;
  }
  const settings = getSqlEnvironment();
  if (settings.mode !== "local-sql") throw new Error("This command only imports into local SQL");
  const database = new Kysely<Record<string, unknown>>(createAuthDatabase(settings));
  try {
    const result = await importApiKeySubscriptions(database, configuration.manifest, configuration.expectedApimResourceId);
    console.log(JSON.stringify({ mode: "complete", ...result, azureWrites: 0 }));
  } finally { await database.destroy(); }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runLocalApiKeySubscriptionImport(process.argv.slice(2)).catch(() => {
    console.error(JSON.stringify({ status: "failed", errorCode: "SubscriptionImportUnavailable" }));
    process.exitCode = 1;
  });
}
