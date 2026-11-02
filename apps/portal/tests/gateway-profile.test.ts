import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { getGatewayProfileSettings, handleGatewayProfile, type GatewayProfileDependencies } from "../lib/api-keys/gateway-profile.ts";
import { unlimitedApiKeyLimits } from "../lib/api-keys/limit-contracts.ts";

const secret = "ab".repeat(64);
const tenantId = "11111111-1111-4111-8111-111111111111";

/** 秘密認証を省かずに通す内部要求です。 */
function request(subscriptionId = "portal-22222222-2222-4222-8222-222222222222", key = secret) {
  return new Request("https://internal.example/api/internal/subscription-profile", {
    method: "GET", headers: { "X-Core-Profile-Key": key, "X-Core-Profile-Subscription": subscriptionId },
  });
}

/** 台帳へアクセスした内容だけを記録するテスト依存です。 */
function fixture() {
  const calls: string[][] = [];
  const dependencies: GatewayProfileDependencies = {
    settings: () => ({ secret, tenantId }),
    readProfile: async (...input) => { calls.push(input); return unlimitedApiKeyLimits(); },
  };
  return { dependencies, calls };
}

test("内部APIはCookieの有無に関係なく専用の秘密認証を要求する", async () => {
  for (const key of ["", "wrong", "cd".repeat(64)]) {
    const { dependencies, calls } = fixture();
    const response = await handleGatewayProfile(request(undefined, key), dependencies);
    assert.equal(response.status, 403); assert.deepEqual(calls, []);
    assert.equal(response.headers.get("cache-control"), "no-store");
  }
});

test("テナントは設定から固定し購読ID以外の自己申告を受け付けない", async () => {
  for (const id of ["../another", "", "x".repeat(257)]) {
    const { dependencies, calls } = fixture();
    assert.equal((await handleGatewayProfile(request(id), dependencies)).status, 400);
    assert.deepEqual(calls, []);
  }
  const { dependencies, calls } = fixture();
  const response = await handleGatewayProfile(request("shared-models"), dependencies);
  assert.equal(response.status, 200); assert.deepEqual(calls, [[tenantId, "shared-models"]]);
  assert.deepEqual(await response.json(), { schemaVersion: 1, subscriptionId: "shared-models", ...unlimitedApiKeyLimits() });
});

test("未管理の購読は照会成功後だけ無制限で返し、SQL障害は503とする", async () => {
  const { dependencies } = fixture();
  dependencies.readProfile = async () => null;
  assert.equal((await handleGatewayProfile(request(), dependencies)).status, 200);
  dependencies.readProfile = async () => { throw new Error("private database details"); };
  const failure = await handleGatewayProfile(request(), dependencies);
  assert.equal(failure.status, 503); assert.doesNotMatch(await failure.text(), /private database/);
  dependencies.readProfile = async () => { throw new ApiKeyError("SubscriptionNotReady", 403); };
  assert.equal((await handleGatewayProfile(request(), dependencies)).status, 403);
});

test("内部APIは過大な識別子・不正な保存済み設定を拒否する", async () => {
  const { dependencies, calls } = fixture();
  assert.equal((await handleGatewayProfile(request("x".repeat(2000)), dependencies)).status, 400);
  assert.deepEqual(calls, []);
  dependencies.readProfile = async () => ({ ...unlimitedApiKeyLimits(), tokensPerMinute: -1 });
  assert.equal((await handleGatewayProfile(request(), dependencies)).status, 503);
});

test("専用鍵の欠落やログイン鍵の代用を受け付けない", () => {
  assert.throws(() => getGatewayProfileSettings({ MICROSOFT_TENANT_ID: tenantId, BETTER_AUTH_SECRET: secret }), ApiKeyError);
  assert.deepEqual(getGatewayProfileSettings({ MICROSOFT_TENANT_ID: tenantId, PORTAL_SUBSCRIPTION_PROFILE_SECRET: secret }), { tenantId, secret });
});
