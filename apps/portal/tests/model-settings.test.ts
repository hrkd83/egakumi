import assert from "node:assert/strict";
import { test } from "node:test";
import { createPortalStore } from "../lib/portal-store.ts";
import { modelSelectionScope, resolveInitialModel } from "../lib/model-selection.ts";
import { restoreModelSettings } from "../lib/model-settings.ts";
import { getEmbeddingModels, getImageGenerationModels, getRealtimeModels, getSpeechModels, getRerankModels } from "../lib/model-catalog.ts";
import { defaultRealtimeInput, validateRealtimeInput } from "../lib/realtime-integration.ts";
import { updateRealtimeModelSelection } from "../lib/realtime-model-choices.ts";
import { getRealtimeVoiceOptions } from "../lib/realtime-voices.ts";
import { defaultEmbeddingInput, buildEmbeddingRequest } from "../lib/embedding-integration.ts";
import { defaultImageInput, buildImageGenerationRequest } from "../lib/image-integration.ts";
import { defaultSpeechInput, buildSpeechRequest, SPEECH_RATES, SPEECH_VOICES } from "../lib/speech-integration.ts";
import { defaultRerankInput, buildRerankRequest } from "../lib/rerank-integration.ts";
import { REALTIME_SERIES } from "../lib/realtime-series.ts";
import type { RealtimeInput } from "../lib/realtime-request.ts";

const realtime = getRealtimeModels();
/** 実画面と同じ公開シリーズIDで設定を分離します。 */
function realtimeScope(family: (typeof realtime)[number]["family"]) { return modelSelectionScope("realtime", REALTIME_SERIES.find((entry) => entry.family === family)!.id); }
/** ローカル会話ストアが破棄された後、同じPortal表示内で設定だけを再構成します。 */
function revisit(portal: ReturnType<typeof createPortalStore>, family: (typeof realtime)[number]["family"]) {
  const models = realtime.filter((entry) => entry.family === family), scope = realtimeScope(family);
  const selected = resolveInitialModel(models, portal.getState().modelSelections[scope])!;
  return { input: restoreModelSettings("realtime", defaultRealtimeInput(selected), portal.getState().modelSettings.realtime[scope], (input) => validateRealtimeInput(selected, input)), apiKeyId: "", connected: false, turns: [] };
}

test("ページ破棄後も4シリーズの設定を独立して復元し、会話や購読は復元しない", () => {
  const portal = createPortalStore();
  for (const family of ["realtime", "voice-live", "live", "transcription"] as const) {
    const selected = realtime.find((entry) => entry.family === family && (family !== "realtime" || entry.modelId.endsWith("-mini")))!;
    assert.ok(selected);
    const scope = realtimeScope(family), options = getRealtimeVoiceOptions(selected);
    const input: RealtimeInput = { ...defaultRealtimeInput(selected), ...(selected.maxOutputTokens !== null ? { maxOutputTokens: family === "realtime" ? 16 : 1 } : {}), ...(options.length ? { voice: options.at(-1)!.id } : {}) };
    portal.getState().rememberModelSelection(scope, { id: selected.id }); portal.getState().setModelSettings("realtime", scope, input);
    const discarded = { input, apiKeyId: "synthetic-ledger", connected: true, turns: ["synthetic prior turn"] }, restored = revisit(portal, family);
    assert.notEqual(restored.input, discarded.input); assert.deepEqual(restored.input, input);
    assert.equal(restored.apiKeyId, ""); assert.equal(restored.connected, false); assert.deepEqual(restored.turns, []);
  }
  assert.equal(revisit(portal, "realtime").input.maxOutputTokens, 16); assert.equal(revisit(portal, "voice-live").input.maxOutputTokens, 1);
  assert.equal(revisit(portal, "live").input.maxOutputTokens, undefined); assert.equal(revisit(portal, "transcription").input.maxOutputTokens, undefined);
});

test("ページ再読み込みに相当する新しいストアでは先頭モデルと既定値から開始する", () => {
  const portal = createPortalStore(), mini = realtime.find((entry) => entry.family === "realtime" && entry.modelId.endsWith("-mini"))!, scope = realtimeScope("realtime");
  portal.getState().rememberModelSelection(scope, { id: mini.id }); portal.getState().setModelSettings("realtime", scope, { modelId: mini.id, maxOutputTokens: 16 });
  assert.equal(revisit(portal, "realtime").input.maxOutputTokens, 16);
  const fresh = createPortalStore(), first = realtime.find((entry) => entry.family === "realtime")!;
  assert.deepEqual(revisit(fresh, "realtime").input, defaultRealtimeInput(first)); assert.deepEqual(fresh.getState().modelSettings.realtime, {});
});

test("実モデル・方式を変えれば最大へ戻り、同一選択の再訪と廃止モデルを区別する", () => {
  const previous = realtime.find((entry) => entry.family === "realtime" && entry.modelId.endsWith("-mini"))!, next = realtime.find((entry) => entry.family === "realtime" && entry.transport === "webrtc")!;
  const current = { input: { modelId: previous.id, maxOutputTokens: 16 }, apiKeyId: "synthetic-ledger" };
  assert.equal(updateRealtimeModelSelection(current, previous, previous).input.maxOutputTokens, 16);
  const changed = updateRealtimeModelSelection(current, previous, next); assert.deepEqual(changed.input, defaultRealtimeInput(next)); assert.equal(changed.apiKeyId, "");
  const portal = createPortalStore(), scope = realtimeScope("realtime"); portal.getState().rememberModelSelection(scope, { id: next.id }); portal.getState().setModelSettings("realtime", scope, changed.input);
  assert.deepEqual(revisit(portal, "realtime").input, changed.input);
  assert.deepEqual(restoreModelSettings("realtime", defaultRealtimeInput(next), { modelId: "synthetic-removed-model", maxOutputTokens: 16 }, (input) => validateRealtimeInput(next, input)), defaultRealtimeInput(next));
  const restricted = { ...next, maxOutputTokens: 8, numericMaxOutputTokens: 8 };
  assert.deepEqual(restoreModelSettings("realtime", defaultRealtimeInput(restricted), { modelId: next.id, maxOutputTokens: 16 }, (input) => validateRealtimeInput(restricted, input)), defaultRealtimeInput(restricted));
  const live = realtime.find((entry) => entry.family === "live")!;
  assert.deepEqual(restoreModelSettings("realtime", defaultRealtimeInput(live), { modelId: live.id, maxOutputTokens: 16 }, (input) => validateRealtimeInput(live, input)), defaultRealtimeInput(live));
});

test("埋め込み次元・用途は提供元別に復元し、共有本文を設定へ追加保存しない", () => {
  const portal = createPortalStore(), openai = getEmbeddingModels().find((entry) => entry.id === "text-embedding-3-large")!, cohere = getEmbeddingModels().find((entry) => entry.providerId === "cohere")!;
  for (const [model, dimensions, inputType] of [[openai, 512, null], [cohere, 256, "search_query"]] as const) {
    const scope = modelSelectionScope("embeddings", model.providerId), input = { ...defaultEmbeddingInput(model), dimensions, inputType, texts: ["synthetic edited text"] };
    portal.getState().setModelSettings("embeddings", scope, input);
    const restored = restoreModelSettings("embeddings", defaultEmbeddingInput(model), portal.getState().modelSettings.embeddings[scope], (value) => buildEmbeddingRequest(model, value));
    assert.equal(restored.dimensions, dimensions); assert.equal(restored.inputType, inputType); assert.deepEqual(restored.texts, defaultEmbeddingInput(model).texts);
  }
  assert.equal(portal.getState().modelSettings.embeddings[modelSelectionScope("embeddings", openai.providerId)].dimensions, 512);
});

test("画像の許可設定と音声の声・速度を復元し、本文は追加保存しない", () => {
  const portal = createPortalStore(), image = getImageGenerationModels().find((entry) => entry.providerId === "black-forest-labs")!, imageScope = modelSelectionScope("image", image.providerId);
  const imageInput = { ...defaultImageInput(image), width: image.minWidth, height: image.minHeight, outputFormat: "png" as const, prompt: "synthetic edited prompt" }; portal.getState().setModelSettings("image", imageScope, imageInput);
  const restored = restoreModelSettings("image", defaultImageInput(image), portal.getState().modelSettings.image[imageScope], (input) => buildImageGenerationRequest(image, input));
  assert.equal(restored.width, image.minWidth); assert.equal(restored.height, image.minHeight); assert.equal(restored.outputFormat, "png"); assert.equal(restored.prompt, defaultImageInput(image).prompt);
  const speech = getSpeechModels().find((entry) => entry.kind === "tts")!, speechScope = modelSelectionScope("speech", speech.providerId), speechInput = { ...defaultSpeechInput(speech), voice: SPEECH_VOICES.at(-1)!, rate: SPEECH_RATES.at(-1)!, text: "synthetic edited speech text" };
  portal.getState().setModelSettings("speech", speechScope, speechInput);
  const restoredSpeech = restoreModelSettings("speech", defaultSpeechInput(speech), portal.getState().modelSettings.speech[speechScope], (input) => buildSpeechRequest(speech, input));
  assert.equal(restoredSpeech.voice, speechInput.voice); assert.equal(restoredSpeech.rate, speechInput.rate); assert.equal(restoredSpeech.text, defaultSpeechInput(speech).text);
});

test("リランクは件数だけを復元し、候補数・現モデルと不整合な古い設定は戻す", () => {
  const portal = createPortalStore(), model = getRerankModels()[0], scope = modelSelectionScope("rerank", model.providerId), edited = { ...defaultRerankInput(model), topN: 1, query: "synthetic edited query", documents: ["synthetic edited doc"] };
  portal.getState().setModelSettings("rerank", scope, edited);
  const restored = restoreModelSettings("rerank", defaultRerankInput(model), portal.getState().modelSettings.rerank[scope], (input) => buildRerankRequest(model, input));
  assert.equal(restored.topN, 1); assert.equal(restored.query, defaultRerankInput(model).query); assert.deepEqual(restored.documents, defaultRerankInput(model).documents);
  assert.deepEqual(restoreModelSettings("rerank", defaultRerankInput(model), { modelId: model.id, topN: model.maxTopN + 1 }, (input) => buildRerankRequest(model, input)), defaultRerankInput(model));
});

test("許可リストは本文・ファイル・キー・接続状態を除き、後からの変更も共有しない", () => {
  const portal = createPortalStore(), scope = "synthetic-scope", extra = { modelId: "synthetic-model", apiKeyId: "synthetic-ledger", handle: "synthetic-handle", connected: true, microphone: {}, result: {}, file: {}, text: "synthetic text", texts: ["synthetic text"], prompt: "synthetic prompt", query: "synthetic query", documents: ["synthetic doc"] };
  const rt = { ...extra, maxOutputTokens: 16, voice: "marin" }; portal.getState().setModelSettings("realtime", scope, rt); rt.maxOutputTokens = 32;
  const embedding = { ...extra, dimensions: 512, inputType: null }; portal.getState().setModelSettings("embeddings", scope, embedding);
  const image = { ...extra, size: "1024x1024", quality: "low", outputFormat: "png" as const, width: 1024, height: 1024 }; portal.getState().setModelSettings("image", scope, image);
  const speech = { ...extra, voice: "ja-JP-NanamiNeural" as const, rate: "0%" as const }; portal.getState().setModelSettings("speech", scope, speech);
  const rerank = { ...extra, topN: 1 }; portal.getState().setModelSettings("rerank", scope, rerank);
  assert.deepEqual(portal.getState().modelSettings.realtime[scope], { modelId: "synthetic-model", voice: "marin", maxOutputTokens: 16 });
  for (const drafts of Object.values(portal.getState().modelSettings)) for (const forbidden of ["apiKeyId", "handle", "connected", "microphone", "result", "file", "text", "texts", "prompt", "query", "documents"]) assert.equal(Object.hasOwn(drafts[scope], forbidden), false, forbidden);
});
