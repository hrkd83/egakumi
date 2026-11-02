import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { applyModelCapabilities, prepareModelCatalog } from "../scripts/prepare-model-catalog.ts";

/** 生成処理の契約を検証するための最小API定義です。 */
function definition(name: string, path = `models/${name}`) {
  return {
    name, path,
    operations: [{ name: "invoke", displayName: "Invoke", method: "POST", urlTemplate: "/invoke" }],
    authorization: { operationConstraints: { invoke: { allowedModels: ["test-model"] } } },
  };
}

test("同じprofileの複数モデルは個別上限を保ち、可変上限を512へ置き換えない", () => {
  const source = definition("text");
  source.authorization.operationConstraints.invoke.allowedModels = ["small", "large", "router"];
  const entries = [
    { modelId: "small", maxOutputTokens: 128000, limitKind: "documented" as const },
    { modelId: "large", maxOutputTokens: 384000, limitKind: "documented" as const },
    { modelId: "router", maxOutputTokens: null, limitKind: "dynamic" as const },
  ].map((entry) => ({ ...entry, apiIds: ["text"], operationIds: ["invoke"], sourceUrls: ["https://example.invalid/official-specification"], checkedAt: "2026-10-31" }));
  const value = applyModelCapabilities([source], { schemaVersion: 1, entries });
  assert.deepEqual(value[0].authorization.operationConstraints.invoke, { allowedModels: ["small", "large", "router"], maxOutputTokens: 384000,
    maxOutputTokensByModel: { small: 128000, large: 384000, router: null }, outputTokenLimitKinds: { small: "documented", large: "documented", router: "dynamic" } });
  assert.deepEqual(source.authorization.operationConstraints.invoke, { allowedModels: ["small", "large", "router"] });
  assert.throws(() => applyModelCapabilities([source], { schemaVersion: 1, entries: entries.slice(1) }), /no verified capability/);
});

test("正本のModel APIだけを取り込み、再実行で正本の変更を反映する", async () => {
  const folder = await mkdtemp(join(tmpdir(), "egakumi-model-catalog-"));
  try {
    const sourceDirectory = join(folder, "apis");
    const snapshotFile = join(folder, "generated", "catalog.json");
    await mkdir(sourceDirectory);
    await writeFile(join(sourceDirectory, "z-model.json"), JSON.stringify(definition("z-model")));
    await writeFile(join(sourceDirectory, "a-model.json"), JSON.stringify(definition("a-model")));
    await writeFile(join(sourceDirectory, "tool.json"), JSON.stringify(definition("tool", "tools/test")));
    assert.deepEqual(await prepareModelCatalog({ sourceDirectory, snapshotFile }), { mode: "generated", apiCount: 2 });
    const snapshot = JSON.parse(await readFile(snapshotFile, "utf8"));
    assert.equal(snapshot.schemaVersion, 1);
    assert.deepEqual(snapshot.definitions.map((item: { name: string }) => item.name), ["a-model", "z-model"]);
    await writeFile(join(sourceDirectory, "a-model.json"), JSON.stringify(definition("updated-model")));
    await prepareModelCatalog({ sourceDirectory, snapshotFile });
    assert.match(await readFile(snapshotFile, "utf8"), /updated-model/);
  } finally { await rm(folder, { recursive: true, force: true }); }
});

test("正本を含まないコンテナーでは検証済みスナップショットを再利用する", async () => {
  const folder = await mkdtemp(join(tmpdir(), "egakumi-model-catalog-"));
  try {
    const sourceDirectory = join(folder, "missing-apis");
    const snapshotFile = join(folder, "catalog.json");
    await writeFile(snapshotFile, JSON.stringify({ schemaVersion: 1, definitions: [definition("bundled-model")] }));
    assert.deepEqual(await prepareModelCatalog({ sourceDirectory, snapshotFile }), { mode: "bundled", apiCount: 1 });
    await writeFile(snapshotFile, JSON.stringify({ schemaVersion: 0, definitions: [] }));
    await assert.rejects(prepareModelCatalog({ sourceDirectory, snapshotFile }));
  } finally { await rm(folder, { recursive: true, force: true }); }
});

test("正本に不正なJSONがあるときは古いスナップショットへ退避しない", async () => {
  const folder = await mkdtemp(join(tmpdir(), "egakumi-model-catalog-"));
  try {
    const sourceDirectory = join(folder, "apis");
    const snapshotFile = join(folder, "catalog.json");
    await mkdir(sourceDirectory);
    await writeFile(snapshotFile, JSON.stringify({ schemaVersion: 1, definitions: [definition("old-model")] }));
    await writeFile(join(sourceDirectory, "broken.json"), "{");
    await assert.rejects(prepareModelCatalog({ sourceDirectory, snapshotFile }));
    assert.match(await readFile(snapshotFile, "utf8"), /old-model/);
  } finally { await rm(folder, { recursive: true, force: true }); }
});

test("公開モデルの既定値を配備と同じファイルから解決し、接続先や認証変数を解決しない", async () => {
  const folder = await mkdtemp(join(tmpdir(), "egakumi-public-models-"));
  try {
    const sourceDirectory = join(folder, "apis"); const snapshotFile = join(folder, "catalog.json");
    await mkdir(sourceDirectory);
    const bindings = { VOICE_LIVE_MODEL: "gpt-4.1", VOICE_LIVE_CALLS_MODEL: "gpt-4.1", VOICE_LIVE_API_VERSION: "2026-04-10", VOICE_LIVE_CALLS_API_VERSION: "2026-01-01-preview" };
    await writeFile(join(folder, "public-bindings.json"), JSON.stringify({ schemaVersion: 1, bindings }));
    await writeFile(join(sourceDirectory, "voice.json"), JSON.stringify({ ...definition("voice"), backendUrl: "__VOICE_LIVE_WS_BASE_URL__", websocket: { queryParameters: { model: "__VOICE_LIVE_MODEL__" } } }));
    await prepareModelCatalog({ sourceDirectory, snapshotFile });
    const snapshot = JSON.parse(await readFile(snapshotFile, "utf8"));
    assert.equal(snapshot.definitions[0].websocket.queryParameters.model, "gpt-4.1");
    assert.equal(snapshot.definitions[0].backendUrl, "__VOICE_LIVE_WS_BASE_URL__");
    await writeFile(join(folder, "public-bindings.json"), JSON.stringify({ schemaVersion: 1, bindings: { ...bindings, SECRET: "forbidden" } }));
    await assert.rejects(prepareModelCatalog({ sourceDirectory, snapshotFile }));
  } finally { await rm(folder, { recursive: true, force: true }); }
});
