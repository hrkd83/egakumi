import assert from "node:assert/strict";
import { test } from "node:test";
import { containsModelCredential } from "../lib/model-credential.ts";

const credential = 'synthetic-"quoted"-\\-credential';

test("JSON再エスケープで文字列検索できないキーも、復号済み値とプロパティ名から検出する", () => {
  for (const value of [{ nested: [null, { text: credential }] }, { [credential]: "synthetic marker" }]) {
    const raw = JSON.stringify(value);
    assert.equal(raw.includes(credential), false);
    assert.equal(containsModelCredential(JSON.parse(raw), credential, raw), true);
    const encoded = [...credential].map((character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`).join("");
    const unicode = raw.replace(JSON.stringify(credential).slice(1, -1), encoded);
    assert.equal(containsModelCredential(JSON.parse(unicode), credential, unicode), true);
  }
});

test("深い構造と循環参照を再帰せずに検査し、不一致を反射と誤認しない", () => {
  let value: unknown = { safe: "synthetic text" };
  for (let index = 0; index < 30_000; index++) value = { next: value };
  assert.equal(containsModelCredential(value, credential), false);
  const cycle: Record<string, unknown> = { next: value }; cycle.self = cycle;
  assert.equal(containsModelCredential(cycle, credential), false);
  cycle.nested = [credential];
  assert.equal(containsModelCredential(cycle, credential), true);
});

test("raw本文の完全一致も検査し、空キーや似た別文字列には一致させない", () => {
  assert.equal(containsModelCredential({}, credential, `synthetic ${credential}`), true);
  assert.equal(containsModelCredential({ value: credential.slice(0, -1) }, credential), false);
  assert.equal(containsModelCredential({ value: credential }, ""), false);
});

test("受理可能な数値キーは指数表記からJSONの数値表現へ戻った場合も検出する", () => {
  const numericCredential = "1234567890123456";
  const raw = '{"metadata":1.234567890123456e15}';
  assert.equal(raw.includes(numericCredential), false);
  const value: unknown = JSON.parse(raw);
  assert.equal(JSON.stringify(value).includes(numericCredential), true);
  assert.equal(containsModelCredential(value, numericCredential, raw), true);
});
