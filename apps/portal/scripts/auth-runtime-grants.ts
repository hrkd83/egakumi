import { sql, type Kysely, type Transaction } from "kysely";
import { z } from "zod";

type Database = Kysely<Record<string, unknown>>;
type GrantConnection = Database | Transaction<Record<string, unknown>>;
type Environment = Readonly<Record<string, string | undefined>>;

/** 公開Portalのログイン、API認可および接続セッションに必要な権限です。 */
export const portalRuntimePermissions = {
  portalUser: "SELECT, INSERT, UPDATE, DELETE",
  portalSession: "SELECT, INSERT, UPDATE, DELETE",
  portalAccount: "SELECT, INSERT, UPDATE, DELETE",
  portalVerification: "SELECT, INSERT, UPDATE, DELETE",
  portalSchemaVersions: "SELECT",
  portalApiAuthContext: "SELECT, INSERT, UPDATE",
  portalApiAuthorizationTransaction: "SELECT, INSERT, UPDATE, DELETE",
  portalApiActiveOperation: "SELECT, INSERT, DELETE",
  portalApiCallCleanup: "SELECT, INSERT, UPDATE, DELETE",
} as const;

type PrincipalPermission = { permission: string; isPlainGrant: boolean; isObject: boolean; object: string | null };

/** 配備側が指定するAzure SQL外部ユーザーのSQL名とEntraアプリケーションIDです。 */
export type RuntimeIdentity = { name: string; clientId: string; sid: Buffer };
export type PortalSqlIdentities = { runtime: RuntimeIdentity };
const nonzeroClientId = z.uuid().refine((value) => value !== "00000000-0000-0000-0000-000000000000");
const principalName = z.string().regex(/^[A-Za-z][A-Za-z0-9_-]{0,127}$/);

/** Azure SQLの外部ユーザー用SIDを、EntraアプリIDのGUIDをlittle-endianに変換して作ります。 */
export function parseRuntimeIdentity(environment: Environment): RuntimeIdentity | null {
  const clientId = environment.PORTAL_SQL_RUNTIME_CLIENT_ID;
  const name = environment.PORTAL_SQL_RUNTIME_USER_NAME;
  if (!clientId && !name) return null;
  const parsed = z.object({ clientId: nonzeroClientId, name: principalName }).safeParse({ clientId, name });
  if (!parsed.success) throw new Error("Portal SQL execution identity configuration is invalid");
  const sid = Buffer.from(parsed.data.clientId.replaceAll("-", ""), "hex");
  sid.subarray(0, 4).reverse();
  sid.subarray(4, 6).reverse();
  sid.subarray(6, 8).reverse();
  return { name: parsed.data.name, clientId: parsed.data.clientId.toLowerCase(), sid };
}

/** 配備時のDDLとPortalの実行は、別々のマネージドIDを使用します。 */
export function parseMigrationSqlIdentities(environment: Environment): PortalSqlIdentities {
  const schema = nonzeroClientId.safeParse(environment.AZURE_CLIENT_ID);
  const runtime = parseRuntimeIdentity(environment);
  if (!schema.success || !runtime || schema.data.toLowerCase() === runtime.clientId) {
    throw new Error("Portal SQL schema and runtime identities must be nonzero and distinct");
  }
  return { runtime };
}

/** 既存の外部ユーザーが設定と異なる場合は、そのユーザーを上書きせず停止します。 */
export function assertRuntimePrincipal(identity: RuntimeIdentity, principals: { name: string; sid: Uint8Array; type: string }[]) {
  if (principals.some((principal) => principal.name !== identity.name
    || principal.type !== "E" || !Buffer.from(principal.sid).equals(identity.sid))) {
    throw new Error("Existing Portal SQL principal differs from the configured identity");
  }
}

/** 単純な権限付与だけを許し、想定外の継承・広域権限は配備で書き換える前に止めます。 */
export async function reconcilePortalSqlPermissions(database: GrantConnection, name: string): Promise<void> {
  const principal = `[${principalName.parse(name)}]`;
  const inherited = await sql`SELECT 1 AS unexpected FROM sys.database_role_members WHERE member_principal_id=USER_ID(${name})
    UNION ALL SELECT 1 FROM sys.schemas WHERE principal_id=USER_ID(${name})
    UNION ALL SELECT 1 FROM sys.objects WHERE principal_id=USER_ID(${name})
    UNION ALL SELECT 1 FROM sys.database_principals WHERE owning_principal_id=USER_ID(${name})`.execute(database);
  if (inherited.rows.length) throw new Error("Portal SQL principal must not inherit roles or own database objects");

  const current = (await sql<PrincipalPermission>`SELECT p.permission_name AS [permission],
    CONVERT(bit, CASE WHEN p.state='G' THEN 1 ELSE 0 END) AS isPlainGrant,
    CONVERT(bit, CASE WHEN p.class=1 AND p.minor_id=0 AND s.name=N'dbo' THEN 1 ELSE 0 END) AS isObject,
    o.name AS [object]
    FROM sys.database_permissions p LEFT JOIN sys.objects o ON p.class=1 AND p.major_id=o.object_id
    LEFT JOIN sys.schemas s ON s.schema_id=o.schema_id
    WHERE p.grantee_principal_id=USER_ID(${name}) AND NOT (p.class=0 AND p.permission_name=N'CONNECT' AND p.state='G')
  `.execute(database)).rows;
  const expected: Readonly<Record<string, string>> = portalRuntimePermissions;
  if (current.some((entry) => !entry.isObject || !entry.isPlainGrant || entry.object === null
      || !Object.hasOwn(expected, entry.object) || !expected[entry.object].split(", ").includes(entry.permission))) {
    throw new Error("Portal SQL principal has grants outside its role");
  }
  for (const [object, permissions] of Object.entries(expected)) {
    const missing = permissions.split(", ").filter((permission) => !current.some((entry) => entry.object === object && entry.permission === permission));
    if (missing.length) await sql.raw(`GRANT ${missing.join(", ")} ON OBJECT::dbo.[${object}] TO ${principal}`).execute(database);
  }
}

/** 初期化ジョブが限定権限を適用し、同じ接続先での再実行では付与差分だけを処理します。 */
export async function grantRuntimeAccess(database: Database, identity: RuntimeIdentity): Promise<void> {
  await database.transaction().execute(async (transaction) => {
    await sql`DECLARE @result int;
      EXEC @result = sp_getapplock @Resource=N'portal-auth-schema', @LockMode='Exclusive',
        @LockOwner='Transaction', @LockTimeout=20000;
      IF @result < 0 THROW 51000, 'Portal migration lock unavailable', 1;`.execute(transaction);
    const found = await sql<{ name: string; sid: Uint8Array; type: string }>`SELECT name, sid, type FROM sys.database_principals
      WHERE name=${identity.name} OR sid=${identity.sid}`.execute(transaction);
    assertRuntimePrincipal(identity, found.rows);
    if (!found.rows.length) {
      await sql.raw(`CREATE USER [${principalName.parse(identity.name)}] WITH SID=0x${identity.sid.toString("hex")}, TYPE=E`).execute(transaction);
    }
    await reconcilePortalSqlPermissions(transaction, identity.name);
  });
}

/** ループバックの単一開発用ログインで、現在の機能をまとめて試せるようにします。 */
export async function grantLocalDevelopmentAccess(database: Database, name: string): Promise<void> {
  const principal = `[${principalName.parse(name)}]`;
  for (const [object, values] of Object.entries(portalRuntimePermissions)) {
    await sql.raw(`GRANT ${values} ON OBJECT::dbo.[${object}] TO ${principal}`).execute(database);
  }
}
