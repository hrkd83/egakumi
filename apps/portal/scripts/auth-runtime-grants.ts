import { sql, type Kysely, type Transaction } from "kysely";
import { z } from "zod";

type Database = Kysely<Record<string, unknown>>;
type GrantConnection = Database | Transaction<Record<string, unknown>>;
type Environment = Readonly<Record<string, string | undefined>>;
type SqlRole = "runtime" | "management" | "profile";

/** 公開Portalのログイン、API認可および接続セッションに必要な権限です。 */
export const portalRuntimePermissions = {
  portalUser: "SELECT, INSERT, UPDATE, DELETE",
  portalSession: "SELECT, INSERT, UPDATE, DELETE",
  portalAccount: "SELECT, INSERT, UPDATE, DELETE",
  portalVerification: "SELECT, INSERT, UPDATE, DELETE",
  portalSchemaVersions: "SELECT",
  portalApiKeyAdministration: "SELECT",
  portalConnectionState: "SELECT, INSERT, UPDATE",
  portalApiAuthContext: "SELECT, INSERT, UPDATE",
  portalApiAuthorizationTransaction: "SELECT, INSERT, UPDATE, DELETE",
  portalApiActiveOperation: "SELECT, INSERT, DELETE",
  portalApiCallCleanup: "SELECT, INSERT, UPDATE, DELETE",
  portalSubscriptionAuthContext: "SELECT, INSERT, UPDATE",
  portalSubscriptionAuthorizationTransaction: "SELECT, INSERT, UPDATE, DELETE",
} as const;

/** 独立した購読管理サービスと配備ジョブが台帳・制限・監査に使う権限です。 */
export const portalManagementPermissions = {
  portalApiKeyGroup: "SELECT",
  portalApiKeyProduct: "SELECT",
  portalApiKey: "SELECT, INSERT, UPDATE",
  portalApiKeyAudit: "SELECT, INSERT",
  portalApiKeyLimit: "SELECT, INSERT, UPDATE",
  portalApiKeyLimitAudit: "SELECT, INSERT",
  portalApiKeyTransferAudit: "SELECT, INSERT",
  portalApiKeyAdministration: "SELECT",
  portalApiKeyCatalogProduct: "SELECT",
  portalApiKeyImportAudit: "INSERT",
  portalSchemaVersions: "SELECT",
  portalCompany: "SELECT, INSERT, UPDATE",
  portalCompanyAudit: "SELECT, INSERT",
  portalSubscriptionSyncAudit: "SELECT, INSERT",
} as const;

/** グループの観測・登録とGatewayのプロファイル照会に使う限定した操作です。 */
export const portalManagementProcedures = ["portalObserveApiKeyGroup", "portalEnsureApiKeyGroup"] as const;
export const portalProfileProcedures = ["portalReadSubscriptionProfile"] as const;

type Permissions = Readonly<Record<string, string>>;
type PrincipalPermission = { permission: string; isPlainGrant: boolean; isObject: boolean; object: string | null; grantor: string | null };

/** 配備側が指定するAzure SQL外部ユーザーのSQL名とEntraアプリケーションIDです。 */
export type RuntimeIdentity = { name: string; clientId: string; sid: Buffer };
export type PortalSqlIdentities = Record<SqlRole, RuntimeIdentity>;
const nonzeroClientId = z.uuid().refine((value) => value !== "00000000-0000-0000-0000-000000000000");
const principalName = z.string().regex(/^[A-Za-z][A-Za-z0-9_-]{0,127}$/);

/** Azure SQLの外部ユーザー用SIDを、EntraアプリIDのGUIDをlittle-endianに変換して作ります。 */
export function parseRuntimeIdentity(environment: Environment, role: SqlRole = "runtime"): RuntimeIdentity | null {
  const prefix = `PORTAL_SQL_${role.toUpperCase()}_`;
  const clientId = environment[`${prefix}CLIENT_ID`];
  const name = environment[`${prefix}USER_NAME`];
  if (!clientId && !name) return null;
  const parsed = z.object({ clientId: nonzeroClientId, name: principalName }).safeParse({ clientId, name });
  if (!parsed.success) throw new Error("Portal SQL execution identity configuration is invalid");
  const sid = Buffer.from(parsed.data.clientId.replaceAll("-", ""), "hex");
  sid.subarray(0, 4).reverse();
  sid.subarray(4, 6).reverse();
  sid.subarray(6, 8).reverse();
  return { name: parsed.data.name, clientId: parsed.data.clientId.toLowerCase(), sid };
}

/** 配備ではDDL、公開Portal、購読管理、プロファイル照会が異なる本人で動作することを検証します。 */
export function parseMigrationSqlIdentities(environment: Environment): PortalSqlIdentities {
  const schema = nonzeroClientId.safeParse(environment.AZURE_CLIENT_ID);
  const runtime = parseRuntimeIdentity(environment);
  const management = parseRuntimeIdentity(environment, "management");
  const profile = parseRuntimeIdentity(environment, "profile");
  if (!schema.success || !runtime || !management || !profile
      || new Set([schema.data.toLowerCase(), runtime.clientId, management.clientId, profile.clientId]).size !== 4
      || new Set([runtime.name, management.name, profile.name].map((name) => name.toLowerCase())).size !== 3) {
    throw new Error("Portal SQL schema, runtime, management, and profile identities must be nonzero and distinct");
  }
  return { runtime, management, profile };
}

/** 既存の外部ユーザーが設定と異なる場合は、そのユーザーを上書きせず停止します。 */
export function assertRuntimePrincipal(identity: RuntimeIdentity, principals: { name: string; sid: Uint8Array; type: string }[]) {
  if (principals.some((principal) => principal.name !== identity.name
    || principal.type !== "E" || !Buffer.from(principal.sid).equals(identity.sid))) {
    throw new Error("Existing Portal SQL principal differs from the configured identity");
  }
}

function rolePermissions(tables: Permissions, procedures: readonly string[] = []): Permissions {
  return { ...tables, ...Object.fromEntries(procedures.map((name) => [name, "EXECUTE"])) };
}

const allowedPermissions = {
  runtime: rolePermissions(portalRuntimePermissions),
  management: rolePermissions(portalManagementPermissions, portalManagementProcedures),
  profile: rolePermissions({}, portalProfileProcedures),
};

function hasPermission(policy: Permissions, entry: PrincipalPermission): boolean {
  return entry.object !== null && Object.hasOwn(policy, entry.object) && policy[entry.object].split(", ").includes(entry.permission);
}

/** 解消対象は、この配備が従来Public Portalに付与していた既知の権限です。 */
const legacyPublicPermissions = rolePermissions({
  ...portalManagementPermissions,
  portalApiKeyImportAudit: "",
}, portalManagementProcedures);

/** 単純な権限付与だけを許し、想定外の継承・広域権限は配備で書き換える前に止めます。 */
export async function reconcilePortalSqlPermissions(database: GrantConnection, name: string, role: SqlRole): Promise<void> {
  const principal = `[${principalName.parse(name)}]`;
  const inherited = await sql`SELECT 1 AS unexpected FROM sys.database_role_members WHERE member_principal_id=USER_ID(${name})
    UNION ALL SELECT 1 FROM sys.schemas WHERE principal_id=USER_ID(${name})
    UNION ALL SELECT 1 FROM sys.objects WHERE principal_id=USER_ID(${name})
    UNION ALL SELECT 1 FROM sys.database_principals WHERE owning_principal_id=USER_ID(${name})`.execute(database);
  if (inherited.rows.length) throw new Error("Portal SQL principal must not inherit roles or own database objects");

  const current = (await sql<PrincipalPermission>`SELECT p.permission_name AS [permission],
    CONVERT(bit, CASE WHEN p.state='G' THEN 1 ELSE 0 END) AS isPlainGrant,
    CONVERT(bit, CASE WHEN p.class=1 AND p.minor_id=0 AND s.name=N'dbo' THEN 1 ELSE 0 END) AS isObject,
    o.name AS [object], USER_NAME(p.grantor_principal_id) AS grantor
    FROM sys.database_permissions p LEFT JOIN sys.objects o ON p.class=1 AND p.major_id=o.object_id
    LEFT JOIN sys.schemas s ON s.schema_id=o.schema_id
    WHERE p.grantee_principal_id=USER_ID(${name}) AND NOT (p.class=0 AND p.permission_name=N'CONNECT' AND p.state='G')
  `.execute(database)).rows;
  const expected = allowedPermissions[role];
  const revoke = current.filter((entry) => !hasPermission(expected, entry));
  if (current.some((entry) => !entry.isObject || !entry.isPlainGrant)
      || revoke.some((entry) => role !== "runtime" || !hasPermission(legacyPublicPermissions, entry) || !entry.grantor)) {
    throw new Error("Portal SQL principal has grants outside its role");
  }
  for (const entry of revoke) {
    // ひとつの権限が複数の付与元に由来する場合も、確認した付与元ごとに解除します。
    const grantor = entry.grantor!.replaceAll("]", "]]");
    await sql.raw(`REVOKE ${entry.permission} ON OBJECT::dbo.[${entry.object}] FROM ${principal} AS [${grantor}]`).execute(database);
  }
  for (const [object, permissions] of Object.entries(expected)) {
    const missing = permissions.split(", ").filter((permission) => !current.some((entry) => entry.object === object && entry.permission === permission));
    if (missing.length) await sql.raw(`GRANT ${missing.join(", ")} ON OBJECT::dbo.[${object}] TO ${principal}`).execute(database);
  }
}

/** 初期化ジョブが限定権限を適用し、同じ接続先での再実行では付与差分だけを処理します。 */
export async function grantRuntimeAccess(database: Database, identities: Partial<PortalSqlIdentities>): Promise<void> {
  await database.transaction().execute(async (transaction) => {
    await sql`DECLARE @result int;
      EXEC @result = sp_getapplock @Resource=N'portal-auth-schema', @LockMode='Exclusive',
        @LockOwner='Transaction', @LockTimeout=20000;
      IF @result < 0 THROW 51000, 'Portal migration lock unavailable', 1;`.execute(transaction);
    for (const role of ["runtime", "management", "profile"] as const) {
      const identity = identities[role];
      if (!identity) continue;
      const found = await sql<{ name: string; sid: Uint8Array; type: string }>`SELECT name, sid, type FROM sys.database_principals
        WHERE name=${identity.name} OR sid=${identity.sid}`.execute(transaction);
      assertRuntimePrincipal(identity, found.rows);
      if (!found.rows.length) {
        await sql.raw(`CREATE USER [${principalName.parse(identity.name)}] WITH SID=0x${identity.sid.toString("hex")}, TYPE=E`).execute(transaction);
      }
      await reconcilePortalSqlPermissions(transaction, identity.name, role);
    }
  });
}

/** ループバックの単一開発用ログインで、現在の機能をまとめて試せるようにします。 */
export async function grantLocalDevelopmentAccess(database: Database, name: string): Promise<void> {
  const principal = `[${principalName.parse(name)}]`;
  const permissions = { ...allowedPermissions.runtime, ...allowedPermissions.management, ...allowedPermissions.profile };
  for (const [object, values] of Object.entries(permissions)) {
    await sql.raw(`GRANT ${values} ON OBJECT::dbo.[${object}] TO ${principal}`).execute(database);
  }
}
