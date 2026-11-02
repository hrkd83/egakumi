import "server-only";

import { randomUUID } from "node:crypto";
import { Kysely, sql, type Transaction } from "kysely";
import { z } from "zod";
import { createAuthDatabase } from "../auth-database.ts";
import { getSqlEnvironment } from "../auth-environment.ts";
import { ApiKeyError, apiKeyIdSchema, apimSubscriptionIdSchema, type ApiKeyActor, type ApiKeyRecord } from "./contracts.ts";
import { readApiKeyGroupPolicy } from "../api-key-administration/repository.ts";
import { isEligibleApiKeyGroup } from "./group-policy.ts";
import { apiKeyLimitProfileSchema, unlimitedApiKeyLimits, updateApiKeyLimitsSchema,
  type ApiKeyLimitProfile, type UpdateApiKeyLimits } from "./limit-contracts.ts";

type Database = Kysely<Record<string, unknown>>;
type TransactionDatabase = Transaction<Record<string, unknown>>;
type LimitRow = {
  version: number; requestCalls: number | null; requestRenewalSeconds: number | null;
  tokensPerMinute: number | null; tokenQuota: number | null;
  tokenQuotaPeriod: "Hourly" | "Daily" | "Weekly" | "Monthly" | "Yearly" | null;
  updatedAt: string;
};
type KeyScope = Pick<ApiKeyRecord, "groupId" | "productId" | "state"> & { groupName: string; securityEnabled: boolean | null };

/** 制限設定だけを永続化します。使用量カウンターやAPIMキーは保存しません。 */
export interface ApiKeyLimitRepository {
  get(tenantId: string, id: string): Promise<ApiKeyLimitProfile>;
  update(actor: ApiKeyActor, record: ApiKeyRecord, input: UpdateApiKeyLimits): Promise<ApiKeyLimitProfile>;
  getGatewayProfile(tenantId: string, id: string): Promise<ApiKeyLimitProfile | null>;
}

const guid = z.uuid().transform((value) => value.toLowerCase());
/** APIMから受け取る購読IDです。既存の基盤購読も読み取り対象にできる形式です。 */
export const gatewaySubscriptionIdSchema = apimSubscriptionIdSchema;
const columns = sql`version, requestCalls, requestRenewalSeconds, tokensPerMinute, tokenQuota, tokenQuotaPeriod,
  CONVERT(varchar(23), updatedAt, 126) + 'Z' AS updatedAt`;
/** 索引列をnvarcharへ暗黙変換せず、検証済みIDの型をDB列へ合わせます。 */
const dbGuid = (value: string) => sql`CAST(${value} AS char(36))`;
/** APIMの既存購読IDも切り詰めず、varcharとして完全一致で比較します。 */
const dbSubscriptionId = (value: string) => sql`CAST(${value} AS varchar(256))`;

/** SQL値も契約に従って検証し、不整合のあるデータを無制限へ読み替えません。 */
function profile(row: LimitRow | undefined): ApiKeyLimitProfile {
  if (!row) return unlimitedApiKeyLimits();
  const validRate = (row.requestCalls === null) === (row.requestRenewalSeconds === null);
  const validQuota = (row.tokenQuota === null) === (row.tokenQuotaPeriod === null);
  if (!validRate || !validQuota) throw new ApiKeyError("LimitStoreUnavailable", 503);
  const parsed = apiKeyLimitProfileSchema.safeParse({
    version: row.version,
    requestRate: row.requestCalls === null ? null : { calls: row.requestCalls, renewalPeriodSeconds: row.requestRenewalSeconds },
    tokensPerMinute: row.tokensPerMinute,
    tokenQuota: row.tokenQuota === null ? null : { tokens: row.tokenQuota, period: row.tokenQuotaPeriod },
    updatedAt: row.updatedAt,
  });
  if (!parsed.success) throw new ApiKeyError("LimitStoreUnavailable", 503);
  return parsed.data;
}

/** 接続情報・SQL文・内部エラーを外へ出さず、設定取得失敗として扱います。 */
async function safe<T>(operation: () => Promise<T>): Promise<T> {
  try { return await operation(); }
  catch (error) {
    if (error instanceof ApiKeyError) throw error;
    throw new ApiKeyError("LimitStoreUnavailable", 503);
  }
}

/** 既存のキー状態変更と同じ排他名を使い、削除中のプロファイル更新を防ぎます。 */
async function lock(database: TransactionDatabase, tenantId: string, id: string): Promise<void> {
  await sql`DECLARE @result int;
    EXEC @result = sp_getapplock @Resource=${`portal-api-key:${tenantId}:${id}`},
      @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=15000;
    IF @result < 0 THROW 51000, 'API key operation is busy', 1;`.execute(database);
}

/** 読み取りと更新後の返却で同じ列・検証を使用します。 */
async function readProfile(database: Database | TransactionDatabase, tenantId: string, id: string): Promise<ApiKeyLimitProfile> {
  const row = (await sql<LimitRow>`SELECT ${columns} FROM dbo.portalApiKeyLimit
    WHERE tenantId=${dbGuid(tenantId)} AND apiKeyId=${dbSubscriptionId(id)}`.execute(database)).rows[0];
  return profile(row);
}

/** SQL内の原子的な版比較・設定更新・監査で、設定の取りこぼしを防ぎます。 */
export class SqlApiKeyLimitRepository implements ApiKeyLimitRepository {
  private readonly database: Database;

  constructor(database: Database) { this.database = database; }

  /** 認可済みの管理画面へ設定を返します。未保存の場合は版0の無制限です。 */
  async get(tenant: string, id: string): Promise<ApiKeyLimitProfile> {
    const tenantId = guid.parse(tenant);
    apiKeyIdSchema.parse(id);
    return safe(() => readProfile(this.database, tenantId, id));
  }

  /** 無制限への変更も新しい版として保存し、以前の変更を消さず監査に残します。 */
  async update(actor: ApiKeyActor, record: ApiKeyRecord, values: UpdateApiKeyLimits): Promise<ApiKeyLimitProfile> {
    const tenantId = guid.parse(actor.tenantId), actorId = guid.parse(actor.objectId);
    const id = apiKeyIdSchema.parse(record.id), input = updateApiKeyLimitsSchema.parse(values);
    return safe(() => this.database.transaction().execute(async (transaction) => {
      await lock(transaction, tenantId, id);
      const key = (await sql<KeyScope>`SELECT k.groupId, k.productId, k.state,
        g.displayName AS groupName,g.directorySecurityEnabled AS securityEnabled FROM dbo.portalApiKey k
        JOIN dbo.portalApiKeyGroup g ON g.tenantId=k.tenantId AND g.groupId=k.groupId
        JOIN dbo.portalApiKeyProduct p ON p.tenantId=k.tenantId AND p.groupId=k.groupId AND p.productId=k.productId
        WHERE k.tenantId=${dbGuid(tenantId)} AND k.id=${dbSubscriptionId(id)} AND g.enabled=1 AND p.enabled=1 AND g.directoryStatus='active'
          AND (g.approvalSource='deployment' OR EXISTS(SELECT 1 FROM dbo.portalApiKeyCatalogProduct c
            WHERE c.tenantId=p.tenantId AND c.productId=p.productId AND c.enabled=1) OR EXISTS(SELECT 1 FROM dbo.portalApiKey existingKey WHERE existingKey.tenantId=p.tenantId AND existingKey.groupId=p.groupId AND existingKey.productId=p.productId AND existingKey.state<>'deleted'))`.execute(transaction)).rows[0];
      if (!key || key.groupId.toLowerCase() !== record.groupId.toLowerCase() || key.productId !== record.productId
          || key.state === "deleted" || key.state === "deleting") throw new ApiKeyError("NotFound", 404);
      const policy = await readApiKeyGroupPolicy(transaction, tenantId);
      if (!isEligibleApiKeyGroup({ id: key.groupId, displayName: key.groupName, securityEnabled: key.securityEnabled === true }, policy)) {
        throw new ApiKeyError("NotFound", 404);
      }
      if (key.state !== "active" && key.state !== "suspended") throw new ApiKeyError("SubscriptionNotReady", 409);
      const previous = await readProfile(transaction, tenantId, id);
      if (previous.version !== input.expectedVersion) throw new ApiKeyError("LimitVersionConflict", 409);
      const version = previous.version + 1;
      const calls = input.requestRate?.calls ?? null, seconds = input.requestRate?.renewalPeriodSeconds ?? null;
      const quota = input.tokenQuota?.tokens ?? null, period = input.tokenQuota?.period ?? null;
      if (previous.version === 0) {
        await sql`INSERT INTO dbo.portalApiKeyLimit
          (tenantId, apiKeyId, version, requestCalls, requestRenewalSeconds, tokensPerMinute, tokenQuota, tokenQuotaPeriod, updatedByObjectId)
          VALUES (${tenantId}, ${id}, ${version}, ${calls}, ${seconds}, ${input.tokensPerMinute}, ${quota}, ${period}, ${actorId})`.execute(transaction);
      } else {
        await sql`UPDATE dbo.portalApiKeyLimit SET version=${version}, requestCalls=${calls}, requestRenewalSeconds=${seconds},
          tokensPerMinute=${input.tokensPerMinute}, tokenQuota=${quota}, tokenQuotaPeriod=${period},
          updatedAt=SYSUTCDATETIME(), updatedByObjectId=${actorId}
          WHERE tenantId=${dbGuid(tenantId)} AND apiKeyId=${dbSubscriptionId(id)}`.execute(transaction);
      }
      const current = await readProfile(transaction, tenantId, id);
      await sql`INSERT INTO dbo.portalApiKeyLimitAudit
        (id, tenantId, apiKeyId, actorObjectId, version, previousProfile, profile)
        VALUES (${randomUUID()}, ${tenantId}, ${id}, ${actorId}, ${version}, ${JSON.stringify(previous)}, ${JSON.stringify(current)})`.execute(transaction);
      return current;
    }));
  }

  /**
   * 内部ゲートウェイへ設定を返します。未管理の既存購読はnullとして明示します。
   * @remarks SQL障害や停止・削除した管理購読を、未設定の無制限とは混同しません。
   */
  async getGatewayProfile(tenant: string, subscriptionId: string): Promise<ApiKeyLimitProfile | null> {
    const tenantId = guid.parse(tenant), id = gatewaySubscriptionIdSchema.parse(subscriptionId);
    return safe(async () => {
      const row = (await sql<LimitRow & { state: ApiKeyRecord["state"]; keyId: string; enabledGroup: boolean; enabledProduct: boolean;
        directoryStatus: string }>`
        SELECT k.id AS keyId, k.state, g.enabled AS enabledGroup, p.enabled AS enabledProduct,
          g.directoryStatus,
          l.version, l.requestCalls, l.requestRenewalSeconds, l.tokensPerMinute, l.tokenQuota, l.tokenQuotaPeriod,
          CASE WHEN l.updatedAt IS NULL THEN NULL ELSE CONVERT(varchar(23), l.updatedAt, 126) + 'Z' END AS updatedAt
        FROM dbo.portalApiKey k
        JOIN dbo.portalApiKeyGroup g ON g.tenantId=k.tenantId AND g.groupId=k.groupId
        JOIN dbo.portalApiKeyProduct p ON p.tenantId=k.tenantId AND p.groupId=k.groupId AND p.productId=k.productId
        LEFT JOIN dbo.portalApiKeyLimit l ON l.tenantId=k.tenantId AND l.apiKeyId=k.id
        WHERE k.tenantId=${dbGuid(tenantId)} AND k.apimSubscriptionId=${dbSubscriptionId(id)}`.execute(this.database)).rows[0];
      if (!row) return null;
      // 発行カタログの変更で既存キーの制限を失効させず、明示的な承認停止は維持します。
      if (row.state !== "active" || !row.enabledGroup || !row.enabledProduct) throw new ApiKeyError("SubscriptionNotReady", 403);
      return row.version === null ? unlimitedApiKeyLimits() : profile(row);
    });
  }
}

let repository: ApiKeyLimitRepository | undefined;
/** 秘密やSQL接続をビルド中に読み込まず、実行時にだけプールを生成します。 */
export function getApiKeyLimitRepository(): ApiKeyLimitRepository {
  repository ??= new SqlApiKeyLimitRepository(new Kysely<Record<string, unknown>>(createAuthDatabase(getSqlEnvironment())));
  return repository;
}
