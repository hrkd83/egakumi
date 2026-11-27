import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { Kysely, MssqlAdapter, MssqlIntrospector, MssqlQueryCompiler, sql,
  type CompiledQuery, type DatabaseConnection, type Driver, type QueryResult } from "kysely";
import { assertRuntimePrincipal, parseMigrationSqlIdentities, parseRuntimeIdentity, portalRuntimePermissions, reconcilePortalSqlPermissions } from "../scripts/auth-runtime-grants.ts";
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

test("Azure SQLのDDLとPortalの実行は別のIDを使用する", () => {
  const azure = { ...environment, AZURE_CLIENT_ID: randomUUID() };
  assert.deepEqual(parseMigrationSqlIdentities(azure).runtime, parseRuntimeIdentity(environment));
  for (const values of [
    { ...azure, AZURE_CLIENT_ID: undefined },
    { ...azure, AZURE_CLIENT_ID: "00000000-0000-0000-0000-000000000000" },
    { ...azure, AZURE_CLIENT_ID: environment.PORTAL_SQL_RUNTIME_CLIENT_ID.toUpperCase() },
    { AZURE_CLIENT_ID: azure.AZURE_CLIENT_ID },
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

test("実行IDには認証用テーブルの必要な権限だけを付与する", async () => {
  const principal = environment.PORTAL_SQL_RUNTIME_USER_NAME;
  const { commands, database } = grantFixture();
  await reconcilePortalSqlPermissions(database, principal);
  assert.deepEqual(commands, Object.entries(portalRuntimePermissions).map(([table, operations]) =>
    `GRANT ${operations} ON OBJECT::dbo.[${table}] TO [${principal}]`));

  const alreadyGranted = Object.entries(portalRuntimePermissions).flatMap(([object, permissions]) =>
    permissions.split(", ").map((permission) => ({ permission, object, isPlainGrant: true, isObject: true })));
  const retry = grantFixture(alreadyGranted);
  await reconcilePortalSqlPermissions(retry.database, principal);
  assert.deepEqual(retry.commands, []);
});

test("継承したロールや期待範囲外の権限を検出したら付与を中止する", async () => {
  const principal = environment.PORTAL_SQL_RUNTIME_USER_NAME;
  for (const fixture of [
    grantFixture([], true),
    grantFixture([{ permission: "DELETE", object: "portalSchemaVersions", isPlainGrant: true, isObject: true }]),
    grantFixture([{ permission: "CREATE TABLE", object: null, isPlainGrant: true, isObject: false }]),
  ]) {
    await assert.rejects(reconcilePortalSqlPermissions(fixture.database, principal));
    assert.deepEqual(fixture.commands, []);
  }
});

test("実行IDは認証用SQLを読め、DDLを実行できない", localSqlTest, async (context) => {
  const { runtime } = await createLocalSqlTestDatabase(context);
  await sql`SELECT TOP(0) id FROM dbo.portalUser`.execute(runtime);
  const result = await sql<{ allowed: number }>`SELECT HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','CREATE TABLE') AS allowed`.execute(runtime);
  assert.equal(result.rows[0]?.allowed, 0);
});
