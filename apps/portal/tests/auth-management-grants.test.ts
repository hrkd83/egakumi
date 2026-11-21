import assert from "node:assert/strict";
import { test } from "node:test";
import { Kysely, MssqlAdapter, MssqlIntrospector, MssqlQueryCompiler,
  type CompiledQuery, type DatabaseConnection, type Driver, type QueryResult } from "kysely";
import { assertManagementPrincipal, grantManagementAccess, managementSqlUserName,
  parseManagementIdentity, type ManagementIdentity } from "../scripts/auth-runtime-grants.ts";

// Independent ordered-byte and repeated-digit fixtures, never environment IDs.
const environment = {
  PORTAL_SQL_MANAGEMENT_CLIENT_ID: "00112233-4455-4677-8899-aabbccddeeff",
  PORTAL_SQL_RUNTIME_CLIENT_ID: "11111111-1111-4111-8111-111111111111",
  PORTAL_SQL_RUNTIME_USER_NAME: "runtime-fixture",
  AZURE_CLIENT_ID: "22222222-2222-4222-8222-222222222222",
};
const identity = parseManagementIdentity(environment);

/** SQLコンパイルとcommit/rollbackを観測し、外部接続せず保守権限だけを検証します。 */
function fixture(options: { principals?: { name: string; sid: Uint8Array; type: string }[];
  member?: unknown; verification?: unknown; failLock?: boolean } = {}) {
  const commands: CompiledQuery[] = [], events: string[] = [];
  const state = { principals: options.principals ?? [], member: "member" in options ? options.member : 0 };
  let snapshot = { ...state }, roleReads = 0;
  const connection: DatabaseConnection = {
    async executeQuery<R>(query: CompiledQuery): Promise<QueryResult<R>> {
      commands.push(query);
      if (query.sql.includes("sp_getapplock") && options.failLock) throw new Error("synthetic lock failure");
      if (query.sql.includes("FROM sys.database_principals")) return { rows: state.principals as R[] };
      if (query.sql.startsWith("CREATE USER")) {
        const sid = /WITH SID=0x([a-f0-9]{32}), TYPE=E/.exec(query.sql);
        assert.ok(sid);
        state.principals = [{ name: managementSqlUserName, sid: Buffer.from(sid[1], "hex"), type: "E" }];
      }
      if (query.sql.startsWith("ALTER ROLE")) state.member = 1;
      if (query.sql.includes("IS_ROLEMEMBER")) {
        roleReads += 1;
        const member = roleReads === 2 && "verification" in options ? options.verification : state.member;
        return { rows: [{ member }] as R[] };
      }
      return { rows: [] as R[] };
    },
    async *streamQuery<R>(): AsyncIterableIterator<QueryResult<R>> { yield { rows: [] as R[] }; },
  };
  const driver: Driver = {
    async init() {}, async acquireConnection() { return connection; },
    async beginTransaction() { events.push("begin"); snapshot = { ...state }; },
    async commitTransaction() { events.push("commit"); },
    async rollbackTransaction() { events.push("rollback"); Object.assign(state, snapshot); },
    async releaseConnection() {}, async destroy() {},
  };
  const database = new Kysely<Record<string, unknown>>({ dialect: {
    createAdapter: () => new MssqlAdapter(), createDriver: () => driver,
    createQueryCompiler: () => new MssqlQueryCompiler(), createIntrospector: (db) => new MssqlIntrospector(db),
  } });
  return { database, commands, events, state };
}

test("保守用SIDはVMのclient IDを使い、固定ユーザー名に対応させる", () => {
  assert.equal(identity.name, "core_management_vm");
  assert.equal(identity.sid.toString("hex"), "33221100554477468899aabbccddeeff");
});

test("管理・実行・配備IDの欠落、ゼロ値、共有、同名ユーザーをDDL前に拒否する", () => {
  for (const key of ["PORTAL_SQL_MANAGEMENT_CLIENT_ID", "PORTAL_SQL_RUNTIME_CLIENT_ID", "AZURE_CLIENT_ID"]) {
    for (const value of [undefined, "", "invalid", "00000000-0000-0000-0000-000000000000"]) {
      assert.throws(() => parseManagementIdentity({ ...environment, [key]: value }));
    }
  }
  for (const changed of [
    { PORTAL_SQL_MANAGEMENT_CLIENT_ID: environment.PORTAL_SQL_RUNTIME_CLIENT_ID },
    { PORTAL_SQL_MANAGEMENT_CLIENT_ID: environment.AZURE_CLIENT_ID },
    { AZURE_CLIENT_ID: environment.PORTAL_SQL_RUNTIME_CLIENT_ID },
    { PORTAL_SQL_RUNTIME_USER_NAME: managementSqlUserName.toUpperCase() },
  ]) assert.throws(() => parseManagementIdentity({ ...environment, ...changed }));
});

test("別SID、別名、別type、重複行へ保守権限を付けない", () => {
  const principal = { name: identity.name, sid: identity.sid, type: "E" };
  assert.doesNotThrow(() => assertManagementPrincipal(identity, []));
  assert.doesNotThrow(() => assertManagementPrincipal(identity, [principal]));
  for (const principals of [[{ ...principal, sid: Buffer.alloc(16) }],
    [{ ...principal, name: "another-user" }], [{ ...principal, type: "S" }], [principal, principal]]) {
    assert.throws(() => assertManagementPrincipal(identity, principals));
  }
});

test("新規保守ユーザーの作成・db_owner付与・再確認を1 transactionで確定する", async () => {
  const f = fixture();
  try {
    await grantManagementAccess(f.database, identity);
    assert.deepEqual(f.events, ["begin", "commit"]);
    assert.equal(f.state.member, 1);
    assert.equal(f.state.principals.length, 1);
    assert.deepEqual(f.state.principals[0].sid, identity.sid);
    assert.match(f.commands[0].sql, /@LockOwner='Transaction'/);
    assert.deepEqual(f.commands[1].parameters, [managementSqlUserName, identity.sid]);
    assert.equal(f.commands.filter((query) => query.sql.includes("IS_ROLEMEMBER")).length, 2);
    assert.equal(f.commands.find((query) => query.sql.startsWith("ALTER ROLE"))?.sql,
      "ALTER ROLE [db_owner] ADD MEMBER [core_management_vm]");
  } finally { await f.database.destroy(); }
});

test("再配備では同一主体のdb_ownerを維持し、ユーザーとmembershipを重複作成しない", async () => {
  const f = fixture({ principals: [{ name: identity.name, sid: identity.sid, type: "E" }], member: 1 });
  try {
    await grantManagementAccess(f.database, identity);
    assert.deepEqual(f.events, ["begin", "commit"]);
    assert.equal(f.state.member, 1);
    assert.ok(f.commands.every((query) => !/^(CREATE|ALTER)/.test(query.sql)));
  } finally { await f.database.destroy(); }
});

test("既存の非owner主体はSIDを維持してownerへ追加する", async () => {
  const f = fixture({ principals: [{ name: identity.name, sid: identity.sid, type: "E" }] });
  try {
    await grantManagementAccess(f.database, identity);
    assert.equal(f.state.member, 1);
    assert.ok(f.commands.every((query) => !query.sql.startsWith("CREATE")));
  } finally { await f.database.destroy(); }
});

test("主体衝突やロック取得失敗では付与前に停止してrollbackする", async () => {
  for (const options of [{ principals: [{ name: "another-user", sid: identity.sid, type: "E" }] },
    { failLock: true }]) {
    const f = fixture(options);
    try {
      await assert.rejects(grantManagementAccess(f.database, identity));
      assert.deepEqual(f.events, ["begin", "rollback"]);
      assert.ok(f.commands.every((query) => !/^(CREATE|ALTER)/.test(query.sql)));
    } finally { await f.database.destroy(); }
  }
});

test("membership不明や付与後の確認失敗では新規ユーザーと付与をrollbackする", async () => {
  for (const options of [{ member: null }, { member: "0" }, { member: 2 }, { verification: 0 },
    { verification: null }]) {
    const f = fixture(options);
    try {
      await assert.rejects(grantManagementAccess(f.database, identity));
      assert.deepEqual(f.events, ["begin", "rollback"]);
      assert.deepEqual(f.state.principals, []);
      assert.equal(f.state.member, "member" in options ? options.member : 0);
    } finally { await f.database.destroy(); }
  }
});

test("任意ユーザー名やゼロSIDを保守ユーザーとして渡さない", async () => {
  for (const invalid of [{ ...identity, name: "runtime-fixture" }, { ...identity, sid: Buffer.alloc(16) },
    { ...identity, sid: Buffer.alloc(15) }] satisfies ManagementIdentity[]) {
    const f = fixture();
    try {
      await assert.rejects(grantManagementAccess(f.database, invalid));
      assert.deepEqual(f.events, []);
      assert.deepEqual(f.commands, []);
    } finally { await f.database.destroy(); }
  }
});
