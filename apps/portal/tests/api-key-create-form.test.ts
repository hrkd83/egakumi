import assert from "node:assert/strict";
import { test } from "node:test";
import { prepareApiKeyIssueAttempt } from "../components/api-keys/create-key-form.ts";
import type { CreateApiKeyInput } from "../lib/api-keys/contracts.ts";

const input: Omit<CreateApiKeyInput, "idempotencyKey"> = {
  groupId: "00000000-0000-4000-8000-000000000001", displayName: "Support app",
  clientType: "custom-system", systemName: "Help desk", teamName: "Platform team",
  companyId: "00000000-0000-4000-8000-000000000002", contactEmail: "platform@example.com",
};

test("結果不明の再送は同じ冪等キーを保ち、前後の空白だけでは別購読を作らない", () => {
  const attempt = prepareApiKeyIssueAttempt(input, null, () => "first-attempt");
  const same = prepareApiKeyIssueAttempt({ ...input, displayName: " Support app ", systemName: " Help desk ", contactEmail: " platform@example.com " }, attempt,
    () => { assert.fail("unchanged input must reuse the previous key"); });
  assert.equal(same, attempt);
});

test("所有者・利用情報・会社・連絡先のいずれを変更しても、以前の要求を上書きしない", () => {
  const attempt = prepareApiKeyIssueAttempt(input, null, () => "first-attempt");
  const changes: Array<Partial<typeof input>> = [
    { groupId: "00000000-0000-4000-8000-000000000003" }, { displayName: "Another app" }, { clientType: "saas" },
    { systemName: "Another system" }, { teamName: "Another team" },
    { companyId: "00000000-0000-4000-8000-000000000004" }, { contactEmail: "another@example.com" },
  ];
  for (const change of changes) {
    const next = prepareApiKeyIssueAttempt({ ...input, ...change }, attempt, () => "next-attempt");
    assert.equal(next.key, "next-attempt");
    assert.notEqual(next.fingerprint, attempt.fingerprint);
  }
});

test("任意項目の空欄と未指定を同じ入力として扱う", () => {
  const attempt = prepareApiKeyIssueAttempt({ ...input, systemName: undefined, teamName: undefined }, null, () => "first-attempt");
  assert.equal(prepareApiKeyIssueAttempt({ ...input, systemName: " ", teamName: "" }, attempt).key, attempt.key);
});
