import { readFile } from "node:fs/promises";
import { Kysely } from "kysely";
import { parseArgs } from "node:util";
import { createAuthDatabase } from "../lib/auth-database.ts";
import { getSqlEnvironment } from "../lib/auth-environment.ts";
import { configureApiKeyGroups, readApiKeyGroupConfiguration } from "./api-key-group-config.ts";
import { sqlFailure } from "./auth-migrations.ts";
import { configureApiKeyAdministration, readApiKeyAdministrationConfiguration } from "./api-key-administration-config.ts";
import { RELEASED_API_PRODUCT_ID } from "../lib/api-keys/contracts.ts";

/** 企業別paramsの承認設定を、開発用のローカルSQLへだけ反映します。既定は計画表示です。 */
async function main() {
  const { values } = parseArgs({ options: { config: { type: "string" }, execute: { type: "boolean", default: false } } });
  if (!values.config) throw new Error("--config must name the existing parameter file");
  const master = JSON.parse(await readFile(values.config, "utf8"));
  const approved = readApiKeyGroupConfiguration({
    PORTAL_API_KEY_TENANT_ID: process.env.MICROSOFT_TENANT_ID,
    PORTAL_API_KEY_GROUPS: JSON.stringify(master.portalApiKeys?.groups ?? []),
  });
  if (!approved?.groups.length) throw new Error("Approved API key groups are required in the parameter file");
  const administration = readApiKeyAdministrationConfiguration({
    PORTAL_API_KEY_TENANT_ID: approved.tenantId,
    PORTAL_API_KEY_PLATFORM_GROUP_ID: master.portalApiKeys?.platformGroupId ?? "",
    PORTAL_API_KEY_GROUP_NAME_PATTERN: master.portalApiKeys?.groupNamePattern,
    PORTAL_API_KEY_CATALOG_PRODUCTS: JSON.stringify([{ productId: RELEASED_API_PRODUCT_ID, displayName: "Released APIs", maxSubscriptions: 5 }]),
  });
  if (!administration || !approved.groups.some((group) => group.groupId === administration.platformGroupId)) {
    throw new Error("Platform group must be explicitly configured among the verification owner groups");
  }
  if (!values.execute) {
    console.log(JSON.stringify({ mode: "plan", groups: approved.groups.length, platformGroupId: administration.platformGroupId,
      groupNamePattern: administration.groupNamePattern, catalogProducts: administration.products.length, sqlWrites: 0, azureWrites: 0 }));
    return;
  }
  const settings = getSqlEnvironment();
  if (settings.mode !== "local-sql") throw new Error("This command only configures local SQL");
  const database = new Kysely<Record<string, unknown>>(createAuthDatabase(settings));
  try {
    await configureApiKeyGroups(database, approved.tenantId, approved.groups);
    await configureApiKeyAdministration(database, administration);
    console.log(JSON.stringify({ mode: "complete", groups: approved.groups.length, azureWrites: 0 }));
  } finally { await database.destroy(); }
}

main().catch((error: unknown) => {
  console.error(sqlFailure(error));
  process.exitCode = 1;
});
