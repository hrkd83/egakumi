import "server-only";

import { Kysely, sql } from "kysely";
import { z } from "zod";
import { createAuthDatabase } from "../auth-database.ts";
import { getSqlEnvironment } from "../auth-environment.ts";
import { ApiKeyError, type ApiKeyProduct } from "../api-keys/contracts.ts";
import { apiKeyGroupPolicySchema, type ApiKeyGroupPolicy } from "../api-keys/group-policy.ts";
import type { AdministrationGroup, ApiKeyAdministrationSummary } from "./contracts.ts";

/** 管理設定と台帳の読取だけを分離し、Graph障害をDBの削除状態へ置き換えません。 */
export interface ApiKeyAdministrationRepository {
  getSettings(tenantId: string): Promise<ApiKeyGroupPolicy>;
  getCatalog(tenantId: string): Promise<ApiKeyProduct[]>;
  listTrackedGroups(tenantId: string, cursor?: string): Promise<{ groups: AdministrationGroup[]; nextCursor: string | null }>;
  getSummary(tenantId: string): Promise<ApiKeyAdministrationSummary & { checkedAt: string | null; syncErrors: number }>;
  listGroupsToRefresh(tenantId: string, limit: number): Promise<string[]>;
}

type GroupRow = {
  id: string; displayName: string; directoryStatus: AdministrationGroup["directoryStatus"];
  lastCheckedAt: string | null; lastErrorCode: string | null; activeSubscriptions: number; subscriptions: number;
};
const tenantSchema = z.uuid().transform((id) => id.toLowerCase());
const productSchema = z.object({ id: z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/),
  displayName: z.string().min(1).max(255), maxSubscriptions: z.number().int().min(1).max(1000) });

/** SQL内部の例外・接続先を応答やログへ渡さず、固定の失敗種別へ変換します。 */
async function safe<T>(operation: () => Promise<T>): Promise<T> {
  try { return await operation(); }
  catch (error) {
    if (error instanceof ApiKeyError) throw error;
    throw new ApiKeyError("KeyStoreUnavailable", 503);
  }
}

/**
 * 管理画面とグループ登録で、同じ環境設定の検証を使用します。
 * @remarks 保持中のトランザクションも受け取り、追加のSQL接続を要求しません。
 */
export async function readApiKeyGroupPolicy(database: Kysely<Record<string, unknown>>, tenant: string): Promise<ApiKeyGroupPolicy> {
  const tenantId = tenantSchema.parse(tenant);
  return safe(async () => {
    const row = (await sql<{ platformGroupId: string; groupNamePattern: string }>`SELECT platformGroupId, groupNamePattern
      FROM dbo.portalApiKeyAdministration WHERE tenantId=CAST(${tenantId} AS char(36))`.execute(database)).rows[0];
    const parsed = apiKeyGroupPolicySchema.safeParse(row);
    if (!parsed.success) throw new ApiKeyError("ConfigurationUnavailable", 503);
    return parsed.data;
  });
}

/** 配備が設定した管理者グループとカタログを、実行アプリからは変更しない読取台帳です。 */
export class SqlApiKeyAdministrationRepository implements ApiKeyAdministrationRepository {
  private readonly database: Kysely<Record<string, unknown>>;
  constructor(database: Kysely<Record<string, unknown>>) { this.database = database; }

  /** 設定のない環境は明示的な503とし、現在のログイン利用者を管理者にしません。 */
  async getSettings(tenant: string): Promise<ApiKeyGroupPolicy> {
    return readApiKeyGroupPolicy(this.database, tenant);
  }

  /** 個別グループに承認されていない場合の、配備済みカタログ製品を返します。 */
  async getCatalog(tenant: string): Promise<ApiKeyProduct[]> {
    const tenantId = tenantSchema.parse(tenant);
    return safe(async () => {
      const rows = (await sql<ApiKeyProduct>`SELECT productId AS id, displayName, maxSubscriptions
        FROM dbo.portalApiKeyCatalogProduct WHERE tenantId=${tenantId} AND enabled=1
        ORDER BY displayName, productId`.execute(this.database)).rows;
      const parsed = z.array(productSchema).max(1000).safeParse(rows);
      if (!parsed.success) throw new ApiKeyError("ConfigurationUnavailable", 503);
      return parsed.data;
    });
  }

  /** 管理者所属を確認したサービスだけが呼ぶ、登録済みグループと影響購読数の一覧です。 */
  async listTrackedGroups(tenant: string, after?: string): Promise<{ groups: AdministrationGroup[]; nextCursor: string | null }> {
    const tenantId = tenantSchema.parse(tenant);
    const cursor = after === undefined ? undefined : tenantSchema.parse(after);
    return safe(async () => {
      const rows = (await sql<GroupRow>`SELECT TOP (101) g.groupId AS id, g.displayName, g.directoryStatus,
        CASE WHEN g.directoryCheckedAt IS NULL THEN NULL ELSE CONVERT(varchar(23), g.directoryCheckedAt, 126) + 'Z' END AS lastCheckedAt,
        g.directoryErrorCode AS lastErrorCode,
        SUM(CASE WHEN k.state='active' THEN 1 ELSE 0 END) AS activeSubscriptions,
        COUNT(k.id) AS subscriptions
        FROM dbo.portalApiKeyGroup g
        LEFT JOIN dbo.portalApiKey k ON k.tenantId=g.tenantId AND k.groupId=g.groupId AND k.state<>'deleted'
        WHERE g.tenantId=${tenantId} ${cursor ? sql`AND g.groupId>${cursor}` : sql``}
        GROUP BY g.groupId, g.displayName, g.directoryStatus, g.directoryCheckedAt, g.directoryErrorCode
        ORDER BY g.groupId`.execute(this.database)).rows;
      const page = rows.slice(0, 100);
      const groups = page.map((row) => ({ id: row.id, displayName: row.displayName,
        directoryStatus: row.directoryStatus, lastCheckedAt: row.lastCheckedAt,
        lastErrorCode: row.lastErrorCode && /^[A-Za-z][A-Za-z0-9]{0,63}$/.test(row.lastErrorCode) ? row.lastErrorCode : null,
        activeSubscriptions: Number(row.activeSubscriptions),
        affectedSubscriptions: row.directoryStatus === "active" ? 0 : Number(row.subscriptions) }));
      return { groups, nextCursor: rows.length > 100 ? page.at(-1)!.id : null };
    });
  }

  /** 状態件数と購読数はページとは別に全体を集計し、読込済みの分を総数と見せません。 */
  async getSummary(tenant: string): Promise<ApiKeyAdministrationSummary & { checkedAt: string | null; syncErrors: number }> {
    const tenantId = tenantSchema.parse(tenant);
    return safe(async () => {
      const row = (await sql<ApiKeyAdministrationSummary & { checkedAt: string | null; syncErrors: number }>`
        WITH groupScope AS (
          SELECT groupId,directoryStatus,directoryCheckedAt,directoryErrorCode FROM dbo.portalApiKeyGroup WHERE tenantId=${tenantId}
        ), groupTotals AS (
          SELECT COUNT(*) AS totalGroups,
            COUNT(CASE WHEN directoryStatus='active' THEN 1 END) AS activeGroups,
            COUNT(CASE WHEN directoryStatus='deleted' THEN 1 END) AS deletedGroups,
            COUNT(CASE WHEN directoryStatus='ineligible' THEN 1 END) AS ineligibleGroups,
            COUNT(CASE WHEN directoryStatus='unknown' THEN 1 END) AS unknownGroups,
            COUNT(CASE WHEN directoryErrorCode IS NOT NULL THEN 1 END) AS syncErrors,
            MAX(directoryCheckedAt) AS checkedAt FROM groupScope
        ), keyTotals AS (
          SELECT COUNT(CASE WHEN k.state='active' THEN 1 END) AS activeSubscriptions,
            COUNT(CASE WHEN g.directoryStatus<>'active' THEN 1 END) AS affectedSubscriptions
          FROM dbo.portalApiKey k JOIN groupScope g ON g.groupId=k.groupId
          WHERE k.tenantId=${tenantId} AND k.state<>'deleted'
        )
        SELECT g.totalGroups,g.activeGroups,g.deletedGroups,g.ineligibleGroups,g.unknownGroups,g.syncErrors,
          CASE WHEN g.checkedAt IS NULL THEN NULL ELSE CONVERT(varchar(23),g.checkedAt,126)+'Z' END AS checkedAt,
          k.activeSubscriptions,k.affectedSubscriptions FROM groupTotals g CROSS JOIN keyTotals k`.execute(this.database)).rows[0];
      if (!row) throw new ApiKeyError("KeyStoreUnavailable", 503);
      return row;
    });
  }

  /** 古い観測から最大100件を選び、テナント全体のGraph一覧は取得しません。 */
  async listGroupsToRefresh(tenant: string, requested: number): Promise<string[]> {
    const tenantId = tenantSchema.parse(tenant);
    const limit = z.number().int().min(1).max(100).parse(requested);
    return safe(async () => (await sql<{ id: string }>`SELECT TOP (${limit}) groupId AS id
      FROM dbo.portalApiKeyGroup WHERE tenantId=${tenantId}
      ORDER BY CASE WHEN directoryCheckedAt IS NULL THEN 0 ELSE 1 END, directoryCheckedAt, groupId`.execute(this.database))
      .rows.map((row) => tenantSchema.parse(row.id)));
  }
}

let repository: ApiKeyAdministrationRepository | undefined;
/** ビルド中に設定・資格情報へ触れず、最初の認証済み操作でDBを用意します。 */
export function getApiKeyAdministrationRepository(): ApiKeyAdministrationRepository {
  repository ??= new SqlApiKeyAdministrationRepository(new Kysely<Record<string, unknown>>(createAuthDatabase(getSqlEnvironment())));
  return repository;
}
