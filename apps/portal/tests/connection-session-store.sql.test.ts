import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { test } from "node:test";
import { Kysely, sql } from "kysely";
import { SqlConnectionSessionStore } from "../lib/connection-session/store.ts";
import type { PrivateState, StateTransaction } from "../lib/connection-session/client.ts";
import { createAuthDatabase } from "../lib/auth-database.ts";
import { getSqlEnvironment } from "../lib/auth-environment.ts";
import { createLocalSqlTestDatabase, localSqlTest } from "./helpers/local-sql.ts";

const secret = "test-only-session-store-secret".repeat(2);
/** テストごとに異なる結び付けを使い、実データの行は参照しません。 */
const binding = () => randomBytes(32).toString("hex");
/** 個人情報の代わりにランダムな証明だけを持つ検証状態です。 */
function state(revision = 1): PrivateState {
  const sessionId = randomBytes(32).toString("hex");
  return { revision, status: "complete", current: `cs2.${sessionId}.${randomBytes(32).toString("hex")}`, sessionId, generation: 1 };
}
/** 待機をタイマーへ依存させず、テストから並行処理の順序を決めます。 */
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

test("SQL保存は暗号化・CASで行い、リース中も別接続から確定済み候補を読み出せる", localSqlTest, async (context) => {
  const { admin, runtime } = await createLocalSqlTestDatabase(context);
  const store = new SqlConnectionSessionStore(runtime, secret), key = binding(), first = state();
  await store.withExclusive(key, async (tx) => {
    assert.equal(await tx.read(), undefined);
    await tx.save(0, first);
    const persisted = await sql<{ revision: string; stateCiphertext: Buffer }>`
      SELECT revision,stateCiphertext FROM dbo.portalConnectionState WHERE bindingHash=${key}`.execute(admin);
    assert.equal(Number(persisted.rows[0].revision), 1, "HTTP確定要求前に別接続でも更新候補が永続化済み");
    assert.equal(persisted.rows[0].stateCiphertext.includes(Buffer.from(first.current)), false);
    assert.deepEqual(await tx.read(), first);
    await assert.rejects(tx.save(0, first), { message: "SessionStoreConflict" });
    await assert.rejects(tx.save(1, { ...first, revision: 4 }), { message: "SessionStoreConflict" });
  });
  const row = await sql<{ leaseToken: string | null; leaseUntil: Date | null }>`
    SELECT leaseToken,leaseUntil FROM dbo.portalConnectionState WHERE bindingHash=${key}`.execute(admin);
  assert.equal(row.rows[0].leaseToken, null);
  assert.equal(row.rows[0].leaseUntil, null);
});

test("複数ストアの同時利用を拒否し、失敗後も次の操作がリースを取得できる", localSqlTest, async (context) => {
  const { runtime } = await createLocalSqlTestDatabase(context);
  const first = new SqlConnectionSessionStore(runtime, secret), second = new SqlConnectionSessionStore(runtime, secret);
  const key = binding(), acquired = deferred(), release = deferred();
  const running = first.withExclusive(key, async () => { acquired.resolve(); await release.promise; throw new Error("local callback interrupted"); });
  // 検証対象のPromiseには早期に拒否ハンドラーを付けます。
  const rejected = assert.rejects(running, { message: "SessionStoreUnavailable" });
  await acquired.promise;
  await assert.rejects(second.withExclusive(key, async () => undefined), { message: "SessionStoreBusy" });
  release.resolve(); await rejected;
  await second.withExclusive(key, async (tx) => { assert.equal(await tx.read(), undefined); });
});

test("SQL時刻で失効したワーカーは更新も読取もできず、新リースを解放できない", localSqlTest, async (context) => {
  const { admin, runtime } = await createLocalSqlTestDatabase(context);
  const first = new SqlConnectionSessionStore(runtime, secret), second = new SqlConnectionSessionStore(runtime, secret);
  const key = binding(), value = state(), entered = deferred(), replacementEntered = deferred(), replacementRelease = deferred();
  let stale!: StateTransaction;
  const running = first.withExclusive(key, async (tx) => {
    stale = tx; await tx.save(0, value); entered.resolve(); await replacementEntered.promise;
    await assert.rejects(tx.read(), { message: "SessionStoreLeaseLost" });
    await assert.rejects(tx.save(1, { ...value, revision: 2 }), { message: "SessionStoreLeaseLost" });
  });
  await entered.promise;
  await sql`UPDATE dbo.portalConnectionState SET leaseUntil=DATEADD(second,-1,SYSUTCDATETIME()) WHERE bindingHash=${key}`.execute(admin);
  await assert.rejects(stale.read(), { message: "SessionStoreLeaseLost" });
  const replacement = second.withExclusive(key, async (tx) => {
    replacementEntered.resolve(); await replacementRelease.promise;
    assert.deepEqual(await tx.read(), value, "古いfinallyは新しいリースを削除しない");
    await tx.save(1, { ...value, revision: 2 });
  });
  await running; replacementRelease.resolve(); await replacement;
});

test("永続化直後に通信が途切れても、接続し直したストアが更新候補を回収する", localSqlTest, async (context) => {
  const { runtime, name } = await createLocalSqlTestDatabase(context, false);
  const key = binding(), first = state();
  const saved: PrivateState = { ...first, pending: { rotationId: randomBytes(32).toString("hex"),
    candidateSession: `cs2.${first.sessionId}.${randomBytes(32).toString("hex")}`, fromGeneration: 1, generation: 2, expiresAt: 1999999999 },
    rotationCommitAttempted: true };
  await assert.rejects(new SqlConnectionSessionStore(runtime, secret).withExclusive(key, async (tx) => {
    await tx.save(0, saved); throw new Error("upstream acknowledgement lost");
  }), { message: "SessionStoreUnavailable" });
  const reconnected = new Kysely<Record<string, unknown>>(createAuthDatabase({ ...getSqlEnvironment(), database: name }));
  try {
    await new SqlConnectionSessionStore(reconnected, secret).withExclusive(key, async (tx) => assert.deepEqual(await tx.read(), saved));
  } finally { await reconnected.destroy(); }
});

test("暗号文の行間移植とNULL証明を拒否し、実行IDに削除権限を付与しない", localSqlTest, async (context) => {
  const { admin, runtime } = await createLocalSqlTestDatabase(context);
  const store = new SqlConnectionSessionStore(runtime, secret), one = binding(), two = binding();
  await store.withExclusive(one, async (tx) => tx.save(0, state()));
  await store.withExclusive(two, async (tx) => tx.save(0, state()));
  await sql`UPDATE dbo.portalConnectionState SET stateCiphertext=(SELECT stateCiphertext FROM dbo.portalConnectionState WHERE bindingHash=${one})
    WHERE bindingHash=${two}`.execute(admin);
  await assert.rejects(store.withExclusive(two, (tx) => tx.read()), { message: "SessionStateInvalid" });
  await assert.rejects(sql`UPDATE dbo.portalConnectionState SET stateCiphertext=NULL WHERE bindingHash=${one}`.execute(admin));
  await assert.rejects(sql`DELETE dbo.portalConnectionState WHERE bindingHash=${one}`.execute(runtime));
});

test("準備確認は状態やリースを作らず、スキーマ未適用を正常と扱わない", localSqlTest, async (context) => {
  const { admin, runtime } = await createLocalSqlTestDatabase(context);
  const store = new SqlConnectionSessionStore(runtime, secret);
  await store.assertReady();
  const rows = await sql<{ count: number }>`SELECT COUNT(*) AS count FROM dbo.portalConnectionState`.execute(admin);
  assert.equal(rows.rows[0].count, 0, "ヘルス確認で本人の状態を生成しない");
  await sql`DELETE dbo.portalSchemaVersions WHERE [version]=10`.execute(admin);
  await assert.rejects(store.assertReady(), { message: "SessionStoreUnavailable" });
});
