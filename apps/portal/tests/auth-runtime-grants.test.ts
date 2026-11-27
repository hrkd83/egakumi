import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { Kysely, MssqlAdapter, MssqlIntrospector, MssqlQueryCompiler, sql,
  type CompiledQuery, type DatabaseConnection, type Driver, type QueryResult } from "kysely";
import { assertRuntimePrincipal, parseMigrationSqlIdentities, parseRuntimeIdentity, portalManagementPermissions,
  portalManagementProcedures, portalRuntimePermissions, reconcilePortalSqlPermissions } from "../scripts/auth-runtime-grants.ts";
import { createLocalSqlTestDatabase, localSqlTest } from "./helpers/local-sql.ts";

const environment = {
  PORTAL_SQL_RUNTIME_CLIENT_ID: "00112233-4455-6677-8899-aabbccddeeff",
  PORTAL_SQL_RUNTIME_USER_NAME: "id-test-platform-portal-pull",
};

test("SQLのSIDはEntraアプリIDをGUIDのlittle-endian形式へ変換する", () => {
  const identity = parseRuntimeIdentity(environment);
  assert.ok(identity);
  assert.equal(identity.sid.toString("hex"), "33221100554477668899aabbccddeeff");
  assert.equal(identity.name, environment.PORTAL_SQL_RUNTIME_USER_NAME);
});

test("初期化に必要なIDの欠落・ゼロ値・SQL識別子の混入を拒否する", () => {
  assert.equal(parseRuntimeIdentity({}), null);
  for (const values of [
    { ...environment, PORTAL_SQL_RUNTIME_CLIENT_ID: undefined },
    { ...environment, PORTAL_SQL_RUNTIME_USER_NAME: undefined },
    { ...environment, PORTAL_SQL_RUNTIME_CLIENT_ID: "00000000-0000-0000-0000-000000000000" },
    { ...environment, PORTAL_SQL_RUNTIME_USER_NAME: "invalid]; DROP TABLE portalUser;--" },
  ]) assert.throws(() => parseRuntimeIdentity(values));
});

test("AzureのDDL・公開Portal・購読管理・照会に、別々のIDとSQLユーザー名を使う", () => {
  const azure = { ...environment, AZURE_CLIENT_ID: randomUUID(),
    PORTAL_SQL_MANAGEMENT_CLIENT_ID: randomUUID(), PORTAL_SQL_MANAGEMENT_USER_NAME: "subscription-admin-test",
    PORTAL_SQL_PROFILE_CLIENT_ID: randomUUID(), PORTAL_SQL_PROFILE_USER_NAME: "subscription-profile-test" };
  const identities = parseMigrationSqlIdentities(azure);
  assert.deepEqual(identities.runtime, parseRuntimeIdentity(environment));
  assert.equal(identities.management.clientId, azure.PORTAL_SQL_MANAGEMENT_CLIENT_ID);
  assert.equal(identities.profile.clientId, azure.PORTAL_SQL_PROFILE_CLIENT_ID);
  for (const values of [
    { ...azure, AZURE_CLIENT_ID: undefined },
    { ...azure, AZURE_CLIENT_ID: "00000000-0000-0000-0000-000000000000" },
    { ...azure, AZURE_CLIENT_ID: environment.PORTAL_SQL_RUNTIME_CLIENT_ID.toUpperCase() },
    { ...azure, PORTAL_SQL_PROFILE_CLIENT_ID: azure.PORTAL_SQL_MANAGEMENT_CLIENT_ID },
    { ...azure, PORTAL_SQL_PROFILE_USER_NAME: azure.PORTAL_SQL_MANAGEMENT_USER_NAME.toUpperCase() },
    { ...environment, AZURE_CLIENT_ID: azure.AZURE_CLIENT_ID },
  ]) assert.throws(() => parseMigrationSqlIdentities(values), /identities must be nonzero and distinct/);
});

test("既存のSQLユーザーが別のIDなら権限を付与せず停止する", () => {
  const identity = parseRuntimeIdentity(environment)!;
  const principal = { name: identity.name, sid: identity.sid, type: "E" };
  assert.doesNotThrow(() => assertRuntimePrincipal(identity, []));
  assert.doesNotThrow(() => assertRuntimePrincipal(identity, [principal]));
  for (const changed of [
    { ...principal, sid: Buffer.alloc(16) },
    { ...principal, type: "S" },
    { ...principal, name: "another-user" },
  ]) assert.throws(() => assertRuntimePrincipal(identity, [changed]));
});

test("公開Portalは認証用テーブルと管理者判定用の設定だけを参照・更新する", () => {
  assert.equal(portalRuntimePermissions.portalApiKeyAdministration, "SELECT");
  assert.equal(portalRuntimePermissions.portalConnectionState, "SELECT, INSERT, UPDATE");
  const shared = Object.keys(portalManagementPermissions).filter((object) => Object.hasOwn(portalRuntimePermissions, object));
  assert.deepEqual(shared, ["portalApiKeyAdministration", "portalSchemaVersions"]);
  assert.equal(portalManagementPermissions.portalApiKey, "SELECT, INSERT, UPDATE");
  assert.equal(portalManagementPermissions.portalApiKeyAudit, "SELECT, INSERT");
  assert.equal(portalManagementPermissions.portalApiKeyLimit, "SELECT, INSERT, UPDATE");
  assert.equal(portalManagementPermissions.portalApiKeyTransferAudit, "SELECT, INSERT");
  assert.equal(portalManagementPermissions.portalApiKeyImportAudit, "INSERT");
  assert.equal(portalManagementPermissions.portalCompany, "SELECT, INSERT, UPDATE");
  assert.deepEqual(portalManagementProcedures, ["portalObserveApiKeyGroup", "portalEnsureApiKeyGroup"]);
});

/** 権限付与のSQLと既存権限の取り扱いを、実際のMSSQLコンパイラー経由で確認します。 */
function grantFixture(permissions: object[] = [], inherited = false) {
  const commands: string[] = [];
  const connection: DatabaseConnection = {
    async executeQuery<R>(query: CompiledQuery): Promise<QueryResult<R>> {
      if (query.sql.includes("FROM sys.database_role_members")) return { rows: (inherited ? [{ unexpected: 1 }] : []) as R[] };
      if (query.sql.includes("FROM sys.database_permissions")) return { rows: permissions as R[] };
      commands.push(query.sql);
      return { rows: [] };
    },
    async *streamQuery<R>(): AsyncIterableIterator<QueryResult<R>> { yield { rows: [] }; },
  };
  const driver: Driver = { async init() {}, async acquireConnection() { return connection; }, async releaseConnection() {},
    async destroy() {}, async beginTransaction() {}, async commitTransaction() {}, async rollbackTransaction() {} };
  const database = new Kysely<Record<string, unknown>>({ dialect: { createAdapter: () => new MssqlAdapter(),
    createDriver: () => driver, createQueryCompiler: () => new MssqlQueryCompiler(), createIntrospector: (db) => new MssqlIntrospector(db) } });
  return { commands, database };
}

test("旧公開Portalの台帳・グループ権限を付与元ごとに解除し、再実行では変更しない", async () => {
  const plain = (object: string, permission: string, grantor = "dbo") => ({ permission, object, grantor, isPlainGrant: true, isObject: true });
  const current = Object.entries(portalRuntimePermissions).flatMap(([name, values]) => values.split(", ").map((value) => plain(name, value)));
  const database = grantFixture([...current, plain("portalApiKey", "UPDATE"), plain("portalApiKey", "UPDATE", "other-grantor"),
    plain("portalApiKeyAudit", "INSERT"), plain("portalEnsureApiKeyGroup", "EXECUTE")]);
  const repeated = grantFixture(current);
  try {
    await reconcilePortalSqlPermissions(database.database, "portal-runtime", "runtime");
    assert.deepEqual(database.commands, [
      "REVOKE UPDATE ON OBJECT::dbo.[portalApiKey] FROM [portal-runtime] AS [dbo]",
      "REVOKE UPDATE ON OBJECT::dbo.[portalApiKey] FROM [portal-runtime] AS [other-grantor]",
      "REVOKE INSERT ON OBJECT::dbo.[portalApiKeyAudit] FROM [portal-runtime] AS [dbo]",
      "REVOKE EXECUTE ON OBJECT::dbo.[portalEnsureApiKeyGroup] FROM [portal-runtime] AS [dbo]",
    ]);
    await reconcilePortalSqlPermissions(repeated.database, "portal-runtime", "runtime");
    assert.deepEqual(repeated.commands, []);
  } finally { await Promise.all([database.database.destroy(), repeated.database.destroy()]); }
});

test("照会IDは専用プロシージャだけを実行でき、既存の広域権限やテーブル参照を拒否する", async () => {
  const profile = grantFixture();
  const unexpected = grantFixture([{ object: "portalApiKey", permission: "SELECT", isPlainGrant: true, isObject: true }]);
  const inherited = grantFixture([], true);
  try {
    await reconcilePortalSqlPermissions(profile.database, "portal-profile", "profile");
    assert.deepEqual(profile.commands, ["GRANT EXECUTE ON OBJECT::dbo.[portalReadSubscriptionProfile] TO [portal-profile]"]);
    await assert.rejects(reconcilePortalSqlPermissions(unexpected.database, "portal-profile", "profile"), /grants outside its role/);
    await assert.rejects(reconcilePortalSqlPermissions(inherited.database, "portal-profile", "profile"), /must not inherit/);
    assert.deepEqual([...unexpected.commands, ...inherited.commands], []);
  } finally { await Promise.all([profile.database.destroy(), unexpected.database.destroy(), inherited.database.destroy()]); }
});

test("SQLは旧公開ユーザーの購読権限を解除し、照会ユーザーにはテーブルの読み取りを許さない", localSqlTest, async (context) => {
  const { admin } = await createLocalSqlTestDatabase(context, false);
  await sql`CREATE USER [portal_runtime_test] WITHOUT LOGIN;
    CREATE USER [portal_management_test] WITHOUT LOGIN;
    CREATE USER [portal_profile_test] WITHOUT LOGIN;
    GRANT SELECT, UPDATE ON OBJECT::dbo.portalApiKey TO [portal_runtime_test];
    GRANT EXECUTE ON OBJECT::dbo.portalEnsureApiKeyGroup TO [portal_runtime_test];`.execute(admin);
  await admin.transaction().execute(async (transaction) => {
    await reconcilePortalSqlPermissions(transaction, "portal_runtime_test", "runtime");
    await reconcilePortalSqlPermissions(transaction, "portal_management_test", "management");
    await reconcilePortalSqlPermissions(transaction, "portal_profile_test", "profile");
    await reconcilePortalSqlPermissions(transaction, "portal_runtime_test", "runtime");
  });
  await admin.connection().execute(async (connection) => {
    await sql`EXECUTE AS USER=N'portal_runtime_test'`.execute(connection);
    try {
      await sql`SELECT TOP(0) id FROM dbo.portalSession`.execute(connection);
      await assert.rejects(sql`SELECT TOP(0) id FROM dbo.portalApiKey`.execute(connection));
      const result = await sql<{ allowed: number }>`SELECT HAS_PERMS_BY_NAME(N'dbo.portalEnsureApiKeyGroup',N'OBJECT',N'EXECUTE') AS allowed`.execute(connection);
      assert.notEqual(result.rows[0]?.allowed, 1);
    } finally { await sql`REVERT`.execute(connection); }
    await sql`EXECUTE AS USER=N'portal_management_test'`.execute(connection);
    try {
      const result = await sql<{ allowed: number }>`SELECT HAS_PERMS_BY_NAME(N'dbo.portalApiKey',N'OBJECT',N'UPDATE') AS allowed`.execute(connection);
      assert.equal(result.rows[0]?.allowed, 1);
      await assert.rejects(sql`SELECT TOP(0) id FROM dbo.portalSession`.execute(connection));
    } finally { await sql`REVERT`.execute(connection); }
    await sql`EXECUTE AS USER=N'portal_profile_test'`.execute(connection);
    try {
      const result = await sql`EXEC dbo.portalReadSubscriptionProfile @tenantId=${randomUUID()}, @apimSubscriptionId='unregistered'`.execute(connection);
      assert.deepEqual(result.rows, []);
      await assert.rejects(sql`SELECT TOP(0) id FROM dbo.portalApiKey`.execute(connection));
    } finally { await sql`REVERT`.execute(connection); }
  });
});
