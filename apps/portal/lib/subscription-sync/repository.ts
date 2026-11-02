import "server-only";
import { randomUUID } from "node:crypto";
import { Kysely, sql } from "kysely";
import { createAuthDatabase } from "../auth-database.ts";
import { getSqlEnvironment } from "../auth-environment.ts";
import { ApiKeyError, type ApiKeyActor } from "../api-keys/contracts.ts";
import type { CanonicalSubscription, SyncAudit } from "./contracts.ts";

type Database = Kysely<Record<string, unknown>>;
/** 固定の同期先に対してだけ実行できる、監査の内容です。 */
export type SyncAuditInput = { planId: string; apiKeyId: string | null; subscriptionId: string; action: "update" | "recreate" | "delete" | "delete-unregistered";
  phase: "attempted" | "succeeded" | "pending" | "failed"; code: string | null; snapshotHash: string };
/** 排他区間の同じ接続から、最新台帳と永続監査を読み書きします。 */
export type SyncLockedContext = { read(): Promise<CanonicalSubscription | null>; audit(input: SyncAuditInput): Promise<void> };
/** SQL・認証とAPIM通信を分けて、競合と中断をテストできる契約です。 */
export interface SubscriptionSyncRepository {
  list(tenantId: string): Promise<CanonicalSubscription[]>;
  history(tenantId: string): Promise<SyncAudit[]>;
  assertCanonical(server: string, database: string): Promise<void>;
  locked<T>(actor: ApiKeyActor, id: string, callback: (context: SyncLockedContext) => Promise<T>): Promise<T>;
}

/** 既存の所有・会社・用途を保持し、削除済みの行も比較対象へ含めます。 */
async function readRows(db: Database, tenant: string, id?: string): Promise<CanonicalSubscription[]> {
  const rows = (await sql<CanonicalSubscription>`SELECT TOP(100001) k.id,k.apimSubscriptionId,k.productId,k.displayName AS purpose,
    k.state,k.creationSource,c.name AS companyName,g.displayName AS groupName
    FROM dbo.portalApiKey k JOIN dbo.portalApiKeyGroup g ON g.tenantId=k.tenantId AND g.groupId=k.groupId
    LEFT JOIN dbo.portalCompany c ON c.tenantId=k.tenantId AND c.id=k.companyId
    WHERE k.tenantId=CAST(${tenant} AS char(36)) ${id ? id.startsWith("apim:")
      ? sql`AND k.apimSubscriptionId=CAST(${id.slice(5)} AS varchar(256))` : sql`AND k.id=CAST(${id} AS varchar(43))` : sql``}
    ORDER BY k.id`.execute(db)).rows;
  if (rows.length > 100_000) throw new ApiKeyError("InventoryTooLarge", 409);
  return rows;
}

/** 同期専用の接続でも通常のキー操作と同じ名前の排他ロックを使います。 */
export class SqlSubscriptionSyncRepository implements SubscriptionSyncRepository {
  private readonly database: Database;
  constructor(database: Database) { this.database = database; }
  async list(tenant: string): Promise<CanonicalSubscription[]> { return readRows(this.database, tenant); }
  /** 監査は最新100イベントを表示します。開始だけ残った処理も管理者が確認できます。 */
  async history(tenant: string): Promise<SyncAudit[]> {
    return (await sql<SyncAudit>`SELECT TOP(100) id,planId,subscriptionId,action,phase,code,
      CONVERT(varchar(23),recordedAt,126)+'Z' AS recordedAt FROM dbo.portalSubscriptionSyncAudit
      WHERE tenantId=CAST(${tenant} AS char(36)) ORDER BY recordedAt DESC,id`.execute(this.database)).rows;
  }
  /** 接続設定だけでなく、実際の接続先が配備済みAzure SQLのDBであることを照合します。 */
  async assertCanonical(server: string, database: string): Promise<void> {
    const row = (await sql<{ edition: number; server: string; database: string }>`SELECT
      CAST(SERVERPROPERTY('EngineEdition') AS int) AS edition,
      CAST(SERVERPROPERTY('ServerName') AS nvarchar(256)) AS server,DB_NAME() AS [database]`.execute(this.database)).rows[0];
    if (!row || row.edition !== 5 || row.server.toLowerCase().replace(/\.database\.windows\.net$/, "") !== server.toLowerCase().replace(/\.database\.windows\.net$/, "")
        || row.database.toLowerCase() !== database.toLowerCase()) throw new ApiKeyError("CanonicalDatabaseRequired", 409);
  }
  async locked<T>(actor: ApiKeyActor, id: string, callback: (context: SyncLockedContext) => Promise<T>): Promise<T> {
    // Sessionロックと自動コミットを併用し、上流の失敗でも開始監査をロールバックしません。
    return this.database.connection().execute(async (connection) => {
      const name = id.startsWith("apim:") ? `portal-subscription-sync:${actor.tenantId}:${id.slice(5)}` : `portal-api-key:${actor.tenantId}:${id}`;
      await sql`DECLARE @r int; EXEC @r=sp_getapplock @Resource=${name},@LockMode='Exclusive',@LockOwner='Session',@LockTimeout=15000;
        IF @r<0 THROW 51000,'Subscription operation busy',1;`.execute(connection);
      try {
        return await callback({
          read: async () => {
            const row = (await readRows(connection, actor.tenantId, id))[0];
            if (id.startsWith("apim:") && row) throw new ApiKeyError("SyncConflict", 409);
            if (!id.startsWith("apim:") && !row) throw new ApiKeyError("SyncConflict", 409);
            return row ?? null;
          },
          audit: async (input) => {
            try {
              await sql`INSERT dbo.portalSubscriptionSyncAudit
                (id,tenantId,actorObjectId,planId,apiKeyId,subscriptionId,action,phase,code,snapshotHash)
                VALUES(${randomUUID()},${actor.tenantId},${actor.objectId},${input.planId},${input.apiKeyId},${input.subscriptionId},
                  ${input.action},${input.phase},${input.code},${input.snapshotHash})`.execute(connection);
            } catch { throw new ApiKeyError("SyncAuditUnavailable", 409); }
          },
        });
      } finally {
        await sql`EXEC sp_releaseapplock @Resource=${name},@LockOwner='Session';`.execute(connection);
      }
    });
  }
}
let repository: SubscriptionSyncRepository | undefined;
/** Better Authと同じDB設定を使い、別の台帳を暗黙に選びません。 */
export function getSubscriptionSyncRepository(): SubscriptionSyncRepository {
  return repository ??= new SqlSubscriptionSyncRepository(new Kysely<Record<string, unknown>>({ dialect: createAuthDatabase(getSqlEnvironment()).dialect }));
}
