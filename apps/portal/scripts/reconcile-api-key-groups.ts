import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Kysely } from "kysely";
import { z } from "zod";
import { createAuthDatabase } from "../lib/auth-database.ts";
import { getSqlEnvironment } from "../lib/auth-environment.ts";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { GraphApiKeyMembership } from "../lib/api-keys/graph.ts";
import { SqlApiKeyRepository } from "../lib/api-keys/repository.ts";
import { SqlApiKeyAdministrationRepository } from "../lib/api-key-administration/repository.ts";
import { ApiKeyAdministrationService } from "../lib/api-key-administration/service.ts";

/** 定期照合はPortalログイン用のCookie署名鍵や公開URLを必要としません。 */
export function readReconciliationDirectoryEnvironment(environment: Readonly<Record<string, string | undefined>>) {
  const parsed = z.object({ microsoftClientId: z.uuid(), microsoftClientSecret: z.string().min(1), microsoftTenantId: z.uuid() })
    .safeParse({ microsoftClientId: environment.MICROSOFT_CLIENT_ID,
      microsoftClientSecret: environment.MICROSOFT_CLIENT_SECRET, microsoftTenantId: environment.MICROSOFT_TENANT_ID });
  if (!parsed.success) throw new ApiKeyError("ConfigurationUnavailable", 503);
  return parsed.data;
}

/** 登録済み所有グループの観測だけを更新し、成功・不明の件数だけを出力します。 */
async function main(): Promise<void> {
  const settings = readReconciliationDirectoryEnvironment(process.env);
  const database = new Kysely<Record<string, unknown>>(createAuthDatabase(getSqlEnvironment()));
  try {
    const repository = new SqlApiKeyAdministrationRepository(database);
    const registry = new SqlApiKeyRepository(database);
    const graph = new GraphApiKeyMembership(settings, { readPolicy: () => repository.getSettings(settings.microsoftTenantId) });
    const service = new ApiKeyAdministrationService({ tenantId: settings.microsoftTenantId, repository,
      isPlatformAdmin: (actor) => graph.isPlatformAdmin(actor), readGroups: (ids) => graph.observeGroups(ids),
      saveGroups: (tenantId, observations) => registry.observeGroups(tenantId, observations) });
    const result = await service.reconcileTrackedGroups();
    console.log(JSON.stringify({ status: result.unknownGroups ? "partial" : "complete", ...result }));
    if (result.unknownGroups) process.exitCode = 1;
  } finally { await database.destroy(); }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error: unknown) => {
    console.error(JSON.stringify({ status: "failed", errorCode: error instanceof ApiKeyError ? error.code : "ReconciliationUnavailable" }));
    process.exitCode = 1;
  });
}
