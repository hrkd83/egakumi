import assert from "node:assert/strict";
import { test } from "node:test";
import { apiKeyGroupPolicySchema, groupNamePatternSchema, isEligibleApiKeyGroup } from "../lib/api-keys/group-policy.ts";
import { sqlFailure } from "../scripts/auth-migrations.ts";

const group = (displayName: string, securityEnabled = true) => ({
  id: "11111111-1111-4111-8111-111111111111", displayName, securityEnabled,
});

test("対象名は企業別の正規表現で選び、接頭辞・選択肢・末尾条件・日本語を扱える", () => {
  const cases = [
    { groupNamePattern: "^ORG_", allowed: "ORG_営業", denied: "org_営業" },
    { groupNamePattern: "^(DEPT_|DIV_)[A-Z]+$", allowed: "DIV_HR", denied: "ORG_HR" },
    { groupNamePattern: "_HR$", allowed: "Company_HR", denied: "HR_Company" },
    { groupNamePattern: "^組織_[0-9]{3}$", allowed: "組織_123", denied: "組織_12" },
    { groupNamePattern: "^\\p{L}+$", allowed: "人事", denied: "人事123" },
  ];
  for (const item of cases) {
    assert.equal(groupNamePatternSchema.safeParse(item.groupNamePattern).success, true);
    assert.equal(isEligibleApiKeyGroup(group(item.allowed), item), true);
    assert.equal(isEligibleApiKeyGroup(group(item.denied), item), false);
    assert.equal(isEligibleApiKeyGroup(group(item.allowed, false), item), false);
  }
});

test("空欄や未対応の先読み・後方参照を、全グループ許可へ置き換えない", () => {
  for (const value of ["", "   ", "[", "(?=ORG_)ORG_", "(ORG_)\\1", "x".repeat(513), "^ORG_\n"]) {
    assert.equal(groupNamePatternSchema.safeParse(value).success, false, JSON.stringify(value));
  }
  const platformGroupId = "22222222-2222-4222-8222-222222222222";
  assert.equal(apiKeyGroupPolicySchema.safeParse({ platformGroupId }).success, false);
  // 全名を許可する場合でも、設定者の明示指定とセキュリティグループ条件を要求します。
  assert.equal(isEligibleApiKeyGroup(group("任意名"), { groupNamePattern: ".*" }), true);
  assert.equal(isEligibleApiKeyGroup(group("任意名", false), { groupNamePattern: ".*" }), false);
});

test("同じ名前にも現在の設定を使い、正規表現の状態を呼び出し間で共有しない", () => {
  const value = group("Company_HR");
  for (let count = 0; count < 5; count++) {
    assert.equal(isEligibleApiKeyGroup(value, { groupNamePattern: "_HR$" }), true);
    assert.equal(isEligibleApiKeyGroup(value, { groupNamePattern: "^OTHER_" }), false);
  }
});

test("配備時の名前規則エラーはSQL接続障害と区別し、設定値や内部例外を表示しない", () => {
  const result = apiKeyGroupPolicySchema.safeParse({
    platformGroupId: "22222222-2222-4222-8222-222222222222", groupNamePattern: "(?=private-pattern)",
  });
  assert.equal(result.success, false);
  if (result.success) return;
  const message = sqlFailure(result.error);
  assert.match(message, /portalApiKeys\.groupNamePattern/);
  assert.doesNotMatch(message, /private-pattern/);
  assert.equal(sqlFailure(new Error("password=do-not-show")), "SQL connection or initialization failed");
});
