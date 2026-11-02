import assert from "node:assert/strict";
import { test } from "node:test";
import { getRealtimeModels } from "../lib/model-catalog.ts";
import { getRealtimeVoiceOptions, resolveRealtimeVoice } from "../lib/realtime-voices.ts";
import { buildRealtimeSessionEvent } from "../lib/realtime-protocol.ts";
import { defaultRealtimeInput, getRealtimeIntegrationCode, getRealtimeRequestExample, getRealtimeRequestSchema, validateRealtimeInput } from "../lib/realtime-integration.ts";
import { updateRealtimeModelSelection } from "../lib/realtime-model-choices.ts";

test("系列ごとの音声だけを選べ、文字起こしには声を指定できない", () => {
  for (const model of getRealtimeModels()) {
    const voices = getRealtimeVoiceOptions(model);
    assert.equal(voices.length, model.family === "realtime" ? 10 : model.family === "voice-live" ? 7 : model.family === "live" ? 1 : 0);
    assert.equal(new Set(voices.map((voice) => voice.id)).size, voices.length);
    for (const voice of voices) {
      const input = { ...defaultRealtimeInput(model), voice: voice.id };
      validateRealtimeInput(model, input);
      assert.equal(resolveRealtimeVoice(model, input), voice.id);
      assert.ok(voice.label.ja && voice.label.en);
    }
    for (const voice of ["unregistered", "https://other.invalid", model.family === "voice-live" ? "cedar" : "ja-JP-AoiNeural"]) {
      assert.throws(() => validateRealtimeInput(model, { ...defaultRealtimeInput(model), voice }));
    }
    if (model.family === "transcription") assert.throws(() => validateRealtimeInput(model, { ...defaultRealtimeInput(model), voice: "marin" }));
  }
});

test("選んだ声はWSS開始イベントとWebRTCの制御・DataChannel設定に反映する", () => {
  for (const model of getRealtimeModels().filter((model) => model.family === "realtime" || model.family === "voice-live")) {
    const voice = model.family === "realtime" ? "cedar" : "ja-JP-AoiNeural";
    const input = { ...defaultRealtimeInput(model), voice };
    const example = JSON.parse(getRealtimeRequestExample(model, input));
    if (model.family === "realtime") {
      const event = model.transport === "webrtc" ? example.afterConnect : example.startEvent;
      assert.equal(event.session.audio.output.voice, voice);
      if (model.transport === "webrtc") assert.deepEqual(example.body, { sdp: "<browser-generated audio-only SDP offer>" });
    } else {
      const event = model.transport === "webrtc" ? example.body : example.startEvent;
      assert.deepEqual(event.session.voice, { type: "azure-standard", name: voice });
    }
    for (const language of ["typescript", "python", "php", "csharp", "curl"] as const) {
      assert.ok(getRealtimeIntegrationCode(model, input, "https://gateway.example.invalid", language).includes(voice), `${model.id}/${language}`);
    }
    assert.ok(JSON.stringify(getRealtimeRequestSchema(model)).includes(voice));
    if (model.transport === "websocket") assert.ok(JSON.stringify(buildRealtimeSessionEvent(model, input)).includes(voice));
  }
});

test("モデルや接続方式を変えても対応する声は保持し、系列をまたぐ未対応の声は持ち込まない", () => {
  const models = getRealtimeModels();
  const rtc = models.find((model) => model.family === "realtime" && model.transport === "webrtc")!;
  const mini = models.find((model) => model.modelId.endsWith("-mini"))!;
  const voiceLive = models.find((model) => model.family === "voice-live")!;
  const transcribe = models.find((model) => model.family === "transcription")!;
  const current = { input: { ...defaultRealtimeInput(rtc), voice: "cedar" }, apiKeyId: "" };
  assert.equal(updateRealtimeModelSelection(current, rtc, mini).input.voice, "cedar");
  assert.equal(updateRealtimeModelSelection(current, rtc, voiceLive).input.voice, undefined);
  assert.equal(updateRealtimeModelSelection(current, rtc, transcribe).input.voice, undefined);
});
