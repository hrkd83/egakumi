import { sql, type Kysely } from "kysely";
import { randomUUID } from "node:crypto";
import { z } from "zod";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import type { ApiKeyApim, ManagedApiKeySubscription } from "../lib/api-keys/apim.ts";
import { getApiKeyEnvironment, type ApiKeyEnvironment } from "../lib/api-keys/environment.ts";
import { readApiKeyGroupPolicy } from "../lib/api-key-administration/repository.ts";
import { apiKeySubscriptionImportHash, apiKeySubscriptionImportSchema, finalizeApiKeySubscriptionReservations,
  lockApiKeyImportProductSlots, readApiKeyImportRecord, reserveApiKeySubscriptionImports,
  type ApiKeySubscriptionImportResult } from "./api-key-subscription-import.ts";

const verificationSubscriptionSchema = z.object({
  subscriptionId: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$/).refine((id) => id.toLowerCase() !== "master"),
  productId: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/), displayName: z.string().trim().min(1).max(80),
}).strict();
const configurationSchema = z.object({ tenantId: z.uuid().transform((id) => id.toLowerCase()),
  platformGroupId: z.uuid().transform((id) => id.toLowerCase()), apimResourceId: z.string().min(1).max(2048),
  subscriptions: z.array(verificationSubscriptionSchema).max(1000)
    .refine((records) => new Set(records.map((record) => record.subscriptionId.toLowerCase())).size === records.length),
}).strict();

/** 配備正本で列挙した、基盤管理グループ用の検証購読だけを作成・登録する設定です。 */
export type ApiKeyVerificationSubscriptionConfiguration = z.infer<typeof configurationSchema>;

/** 既存台帳の読み飛ばしと、今回作成・登録した件数を秘密情報なしで返します。 */
export type ApiKeyVerificationSubscriptionResult = ApiKeySubscriptionImportResult & { created: number };

/** 未設定では何も作らず、設定済みの配備正本だけを読み取ります。 */
export function readApiKeyVerificationSubscriptions(environment: Readonly<Record<string, string | undefined>>): ApiKeyVerificationSubscriptionConfiguration | null {
  if (environment.PORTAL_API_KEY_VERIFICATION_SUBSCRIPTIONS === undefined) return null;
  const configuration = configurationSchema.parse({ tenantId: environment.PORTAL_API_KEY_TENANT_ID,
    platformGroupId: environment.PORTAL_API_KEY_PLATFORM_GROUP_ID, apimResourceId: environment.PORTAL_APIM_RESOURCE_ID,
    subscriptions: JSON.parse(environment.PORTAL_API_KEY_VERIFICATION_SUBSCRIPTIONS) });
  // import側と同じARM宛先検証を使い、別形式のURLや全APIスコープを認めません。
  apiKeySubscriptionImportSchema.parse({ ...configuration, subscriptions: [] });
  return configuration;
}

/** SQL初期化用MIとAPIM操作用MIを混同せず、後者をAPIMクライアントへ明示します。 */
export function readApiKeyBootstrapApimEnvironment(environment: Readonly<Record<string, string | undefined>>): ApiKeyEnvironment {
  const clientId = z.uuid().parse(environment.PORTAL_API_KEY_APIM_CLIENT_ID);
  return getApiKeyEnvironment({ ...environment, AZURE_CLIENT_ID: clientId });
}

/** APIMで確認したID・製品スコープ・実行可能な状態だけを、登録マニフェストへ採用します。 */
function verifiedSubscription(configuration: ApiKeyVerificationSubscriptionConfiguration,
  record: z.infer<typeof verificationSubscriptionSchema>, actual: ManagedApiKeySubscription) {
  const expectedScope = `${configuration.apimResourceId}/products/${record.productId}`;
  if (actual.id.toLowerCase() !== record.subscriptionId.toLowerCase() || actual.scope.toLowerCase() !== expectedScope.toLowerCase()
      || (actual.state !== "active" && actual.state !== "suspended")) throw new Error("Verification subscription metadata differs from the configured product");
  return { subscriptionId: actual.id, productId: record.productId, displayName: record.displayName,
    state: actual.state as "active" | "suspended", scope: actual.scope };
}

/**
 * 初期構築の検証購読を、明示設定と現在のSQL台帳を照合して初回だけ作ります。
 * @remarks 移管・削除済みはAPIMへ照会しません。creating予約は安全に再開します。
 * 作成前にSQLの発行枠を予約し、HTTP中はトランザクションを開かずSessionのキー排他を保持します。
 */
export async function bootstrapApiKeySubscriptions(database: Kysely<Record<string, unknown>>, input: ApiKeyVerificationSubscriptionConfiguration,
  apim: Pick<ApiKeyApim, "scopeFor" | "get" | "create">): Promise<ApiKeyVerificationSubscriptionResult> {
  const configuration = configurationSchema.parse(input);
  apiKeySubscriptionImportSchema.parse({ ...configuration, subscriptions: [] });
  const policy = await readApiKeyGroupPolicy(database, configuration.tenantId);
  if (policy.platformGroupId !== configuration.platformGroupId) throw new Error("Verification subscriptions must belong to the configured platform group");
  let skipped = 0, created = 0;
  const pending: { record: z.infer<typeof verificationSubscriptionSchema>; actual: ManagedApiKeySubscription | null }[] = [];
  for (const record of configuration.subscriptions) {
    const existing = await readApiKeyImportRecord(database, configuration.tenantId, record.subscriptionId);
    if (existing && (existing.state !== "creating" || existing.creationSource !== "deployment"
        || existing.groupId !== configuration.platformGroupId || existing.productId !== record.productId)) { skipped++; continue; }
    if (apim.scopeFor(record.productId).toLowerCase() !== `${configuration.apimResourceId}/products/${record.productId}`.toLowerCase()) {
      throw new Error("APIM verification client differs from the configured resource");
    }
    pending.push({ record, actual: null });
  }
  // 全既存メタデータを先に確認し、別製品の購読を見つけた場合は新規発行を始めません。
  for (const item of pending) {
    item.actual = await apim.get(item.record.subscriptionId);
    if (item.actual) verifiedSubscription(configuration, item.record, item.actual);
  }
  const intent = apiKeySubscriptionImportSchema.parse({ ...configuration, subscriptions: pending.map((item) => ({
    ...item.record, state: item.actual?.state ?? "active", scope: `${configuration.apimResourceId}/products/${item.record.productId}`,
  })) });
  const reservation = await reserveApiKeySubscriptionImports(database, intent, configuration.apimResourceId);
  skipped += reservation.skipped;
  let releaseFailed = false;
  try {
    return await database.connection().execute(async (connection) => {
      const attempted: string[] = [];
      try {
        // 同じ接続に全キーの排他を一定順で保持します。Portalの削除・他の配備はここを越えられません。
        for (const id of [...new Set(reservation.reserved.map((record) => record.id))].sort()) {
          const key = `portal-api-key:${configuration.tenantId}:${id}`; attempted.push(key);
          await sql`DECLARE @result int;
            EXEC @result=sp_getapplock @Resource=${key},@LockMode='Exclusive',@LockOwner='Session',@LockTimeout=20000;
            IF @result<0 THROW 51000,'API subscription is busy',1;`.execute(connection);
        }
        const completed = [];
        for (const reserved of reservation.reserved) {
          const item = pending.find((value) => value.record.subscriptionId.toLowerCase() === reserved.subscriptionId.toLowerCase())!;
          const existing = await readApiKeyImportRecord(connection, configuration.tenantId, reserved.subscriptionId);
          if (!existing || existing.id !== reserved.id || existing.state !== "creating" || existing.creationSource !== "deployment"
              || existing.groupId !== configuration.platformGroupId || existing.productId !== item.record.productId) { skipped++; continue; }
          // 予約後の現在値を読み直します。HTTP中はSQLトランザクションを保持しません。
          let actual = await apim.get(item.record.subscriptionId);
          if (!actual) {
            try { actual = await apim.create(item.record.subscriptionId, item.record.productId, item.record.displayName); created++; }
            catch (error) {
              if (!(error instanceof ApiKeyError) || error.code !== "UpstreamConflict") throw error;
              actual = await apim.get(item.record.subscriptionId); if (!actual) throw error;
            }
          }
          completed.push(verifiedSubscription(configuration, item.record, actual));
        }
        const manifest = apiKeySubscriptionImportSchema.parse({ ...configuration, subscriptions: completed });
        const runId = randomUUID(), manifestHash = apiKeySubscriptionImportHash(manifest);
        const result = await connection.transaction().execute(async (transaction) => {
          await lockApiKeyImportProductSlots(transaction, manifest);
          return finalizeApiKeySubscriptionReservations(transaction, manifest, configuration.apimResourceId, runId, manifestHash);
        });
        return { ...result, skipped: result.skipped + skipped, created };
      } finally {
        for (const key of attempted.reverse()) {
          try {
            await sql`DECLARE @result int;
              EXEC @result=sp_releaseapplock @Resource=${key},@LockOwner='Session';
              IF @result<0 THROW 51000,'API subscription lock release failed',1;`.execute(connection);
          } catch { releaseFailed = true; }
        }
        if (releaseFailed) throw new Error("API subscription lock release failed");
      }
    });
  } catch (error) {
    // 解放を確認できない接続はプールへ残さず、管理ジョブ全体を停止します。
    if (releaseFailed) await database.destroy();
    throw error;
  }
}
