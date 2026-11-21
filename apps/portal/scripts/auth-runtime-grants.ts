import { sql, type Kysely } from "kysely";
import { z } from "zod";

/** テーブルごとの実行権限です。承認設定・監査・台帳には不要な変更権限を渡しません。 */
export const portalRuntimePermissions = {
  portalUser: "SELECT, INSERT, UPDATE, DELETE",
  portalSession: "SELECT, INSERT, UPDATE, DELETE",
  portalAccount: "SELECT, INSERT, UPDATE, DELETE",
  portalVerification: "SELECT, INSERT, UPDATE, DELETE",
  portalApiKeyGroup: "SELECT",
  portalApiKeyProduct: "SELECT",
  portalApiKey: "SELECT, INSERT, UPDATE",
  portalApiKeyAudit: "SELECT, INSERT",
  portalApiKeyLimit: "SELECT, INSERT, UPDATE",
  portalApiKeyLimitAudit: "SELECT, INSERT",
  portalApiKeyTransferAudit: "SELECT, INSERT",
  portalApiKeyAdministration: "SELECT",
  portalApiKeyCatalogProduct: "SELECT",
  portalSchemaVersions: "SELECT",
  portalCompany: "SELECT, INSERT, UPDATE",
  portalCompanyAudit: "SELECT, INSERT",
  portalSubscriptionSyncAudit: "SELECT, INSERT",
  portalConnectionState: "SELECT, INSERT, UPDATE",
} as const;

/** 観測と初回登録だけを許すストアドプロシージャです。承認設定全体の更新権限は付けません。 */
export const portalRuntimeProcedures = ["portalObserveApiKeyGroup", "portalEnsureApiKeyGroup"] as const;

/** 実行用IDには、台帳に必要な限定操作だけのEXECUTEを付与します。 */
export async function grantRuntimeProcedureAccess(database: Kysely<Record<string, unknown>>, userName: string): Promise<void> {
  if (!/^[A-Za-z][A-Za-z0-9_-]{0,127}$/.test(userName)) throw new Error("Invalid SQL runtime principal");
  for (const procedure of portalRuntimeProcedures) {
    await sql.raw(`GRANT EXECUTE ON OBJECT::dbo.[${procedure}] TO [${userName}]`).execute(database);
  }
}

/** 配備側が指定する、Portal実行用のEntraマネージドIDです。 */
export type RuntimeIdentity = { name: string; sid: Buffer };

/** 構築した管理VM自身のSQL保守ユーザー名です。公開設定では変更しません。 */
export const managementSqlUserName = "core_management_vm";

/** 配備が確認した管理VMのclient IDをSQLの外部ユーザーへ対応させます。 */
export type ManagementIdentity = RuntimeIdentity;

/**
 * Azure SQLの外部ユーザー用SIDを、マネージドIDのアプリケーションIDから作ります。
 * @remarks Azure RBACで用いるオブジェクトIDとは異なり、SQLにはGUIDのlittle-endian形式を渡します。
 */
export function parseRuntimeIdentity(environment: Readonly<Record<string, string | undefined>>): RuntimeIdentity | null {
  const clientId = environment.PORTAL_SQL_RUNTIME_CLIENT_ID;
  const name = environment.PORTAL_SQL_RUNTIME_USER_NAME;
  if (!clientId && !name) return null;
  const parsed = z.object({
    clientId: z.uuid().refine((value) => value !== "00000000-0000-0000-0000-000000000000"),
    name: z.string().regex(/^[A-Za-z][A-Za-z0-9_-]{0,127}$/),
  }).safeParse({ clientId, name });
  if (!parsed.success) throw new Error("Portal SQL runtime identity configuration is invalid");
  const sid = Buffer.from(parsed.data.clientId.replaceAll("-", ""), "hex");
  sid.subarray(0, 4).reverse();
  sid.subarray(4, 6).reverse();
  sid.subarray(6, 8).reverse();
  return { name: parsed.data.name, sid };
}

/**
 * Azure配備の3主体を検査し、管理VMだけの保守ユーザーを返します。
 * @remarks SAMI/UAMIのSQL SIDにはclient IDを使います。Graphによる名前解決は行いません。
 * https://learn.microsoft.com/en-us/sql/t-sql/statements/create-user-transact-sql?view=azuresqldb-current#k-create-a-contained-database-user-from-a-microsoft-entra-principal-without-validation
 */
export function parseManagementIdentity(environment: Readonly<Record<string, string | undefined>>): ManagementIdentity {
  const clientId = z.uuid().refine((value) => value !== "00000000-0000-0000-0000-000000000000");
  const parsed = z.object({ management: clientId, runtime: clientId, schema: clientId }).safeParse({
    management: environment.PORTAL_SQL_MANAGEMENT_CLIENT_ID,
    runtime: environment.PORTAL_SQL_RUNTIME_CLIENT_ID,
    schema: environment.AZURE_CLIENT_ID,
  });
  if (!parsed.success || new Set(Object.values(parsed.data).map((value) => value.toLowerCase())).size !== 3) {
    throw new Error("Portal SQL management, runtime and schema identities must be nonzero and distinct");
  }
  const runtime = parseRuntimeIdentity(environment);
  if (!runtime || runtime.name.toLowerCase() === managementSqlUserName) {
    throw new Error("Portal SQL management and runtime principal names must differ");
  }
  return parseRuntimeIdentity({ PORTAL_SQL_RUNTIME_CLIENT_ID: parsed.data.management,
    PORTAL_SQL_RUNTIME_USER_NAME: managementSqlUserName })!;
}

/** 保守ユーザー名とSIDの衝突や、複数主体への曖昧な対応を拒否します。 */
export function assertManagementPrincipal(identity: ManagementIdentity,
  principals: { name: string; sid: Uint8Array; type: string }[]): void {
  if (principals.length > 1 || principals.some((principal) => principal.name !== identity.name
    || principal.type !== "E" || !(principal.sid instanceof Uint8Array)
    || !Buffer.from(principal.sid).equals(identity.sid))) {
    throw new Error("Existing Portal SQL management principal differs from the constructed identity");
  }
}

/**
 * 初期化Jobの接続先DBだけに、構築した管理VMのdb_ownerを常設します。
 * @remarks 同じDB内の排他ロックとトランザクションで作成・付与・確認をまとめます。
 */
export async function grantManagementAccess(database: Kysely<Record<string, unknown>>,
  identity: ManagementIdentity): Promise<void> {
  if (identity.name !== managementSqlUserName || !Buffer.isBuffer(identity.sid)
    || identity.sid.length !== 16 || !identity.sid.some((value) => value !== 0)) {
    throw new Error("Portal SQL management identity is invalid");
  }
  await database.transaction().execute(async (transaction) => {
    await sql`DECLARE @result int;
      EXEC @result = sp_getapplock @Resource=N'core-management-sql-grants', @LockMode='Exclusive',
        @LockOwner='Transaction', @LockTimeout=20000;
      IF @result < 0 THROW 51000, 'SQL management grant lock unavailable', 1;`.execute(transaction);
    const found = await sql<{ name: string; sid: Uint8Array; type: string }>`
      SELECT name, sid, type FROM sys.database_principals
      WHERE name=${identity.name} OR sid=${identity.sid}`.execute(transaction);
    assertManagementPrincipal(identity, found.rows);
    const principal = `[${managementSqlUserName}]`;
    if (!found.rows.length) {
      await sql.raw(`CREATE USER ${principal} WITH SID=0x${identity.sid.toString("hex")}, TYPE=E`).execute(transaction);
    }
    const membership = await sql<{ member: number | null }>`
      SELECT IS_ROLEMEMBER(N'db_owner', ${identity.name}) AS member`.execute(transaction);
    if (membership.rows.length !== 1 || ![0, 1].includes(membership.rows[0].member as number)) {
      throw new Error("Portal SQL management role membership is not known");
    }
    if (membership.rows[0].member === 0) {
      await sql.raw(`ALTER ROLE [db_owner] ADD MEMBER ${principal}`).execute(transaction);
    }
    const verified = await sql<{ member: number | null }>`
      SELECT IS_ROLEMEMBER(N'db_owner', ${identity.name}) AS member`.execute(transaction);
    if (verified.rows.length !== 1 || verified.rows[0].member !== 1) {
      throw new Error("Portal SQL management role membership was not established");
    }
  });
}

/** SQLユーザーが別IDへ置き換わっていないことを確認します。 */
export function assertRuntimePrincipal(identity: RuntimeIdentity, principals: { name: string; sid: Uint8Array; type: string }[]) {
  if (principals.some((principal) => principal.name !== identity.name
    || principal.type !== "E" || !Buffer.from(principal.sid).equals(identity.sid))) {
    throw new Error("Existing Portal SQL runtime principal differs from the configured identity");
  }
}

/** 管理用IDで実行し、実行用IDには各機能が必要とするテーブル権限だけを付与します。 */
export async function grantRuntimeAccess(database: Kysely<Record<string, unknown>>, identity: RuntimeIdentity) {
  await database.transaction().execute(async (transaction) => {
    await sql`DECLARE @result int;
      EXEC @result = sp_getapplock @Resource=N'portal-auth-schema', @LockMode='Exclusive',
        @LockOwner='Transaction', @LockTimeout=20000;
      IF @result < 0 THROW 51000, 'Portal migration lock unavailable', 1;`.execute(transaction);
    const found = await sql<{ name: string; sid: Uint8Array; type: string }>`
      SELECT name, sid, type FROM sys.database_principals
      WHERE name=${identity.name} OR sid=${identity.sid}`.execute(transaction);
    assertRuntimePrincipal(identity, found.rows);
    const principal = `[${identity.name}]`;
    if (!found.rows.length) {
      await sql.raw(`CREATE USER ${principal} WITH SID=0x${identity.sid.toString("hex")}, TYPE=E`).execute(transaction);
    }
    for (const [table, permissions] of Object.entries(portalRuntimePermissions)) {
      await sql.raw(`GRANT ${permissions} ON OBJECT::dbo.[${table}] TO ${principal}`).execute(transaction);
    }
    for (const procedure of portalRuntimeProcedures) {
      await sql.raw(`GRANT EXECUTE ON OBJECT::dbo.[${procedure}] TO ${principal}`).execute(transaction);
    }
  });
}
