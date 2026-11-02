import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { z } from "zod";
import { PII_API, PII_SAMPLE } from "../lib/pii-contract.ts";
import { getPiiMcpIntegrationExample } from "../lib/pii-mcp-integration.ts";

const normal = { redactedText: "[Email]", language: "ja", detectedCount: 1, modelVersion: PII_API.modelVersion,
  warningCodes: [], requestId: "11111111-2222-4333-8444-555555555555" };
/** 型・上限・固定メタデータを、実際の表示例に埋め込む検証へ渡します。 */
const vectors = [
  { name: "valid", value: normal, valid: true },
  { name: "nil-uuid", value: { ...normal, requestId: "00000000-0000-0000-0000-000000000000" }, valid: true },
  { name: "invalid-uuid", value: { ...normal, requestId: "not-a-uuid" }, valid: false },
  { name: "wrong-language", value: { ...normal, language: "en" }, valid: false },
  { name: "wrong-version", value: { ...normal, modelVersion: "private-upstream-string" }, valid: false },
  { name: "warnings", value: { ...normal, warningCodes: ["unknown"] }, valid: false },
  { name: "negative-count", value: { ...normal, detectedCount: -1 }, valid: false },
  { name: "fraction-count", value: { ...normal, detectedCount: 0.5 }, valid: false },
  { name: "boolean-count", value: { ...normal, detectedCount: true }, valid: false },
  { name: "unsafe-count", value: { ...normal, detectedCount: Number.MAX_SAFE_INTEGER + 1 }, valid: false },
  { name: "text-type", value: { ...normal, redactedText: 17 }, valid: false },
  { name: "text-limit", value: { ...normal, redactedText: "a".repeat(131072) }, valid: true },
  { name: "text-over", value: { ...normal, redactedText: "a".repeat(131073) }, valid: false },
  { name: "unicode-limit", value: { ...normal, redactedText: "😀".repeat(131072) }, valid: true },
  { name: "unicode-over", value: { ...normal, redactedText: "😀".repeat(131073) }, valid: false },
  { name: "extra-field", value: { ...normal, rawInput: "private-upstream-string" }, valid: false },
  { name: "missing-field", value: { ...normal, requestId: undefined }, valid: false },
];

test("TypeScriptのMCP表示例は公開成功スキーマの型・上限を検証する", () => {
  const source = getPiiMcpIntegrationExample("typescript", PII_SAMPLE, "https://gateway.example.com");
  const schema = source.match(/const outputSchema = ([\s\S]*?\.strict\(\));/);
  assert.ok(schema);
  const parse = new Function("z", `return ${schema[1]};`)(z) as z.ZodType;
  for (const vector of vectors) assert.equal(parse.safeParse(vector.value).success, vector.valid, vector.name);
  assert.ok(source.includes('throw new Error("Invalid PII success result")'));
});

for (const language of ["python", "curl"] as const) {
  test(`${language}のMCP表示例も文字数・安全整数・UUIDと固定メタデータを検証する`, (context) => {
    const python = process.env.PORTAL_TEST_PYTHON ?? "python3";
    const probe = spawnSync(python, ["--version"], { encoding: "utf8", timeout: 5000 });
    if (probe.error || probe.status !== 0) { context.skip("Python 3実行環境が必要です。PORTAL_TEST_PYTHONでも指定できます。"); return; }
    const source = getPiiMcpIntegrationExample(language, PII_SAMPLE, "https://gateway.example.com");
    const start = source.indexOf("def valid_pii_output(value):");
    const end = source.indexOf(language === "python" ? "\n\nasync def main" : "\nraw = pathlib.Path", start);
    assert.ok(start > 0 && end > start);
    const program = "import json, re, sys\n" + source.slice(start, end) +
      "\nrows=json.load(sys.stdin)\nprint(json.dumps([{'name':row['name'],'valid':valid_pii_output(row['value'])} for row in rows]))\n";
    const result = spawnSync(python, ["-X", "utf8", "-c", program], { encoding: "utf8", input: JSON.stringify(vectors), timeout: 5000 });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), vectors.map(({ name, valid }) => ({ name, valid })));
    assert.equal(result.stdout.includes("private-upstream-string"), false);
  });
}
