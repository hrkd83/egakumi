import type { SqlEnvironment } from "../auth-environment.ts";
import type { ApiKeyEnvironment } from "../api-keys/environment.ts";

/** Azure配備資材が固定する同期先です。ブラウザーからは変更できません。 */
export type SyncBinding = { writable: boolean; source: "local" | "azure"; server: string; database: string; apimResourceId: string };
/** ローカルSQLや開発用CLIから、Azure APIMへ同期を書き込むことを拒否します。 */
export function syncBinding(sql: SqlEnvironment, apim: ApiKeyEnvironment, env: Readonly<Record<string, string | undefined>> = process.env): SyncBinding {
  return { source: sql.mode === "local-sql" ? "local" : "azure", server: sql.server, database: sql.database, apimResourceId: apim.apimResourceId,
    writable: env.NODE_ENV === "production" && env.PORTAL_SUBSCRIPTION_SYNC_ENABLED === "true"
      && sql.mode === "managed-identity" && apim.mode === "managed-identity"
      && !!env.PORTAL_CANONICAL_SQL_SERVER && !!env.PORTAL_CANONICAL_SQL_DATABASE
      && sql.server.toLowerCase() === env.PORTAL_CANONICAL_SQL_SERVER.toLowerCase()
      && sql.database.toLowerCase() === env.PORTAL_CANONICAL_SQL_DATABASE.toLowerCase() };
}
