import assert from "node:assert/strict";
import { test } from "node:test";
import { Kysely, MssqlAdapter, MssqlIntrospector, MssqlQueryCompiler,
  type DatabaseConnection, type Driver, type CompiledQuery, type QueryResult } from "kysely";
import { readApiKeyGroupPolicy, SqlApiKeyAdministrationRepository } from "../lib/api-key-administration/repository.ts";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";

const tenantId = "11111111-1111-4111-8111-111111111111";
const cursor = "22222222-2222-4222-8222-222222222222";

/** 実際のMSSQLコンパイラーを通し、SQL文とパラメーター境界を検査する読取専用の接続です。 */
function fixture(rows: Record<string, unknown>[]) {
  const commands: CompiledQuery[] = [];
  const connection: DatabaseConnection = {
    async executeQuery<R>(query: CompiledQuery): Promise<QueryResult<R>> { commands.push(query); return { rows: rows as R[] }; },
    async *streamQuery<R>(): AsyncIterableIterator<QueryResult<R>> { yield { rows: [] as R[] }; },
  };
  const driver: Driver = {
    async init() {}, async acquireConnection() { return connection; }, async beginTransaction() {},
    async commitTransaction() {}, async rollbackTransaction() {}, async releaseConnection() {}, async destroy() {},
  };
  const database = new Kysely<Record<string, unknown>>({ dialect: {
    createAdapter: () => new MssqlAdapter(), createDriver: () => driver, createQueryCompiler: () => new MssqlQueryCompiler(),
    createIntrospector: (db) => new MssqlIntrospector(db),
  } });
  return { repository: new SqlApiKeyAdministrationRepository(database), database, commands };
}

test("グループ名規則は同じSQL接続で読み、未設定や破損を許可へ読み替えない", async () => {
  const f = fixture([{ platformGroupId: cursor, groupNamePattern: "^TEAM_[A-Z]+$" }]);
  try {
    assert.deepEqual(await readApiKeyGroupPolicy(f.database, tenantId), { platformGroupId: cursor, groupNamePattern: "^TEAM_[A-Z]+$" });
    assert.deepEqual(f.commands[0]!.parameters, [tenantId]);
  } finally { await f.database.destroy(); }
  for (const rows of [[], [{ platformGroupId: cursor, groupNamePattern: "[" }]]) {
    const invalid = fixture(rows);
    try { await assert.rejects(invalid.repository.getSettings(tenantId),
      (error: unknown) => error instanceof ApiKeyError && error.code === "ConfigurationUnavailable"); }
    finally { await invalid.database.destroy(); }
  }
});

test("台帳一覧は追加の一行で次ページを判定し、最後に返したIDを継続位置にする", async () => {
  const rows = Array.from({ length: 101 }, (_, index) => ({
    id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`, displayName: `ORG_${index}`,
    directoryStatus: "active", lastCheckedAt: null, lastErrorCode: null, activeSubscriptions: 1, subscriptions: 1,
  }));
  const f = fixture(rows);
  try {
    const page = await f.repository.listTrackedGroups(tenantId, cursor);
    assert.equal(page.groups.length, 100);
    assert.equal(page.nextCursor, rows[99]!.id);
    assert.deepEqual(f.commands[0]!.parameters, [tenantId, cursor]);
  } finally { await f.database.destroy(); }
});

test("最後の100行以下のページは継続なしとし、空の最終ページも正常に返す", async () => {
  for (const amount of [0, 1, 100]) {
    const rows = Array.from({ length: amount }, (_, index) => ({ id: `group-${index}`, displayName: "ORG_x",
      directoryStatus: "unknown", lastCheckedAt: null, lastErrorCode: null, activeSubscriptions: 0, subscriptions: 0 }));
    const f = fixture(rows);
    try {
      const page = await f.repository.listTrackedGroups(tenantId);
      assert.equal(page.groups.length, amount);
      assert.equal(page.nextCursor, null);
      assert.deepEqual(f.commands[0]!.parameters, [tenantId]);
    } finally { await f.database.destroy(); }
  }
});
