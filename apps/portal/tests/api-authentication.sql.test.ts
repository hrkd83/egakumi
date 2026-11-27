import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";
import { test } from "node:test";
import { sql } from "kysely";
import { ApiAuthStore } from "../lib/api-auth/store.ts";
import { createLocalSqlTestDatabase, localSqlTest } from "./helpers/local-sql.ts";

/** 合成セッションを使い、認証と接続解除のSQL境界を実行ユーザーで確認します。 */
test("API認証取引は一回消費し、解除後は同じ世代と期限切れsnapshotを利用できない", localSqlTest, async (context) => {
  const { admin, runtime } = await createLocalSqlTestDatabase(context);
  const tenantId = randomUUID(), objectId = randomUUID(), userId = randomUUID(), sessionId = randomUUID();
  const hash = () => randomBytes(32).toString("hex");
  const binding = { tenantId, objectId, sessionId, hash: hash() };
  await sql`INSERT dbo.portalUser(id,name,email,createdAt,updatedAt,entraTenantId,entraObjectId)
    VALUES(${userId},'Example User','user@example.invalid',SYSUTCDATETIME(),SYSUTCDATETIME(),${tenantId},${objectId})`.execute(admin);
  await sql`INSERT dbo.portalSession(id,expiresAt,token,createdAt,updatedAt,userId)
    VALUES(${sessionId},DATEADD(hour,1,SYSUTCDATETIME()),${hash()},SYSUTCDATETIME(),SYSUTCDATETIME(),${userId})`.execute(admin);
  const store = new ApiAuthStore(runtime);
  await store.assertReady();
  const attempt = randomUUID(), state = hash(), correlation = hash(), ciphertext = randomBytes(75);
  assert.equal(await store.begin(binding, attempt), 0);
  await store.putTransaction(binding, attempt, 0, state, correlation, ciphertext);
  assert.equal(await store.pendingTransaction(state, hash()), undefined);
  const pending = await store.pendingTransaction(state, correlation);
  assert.equal(pending?.attemptId.toLowerCase(), attempt);
  assert.equal(await store.recordCode(attempt, state, correlation, ciphertext), true);
  assert.equal(await store.recordCode(attempt, state, correlation, ciphertext), false);
  assert.equal(await store.takeTransaction({ ...binding, objectId: randomUUID() }, correlation), undefined);
  assert.equal((await store.takeTransaction(binding, correlation))?.attemptId.toLowerCase(), attempt);
  assert.equal(await store.takeTransaction(binding, correlation), undefined);
  const initial = await store.context(binding);
  assert.ok(initial);
  const validUntil = new Date(initial.now.getTime() + 600_000);
  assert.equal(await store.saveAuthorization(binding, initial, attempt, ciphertext, hash(), validUntil, validUntil), true);
  const operation = randomUUID(), operationHash = hash();
  assert.equal(await store.putOperation(binding, 0, "document", operation, operationHash, ciphertext, validUntil), true);
  assert.deepEqual(Buffer.from((await store.getOperation(binding, 0, "document", operation, operationHash))!), ciphertext);
  assert.equal(await store.getOperation(binding, 0, "realtime", operation, operationHash), undefined);
  const current = await store.context(binding);
  assert.ok(current);
  const lease = randomUUID();
  assert.ok(await store.acquireRefresh(binding, current, lease));
  assert.equal(await store.acquireRefresh(binding, current, randomUUID()), undefined);
  await store.disconnect(binding);
  assert.equal((await store.context(binding))?.generation, 1);
  assert.equal(await store.getOperation(binding, 0, "document", operation, operationHash), undefined);
  assert.equal(await store.saveRefresh(binding, current, lease, ciphertext, validUntil), false);
  assert.equal((await store.claimCallStop(hash(), ciphertext, new Date(initial.now.getTime() - 1000), validUntil)).ciphertext, undefined);
  const cleanup = hash();
  assert.ok((await store.claimCallStop(cleanup, ciphertext, validUntil, validUntil)).ciphertext);
  assert.equal((await store.claimCallStop(cleanup, ciphertext, validUntil, validUntil)).ciphertext, undefined);
  await store.finishCallStop(cleanup, true);
  assert.equal((await store.claimCallStop(cleanup, ciphertext, validUntil, validUntil)).confirmed, true);
});

test("Gatewayと購読管理の認証取引は、同じ本人でもそれぞれの解除や状態に影響しない", localSqlTest, async (context) => {
  const { admin, runtime } = await createLocalSqlTestDatabase(context);
  const tenantId = randomUUID(), objectId = randomUUID(), userId = randomUUID(), sessionId = randomUUID();
  const hash = () => randomBytes(32).toString("hex");
  const gatewayBinding = { tenantId, objectId, sessionId, hash: hash() };
  const managementBinding = { ...gatewayBinding, hash: hash() };
  await sql`INSERT dbo.portalUser(id,name,email,createdAt,updatedAt,entraTenantId,entraObjectId)
    VALUES(${userId},'Example User','user@example.invalid',SYSUTCDATETIME(),SYSUTCDATETIME(),${tenantId},${objectId})`.execute(admin);
  await sql`INSERT dbo.portalSession(id,expiresAt,token,createdAt,updatedAt,userId)
    VALUES(${sessionId},DATEADD(hour,1,SYSUTCDATETIME()),${hash()},SYSUTCDATETIME(),SYSUTCDATETIME(),${userId})`.execute(admin);
  const gateway = new ApiAuthStore(runtime);
  const management = new ApiAuthStore(runtime, "subscription-management");
  await management.assertReady();
  const gatewayAttempt = randomUUID(), managementAttempt = randomUUID(), gatewayState = hash(), managementState = hash();
  const correlation = hash(), ciphertext = randomBytes(75);
  assert.equal(await gateway.begin(gatewayBinding, gatewayAttempt), 0);
  await gateway.putTransaction(gatewayBinding, gatewayAttempt, 0, gatewayState, correlation, ciphertext);
  assert.equal(await management.begin(managementBinding, managementAttempt), 0);
  await management.putTransaction(managementBinding, managementAttempt, 0, managementState, correlation, ciphertext);
  assert.equal(await management.pendingTransaction(gatewayState, correlation), undefined);
  assert.equal(await gateway.pendingTransaction(managementState, correlation), undefined);
  assert.equal((await management.pendingTransaction(managementState, correlation))?.attemptId.toLowerCase(), managementAttempt);
  await gateway.disconnect(gatewayBinding);
  assert.equal((await management.pendingTransaction(managementState, correlation))?.attemptId.toLowerCase(), managementAttempt);
});
