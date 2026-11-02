import assert from "node:assert/strict";
import { test } from "node:test";
import type { RealtimeModel } from "../lib/model-types.ts";
import { resolveRealtimeOutputTokens, type RealtimeInput } from "../lib/realtime-request.ts";
import { buildRealtimeSessionEvent } from "../lib/realtime-protocol.ts";
import { getRealtimeRequestExample, getRealtimeRequestSchema } from "../lib/realtime-integration.ts";

const model: RealtimeModel = { id: "realtime", label: "Realtime", providerId: "openai", provider: "OpenAI", family: "realtime", transport: "websocket", apiId: "openai-realtime-ws-v1", operationId: "invoke", path: "/models/openai-realtime/v1/ws", modelId: "gpt-realtime-2.1", voice: "marin", maxRequestBytes: 65536, maxOutputTokens: 4096 };
const voice: RealtimeModel = { ...model, id: "voice", family: "voice-live", apiId: "azure-voice-live-ws-v1", path: "/models/voice-live/v1/ws", modelId: "gpt-6.1-sol", voice: "ja-JP-NanamiNeural", maxOutputTokens: 128000, numericMaxOutputTokens: 4096, supportsMaximumOutput: true };

test("Realtimeはモデルの4096まで指定でき、固定512上限と128下限を残さない", () => {
  for (const limit of [1, 513, 4096]) assert.equal(resolveRealtimeOutputTokens(model, { modelId: model.id, maxOutputTokens: limit }), limit);
  for (const limit of [0, 4097, "model-max", undefined] as const) assert.throws(() => resolveRealtimeOutputTokens(model, { modelId: model.id, maxOutputTokens: limit }));
});

test("Voice Liveは数値範囲とモデル最大を区別し、指定を勝手に無制限へ拡大しない", () => {
  for (const limit of [1, 4096]) assert.equal(resolveRealtimeOutputTokens(voice, { modelId: voice.id, maxOutputTokens: limit }), limit);
  assert.equal(resolveRealtimeOutputTokens(voice, { modelId: voice.id, maxOutputTokens: "model-max" }), "inf");
  for (const limit of [4097, 5000, 128000]) assert.throws(() => resolveRealtimeOutputTokens(voice, { modelId: voice.id, maxOutputTokens: limit }));
  const input: RealtimeInput = { modelId: voice.id, maxOutputTokens: "model-max" };
  assert.match(JSON.stringify(buildRealtimeSessionEvent(voice, input)), /"max_response_output_tokens":"inf"/);
  const rtc = { ...voice, transport: "webrtc" as const };
  assert.match(getRealtimeRequestExample(rtc, input), /"max_response_output_tokens": "inf"/);
  assert.match(JSON.stringify(getRealtimeRequestSchema(rtc)), /"maximum":4096/);
  assert.match(JSON.stringify(getRealtimeRequestSchema(rtc)), /128000/);
});

test("生成上限の項目がないLiveと文字起こしは要求にも含めない", () => {
  for (const family of ["live", "transcription"] as const) {
    const unsupported = { ...model, family, maxOutputTokens: null };
    assert.equal(resolveRealtimeOutputTokens(unsupported, { modelId: model.id }), undefined);
    assert.throws(() => resolveRealtimeOutputTokens(unsupported, { modelId: model.id, maxOutputTokens: 1 }));
  }
  assert.throws(() => resolveRealtimeOutputTokens(model, { modelId: "different", maxOutputTokens: 1 }));
});
