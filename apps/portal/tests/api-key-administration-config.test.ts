import assert from "node:assert/strict";
import { test } from "node:test";
import { apiKeyAdministrationConfigurationSchema, readApiKeyAdministrationConfiguration } from "../scripts/api-key-administration-config.ts";

const tenantId = "11111111-1111-4111-8111-111111111111";
const platformGroupId = "22222222-2222-4222-8222-222222222222";
const products = [{ productId: "model-users", displayName: "Model", maxSubscriptions: 5 }];

test("配備ジョブのARM再解釈防止用の先頭空白を含むJSONでも製品カタログを読み取れる", () => {
  const result = readApiKeyAdministrationConfiguration({ PORTAL_API_KEY_TENANT_ID: tenantId,
    PORTAL_API_KEY_PLATFORM_GROUP_ID: platformGroupId, PORTAL_API_KEY_GROUP_NAME_PATTERN: "^ORG_",
    PORTAL_API_KEY_CATALOG_PRODUCTS: ` ${JSON.stringify(products)}` });
  assert.deepEqual(result?.products, products);
});

test("基盤管理グループは明示設定を要求し、未設定から管理者を推測しない", () => {
  assert.equal(readApiKeyAdministrationConfiguration({}), null);
  assert.throws(() => readApiKeyAdministrationConfiguration({ PORTAL_API_KEY_PLATFORM_GROUP_ID: platformGroupId }));
  const settings = readApiKeyAdministrationConfiguration({ PORTAL_API_KEY_TENANT_ID: tenantId,
    PORTAL_API_KEY_PLATFORM_GROUP_ID: platformGroupId, PORTAL_API_KEY_CATALOG_PRODUCTS: JSON.stringify(products),
    PORTAL_API_KEY_GROUP_NAME_PATTERN: "^ORG_" });
  assert.deepEqual(settings, { tenantId, platformGroupId, groupNamePattern: "^ORG_", products });
  assert.throws(() => readApiKeyAdministrationConfiguration({ PORTAL_API_KEY_TENANT_ID: tenantId,
    PORTAL_API_KEY_PLATFORM_GROUP_ID: platformGroupId, PORTAL_API_KEY_CATALOG_PRODUCTS: JSON.stringify(products) }),
  "名前規則の未設定を、特定の企業の接頭辞へ読み替えない");
});

test("環境別の正規表現を受け付け、不正な規則・例外・重複製品を拒否する", () => {
  const input = { tenantId, platformGroupId, groupNamePattern: "^ORG_", products };
  assert.equal(apiKeyAdministrationConfigurationSchema.safeParse({ ...input, groupNamePattern: "^PJT_[A-Z0-9]+$" }).success, true);
  for (const candidate of [
    { ...input, groupNamePattern: "" }, { ...input, groupNamePattern: "[" }, { ...input, groupNamePattern: "x".repeat(513) },
    { ...input, allowPlatformGroupException: true }, { ...input, products: [...products, ...products] },
    { ...input, products: [] }, { ...input, products: [{ ...products[0], productId: "../all-apis" }] },
  ]) assert.equal(apiKeyAdministrationConfigurationSchema.safeParse(candidate).success, false);
});
