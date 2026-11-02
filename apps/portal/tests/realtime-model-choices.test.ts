import assert from "node:assert/strict";
import { test } from "node:test";
import { chooseRealtimeVariant, groupRealtimeModels, updateRealtimeModelSelection } from "../lib/realtime-model-choices.ts";
import type { RealtimeModel } from "../lib/model-types.ts";
import { getRealtimeAudioDictionary } from "../lib/i18n/realtime-audio.ts";
import { defaultRealtimeInput, getRealtimeRequestExample } from "../lib/realtime-integration.ts";

/** 表示名が同じでも、モデルIDと接続契約を区別できるカタログを用意します。 */
function model(family: RealtimeModel["family"], modelId: string, transport: RealtimeModel["transport"]): RealtimeModel {
  return { id: `${family}-${modelId}-${transport}`, label: "Same label", providerId: family === "voice-live" ? "azure-ai-speech" : "openai", provider: "Provider", family, modelId, transport, apiId: `${family}-${transport}`, operationId: "invoke", path: "/models/fixture", voice: family === "voice-live" ? "ja-JP-NanamiNeural" : "marin", maxRequestBytes: 65536, maxOutputTokens: family === "live" || family === "transcription" ? null : family === "voice-live" ? 128_000 : 4_096, ...(family === "voice-live" ? { numericMaxOutputTokens: 4_096, supportsMaximumOutput: true } : {}) };
}
const entries = [
  model("realtime", "gpt-realtime-mini", "websocket"),
  model("realtime", "gpt-realtime-2.1", "websocket"), model("realtime", "gpt-realtime-2.1", "webrtc"),
  model("live", "gpt-live-1", "websocket"), model("live", "gpt-live-1", "webrtc"),
  model("voice-live", "gpt-6.1-sol", "websocket"), model("voice-live", "gpt-6.1-sol", "webrtc"),
  model("transcription", "gpt-live-transcribe", "websocket"),
];

test("モデルと接続方式を別々に選べるよう、同じモデルの対応方式だけをまとめる", () => {
  const choices = groupRealtimeModels(entries); assert.equal(choices.length, 5);
  assert.deepEqual(choices.map((choice) => [choice.modelId, choice.variants.map((entry) => entry.transport)]), [
    ["gpt-realtime-mini", ["websocket"]], ["gpt-realtime-2.1", ["websocket", "webrtc"]],
    ["gpt-live-1", ["websocket", "webrtc"]], ["gpt-6.1-sol", ["websocket", "webrtc"]], ["gpt-live-transcribe", ["websocket"]],
  ]);
  assert.equal(entries.length, 8);
});

test("表示名やモデル文字列が同じでも、提供元とAPI種別が異なるものはまとめない", () => {
  const first = model("realtime", "shared-model", "websocket");
  const choices = groupRealtimeModels([first, { ...first, id: "other-provider", providerId: "azure-ai-speech" }, { ...first, id: "other-family", family: "transcription" }]);
  assert.equal(choices.length, 3); assert.equal(new Set(choices.map((choice) => choice.id)).size, 3);
});

test("モデル変更時は現在の接続方式を維持し、非対応のminiではWebSocketへ戻る", () => {
  const choices = groupRealtimeModels(entries);
  for (const choice of choices.filter((entry) => entry.variants.length === 2)) {
    assert.equal(chooseRealtimeVariant(choice, "webrtc").transport, "webrtc");
    assert.equal(chooseRealtimeVariant(choice, "websocket").transport, "websocket");
  }
  assert.equal(chooseRealtimeVariant(choices[0], "webrtc").id, entries[0].id);
});

test("初回・モデル・方式の変更で最大値を選び、実APIが変わればキー選択を更新する", () => {
  const previous = entries[1], next = { ...entries[2], maxOutputTokens: 192 };
  const current = { input: { modelId: previous.id, maxOutputTokens: 400 }, apiKeyId: "chosen-key" };
  assert.deepEqual(updateRealtimeModelSelection(current, previous, next), { input: { modelId: next.id, maxOutputTokens: 192 }, apiKeyId: "" });
  assert.equal(updateRealtimeModelSelection(current, previous, entries[0]).apiKeyId, "chosen-key");
  assert.equal(updateRealtimeModelSelection({ ...current, input: { ...current.input, maxOutputTokens: Number.NaN } }, previous, next).input.maxOutputTokens, 192);
  assert.equal(updateRealtimeModelSelection(current, undefined, next).apiKeyId, "");
  const larger = { ...next, maxOutputTokens: 16_384 };
  assert.equal(updateRealtimeModelSelection({ ...current, input: { ...current.input, maxOutputTokens: 8_192 } }, previous, larger).input.maxOutputTokens, 16_384);
  assert.equal(updateRealtimeModelSelection({ ...current, input: { ...current.input, maxOutputTokens: 1 } }, previous, larger).input.maxOutputTokens, 16_384);
  assert.equal(defaultRealtimeInput(entries[0]).maxOutputTokens, 4_096);
  assert.equal(defaultRealtimeInput(entries[1]).maxOutputTokens, 4_096);
});

test("Voice Liveは初回からモデル最大を選び、数値指定へ戻しても別方式では再びモデル最大にする", () => {
  const current = { input: { modelId: entries[5].id, maxOutputTokens: "model-max" as const }, apiKeyId: "key" };
  assert.equal(defaultRealtimeInput(entries[5]).maxOutputTokens, "model-max");
  assert.equal(defaultRealtimeInput(entries[6]).maxOutputTokens, "model-max");
  assert.match(getRealtimeRequestExample(entries[5], defaultRealtimeInput(entries[5])), /"max_response_output_tokens": "inf"/);
  assert.equal(updateRealtimeModelSelection(current, entries[5], entries[6]).input.maxOutputTokens, "model-max");
  assert.equal(updateRealtimeModelSelection(current, entries[5], entries[1]).input.maxOutputTokens, 4_096);
  assert.deepEqual(updateRealtimeModelSelection(current, entries[5], entries[3]).input, { modelId: entries[3].id });
  assert.equal(updateRealtimeModelSelection({ ...current, input: { ...current.input, maxOutputTokens: 1 } }, entries[5], entries[6]).input.maxOutputTokens, "model-max");
});

test("同じモデル・方式で利用者が入力した数値は保持し、Live・文字起こしに出力設定を追加しない", () => {
  for (const selected of [entries[0], entries[1], entries[5], entries[6]]) {
    const input = { modelId: selected.id, maxOutputTokens: 1 };
    assert.equal(updateRealtimeModelSelection({ input, apiKeyId: "key" }, selected, selected).input, input);
  }
  for (const selected of [entries[3], entries[4], entries[7]]) {
    assert.deepEqual(defaultRealtimeInput(selected), { modelId: selected.id });
    assert.deepEqual(updateRealtimeModelSelection({ input: { modelId: entries[1].id, maxOutputTokens: 1 }, apiKeyId: "key" }, entries[1], selected).input, { modelId: selected.id });
  }
});

test("両言語でモデルと接続方式を区別し、出力範囲は選択モデルの上限へ連動する", () => {
  for (const locale of ["ja", "en"] as const) {
    const dictionary = getRealtimeAudioDictionary(locale);
    assert.notEqual(dictionary.model, dictionary.transport); assert.ok(dictionary.transportHelp);
    assert.ok(dictionary.tokenRange.includes("{maximum}")); assert.ok(dictionary.tokenHelp);
  }
  assert.match(getRealtimeAudioDictionary("ja").modelMaximum, /モデル最大/);
  assert.match(getRealtimeAudioDictionary("en").modelMaximum, /Model maximum/);
});
