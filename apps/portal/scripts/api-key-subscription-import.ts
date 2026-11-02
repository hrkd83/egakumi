import { createHash, randomUUID } from "node:crypto";
import { sql, type Kysely, type Transaction } from "kysely";
import { z } from "zod";
import { readApiKeyGroupPolicy } from "../lib/api-key-administration/repository.ts";

const uuid = z.uuid().transform((value) => value.toLowerCase());
const subscriptionId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$/).refine((value) => value.toLowerCase() !== "master");
const productId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/);
const apimResourceId = z.string().max(2048).regex(
  /^\/subscriptions\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/resourceGroups\/[A-Za-z0-9_.()-]{1,90}\/providers\/Microsoft\.ApiManagement\/service\/[A-Za-z][A-Za-z0-9-]{0,49}$/i,
).transform((value) => value.toLowerCase());
const recordSchema = z.object({ subscriptionId, productId,
  displayName: z.string().trim().min(1).max(80), state: z.enum(["active", "suspended"]), scope: z.string().min(1).max(2048),
}).strict();

/** APIMで実在・製品・状態を確認した購読だけを列挙する、秘密値を持たない配備用マニフェストです。 */
export const apiKeySubscriptionImportSchema = z.object({
  schemaVersion: z.literal(1).default(1), tenantId: uuid, apimResourceId, platformGroupId: uuid,
  subscriptions: z.array(recordSchema).max(1000),
}).strict().superRefine((manifest, context) => {
  const ids = manifest.subscriptions.map((record) => record.subscriptionId.toLowerCase());
  if (new Set(ids).size !== ids.length) context.addIssue({ code: "custom", message: "Subscription IDs must be unique" });
  manifest.subscriptions.forEach((record, index) => {
    if (record.scope.toLowerCase() !== `${manifest.apimResourceId}/products/${record.productId.toLowerCase()}`) {
      context.addIssue({ code: "custom", path: ["subscriptions", index, "scope"], message: "Subscription scope must match its configured product" });
    }
  });
});

/** 管理者が明示したAPIM購読のメタデータです。サブスクキー・個人の識別子は含めません。 */
export type ApiKeySubscriptionImport = z.infer<typeof apiKeySubscriptionImportSchema>;

/** 配備正本とマニフェストを独立に照合するための、登録実行設定です。 */
export type ApiKeySubscriptionImportConfiguration = { manifest: ApiKeySubscriptionImport; expectedApimResourceId: string };

/** 登録済みの購読は変更せず、今回追加した件数と読み飛ばした件数を報告します。 */
export type ApiKeySubscriptionImportResult = { runId: string; manifestHash: string; imported: number; skipped: number };

/** マニフェストのAPIMが配備対象と同じことを確認し、別環境のメタデータ混入を拒否します。 */
function parseImport(input: ApiKeySubscriptionImport, expected: string): ApiKeySubscriptionImport {
  const manifest = apiKeySubscriptionImportSchema.parse(input);
  if (manifest.apimResourceId !== apimResourceId.parse(expected)) throw new Error("APIM import target differs from the configured resource");
  return manifest;
}

/** 配備ジョブが明示した場合だけ読み込み、未指定を全APIM購読の自動登録へ読み替えません。 */
export function readApiKeySubscriptionImport(environment: Readonly<Record<string, string | undefined>>): ApiKeySubscriptionImportConfiguration | null {
  if (environment.PORTAL_API_KEY_SUBSCRIPTION_IMPORT === undefined) return null;
  const expected = apimResourceId.parse(environment.PORTAL_APIM_RESOURCE_ID);
  const manifest = parseImport(JSON.parse(environment.PORTAL_API_KEY_SUBSCRIPTION_IMPORT), expected);
  const tenantId = uuid.parse(environment.PORTAL_API_KEY_TENANT_ID);
  if (manifest.tenantId !== tenantId) throw new Error("APIM import tenant differs from the configured tenant");
  return { manifest, expectedApimResourceId: expected };
}

/**
 * 同じテナント・APIM・購読の組を、毎回同じポータル内部IDへ変換します。
 * @remarks UUIDv5のURL名前空間を使います。ARM IDの大文字小文字で別の購読を作りません。
 */
export function stableApiKeyImportId(input: { tenantId: string; apimResourceId: string; subscriptionId: string }): string {
  const tenant = uuid.parse(input.tenantId), resource = apimResourceId.parse(input.apimResourceId);
  const actualId = subscriptionId.parse(input.subscriptionId).toLowerCase();
  const namespace = Buffer.from("6ba7b8119dad11d180b400c04fd430c8", "hex");
  const digest = createHash("sha1").update(namespace).update(`urn:egakumi:portal:apim-subscription:v1:${JSON.stringify([tenant, resource, actualId])}`).digest();
  digest[6] = (digest[6]! & 0x0f) | 0x50;
  digest[8] = (digest[8]! & 0x3f) | 0x80;
  const value = digest.subarray(0, 16).toString("hex");
  return `portal-${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
}

/** 表示順やARM ID表記の違いを除いた、監査用のメタデータ要約を生成します。 */
export function apiKeySubscriptionImportHash(input: ApiKeySubscriptionImport): string {
  const manifest = apiKeySubscriptionImportSchema.parse(input);
  const records = manifest.subscriptions.map((record) => ({ ...record, subscriptionId: record.subscriptionId.toLowerCase(), scope: record.scope.toLowerCase() }))
    .sort((left, right) => left.subscriptionId.localeCompare(right.subscriptionId));
  return createHash("sha256").update(JSON.stringify({ schemaVersion: 1, tenantId: manifest.tenantId,
    apimResourceId: manifest.apimResourceId, platformGroupId: manifest.platformGroupId, subscriptions: records })).digest("hex");
}

/** 索引列と同じ型で比較し、別グループや別購読の行全体を暗黙変換で走査しません。 */
const dbUuid = (value: string) => sql`CAST(${value} AS char(36))`;
/** 実APIM購読IDの長さを保ち、登録済みマッピングを完全一致で調べます。 */
const dbSubscriptionId = (value: string) => sql`CAST(${value} AS varchar(256))`;
/** 製品承認の索引型に合わせて、設定済みの製品だけを照合します。 */
const dbProductId = (value: string) => sql`CAST(${value} AS varchar(80))`;

/** 作成予約の再開可否だけに使う台帳情報です。秘密値や個人のOIDは読みません。 */
export type ApiKeyImportRecord = { id: string; groupId: string; productId: string; state: string; creationSource: string; apimSubscriptionId: string };
type ImportTransaction = Transaction<Record<string, unknown>>;

/** 削除済みも含む実IDの対応を読み、再配備で同じ購読を作り直さない判断に使います。 */
export async function readApiKeyImportRecord(database: Kysely<Record<string, unknown>>, tenantId: string,
  actualId: string): Promise<ApiKeyImportRecord | undefined> {
  return (await sql<ApiKeyImportRecord>`SELECT id,groupId,productId,state,creationSource,apimSubscriptionId FROM dbo.portalApiKey
    WHERE tenantId=${dbUuid(tenantId)} AND apimSubscriptionId=${dbSubscriptionId(actualId)}`.execute(database)).rows[0];
}

/** 台帳のキー変更と同じ排他名を使い、必ず製品枠より前に取得します。 */
async function lockImportKeys(transaction: ImportTransaction, manifest: ApiKeySubscriptionImport): Promise<void> {
  const keys = new Set<string>();
  for (const record of manifest.subscriptions) {
    const existing = await readApiKeyImportRecord(transaction, manifest.tenantId, record.subscriptionId);
    keys.add(existing?.id ?? stableApiKeyImportId({ ...manifest, subscriptionId: record.subscriptionId }));
  }
  for (const id of [...keys].sort()) await sql`DECLARE @result int;
    EXEC @result=sp_getapplock @Resource=${`portal-api-key:${manifest.tenantId}:${id}`},
      @LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=20000;
    IF @result<0 THROW 51000,'API subscription is busy',1;`.execute(transaction);
}

/** 複数製品の枠を一定順で取得し、通常発行・移管・設定更新と競合しても枠を守ります。 */
export async function lockApiKeyImportProductSlots(transaction: ImportTransaction, manifest: ApiKeySubscriptionImport): Promise<void> {
  for (const product of [...new Set(manifest.subscriptions.map((record) => record.productId))].sort()) await sql`DECLARE @result int;
    EXEC @result=sp_getapplock @Resource=${`portal-api-key-cap:${manifest.tenantId}:${manifest.platformGroupId}:${product}`},
      @LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=20000;
    IF @result<0 THROW 51000,'API subscription approval is busy',1;`.execute(transaction);
}

/** 配備正本の管理グループが現在も同じことを、更新するトランザクション内で確認します。 */
async function requirePlatform(transaction: ImportTransaction, manifest: ApiKeySubscriptionImport): Promise<void> {
  if ((await readApiKeyGroupPolicy(transaction, manifest.tenantId)).platformGroupId !== manifest.platformGroupId) {
    throw new Error("Imported subscription owner must be the configured platform group");
  }
}

/** 製品の明示承認を照合し、新しい予約・登録のときだけ発行済み件数を加味します。 */
async function requireApproval(transaction: ImportTransaction, manifest: ApiKeySubscriptionImport, product: string, newRecord: boolean): Promise<void> {
  const approved = (await sql<{ maxSubscriptions: number }>`SELECT p.maxSubscriptions FROM dbo.portalApiKeyProduct p
    JOIN dbo.portalApiKeyGroup g ON g.tenantId=p.tenantId AND g.groupId=p.groupId
    WHERE p.tenantId=${dbUuid(manifest.tenantId)} AND p.groupId=${dbUuid(manifest.platformGroupId)} AND p.productId=${dbProductId(product)}
      AND p.enabled=1 AND g.enabled=1 AND g.approvalSource='deployment'`.execute(transaction)).rows[0];
  if (!approved) throw new Error("Imported subscription product must be explicitly approved for the platform group");
  if (!newRecord) return;
  const amount = (await sql<{ amount: number }>`SELECT COUNT(*) AS amount FROM dbo.portalApiKey
    WHERE tenantId=${dbUuid(manifest.tenantId)} AND groupId=${dbUuid(manifest.platformGroupId)}
      AND productId=${dbProductId(product)} AND state<>'deleted'`.execute(transaction)).rows[0]?.amount;
  if (typeof amount !== "number" || amount >= approved.maxSubscriptions) throw new Error("Imported subscription would exceed the approved group limit");
}

/** 通常のユーザー操作監査へ架空の本人を記録せず、配備のメタデータだけを追記します。 */
async function recordImportAudit(transaction: ImportTransaction, manifest: ApiKeySubscriptionImport,
  record: ApiKeySubscriptionImport["subscriptions"][number], id: string, runId: string, manifestHash: string): Promise<void> {
  await sql`INSERT INTO dbo.portalApiKeyImportAudit(id,runId,manifestHash,tenantId,apiKeyId,apimResourceId,
    apimSubscriptionId,apimScope,groupId,productId)
    VALUES(${randomUUID()},${runId},${manifestHash},${manifest.tenantId},${id},${manifest.apimResourceId},
      ${record.subscriptionId},${record.scope},${manifest.platformGroupId},${record.productId})`.execute(transaction);
}

/** APIM操作前の予約を保存します。途中で失敗しても未管理のAPIM購読にせず、creatingから再開できます。 */
export async function reserveApiKeySubscriptionImports(database: Kysely<Record<string, unknown>>, input: ApiKeySubscriptionImport,
  expectedApimResourceId: string): Promise<{ reserved: { id: string; subscriptionId: string }[]; skipped: number }> {
  const manifest = parseImport(input, expectedApimResourceId);
  return database.transaction().execute(async (transaction) => {
    await lockImportKeys(transaction, manifest);
    await lockApiKeyImportProductSlots(transaction, manifest);
    await requirePlatform(transaction, manifest);
    const reserved: { id: string; subscriptionId: string }[] = []; let skipped = 0;
    for (const record of manifest.subscriptions) {
      const existing = await readApiKeyImportRecord(transaction, manifest.tenantId, record.subscriptionId);
      if (existing) {
        if (existing.state === "creating" && existing.creationSource === "deployment"
            && existing.groupId === manifest.platformGroupId && existing.productId === record.productId) {
          await requireApproval(transaction, manifest, record.productId, false);
          reserved.push({ id: existing.id, subscriptionId: record.subscriptionId });
        } else skipped++;
        continue;
      }
      await requireApproval(transaction, manifest, record.productId, true);
      const id = stableApiKeyImportId({ ...manifest, subscriptionId: record.subscriptionId });
      await sql`INSERT INTO dbo.portalApiKey(id,tenantId,groupId,productId,displayName,state,budgetMode,
        createdByObjectId,idempotencyKey,apimSubscriptionId,creationSource)
        VALUES(${id},${manifest.tenantId},${manifest.platformGroupId},${record.productId},${record.displayName},${"creating"},'unlimited',
          NULL,${id.slice(7)},${record.subscriptionId},'deployment')`.execute(transaction);
      reserved.push({ id, subscriptionId: record.subscriptionId });
    }
    return { reserved, skipped };
  });
}

/**
 * 作成済みの予約だけを確定し、削除・移管・通常発行の行を更新しません。
 * @remarks 呼出元が同じ内部keyの排他と製品枠の排他を取得済みであることを前提とします。
 */
export async function finalizeApiKeySubscriptionReservations(transaction: ImportTransaction, input: ApiKeySubscriptionImport,
  expectedApimResourceId: string, runId: string, manifestHash: string): Promise<ApiKeySubscriptionImportResult> {
  const manifest = parseImport(input, expectedApimResourceId);
  await requirePlatform(transaction, manifest);
  let imported = 0, skipped = 0;
  for (const record of manifest.subscriptions) {
    const existing = await readApiKeyImportRecord(transaction, manifest.tenantId, record.subscriptionId);
    if (!existing || existing.state !== "creating" || existing.creationSource !== "deployment"
        || existing.groupId !== manifest.platformGroupId || existing.productId !== record.productId) { skipped++; continue; }
    await requireApproval(transaction, manifest, record.productId, false);
    await sql`UPDATE dbo.portalApiKey SET state=${record.state},updatedAt=SYSUTCDATETIME()
      WHERE tenantId=${dbUuid(manifest.tenantId)} AND id=CAST(${existing.id} AS varchar(43)) AND state='creating'`.execute(transaction);
    await recordImportAudit(transaction, manifest, record, existing.id, runId, manifestHash); imported++;
  }
  return { runId, manifestHash, imported, skipped };
}

/**
 * 明示された既存検証購読を、基盤管理グループの台帳へ初回だけ登録します。
 * @remarks 管理用SQL ID専用です。APIMへ接続せず、キー取得・購読変更は行いません。
 * 既存行は所有者・削除状態・制限・監査を保持し、新規行は無制限で登録します。
 */
export async function importApiKeySubscriptions(database: Kysely<Record<string, unknown>>, input: ApiKeySubscriptionImport,
  expectedApimResourceId: string): Promise<ApiKeySubscriptionImportResult> {
  const manifest = parseImport(input, expectedApimResourceId);
  const runId = randomUUID(), manifestHash = apiKeySubscriptionImportHash(manifest);
  if (!manifest.subscriptions.length) return { runId, manifestHash, imported: 0, skipped: 0 };
  return database.transaction().execute(async (transaction) => {
    // 複数の配備で同じ実IDを同時登録せず、アプリの発行枠ロックは既存順序に合わせます。
    await sql`DECLARE @result int;
      EXEC @result=sp_getapplock @Resource=${`portal-api-key-subscription-import:${manifest.tenantId}`},
        @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=20000;
      IF @result<0 THROW 51000,'API subscription import is busy',1;`.execute(transaction);
    await lockImportKeys(transaction, manifest);
    await lockApiKeyImportProductSlots(transaction, manifest);
    await requirePlatform(transaction, manifest);
    let imported = 0, skipped = 0;
    for (const record of [...manifest.subscriptions].sort((left, right) => left.subscriptionId.toLowerCase().localeCompare(right.subscriptionId.toLowerCase()))) {
      const existing = await readApiKeyImportRecord(transaction, manifest.tenantId, record.subscriptionId);
      if (existing) {
        const result = await finalizeApiKeySubscriptionReservations(transaction, { ...manifest, subscriptions: [record] }, expectedApimResourceId, runId, manifestHash);
        imported += result.imported; skipped += result.skipped; continue;
      }
      await requireApproval(transaction, manifest, record.productId, true);
      const id = stableApiKeyImportId({ ...manifest, subscriptionId: record.subscriptionId });
      await sql`INSERT INTO dbo.portalApiKey(id,tenantId,groupId,productId,displayName,state,budgetMode,
        createdByObjectId,idempotencyKey,apimSubscriptionId,creationSource)
        VALUES(${id},${manifest.tenantId},${manifest.platformGroupId},${record.productId},${record.displayName},${record.state},'unlimited',
          NULL,${id.slice(7)},${record.subscriptionId},'deployment')`.execute(transaction);
      await recordImportAudit(transaction, manifest, record, id, runId, manifestHash);
      imported++;
    }
    return { runId, manifestHash, imported, skipped };
  });
}
