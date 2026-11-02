import "server-only";

import { z } from "zod";
import { ApiKeyError } from "./contracts.ts";

const resourceIdPattern = /^\/subscriptions\/([0-9a-f-]{36})\/resourceGroups\/([a-zA-Z0-9_.()-]{1,90})\/providers\/Microsoft\.ApiManagement\/service\/([a-zA-Z][a-zA-Z0-9-]{0,49})$/i;
const identifier = z.uuid();

/** APIM管理の宛先と実行IDです。ブラウザーから指定を受け付けません。 */
export type ApiKeyEnvironment = {
  apimResourceId: string;
  mode: "azure-cli" | "managed-identity";
  tenantId?: string;
  clientId?: string;
};

/**
 * 単一APIMリソースを固定し、ローカルのAzure CLIとAzure上の実行IDを使い分けます。
 * @remarks Azure環境でも対話ユーザーへ暗黙に切り替わるDefaultAzureCredentialは使いません。
 */
export function getApiKeyEnvironment(environment: Readonly<Record<string, string | undefined>> = process.env): ApiKeyEnvironment {
  const apimResourceId = environment.PORTAL_APIM_RESOURCE_ID;
  const resourceParts = apimResourceId?.match(resourceIdPattern);
  const mode = environment.PORTAL_APIM_AUTH ?? (environment.NODE_ENV === "production" ? "managed-identity" : "azure-cli");
  const tenantId = environment.AZURE_TENANT_ID;
  const clientId = environment.AZURE_CLIENT_ID;
  if (!resourceParts || !identifier.safeParse(resourceParts[1]).success
      || !["azure-cli", "managed-identity"].includes(mode)
      || (mode === "azure-cli" && (environment.NODE_ENV === "production" || !identifier.safeParse(tenantId).success))
      || (mode === "managed-identity" && !identifier.safeParse(clientId).success)) {
    throw new ApiKeyError("ConfigurationUnavailable", 503);
  }
  return { apimResourceId: apimResourceId!, mode: mode as ApiKeyEnvironment["mode"], tenantId, clientId };
}
