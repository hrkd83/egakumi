import assert from "node:assert/strict";
import { test } from "node:test";
import { encryptConnectionState, decryptConnectionState, MAX_CONNECTION_STATE_BYTES } from "../lib/connection-session/crypto.ts";
import type { PrivateState } from "../lib/connection-session/client.ts";

const binding = "a".repeat(64), secret = "test-only-domain-separated-secret".repeat(2);
const value: PrivateState = { revision: 1, status: "complete", current: `cs2.${"b".repeat(64)}.${"c".repeat(64)}`,
  sessionId: "b".repeat(64), generation: 1 };

test("接続証明はランダム暗号文として往復し、値と利用者の結び付けを保持する", () => {
  const one = encryptConnectionState(binding, value, secret), two = encryptConnectionState(binding, value, secret);
  assert.notDeepEqual(one, two, "同じ状態でもIVを使い回さない");
  assert.equal(one[0], 1);
  assert.equal(one.includes(Buffer.from(value.current)), false, "セッションを平文で保存しない");
  assert.deepEqual(decryptConnectionState(binding, one, secret), value);
});

test("別利用者への移植、暗号文・タグ・鍵版の変更、別の秘密鍵を拒否する", () => {
  const cipher = encryptConnectionState(binding, value, secret);
  for (const changedIndex of [0, 1, 13, cipher.length - 1]) {
    const changed = Buffer.from(cipher); changed[changedIndex] ^= 1;
    assert.throws(() => decryptConnectionState(binding, changed, secret), { message: "SessionStateInvalid" });
  }
  assert.throws(() => decryptConnectionState("d".repeat(64), cipher, secret), { message: "SessionStateInvalid" });
  assert.throws(() => decryptConnectionState(binding, cipher, "a-different-test-only-secret-key".repeat(2)), { message: "SessionStateInvalid" });
});

test("保存上限と状態の版数を検証し、例外へ秘密値を混入しない", () => {
  for (const state of [{ ...value, revision: 0 }, { ...value, current: "x".repeat(MAX_CONNECTION_STATE_BYTES) }]) {
    assert.throws(() => encryptConnectionState(binding, state, secret), { message: "SessionStateInvalid" });
  }
  assert.throws(() => encryptConnectionState("invalid", value, secret), { message: "SessionStateInvalid" });
  assert.throws(() => encryptConnectionState(binding, value, "short"), { message: "SessionStoreConfiguration" });
  for (const data of [Buffer.alloc(0), Buffer.alloc(MAX_CONNECTION_STATE_BYTES + 30)]) {
    assert.throws(() => decryptConnectionState(binding, data, secret), { message: "SessionStateInvalid" });
  }
});
