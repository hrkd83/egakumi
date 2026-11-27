import assert from "node:assert/strict";
import { test } from "node:test";
import { assertRuntimePrincipal, grantRuntimeProcedureAccess, parseMigrationRuntimeIdentity, parseRuntimeIdentity, portalRuntimePermissions, portalRuntimeProcedures } from "../scripts/auth-runtime-grants.ts";

const environment = {
  PORTAL_SQL_RUNTIME_CLIENT_ID: "00112233-4455-6677-8899-aabbccddeeff",
  PORTAL_SQL_RUNTIME_USER_NAME: "id-test-platform-portal-pull",
};

test("SQLのSIDはEntraアプリIDをGUIDのlittle-endian形式へ変換する", () => {
  const identity = parseRuntimeIdentity(environment);
  assert.ok(identity);
  assert.equal(identity.sid.toString("hex"), "33221100554477668899aabbccddeeff");
  assert.equal(identity.name, environment.PORTAL_SQL_RUNTIME_USER_NAME);
});

test("初期化に必要なIDの欠落・ゼロ値・SQL識別子の混入を拒否する", () => {
  assert.equal(parseRuntimeIdentity({}), null);
  for (const values of [
    { ...environment, PORTAL_SQL_RUNTIME_CLIENT_ID: undefined },
    { ...environment, PORTAL_SQL_RUNTIME_USER_NAME: undefined },
    { ...environment, PORTAL_SQL_RUNTIME_CLIENT_ID: "00000000-0000-0000-0000-000000000000" },
    { ...environment, PORTAL_SQL_RUNTIME_USER_NAME: "invalid]; DROP TABLE portalUser;--" },
  ]) assert.throws(() => parseRuntimeIdentity(values));
});

test("Azure初期化はスキーマ更新用と実行用に異なるEntraクライアントIDを使う", () => {
  const schemaClientId = environment.PORTAL_SQL_RUNTIME_CLIENT_ID.replace("00112233", "33221100");
  assert.deepEqual(parseMigrationRuntimeIdentity({ ...environment, AZURE_CLIENT_ID: schemaClientId }),
    parseRuntimeIdentity(environment));
  for (const value of [undefined, "not-a-guid", "00000000-0000-0000-0000-000000000000",
    environment.PORTAL_SQL_RUNTIME_CLIENT_ID.toUpperCase()]) {
    assert.throws(() => parseMigrationRuntimeIdentity({ ...environment, AZURE_CLIENT_ID: value }), /nonzero and distinct/);
  }
  assert.throws(() => parseMigrationRuntimeIdentity({ AZURE_CLIENT_ID: schemaClientId }), /nonzero and distinct/);
});

test("既存のSQLユーザーが別のIDなら権限を付与せず停止する", () => {
  const identity = parseRuntimeIdentity(environment)!;
  const principal = { name: identity.name, sid: identity.sid, type: "E" };
  assert.doesNotThrow(() => assertRuntimePrincipal(identity, []));
  assert.doesNotThrow(() => assertRuntimePrincipal(identity, [principal]));
  for (const changed of [
    { ...principal, sid: Buffer.alloc(16) },
    { ...principal, type: "S" },
    { ...principal, name: "another-user" },
  ]) assert.throws(() => assertRuntimePrincipal(identity, [changed]));
});

test("Portal実行者は承認設定を読み取り、台帳と監査に必要な権限だけを持つ", async () => {
  assert.equal(portalRuntimePermissions.portalApiKeyGroup, "SELECT");
  assert.equal(portalRuntimePermissions.portalApiKeyProduct, "SELECT");
  assert.equal(portalRuntimePermissions.portalApiKey, "SELECT, INSERT, UPDATE");
  assert.equal(portalRuntimePermissions.portalApiKeyAudit, "SELECT, INSERT");
  assert.equal(portalRuntimePermissions.portalApiKeyAdministration, "SELECT");
  assert.equal(portalRuntimePermissions.portalApiKeyCatalogProduct, "SELECT");
  assert.equal(portalRuntimePermissions.portalApiKeyTransferAudit, "SELECT, INSERT");
  assert.equal(portalRuntimePermissions.portalConnectionState, "SELECT, INSERT, UPDATE");
  assert.deepEqual(portalRuntimeProcedures, ["portalObserveApiKeyGroup", "portalEnsureApiKeyGroup"]);
  await assert.rejects(grantRuntimeProcedureAccess({} as never, "user]; GRANT CONTROL TO PUBLIC;--"));
});
