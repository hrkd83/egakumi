import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { sql } from "kysely";
import { createLocalSqlTestDatabase, localSqlTest } from "./helpers/local-sql.ts";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { ApiKeyService } from "../lib/api-keys/service.ts";
import type { ApiKeyApim } from "../lib/api-keys/apim.ts";
import { configureApiKeyGroups } from "../scripts/api-key-group-config.ts";
import { configureApiKeyAdministration } from "../scripts/api-key-administration-config.ts";

test("SQLの所有権移管は並行処理でも発行枠・秘密非保持・監査・観測と承認の分離を守る", localSqlTest, async (context) => {
  const { SqlApiKeyRepository } = await import("../lib/api-keys/repository.ts");
  const { SqlApiKeyLimitRepository } = await import("../lib/api-keys/limit-repository.ts");
  const { admin, runtime: runtime } = await createLocalSqlTestDatabase(context);
  const repository = new SqlApiKeyRepository(runtime), limits = new SqlApiKeyLimitRepository(runtime);
  const tenantId = randomUUID(), sourceId = randomUUID(), targetId = randomUUID(), dynamicId = randomUUID();
  const actor = { tenantId, objectId: randomUUID() }, productId = "released-apis";
  const usage = { clientType: "custom-system" as const, companyId: randomUUID(), contactEmail: "ownership-team@example.invalid" };
  await sql`INSERT dbo.portalCompany(tenantId,id,name,nameKey)
    VALUES(${tenantId},${usage.companyId},N'Ownership test company',N'ownership test company')`.execute(admin);
  await configureApiKeyGroups(admin, tenantId, [
    { groupId: sourceId, products: [{ productId, maxSubscriptions: 6 }] },
    { groupId: targetId, products: [{ productId, maxSubscriptions: 1 }] },
  ]);
  await configureApiKeyAdministration(admin, { tenantId, platformGroupId: sourceId, groupNamePattern: "^ORG_",
    products: [{ productId, displayName: "Models", maxSubscriptions: 1 }] });
  await sql`INSERT dbo.portalApiKeyCatalogProduct(tenantId,productId,displayName,maxSubscriptions,enabled)
    SELECT ${tenantId},${productId},'Models',1,1 WHERE NOT EXISTS(SELECT 1 FROM dbo.portalApiKeyCatalogProduct WHERE tenantId=${tenantId} AND productId=${productId})`.execute(admin);
  const checkedAt = new Date().toISOString();
  await repository.observeGroups(tenantId, [sourceId, targetId].map((id) => ({ id,
    displayName: id === sourceId ? "ORG_Source" : "ORG_Target", securityEnabled: true, status: "active" as const, checkedAt, errorCode: null })));
  await repository.ensureRegistry(tenantId, [{ id: dynamicId, displayName: "ORG_Dynamic", securityEnabled: true }]);
  await repository.observeGroups(tenantId, [{ id: dynamicId, displayName: "ORG_Dynamic", securityEnabled: true,
    status: "active", checkedAt, errorCode: null }]);
  const groups = await repository.getGroups(tenantId);
  assert.equal(groups.find((group) => group.id === dynamicId)?.products[0]?.maxSubscriptions, 1);
  await sql`UPDATE dbo.portalApiKeyProduct SET enabled=0,maxSubscriptions=7
    WHERE tenantId=${tenantId} AND groupId=${dynamicId} AND productId=${productId};
    INSERT dbo.portalApiKeyCatalogProduct(tenantId,productId,displayName,maxSubscriptions,enabled)
      VALUES(${tenantId},'pii-users','PII',2,1);`.execute(admin);
  await repository.ensureRegistry(tenantId, [dynamicId, sourceId].map((id) => ({ id,
    displayName: id === sourceId ? "ORG_Source" : "ORG_Dynamic", securityEnabled: true })));
  const dynamicProducts = (await repository.getGroups(tenantId)).find((group) => group.id === dynamicId)?.products;
  assert.deepEqual(dynamicProducts?.map((product) => product.id), ["pii-users"], "追加カタログは動的グループへ不足分だけ補完する");
  const override = (await sql<{ enabled: boolean; maxSubscriptions: number }>`SELECT enabled,maxSubscriptions
    FROM dbo.portalApiKeyProduct WHERE tenantId=${tenantId} AND groupId=${dynamicId} AND productId=${productId}`.execute(admin)).rows[0];
  assert.equal(Boolean(override?.enabled), false); assert.equal(override?.maxSubscriptions, 7, "既存の無効化・個別上限を変更しない");
  assert.deepEqual((await repository.getGroups(tenantId)).find((group) => group.id === sourceId)?.products.map((product) => product.id), ["pii-users", productId],
    "配備グループへも通常カタログの不足分だけ補充し、既存の個別承認を維持する");
  const records = [];
  for (const tokens of [10, 20]) {
    const reserved = await repository.reserve(actor, { ...usage, groupId: sourceId, productId, displayName: `Test ${tokens}`, idempotencyKey: randomUUID() });
    const record = await repository.withLockedKey(actor, reserved.record.id, (context) => context.updateState("active"));
    await limits.update(actor, record, { expectedVersion: 0, requestRate: null, tokensPerMinute: tokens, tokenQuota: null });
    records.push(record);
  }
  const profiles = await Promise.all(records.map((record) => limits.get(tenantId, record.id)));
  const moved = await Promise.allSettled(records.map((record) => repository.transferOwnership(actor, record.id,
    { targetGroupId: targetId, expectedSourceGroupId: sourceId }, async () => undefined)));
  assert.equal(moved.filter((item) => item.status === "fulfilled").length, 1, "並行する移管も受入枠を超えない");
  const failed = moved.find((item) => item.status === "rejected");
  assert.ok(failed?.status === "rejected" && failed.reason instanceof ApiKeyError && failed.reason.code === "TargetSubscriptionLimitReached");
  const winner = moved.find((item) => item.status === "fulfilled"); assert.ok(winner?.status === "fulfilled");
  assert.equal(winner.value.groupId, targetId); assert.equal(winner.value.productId, productId); assert.equal(winner.value.state, "active");
  assert.deepEqual(await Promise.all(records.map((record) => limits.get(tenantId, record.id))), profiles, "キーIDに紐付く制限設定と版を維持する");
  await assert.rejects(repository.transferOwnership(actor, winner.value.id, { targetGroupId: dynamicId, expectedSourceGroupId: sourceId }, async () => undefined),
    (error: unknown) => error instanceof ApiKeyError && error.code === "OwnershipConflict");
  const audit = (await sql<{ sourceGroupId: string; targetGroupId: string; actorObjectId: string }>`SELECT sourceGroupId,targetGroupId,actorObjectId
    FROM dbo.portalApiKeyTransferAudit WHERE tenantId=${tenantId}`.execute(admin)).rows;
  assert.deepEqual(audit, [{ sourceGroupId: sourceId, targetGroupId: targetId, actorObjectId: actor.objectId }]);
  await assert.rejects(sql`DELETE dbo.portalApiKeyTransferAudit WHERE tenantId=${tenantId}`.execute(runtime));
  await assert.rejects(sql`UPDATE dbo.portalApiKeyGroup SET enabled=0 WHERE tenantId=${tenantId}`.execute(runtime));

  const actualId = "verification-model-users";
  await sql`UPDATE dbo.portalApiKey SET apimSubscriptionId=${actualId},creationSource='deployment',createdByObjectId=NULL
    WHERE tenantId=${tenantId} AND id=${winner.value.id};
    INSERT dbo.portalApiKeyImportAudit(id,runId,manifestHash,tenantId,apiKeyId,apimResourceId,apimSubscriptionId,apimScope,groupId,productId)
    VALUES(${randomUUID()},${randomUUID()},${"a".repeat(64)},${tenantId},${winner.value.id},'/test/apim',${actualId},${'/test/apim/products/' + productId},${targetId},${productId});`.execute(admin);
  assert.equal((await repository.getKey(tenantId, winner.value.id))?.apimSubscriptionId, actualId);
  assert.deepEqual(await limits.getGatewayProfile(tenantId, actualId), await limits.get(tenantId, winner.value.id));
  assert.equal(await limits.getGatewayProfile(tenantId, winner.value.id), null, "ゲートウェイは実APIM IDを内部UUIDと混同しない");
  const otherId = records.find((record) => record.id !== winner.value.id)!.id;
  await assert.rejects(sql`UPDATE dbo.portalApiKey SET apimSubscriptionId=${actualId.toUpperCase()} WHERE tenantId=${tenantId} AND id=${otherId}`.execute(admin),
    "実IDの大文字小文字だけを変えた二重登録を許可しない");
  await assert.rejects(sql`UPDATE dbo.portalApiKey SET creationSource='portal' WHERE tenantId=${tenantId} AND id=${winner.value.id}`.execute(admin),
    "通常発行は実本人OIDを必須にする");

  // 実プール上限と同じ5接続をすべて排他中にし、別接続へ自己待ちしないことを確認します。
  for (let number = 0; number < 3; number++) {
    const reserved = await repository.reserve(actor, { ...usage, groupId: sourceId, productId, displayName: `Parallel ${number}`, idempotencyKey: randomUUID() });
    records.push(await repository.withLockedKey(actor, reserved.record.id, (context) => context.updateState("active")));
  }
  let arrivals = 0; let release: () => void = () => undefined;
  const allLocked = new Promise<void>((resolve) => { release = resolve; });
  const releaseAfterFailure = setTimeout(release, 10_000);
  const apim: ApiKeyApim = {
    scopeFor: (product) => `/apim/products/${product}`,
    get: async (id) => ({ id, displayName: "Test", scope: `/apim/products/${productId}`, state: "active", etag: '"test"' }),
    create: async () => { throw new Error("SQL検証ではAzureの購読を作成しない"); },
    productIncludesApi: async () => { throw new Error("SQL検証ではAzureのAPI所属を照会しない"); },
    setState: async () => { throw new Error("SQL検証ではAzureの購読を変更しない"); },
    delete: async () => { throw new Error("SQL検証ではAzureの購読を削除しない"); },
    secrets: async () => { throw new Error("SQL検証ではAzureのキーを取得しない"); },
  };
  const service = new ApiKeyService({ tenantId, repository, apim,
    membership: { check: async (_actor, ids) => new Set(ids) },
    directory: {
      listEligibleGroups: async () => [], listEligibleGroupPage: async () => ({ groups: [], nextCursor: null }),
      checkGroups: async (_actor, ids) => {
        arrivals++; if (arrivals === 5) { clearTimeout(releaseAfterFailure); release(); } await allLocked;
        return new Map(ids.map((id) => [id, { id, displayName: id === sourceId ? "ORG_Source" : "ORG_Target",
          securityEnabled: true, status: "active" as const, checkedAt: new Date().toISOString(), errorCode: null }]));
      },
    },
  });
  const reviewed = await Promise.allSettled(records.map((record) => service.act(actor, record.id, "review")));
  clearTimeout(releaseAfterFailure);
  assert.equal(reviewed.filter((result) => result.status === "rejected").length, 0,
    reviewed.map((result) => result.status === "fulfilled" ? "ok" : result.reason instanceof ApiKeyError ? result.reason.code : "failed").join(","));
  assert.equal(arrivals, 5);

  // 観測だけではAPI実行を自動停止しません。停止判断は承認設定・購読状態と分けます。
  const observedAt = new Date(Date.now() + 1000).toISOString();
  await repository.observeGroups(tenantId, [{ id: targetId, displayName: null, securityEnabled: null,
    status: "deleted", checkedAt: observedAt, errorCode: "GroupNotFound" }]);
  assert.deepEqual(await limits.getGatewayProfile(tenantId, actualId), await limits.get(tenantId, winner.value.id));
  await repository.observeGroups(tenantId, [{ id: targetId, displayName: "ORG_OldName", securityEnabled: true,
    status: "active", checkedAt: "2000-01-01T00:00:00.000Z", errorCode: null }]);
  assert.equal((await repository.getGroups(tenantId)).find((group) => group.id === targetId)?.directoryStatus, "deleted");
  assert.equal((await repository.getGroups(tenantId)).find((group) => group.id === targetId)?.displayName, "ORG_Target");

  await sql`UPDATE dbo.portalApiKeyCatalogProduct SET enabled=0 WHERE tenantId=${tenantId}`.execute(admin);
  assert.equal((await repository.getGroups(tenantId)).some((group) => group.id === dynamicId), false, "動的な承認はカタログ無効化へ追従する");
  assert.ok((await repository.getGroups(tenantId)).some((group) => group.id === sourceId), "配備時の個別承認を維持する");
});
