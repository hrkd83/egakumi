import assert from "node:assert/strict";
import { test } from "node:test";
import { getRealtimeModels } from "../lib/model-catalog.ts";
import { modelSelectionScope, resolveInitialModel } from "../lib/model-selection.ts";
import { createPortalStore } from "../lib/portal-store.ts";
import { getRealtimeSeries, groupRealtimeSeries, REALTIME_SERIES } from "../lib/realtime-series.ts";
import { groupRealtimeModels } from "../lib/realtime-model-choices.ts";
import { defaultRealtimeInput } from "../lib/realtime-integration.ts";

test("音声4シリーズは提供元IDを保った固定順で解決し、未知の識別子は受け付けない", () => {
  assert.deepEqual(REALTIME_SERIES, [
    { id: "gpt-live", family: "live", label: "GPT-Live", providerId: "openai" },
    { id: "gpt-realtime", family: "realtime", label: "GPT Realtime", providerId: "openai" },
    { id: "voice-live-gpt", family: "voice-live", label: "Voice Live + GPT", providerId: "azure-ai-speech" },
    { id: "gpt-live-transcribe", family: "transcription", label: "GPT Live Transcribe", providerId: "openai" },
  ]);
  for (const series of REALTIME_SERIES) assert.equal(getRealtimeSeries(series.id), series);
  for (const id of ["openai", "azure-ai-speech", "GPT-Live", "missing", ""]) assert.equal(getRealtimeSeries(id), undefined);
});

test("公開カタログのモデルと方式をfamilyだけで分類し、入力順・提供元・登録内容を保持する", () => {
  const models = Object.freeze(getRealtimeModels().reverse());
  const groups = groupRealtimeSeries(models);
  assert.deepEqual(groups.map((series) => series.id), REALTIME_SERIES.map((series) => series.id));
  for (const series of groups) {
    assert.deepEqual(series.models, models.filter((model) => model.family === series.family));
    for (const model of series.models) assert.ok(models.includes(model));
  }
  assert.equal(groups.flatMap((series) => series.models).length, models.length);
  const mini = groups.find((series) => series.id === "gpt-realtime")!.models.filter((model) => model.modelId.endsWith("-mini"));
  assert.deepEqual(mini.map((model) => model.transport), ["websocket"]);
  const changedProvider = { ...models.find((model) => model.family === "live")!, providerId: "azure-ai-speech" as const };
  assert.equal(groupRealtimeSeries([changedProvider])[0].models[0], changedProvider);
});

test("配備されていないfamilyのシリーズや空カタログに架空モデルを追加しない", () => {
  const models = getRealtimeModels().filter((model) => model.family === "voice-live");
  assert.deepEqual(groupRealtimeSeries(models).map((series) => series.id), ["voice-live-gpt"]);
  assert.deepEqual(groupRealtimeSeries([]), []);
});

test("初回は対応モデルのWebRTCを選び、WebSocket専用モデルと明示選択はその方式を使う", () => {
  for (const series of groupRealtimeSeries(getRealtimeModels())) {
    const initial = resolveInitialModel(series.models)!;
    assert.equal(initial.transport, series.family === "transcription" ? "websocket" : "webrtc");
    assert.equal(defaultRealtimeInput(initial).modelId, initial.id);
    for (const choice of groupRealtimeModels(series.models)) {
      const supportsWebRtc = choice.variants.some((model) => model.transport === "webrtc");
      assert.equal(choice.variants[0].transport, supportsWebRtc ? "webrtc" : "websocket");
    }
    const websocket = series.models.find((model) => model.transport === "websocket")!;
    assert.equal(resolveInitialModel(series.models, { id: websocket.id }), websocket);
    assert.equal(resolveInitialModel(series.models, { id: "removed-selection" }), initial);
  }
});

test("同じ提供元でもシリーズ別の選択を保持し、旧提供元スコープや他カテゴリへ干渉しない", () => {
  const store = createPortalStore();
  const groups = groupRealtimeSeries(getRealtimeModels());
  const providerScope = modelSelectionScope("realtime", "openai");
  store.getState().rememberModelSelection(providerScope, { id: "legacy-provider-choice" });
  store.getState().rememberModelSelection(modelSelectionScope("text", "openai"), { id: "text-choice" });
  for (const series of groups) {
    const scope = modelSelectionScope("realtime", series.id);
    assert.equal(store.getState().modelSelections[scope], undefined);
    const selected = series.models.at(-1)!;
    store.getState().rememberModelSelection(scope, { id: selected.id });
  }
  for (const series of groups) {
    const scope = modelSelectionScope("realtime", series.id);
    assert.equal(resolveInitialModel(series.models, store.getState().modelSelections[scope]), series.models.at(-1));
  }
  assert.deepEqual(store.getState().modelSelections[providerScope], { id: "legacy-provider-choice" });
  assert.deepEqual(store.getState().modelSelections[modelSelectionScope("text", "openai")], { id: "text-choice" });
});
