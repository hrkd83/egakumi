import assert from "node:assert/strict";
import { test } from "node:test";
import { Kysely, MssqlAdapter, MssqlIntrospector, MssqlQueryCompiler,
  type DatabaseConnection, type Driver, type CompiledQuery, type QueryResult } from "kysely";
import { observeAuthSchemaBeforeMigration } from "../scripts/auth-migrations.ts";

/** SQL Serverコンパイラーを通し、外部接続せずDDL前の観測結果だけを返します。 */
function fixture(rows: Record<string, unknown>[], failure = false) {
  const commands: CompiledQuery[] = [];
  const connection: DatabaseConnection = {
    async executeQuery<R>(query: CompiledQuery): Promise<QueryResult<R>> {
      commands.push(query);
      if (failure) throw new Error("synthetic metadata unavailable");
      return { rows: rows as R[] };
    },
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
  return { database, commands };
}

test("初回空DBと既存DBを区別し、固定stageと件数・boolだけを返す", async () => {
  for (const [count, present] of [[0, 0], [19, 1]]) {
    const f = fixture([{ userTableCount: count, migrationTablePresent: present, ignoredPrivateField: "synthetic-not-returned" }]);
    try {
      assert.deepEqual(await observeAuthSchemaBeforeMigration(f.database), {
        stage: "before-migration", userTableCount: count, migrationTablePresent: present === 1,
      });
      assert.equal(f.commands.length, 1);
      assert.match(f.commands[0]!.sql, /sys\.tables WHERE is_ms_shipped=0/);
      assert.match(f.commands[0]!.sql, /dbo\.portalSchemaVersions/);
      assert.match(f.commands[0]!.sql, /HAS_PERMS_BY_NAME\(DB_NAME\(\),'DATABASE','VIEW DEFINITION'\)/);
      assert.deepEqual(f.commands[0]!.parameters, []);
      assert.doesNotMatch(f.commands[0]!.sql, /\b(?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b/i);
    } finally { await f.database.destroy(); }
  }
});

test("不可視メタデータ・不正値・取得障害を空DBへ置き換えず停止する", async () => {
  for (const rows of [[], [{ userTableCount: null, migrationTablePresent: 0 }],
    [{ userTableCount: -1, migrationTablePresent: 0 }], [{ userTableCount: "0", migrationTablePresent: 0 }],
    [{ userTableCount: 0, migrationTablePresent: null }], [{ userTableCount: 0, migrationTablePresent: 2 }]]) {
    const f = fixture(rows);
    try { await assert.rejects(observeAuthSchemaBeforeMigration(f.database), /Initial schema metadata could not be verified/); }
    finally { await f.database.destroy(); }
  }
  const failed = fixture([], true);
  try { await assert.rejects(observeAuthSchemaBeforeMigration(failed.database), /metadata unavailable/); }
  finally { await failed.database.destroy(); }
});
