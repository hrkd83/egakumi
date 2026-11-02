import assert from "node:assert/strict";
import { test } from "node:test";
import { apiKeyGroupConfigurationSchema, readApiKeyGroupConfiguration } from "../scripts/api-key-group-config.ts";

const tenant = "11111111-1111-4111-8111-111111111111";
const group = { groupId: "22222222-2222-4222-8222-222222222222",
  products: [{ productId: "model-users" }] };

test("所有グループ承認は明示的な配備入力だけを使い、既定の発行枠を5にする", () => {
  assert.equal(readApiKeyGroupConfiguration({}), null);
  const input = { PORTAL_API_KEY_TENANT_ID: tenant, PORTAL_API_KEY_GROUPS: JSON.stringify([group]) };
  assert.equal(readApiKeyGroupConfiguration(input)?.groups[0]?.products[0]?.maxSubscriptions, 5);
  assert.deepEqual(readApiKeyGroupConfiguration({ ...input, PORTAL_API_KEY_GROUPS: "[]" })?.groups, []);
});

test("重複グループ・重複製品・上限外の発行枠・任意項目を承認設定から除外する", () => {
  for (const value of [[group, group], [{ ...group, products: [...group.products, ...group.products] }],
    [{ ...group, products: [{ productId: "model-users", maxSubscriptions: 0 }] }],
    [{ ...group, primaryKey: "forbidden" }], [{ ...group, displayName: "ORG_StaleName" }],
    [{ ...group, products: [{ productId: "all/APIs" }] }]]) {
    assert.equal(apiKeyGroupConfigurationSchema.safeParse(value).success, false);
  }
  assert.throws(() => readApiKeyGroupConfiguration({ PORTAL_API_KEY_TENANT_ID: tenant }));
});
