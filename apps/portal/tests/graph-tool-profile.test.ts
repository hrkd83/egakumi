import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { GRAPH_TOOL_PROFILE_FIELDS, GRAPH_TOOL_PROFILE_SELECT, graphToolProfileSchema, graphToolProfileValue } from "../lib/graph-tool-contracts.ts";
import { getGraphToolDictionary } from "../lib/i18n/graph-tool.ts";
import { graphToolSuccessContract } from "../components/tools/graph-tool-examples.ts";

/** Gatewayと公開スキーマの読み取り項目を突合し、属性の追加漏れや任意拡張を検知します。 */
test("本人情報の12属性がGatewayの固定select・画面・JSON仕様で一致する", () => {
  const api = JSON.parse(readFileSync(new URL("../../gateway/apim/apis/graph-profile-v1.json", import.meta.url), "utf8"));
  const operation = api.operations.find((item: { name: string }) => item.name === "graph_profile_self");
  assert.equal(operation.method, "GET");
  assert.equal(operation.urlTemplate, "/me");
  assert.equal(operation.backendUrlTemplate, `/me?$select=${GRAPH_TOOL_PROFILE_SELECT}`);
  assert.equal(GRAPH_TOOL_PROFILE_FIELDS.length, 12);
  assert.deepEqual(Object.keys(graphToolSuccessContract.properties).filter((field) => field !== "@odata.context").sort(), [...GRAPH_TOOL_PROFILE_FIELDS].sort());
  for (const locale of ["ja", "en"] as const) {
    assert.deepEqual(Object.keys(getGraphToolDictionary(locale).profileFields).sort(), [...GRAPH_TOOL_PROFILE_FIELDS].sort());
  }
});

test("すべての任意属性はnull・未返却を受け付け、元の値を保持する", () => {
  const nullable = Object.fromEntries(GRAPH_TOOL_PROFILE_FIELDS.filter((field) => field !== "id").map((field) => [field, null]));
  const expected = { ...nullable, id: "verified-object-id" };
  assert.deepEqual(graphToolProfileSchema.parse(expected), expected);
  assert.deepEqual(graphToolProfileSchema.parse({ id: "verified-object-id" }), { id: "verified-object-id" });
  const complete = { id: "verified-object-id", displayName: "利用者", givenName: "太郎", surname: "山田", userPrincipalName: "taro@example.invalid", jobTitle: "Engineer", companyName: "Example", department: "Development", employeeId: "00001234", officeLocation: "Tokyo", mail: "taro.mail@example.invalid", accountEnabled: true };
  assert.deepEqual(graphToolProfileSchema.parse(complete), complete);
});

test("null・欠損・空文字は記号、accountEnabledのtrueとfalseは別の表示にする", () => {
  for (const locale of ["ja", "en"] as const) {
    const labels = getGraphToolDictionary(locale);
    for (const value of [undefined, null, "", "  "]) {
      assert.equal(graphToolProfileValue({ id: "user", displayName: value }, "displayName", labels), "－");
    }
    assert.equal(graphToolProfileValue({ id: "user", accountEnabled: true }, "accountEnabled", labels), labels.accountEnabled);
    assert.equal(graphToolProfileValue({ id: "user", accountEnabled: false }, "accountEnabled", labels), labels.accountDisabled);
    assert.notEqual(labels.accountDisabled, labels.unavailableField);
    assert.equal(graphToolProfileValue({ id: "user", employeeId: "00001234" }, "employeeId", labels), "00001234");
  }
});

test("文字列のboolや想定外属性・過大属性を拒否し、本人IDにはnullを認めない", () => {
  for (const accountEnabled of ["false", "true", 0, 1, {}, []]) assert.equal(graphToolProfileSchema.safeParse({ id: "user", accountEnabled }).success, false);
  for (const field of GRAPH_TOOL_PROFILE_FIELDS.filter((field) => field !== "id" && field !== "accountEnabled")) {
    assert.equal(graphToolProfileSchema.safeParse({ id: "user", [field]: 123 }).success, false);
    assert.equal(graphToolProfileSchema.safeParse({ id: "user", [field]: "x".repeat(321) }).success, false);
  }
  assert.equal(graphToolProfileSchema.safeParse({ id: null }).success, false);
  assert.equal(graphToolProfileSchema.safeParse({ id: "user", accessToken: "hidden" }).success, false);
});
