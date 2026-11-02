import { sql, type Kysely } from "kysely";
import { z } from "zod";
import { groupNamePatternSchema } from "../lib/api-keys/group-policy.ts";

const productSchema = z.object({
  productId: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/),
  displayName: z.string().trim().min(1).max(255),
  maxSubscriptions: z.number().int().min(1).max(1000),
}).strict();

/** 配備正本からだけ設定する、基盤管理グループと通常発行可能な製品です。 */
export const apiKeyAdministrationConfigurationSchema = z.object({
  tenantId: z.uuid().transform((value) => value.toLowerCase()),
  platformGroupId: z.uuid().transform((value) => value.toLowerCase()),
  groupNamePattern: groupNamePatternSchema,
  products: z.array(productSchema).min(1).max(100)
    .refine((products) => new Set(products.map((product) => product.productId)).size === products.length),
}).strict();

/** 基盤管理の正本を、アプリ実行時の環境変数と混同しない設定型です。 */
export type ApiKeyAdministrationConfiguration = z.infer<typeof apiKeyAdministrationConfigurationSchema>;

/** 明示された配備設定だけを読み、通常の認証DB初期化では管理グループを推測しません。 */
export function readApiKeyAdministrationConfiguration(environment: Readonly<Record<string, string | undefined>>): ApiKeyAdministrationConfiguration | null {
  if (environment.PORTAL_API_KEY_PLATFORM_GROUP_ID === undefined
      && environment.PORTAL_API_KEY_CATALOG_PRODUCTS === undefined
      && environment.PORTAL_API_KEY_GROUP_NAME_PATTERN === undefined) return null;
  return apiKeyAdministrationConfigurationSchema.parse({
    tenantId: environment.PORTAL_API_KEY_TENANT_ID,
    platformGroupId: environment.PORTAL_API_KEY_PLATFORM_GROUP_ID,
    groupNamePattern: environment.PORTAL_API_KEY_GROUP_NAME_PATTERN,
    products: JSON.parse(environment.PORTAL_API_KEY_CATALOG_PRODUCTS ?? ""),
  });
}

/**
 * 管理用SQL IDで基盤管理グループと承認製品を登録します。
 * @remarks Graphの観測状態・購読・操作履歴は変更しません。製品が設定から消えた場合はカタログを無効化します。
 */
export async function configureApiKeyAdministration(database: Kysely<Record<string, unknown>>, input: ApiKeyAdministrationConfiguration): Promise<void> {
  const settings = apiKeyAdministrationConfigurationSchema.parse(input);
  await database.transaction().execute(async (transaction) => {
    await sql`DECLARE @result int;
      EXEC @result = sp_getapplock @Resource=N'portal-api-key-registry', @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=20000;
      IF @result < 0 THROW 51000, 'API key administration is busy', 1;`.execute(transaction);
    const group = await sql`SELECT groupId FROM dbo.portalApiKeyGroup
      WHERE tenantId=${settings.tenantId} AND groupId=${settings.platformGroupId} AND enabled=1 AND approvalSource='deployment'`.execute(transaction);
    if (group.rows.length !== 1) throw new Error("Platform group must own the configured foundation verification subscriptions");
    await sql`UPDATE dbo.portalApiKeyAdministration
      SET platformGroupId=${settings.platformGroupId}, groupNamePattern=${settings.groupNamePattern}, updatedAt=SYSUTCDATETIME()
      WHERE tenantId=${settings.tenantId};
      IF @@ROWCOUNT=0 INSERT INTO dbo.portalApiKeyAdministration (tenantId,platformGroupId,groupNamePattern)
        VALUES (${settings.tenantId},${settings.platformGroupId},${settings.groupNamePattern});`.execute(transaction);
    await sql`UPDATE dbo.portalApiKeyCatalogProduct SET enabled=0,updatedAt=SYSUTCDATETIME() WHERE tenantId=${settings.tenantId}`.execute(transaction);
    for (const product of settings.products) {
      await sql`UPDATE dbo.portalApiKeyCatalogProduct
        SET displayName=${product.displayName},maxSubscriptions=${product.maxSubscriptions},enabled=1,updatedAt=SYSUTCDATETIME()
        WHERE tenantId=${settings.tenantId} AND productId=${product.productId};
        IF @@ROWCOUNT=0 INSERT INTO dbo.portalApiKeyCatalogProduct (tenantId,productId,displayName,maxSubscriptions)
          VALUES (${settings.tenantId},${product.productId},${product.displayName},${product.maxSubscriptions});`.execute(transaction);
    }
  });
}
