import "server-only";

import { randomUUID } from "node:crypto";
import { Kysely, sql, type Transaction } from "kysely";
import { z } from "zod";
import { createAuthDatabase } from "../auth-database.ts";
import { getSqlEnvironment } from "../auth-environment.ts";
import { ApiKeyError, apiKeyIdSchema, reserveApiKeySchema, transferApiKeyOwnershipSchema } from "./contracts.ts";
import type { ApiKeyActor, ApiKeyGroup, ApiKeyRecord, ReserveApiKeyInput, TransferApiKeyOwnershipInput } from "./contracts.ts";
import type { DirectoryGroup, DirectoryGroupObservation } from "./group-policy.ts";
import { isEligibleApiKeyGroup, type ApiKeyGroupPolicy } from "./group-policy.ts";
import { readApiKeyGroupPolicy } from "../api-key-administration/repository.ts";

type Database = Kysely<Record<string, unknown>>;
type KeyRow = ApiKeyRecord & { tenantId: string; createdByObjectId: string | null; idempotencyKey: string };
type GroupRow = { groupId: string; groupName: string; productId: string; productName: string; maxSubscriptions: number;
  directoryStatus: "active" | "deleted" | "ineligible" | "unknown"; lastCheckedAt: string | null };
type GroupEligibilityRow = { groupName: string; securityEnabled: boolean | null };
/** 秘密を含めず記録できる、利用者によるキー管理操作です。 */
export type ApiKeyAuditAction = "create" | "reveal" | "suspend" | "resume" | "review" | "delete";

/** 同じ購読の並行操作を直列化する間だけ使える、更新と監査の窓口です。 */
export type ApiKeyLockedRecord = {
  record: ApiKeyRecord;
  confirmGroupApproval: (observation: DirectoryGroupObservation) => Promise<void>;
  updateState: (state: ApiKeyRecord["state"]) => Promise<ApiKeyRecord>;
  markReviewed: () => Promise<ApiKeyRecord>;
  audit: (action: ApiKeyAuditAction) => Promise<void>;
};

/** Graph・APIMとの通信から分離した、グループ所有購読台帳の永続化契約です。 */
export interface ApiKeyRepository {
  getGroups(tenantId: string): Promise<ApiKeyGroup[]>;
  listKeys(tenantId: string, groupIds: readonly string[]): Promise<ApiKeyRecord[]>;
  getKey(tenantId: string, id: string): Promise<ApiKeyRecord | null>;
  reserve(actor: ApiKeyActor, input: ReserveApiKeyInput): Promise<{ record: ApiKeyRecord; created: boolean }>;
  withLockedKey<T>(actor: ApiKeyActor, id: string, callback: (context: ApiKeyLockedRecord) => Promise<T>): Promise<T>;
  ensureRegistry(tenantId: string, groups: readonly DirectoryGroup[]): Promise<void>;
  observeGroups(tenantId: string, observations: readonly DirectoryGroupObservation[]): Promise<void>;
  transferOwnership(actor: ApiKeyActor, id: string, input: TransferApiKeyOwnershipInput,
    authorize: (record: ApiKeyRecord) => Promise<void>): Promise<ApiKeyRecord>;
}

const guid = z.uuid().transform((value) => value.toLowerCase());
const observationSchema = z.object({ id: guid, displayName: z.string().min(1).max(255).nullable(),
  securityEnabled: z.boolean().nullable(), status: z.enum(["active", "deleted", "ineligible", "unknown"]),
  checkedAt: z.iso.datetime(), errorCode: z.string().regex(/^[A-Za-z0-9_-]{1,64}$/).nullable(),
});
const stateChanges: Record<ApiKeyRecord["state"], readonly ApiKeyRecord["state"][]> = {
  creating: ["creating", "active", "deleting"],
  active: ["active", "suspended", "deleting"],
  suspended: ["suspended", "active", "deleting"],
  deleting: ["deleting", "deleted"],
  deleted: ["deleted"],
};
const auditActions = new Set<ApiKeyAuditAction>(["create", "reveal", "suspend", "resume", "review", "delete"]);
const columns = sql`id, apimSubscriptionId, clientType, systemName, teamName, companyId, contactEmail,
  (SELECT c.name FROM dbo.portalCompany c WHERE c.tenantId=portalApiKey.tenantId AND c.id=portalApiKey.companyId) AS companyName, tenantId, groupId, productId, displayName, state, budgetMode, createdByObjectId, idempotencyKey,
  CONVERT(varchar(23), createdAt, 126) + 'Z' AS createdAt,
  CONVERT(varchar(23), updatedAt, 126) + 'Z' AS updatedAt,
  CASE WHEN lastReviewedAt IS NULL THEN NULL ELSE CONVERT(varchar(23), lastReviewedAt, 126) + 'Z' END AS lastReviewedAt`;

/** 文字列パラメーターの暗黙変換で索引全体を走査し、別の購読のロックを待つことを防ぎます。 */
const dbGuid = (value: string) => sql`CAST(${value} AS char(36))`;
/** 管理台帳の購読IDはZod検証後に、主キーと同じ型で比較します。 */
const dbKeyId = (value: string) => sql`CAST(${value} AS varchar(43))`;
/** 製品IDは承認索引と同じ型で比較し、他製品の行を走査しません。 */
const dbProductId = (value: string) => sql`CAST(${value} AS varchar(80))`;

/** SQLに残る古いactiveだけで認可せず、このトランザクションで読んだ現在規則を再検査します。 */
function matchesCurrentGroup(row: GroupEligibilityRow | undefined, groupId: string, policy: ApiKeyGroupPolicy): boolean {
  return !!row && isEligibleApiKeyGroup({ id: groupId, displayName: row.groupName, securityEnabled: row.securityEnabled === true }, policy);
}

/** DB内部の冪等性・操作主体列を、ブラウザーへ返す購読情報から除きます。 */
function publicRecord(row: KeyRow): ApiKeyRecord {
  return { id: row.id, apimSubscriptionId: row.apimSubscriptionId, groupId: row.groupId, productId: row.productId, displayName: row.displayName,
    clientType: row.clientType, systemName: row.systemName, teamName: row.teamName, companyId: row.companyId,
    companyName: row.companyName, contactEmail: row.contactEmail,
    state: row.state, budgetMode: row.budgetMode, createdAt: row.createdAt, updatedAt: row.updatedAt, lastReviewedAt: row.lastReviewedAt };
}

/** SQL例外には接続情報を含めず、安全な固定の失敗種別へ置き換えます。 */
async function safe<T>(operation: () => Promise<T>): Promise<T> {
  try { return await operation(); }
  catch (error) {
    if (error instanceof ApiKeyError) throw error;
    throw new ApiKeyError("KeyStoreUnavailable", 503);
  }
}

/** トランザクション終了時に自動解除される、DB内だけの排他ロックを取得します。 */
async function lock(transaction: Transaction<Record<string, unknown>>, name: string): Promise<void> {
  await sql`DECLARE @result int;
    EXEC @result = sp_getapplock @Resource=${name}, @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=15000;
    IF @result < 0 THROW 51000, 'API key operation is busy', 1;`.execute(transaction);
}

/** 同じテナントの購読だけを取得し、管理対象外の既存APIM購読を取り込みません。 */
async function selectKey(database: Database | Transaction<Record<string, unknown>>, tenantId: string, id: string): Promise<KeyRow | undefined> {
  return (await sql<KeyRow>`SELECT ${columns} FROM dbo.portalApiKey WHERE tenantId=${dbGuid(tenantId)} AND id=${dbKeyId(id)}`.execute(database)).rows[0];
}

/**
 * SQL Serverのトランザクションで発行枠・冪等性・購読状態を守る台帳です。
 * @remarks ネットワークを呼ぶservice層はwithLockedKeyの中で操作し、同じ購読の変更を重ねません。
 */
export class SqlApiKeyRepository implements ApiKeyRepository {
  private readonly database: Database;

  constructor(database: Database) { this.database = database; }

  /** 管理者が承認したグループと製品だけを返します。所属確認は呼出元がGraphで行います。 */
  async getGroups(tenant: string): Promise<ApiKeyGroup[]> {
    const tenantId = guid.parse(tenant);
    return safe(async () => {
      const rows = (await sql<GroupRow>`SELECT g.groupId, g.displayName AS groupName,
        p.productId, p.displayName AS productName, p.maxSubscriptions, g.directoryStatus,
        CASE WHEN g.directoryCheckedAt IS NULL THEN NULL ELSE CONVERT(varchar(23),g.directoryCheckedAt,126)+'Z' END AS lastCheckedAt
        FROM dbo.portalApiKeyGroup g JOIN dbo.portalApiKeyProduct p ON p.tenantId=g.tenantId AND p.groupId=g.groupId
        WHERE g.tenantId=${dbGuid(tenantId)} AND g.enabled=1 AND p.enabled=1
          AND (g.approvalSource='deployment' OR EXISTS(SELECT 1 FROM dbo.portalApiKeyCatalogProduct c
            WHERE c.tenantId=p.tenantId AND c.productId=p.productId AND c.enabled=1) OR EXISTS(SELECT 1 FROM dbo.portalApiKey existingKey WHERE existingKey.tenantId=p.tenantId AND existingKey.groupId=p.groupId AND existingKey.productId=p.productId AND existingKey.state<>'deleted'))
        ORDER BY g.displayName, g.groupId, p.displayName, p.productId`.execute(this.database)).rows;
      const groups = new Map<string, ApiKeyGroup>();
      for (const row of rows) {
        let group = groups.get(row.groupId);
        if (!group) { group = { id: row.groupId, displayName: row.groupName, products: [], directoryStatus: row.directoryStatus,
          lastCheckedAt: row.lastCheckedAt }; groups.set(row.groupId, group); }
        group.products.push({ id: row.productId, displayName: row.productName, maxSubscriptions: row.maxSubscriptions });
      }
      return [...groups.values()];
    });
  }

  /** Graphで所属を確認したグループ群の、未削除の購読だけを一覧化します。 */
  async listKeys(tenant: string, ids: readonly string[]): Promise<ApiKeyRecord[]> {
    const tenantId = guid.parse(tenant);
    const groupIds = [...new Set(z.array(guid).max(1000).parse(ids))];
    if (!groupIds.length) return [];
    return safe(async () => (await sql<KeyRow>`SELECT ${columns} FROM dbo.portalApiKey
      WHERE tenantId=${dbGuid(tenantId)} AND groupId IN (${sql.join(groupIds.map(dbGuid))}) AND state<>'deleted'
      ORDER BY createdAt DESC, id`.execute(this.database)).rows.map(publicRecord));
  }

  /** 削除済みを含む台帳の一件を取得し、再試行時も同じ購読を参照します。 */
  async getKey(tenant: string, id: string): Promise<ApiKeyRecord | null> {
    const tenantId = guid.parse(tenant);
    apiKeyIdSchema.parse(id);
    return safe(async () => {
      const row = await selectKey(this.database, tenantId, id);
      return row ? publicRecord(row) : null;
    });
  }

  /** 同一の発行依頼は同じ購読を返し、グループ・製品の発行上限を並行処理でも超えさせません。 */
  async reserve(actor: ApiKeyActor, values: ReserveApiKeyInput): Promise<{ record: ApiKeyRecord; created: boolean }> {
    const tenantId = guid.parse(actor.tenantId), objectId = guid.parse(actor.objectId);
    const input = reserveApiKeySchema.parse(values);
    input.groupId = input.groupId.toLowerCase();
    input.idempotencyKey = input.idempotencyKey.toLowerCase();
    input.companyId = input.companyId.toLowerCase();
    const systemName = input.systemName || null, teamName = input.teamName || null;
    return safe(() => this.database.transaction().execute(async (transaction) => {
      // 同じ依頼IDを異なる所有者へ送り直す場合も、重複作成せず競合として扱います。
      await lock(transaction, `portal-api-key-request:${tenantId}:${objectId}:${input.idempotencyKey}`);
      const existing = (await sql<KeyRow>`SELECT ${columns} FROM dbo.portalApiKey
        WHERE tenantId=${dbGuid(tenantId)} AND createdByObjectId=${dbGuid(objectId)} AND idempotencyKey=${dbGuid(input.idempotencyKey)}`.execute(transaction)).rows[0];
      if (existing) {
        if (existing.groupId !== input.groupId || existing.productId !== input.productId || existing.displayName !== input.displayName
            || existing.clientType !== input.clientType || existing.systemName !== systemName || existing.teamName !== teamName
            || existing.companyId !== input.companyId || existing.contactEmail !== input.contactEmail) {
          throw new ApiKeyError("IdempotencyConflict", 409);
        }
        return { record: publicRecord(existing), created: false };
      }
      await lock(transaction, `portal-api-key-cap:${tenantId}:${input.groupId}:${input.productId}`);
      const policy = await readApiKeyGroupPolicy(transaction, tenantId);
      const permission = (await sql<GroupEligibilityRow & { maxSubscriptions: number }>`SELECT p.maxSubscriptions,
        g.displayName AS groupName,g.directorySecurityEnabled AS securityEnabled
        FROM dbo.portalApiKeyProduct p JOIN dbo.portalApiKeyGroup g ON g.tenantId=p.tenantId AND g.groupId=p.groupId
        WHERE p.tenantId=${dbGuid(tenantId)} AND p.groupId=${dbGuid(input.groupId)} AND p.productId=${dbProductId(input.productId)}
          AND p.enabled=1 AND g.enabled=1 AND g.directoryStatus='active'
          AND EXISTS(SELECT 1 FROM dbo.portalApiKeyCatalogProduct c
            WHERE c.tenantId=p.tenantId AND c.productId=p.productId AND c.enabled=1)`.execute(transaction)).rows[0];
      if (!matchesCurrentGroup(permission, input.groupId, policy)) throw new ApiKeyError("ProductNotAllowed", 403);
      const count = (await sql<{ amount: number }>`SELECT COUNT(*) AS amount FROM dbo.portalApiKey
        WHERE tenantId=${dbGuid(tenantId)} AND groupId=${dbGuid(input.groupId)} AND productId=${dbProductId(input.productId)} AND state<>'deleted'`.execute(transaction)).rows[0]?.amount ?? 0;
      if (count >= permission!.maxSubscriptions) throw new ApiKeyError("SubscriptionLimitReached", 409);
      // 会社の無効化と発行を同一行ロックで直列化し、確認直後の無効会社への発行を防ぎます。
      const company = (await sql<{ id: string }>`SELECT id FROM dbo.portalCompany WITH(UPDLOCK,HOLDLOCK)
        WHERE tenantId=${dbGuid(tenantId)} AND id=${dbGuid(input.companyId)} AND isActive=1`.execute(transaction)).rows[0];
      if (!company) throw new ApiKeyError("CompanyNotAvailable", 409);
      const id = `portal-${randomUUID()}`;
      await sql`INSERT INTO dbo.portalApiKey (id, apimSubscriptionId, tenantId, groupId, productId, displayName, state, createdByObjectId, idempotencyKey,
        clientType,systemName,teamName,companyId,contactEmail)
        VALUES (${id}, ${id}, ${tenantId}, ${input.groupId}, ${input.productId}, ${input.displayName}, 'creating', ${objectId}, ${input.idempotencyKey},
          ${input.clientType},${systemName},${teamName},${input.companyId},${input.contactEmail})`.execute(transaction);
      const row = await selectKey(transaction, tenantId, id);
      if (!row) throw new ApiKeyError("KeyStoreUnavailable", 503);
      return { record: publicRecord(row), created: true };
    }));
  }

  /** 一件の購読変更と監査を排他的なトランザクションにまとめます。所有グループの変更は提供しません。 */
  async withLockedKey<T>(actor: ApiKeyActor, id: string, callback: (context: ApiKeyLockedRecord) => Promise<T>): Promise<T> {
    const tenantId = guid.parse(actor.tenantId), objectId = guid.parse(actor.objectId);
    apiKeyIdSchema.parse(id);
    return safe(() => this.database.transaction().execute(async (transaction) => {
      await lock(transaction, `portal-api-key:${tenantId}:${id}`);
      const row = await selectKey(transaction, tenantId, id);
      if (!row) throw new ApiKeyError("KeyNotFound", 404);
      /** 更新後のDB値を読み直し、返却時刻と監査の状態を同じものに揃えます。 */
      const reload = async () => {
        const current = await selectKey(transaction, tenantId, id);
        if (!current) throw new ApiKeyError("KeyStoreUnavailable", 503);
        context.record = publicRecord(current);
        return context.record;
      };
      const context: ApiKeyLockedRecord = {
        record: publicRecord(row),
        confirmGroupApproval: async (value) => {
          const observed = observationSchema.parse(value);
          if (observed.id !== context.record.groupId || observed.status !== "active") throw new ApiKeyError("NotFound", 404);
          const policy = await readApiKeyGroupPolicy(transaction, tenantId);
          if (!observed.displayName || !isEligibleApiKeyGroup({ id: observed.id, displayName: observed.displayName,
            securityEnabled: observed.securityEnabled === true }, policy)) throw new ApiKeyError("NotFound", 404);
          // 排他中に別のSQL接続を取りに行かず、観測と承認の再照合をこの接続で完結させます。
          await sql`EXEC dbo.portalObserveApiKeyGroup @tenantId=${tenantId}, @groupId=${observed.id},
            @displayName=${observed.displayName}, @securityEnabled=${observed.securityEnabled}, @status=${observed.status},
            @checkedAt=${observed.checkedAt}, @errorCode=${observed.errorCode}`.execute(transaction);
          const allowed = (await sql<GroupEligibilityRow>`SELECT g.displayName AS groupName,g.directorySecurityEnabled AS securityEnabled
            FROM dbo.portalApiKeyProduct p
            JOIN dbo.portalApiKeyGroup g ON g.tenantId=p.tenantId AND g.groupId=p.groupId
            WHERE p.tenantId=${dbGuid(tenantId)} AND p.groupId=${dbGuid(context.record.groupId)} AND p.productId=${dbProductId(context.record.productId)}
              AND p.enabled=1 AND g.enabled=1 AND g.directoryStatus='active'
              AND (g.approvalSource='deployment' OR EXISTS(SELECT 1 FROM dbo.portalApiKeyCatalogProduct c
                WHERE c.tenantId=p.tenantId AND c.productId=p.productId AND c.enabled=1) OR EXISTS(SELECT 1 FROM dbo.portalApiKey existingKey WHERE existingKey.tenantId=p.tenantId AND existingKey.groupId=p.groupId AND existingKey.productId=p.productId AND existingKey.state<>'deleted'))`.execute(transaction)).rows[0];
          if (!matchesCurrentGroup(allowed, context.record.groupId, policy)) throw new ApiKeyError("NotFound", 404);
        },
        updateState: async (state) => {
          if (!stateChanges[context.record.state].includes(state)) throw new ApiKeyError("InvalidKeyState", 409);
          await sql`UPDATE dbo.portalApiKey SET state=${state}, updatedAt=SYSUTCDATETIME()
            WHERE tenantId=${dbGuid(tenantId)} AND id=${dbKeyId(id)}`.execute(transaction);
          return reload();
        },
        markReviewed: async () => {
          if (!["active", "suspended"].includes(context.record.state)) throw new ApiKeyError("InvalidKeyState", 409);
          await sql`UPDATE dbo.portalApiKey SET lastReviewedAt=SYSUTCDATETIME(), lastReviewedByObjectId=${objectId}, updatedAt=SYSUTCDATETIME()
            WHERE tenantId=${dbGuid(tenantId)} AND id=${dbKeyId(id)}`.execute(transaction);
          return reload();
        },
        audit: async (action) => {
          if (!auditActions.has(action)) throw new ApiKeyError("InvalidRequest", 400);
          await sql`INSERT INTO dbo.portalApiKeyAudit (id, tenantId, apiKeyId, actorObjectId, action, state)
            VALUES (${randomUUID()}, ${tenantId}, ${id}, ${objectId}, ${action}, ${context.record.state})`.execute(transaction);
        },
      };
      return callback(context);
    }));
  }

  /** Graphで適格と確認した新規グループだけを登録し、既存の承認設定を維持します。 */
  async ensureRegistry(tenant: string, groups: readonly DirectoryGroup[]): Promise<void> {
    const tenantId = guid.parse(tenant);
    const parsed = z.array(z.object({ id: guid, displayName: z.string().min(1).max(255).refine((value) => value.trim().length > 0),
      securityEnabled: z.literal(true) })).max(1000).parse(groups);
    if (!parsed.length) return;
    return safe(() => this.database.transaction().execute(async (transaction) => {
      const policy = await readApiKeyGroupPolicy(transaction, tenantId);
      if (parsed.some((group) => !isEligibleApiKeyGroup(group, policy))) throw new ApiKeyError("AccessDenied", 403);
      for (const group of parsed) await sql`EXEC dbo.portalEnsureApiKeyGroup @tenantId=${tenantId},
        @groupId=${group.id}, @displayName=${group.displayName}`.execute(transaction);
    }));
  }

  /** 古い観測結果で新しい結果を上書きせず、未知の名前は既存値を保持します。 */
  async observeGroups(tenant: string, observations: readonly DirectoryGroupObservation[]): Promise<void> {
    const tenantId = guid.parse(tenant);
    const parsed = z.array(observationSchema).max(1000).parse(observations);
    if (!parsed.length) return;
    return safe(() => this.database.transaction().execute(async (transaction) => {
      const policy = parsed.some((group) => group.status === "active") ? await readApiKeyGroupPolicy(transaction, tenantId) : null;
      for (const observed of parsed) {
        // Graph取得後の設定変更を反映し、過去のactive観測で現在の対象外グループを復活させません。
        const mismatch = observed.status === "active" && policy && (!observed.displayName || !isEligibleApiKeyGroup({
          id: observed.id, displayName: observed.displayName, securityEnabled: observed.securityEnabled === true }, policy));
        const group = mismatch ? { ...observed, status: "ineligible", errorCode: "GroupNamePolicyMismatch" } : observed;
        await sql`EXEC dbo.portalObserveApiKeyGroup @tenantId=${tenantId}, @groupId=${group.id},
          @displayName=${group.displayName}, @securityEnabled=${group.securityEnabled}, @status=${group.status},
          @checkedAt=${group.checkedAt}, @errorCode=${group.errorCode}`.execute(transaction);
      }
    }));
  }

  /**
   * APIM資源やキー値には触れず、所有関係と移管監査だけを同時に更新します。
   * @remarks キーと両方の発行枠を一定順でロックし、移管・発行・削除の競合を防ぎます。
   */
  async transferOwnership(actor: ApiKeyActor, id: string, values: TransferApiKeyOwnershipInput,
    authorize: (record: ApiKeyRecord) => Promise<void>): Promise<ApiKeyRecord> {
    const tenantId = guid.parse(actor.tenantId), objectId = guid.parse(actor.objectId);
    apiKeyIdSchema.parse(id);
    const input = transferApiKeyOwnershipSchema.parse(values);
    input.targetGroupId = input.targetGroupId.toLowerCase(); input.expectedSourceGroupId = input.expectedSourceGroupId.toLowerCase();
    return safe(() => this.database.transaction().execute(async (transaction) => {
      await lock(transaction, `portal-api-key:${tenantId}:${id}`);
      const row = await selectKey(transaction, tenantId, id);
      if (!row || row.state === "deleted" || row.state === "deleting") throw new ApiKeyError("NotFound", 404);
      if (row.groupId !== input.expectedSourceGroupId) throw new ApiKeyError("OwnershipConflict", 409);
      if (row.state !== "active" && row.state !== "suspended") throw new ApiKeyError("SubscriptionNotReady", 409);
      for (const group of [...new Set([row.groupId, input.targetGroupId])].sort()) {
        await lock(transaction, `portal-api-key-cap:${tenantId}:${group}:${row.productId}`);
      }
      await authorize(publicRecord(row));
      const policy = await readApiKeyGroupPolicy(transaction, tenantId);
      const source = (await sql<GroupEligibilityRow>`SELECT g.displayName AS groupName,g.directorySecurityEnabled AS securityEnabled
        FROM dbo.portalApiKeyProduct p
        JOIN dbo.portalApiKeyGroup g ON g.tenantId=p.tenantId AND g.groupId=p.groupId
        WHERE p.tenantId=${dbGuid(tenantId)} AND p.groupId=${dbGuid(row.groupId)} AND p.productId=${dbProductId(row.productId)}
          AND p.enabled=1 AND g.enabled=1 AND g.directoryStatus='active'
          AND (g.approvalSource='deployment' OR EXISTS(SELECT 1 FROM dbo.portalApiKeyCatalogProduct c
            WHERE c.tenantId=p.tenantId AND c.productId=p.productId AND c.enabled=1) OR EXISTS(SELECT 1 FROM dbo.portalApiKey existingKey WHERE existingKey.tenantId=p.tenantId AND existingKey.groupId=p.groupId AND existingKey.productId=p.productId AND existingKey.state<>'deleted'))`.execute(transaction)).rows[0];
      if (!matchesCurrentGroup(source, row.groupId, policy)) throw new ApiKeyError("NotFound", 404);
      if (row.groupId === input.targetGroupId) return publicRecord(row);
      const target = (await sql<GroupEligibilityRow & { maxSubscriptions: number }>`SELECT p.maxSubscriptions,
        g.displayName AS groupName,g.directorySecurityEnabled AS securityEnabled
        FROM dbo.portalApiKeyProduct p JOIN dbo.portalApiKeyGroup g ON g.tenantId=p.tenantId AND g.groupId=p.groupId
        WHERE p.tenantId=${dbGuid(tenantId)} AND p.groupId=${dbGuid(input.targetGroupId)} AND p.productId=${dbProductId(row.productId)}
          AND p.enabled=1 AND g.enabled=1 AND g.directoryStatus='active'
          AND (g.approvalSource='deployment' OR EXISTS(SELECT 1 FROM dbo.portalApiKeyCatalogProduct c
            WHERE c.tenantId=p.tenantId AND c.productId=p.productId AND c.enabled=1) OR EXISTS(SELECT 1 FROM dbo.portalApiKey existingKey WHERE existingKey.tenantId=p.tenantId AND existingKey.groupId=p.groupId AND existingKey.productId=p.productId AND existingKey.state<>'deleted'))`.execute(transaction)).rows[0];
      if (!matchesCurrentGroup(target, input.targetGroupId, policy)) throw new ApiKeyError("TargetGroupNotAllowed", 403);
      const count = (await sql<{ amount: number }>`SELECT COUNT(*) AS amount FROM dbo.portalApiKey
        WHERE tenantId=${dbGuid(tenantId)} AND groupId=${dbGuid(input.targetGroupId)} AND productId=${dbProductId(row.productId)}
          AND state<>'deleted'`.execute(transaction)).rows[0]?.amount ?? 0;
      if (count >= target!.maxSubscriptions) throw new ApiKeyError("TargetSubscriptionLimitReached", 409);
      await sql`UPDATE dbo.portalApiKey SET groupId=${input.targetGroupId}, updatedAt=SYSUTCDATETIME()
        WHERE tenantId=${dbGuid(tenantId)} AND id=${dbKeyId(id)}`.execute(transaction);
      await sql`INSERT INTO dbo.portalApiKeyTransferAudit(id,tenantId,apiKeyId,sourceGroupId,targetGroupId,actorObjectId)
        VALUES(${randomUUID()},${tenantId},${id},${row.groupId},${input.targetGroupId},${objectId})`.execute(transaction);
      const updated = await selectKey(transaction, tenantId, id);
      if (!updated) throw new ApiKeyError("KeyStoreUnavailable", 503);
      return publicRecord(updated);
    }));
  }
}

let repository: ApiKeyRepository | undefined;
/** リクエスト時に初めてSQLプールを用意し、ビルド中は資格情報を読み込みません。 */
export function getApiKeyRepository(): ApiKeyRepository {
  repository ??= new SqlApiKeyRepository(new Kysely<Record<string, unknown>>(createAuthDatabase(getSqlEnvironment())));
  return repository;
}
