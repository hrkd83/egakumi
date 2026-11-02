import { sql, type Kysely } from "kysely";
import { z } from "zod";
import { RELEASED_API_PRODUCT_ID } from "../lib/api-keys/contracts.ts";

/** 配備担当者が承認した所有グループと製品です。本人の所属情報やキーは含めません。 */
export const apiKeyGroupConfigurationSchema = z.array(z.object({
  groupId: z.uuid().transform((value) => value.toLowerCase()),
  products: z.array(z.object({
    productId: z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/),
    displayName: z.string().trim().min(1).max(255).optional(),
    maxSubscriptions: z.number().int().min(1).max(1000).default(5),
  }).strict()).min(1).max(100).refine((products) => new Set(products.map((item) => item.productId)).size === products.length),
}).strict()).max(1000).refine((groups) => new Set(groups.map((item) => item.groupId)).size === groups.length);

/** 検証済みのグループ承認設定です。 */
export type ApiKeyGroupConfiguration = z.infer<typeof apiKeyGroupConfigurationSchema>;

/** 配備ジョブが設定を渡したときだけ登録を更新し、従来の単独マイグレーションでは維持します。 */
export function readApiKeyGroupConfiguration(environment: Readonly<Record<string, string | undefined>>) {
  if (environment.PORTAL_API_KEY_GROUPS === undefined && environment.PORTAL_API_KEY_TENANT_ID === undefined) return null;
  const tenantId = z.uuid().parse(environment.PORTAL_API_KEY_TENANT_ID).toLowerCase();
  const groups = apiKeyGroupConfigurationSchema.parse(JSON.parse(environment.PORTAL_API_KEY_GROUPS ?? ""));
  return { tenantId, groups };
}

/**
 * 配備時の管理用SQLユーザーだけが、正本で承認された所有グループと製品を登録します。
 * @remarks 消えた承認は無効化し、既存の購読・監査行を削除しません。アプリには設定の書込権限を付けません。
 */
export async function configureApiKeyGroups(database: Kysely<Record<string, unknown>>, tenantId: string, groups: ApiKeyGroupConfiguration): Promise<void> {
  const tenant = z.uuid().parse(tenantId).toLowerCase();
  const approved = apiKeyGroupConfigurationSchema.parse(groups);
  await database.transaction().execute(async (transaction) => {
    await sql`DECLARE @result int;
      EXEC @result = sp_getapplock @Resource=N'portal-api-key-registry', @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=20000;
      IF @result < 0 THROW 51000, 'API key registry is busy', 1;`.execute(transaction);
    // 登録の無効化と購読作成が競合しないよう、発行側と同じ製品ロックも取得します。
    const current = (await sql<{ groupId: string; productId: string }>`SELECT groupId, productId FROM dbo.portalApiKeyProduct WHERE tenantId=${tenant}`.execute(transaction)).rows;
    const pairs = [...new Set([...current.map((item) => `${item.groupId}:${item.productId}`),
      ...approved.flatMap((group) => group.products.map((product) => `${group.groupId}:${product.productId}`))])].sort();
    for (const pair of pairs) {
      await sql`DECLARE @result int;
        EXEC @result = sp_getapplock @Resource=${`portal-api-key-cap:${tenant}:${pair}`}, @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=20000;
        IF @result < 0 THROW 51000, 'API key approval is busy', 1;`.execute(transaction);
    }
    // 検証用の承認だけを再設定します。通常製品はカタログと個別の停止設定が管理するため、
    // 配備用グループの検証製品一覧に含まれなくても既存承認を無効化しません。
    // Graphから登録したグループも、検証設定へ全件列挙する対象ではありません。
    await sql`UPDATE p SET enabled=0, updatedAt=SYSUTCDATETIME()
      FROM dbo.portalApiKeyProduct p JOIN dbo.portalApiKeyGroup g ON g.tenantId=p.tenantId AND g.groupId=p.groupId
      WHERE p.tenantId=${tenant} AND g.approvalSource='deployment' AND p.productId<>${RELEASED_API_PRODUCT_ID}`.execute(transaction);
    await sql`UPDATE dbo.portalApiKeyGroup SET enabled=0, updatedAt=SYSUTCDATETIME()
      WHERE tenantId=${tenant} AND approvalSource='deployment'`.execute(transaction);
    for (const group of approved) {
      // 表示名の正本はEntraです。再配備でGraphの観測値を上書きせず、新規行だけIDを仮表示します。
      await sql`UPDATE dbo.portalApiKeyGroup SET enabled=1, approvalSource='deployment', updatedAt=SYSUTCDATETIME()
        WHERE tenantId=${tenant} AND groupId=${group.groupId};
        IF @@ROWCOUNT=0 INSERT INTO dbo.portalApiKeyGroup (tenantId, groupId, displayName)
        VALUES (${tenant}, ${group.groupId}, ${group.groupId});`.execute(transaction);
      for (const product of group.products) {
        const name = product.displayName ?? product.productId;
        await sql`UPDATE dbo.portalApiKeyProduct SET displayName=${name}, maxSubscriptions=${product.maxSubscriptions}, enabled=1, updatedAt=SYSUTCDATETIME()
          WHERE tenantId=${tenant} AND groupId=${group.groupId} AND productId=${product.productId};
          IF @@ROWCOUNT=0 INSERT INTO dbo.portalApiKeyProduct (tenantId, groupId, productId, displayName, maxSubscriptions)
          VALUES (${tenant}, ${group.groupId}, ${product.productId}, ${name}, ${product.maxSubscriptions});`.execute(transaction);
      }
    }
  });
}
