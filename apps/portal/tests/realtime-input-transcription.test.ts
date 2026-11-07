import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { getRealtimeModels } from "../lib/model-catalog.ts";
import { buildRealtimeSessionEvent, RealtimeProtocol } from "../lib/realtime-protocol.ts";
import { defaultRealtimeInput } from "../lib/realtime-integration.ts";
import { normalizeRealtimeBrowserEvent } from "../lib/realtime-client.ts";
import { realtimeInputSchema } from "../lib/realtime-request.ts";
import type { RealtimeModel } from "../lib/model-types.ts";

test("会話の入力字幕モデルは文字起こしの公開登録から導き、別系列へ追加しない", () => {
  const definition = JSON.parse(readFileSync(new URL("../../gateway/apim/apis/openai-transcription-ws-v1.json", import.meta.url), "utf8"));
  assert.equal(definition.websocket.session.models.length, 1);
  const models = getRealtimeModels();
  assert.equal(models.filter((model) => model.family === "realtime").length, 3);
  for (const model of models) assert.equal(model.inputTranscriptionModel, model.family === "realtime" ? definition.websocket.session.models[0] : undefined);
});

test("設定済みaliasをWSとRTCの同じsession.updateへ渡し、音声・VAD・上限の既存設定を維持する", () => {
  for (const transport of ["websocket", "webrtc"] as const) {
    const actual = getRealtimeModels().find((model) => model.family === "realtime" && model.transport === transport)!;
    const model = { ...actual, inputTranscriptionModel: "synthetic-asr-custom-deployment" };
    const configured = buildRealtimeSessionEvent(model, defaultRealtimeInput(model)) as { session: { audio: { input: { transcription?: { model: string } } } } };
    assert.deepEqual(configured.session.audio.input.transcription, { model: "synthetic-asr-custom-deployment" });
    delete configured.session.audio.input.transcription;
    const { inputTranscriptionModel: _ignored, ...withoutAsr } = actual;
    assert.deepEqual(configured, buildRealtimeSessionEvent(withoutAsr, defaultRealtimeInput(withoutAsr)));
  }
});

test("入力から補助モデルを選ばせず、不正なcatalog束縛も上流設定へ入れない", () => {
  const model = getRealtimeModels().find((item) => item.family === "realtime")!;
  for (const inputTranscriptionModel of ["", "__UNRESOLVED__", "https://example.invalid/asr", "with space", "a".repeat(121), null, 1]) {
    const broken = { ...model, inputTranscriptionModel } as RealtimeModel;
    assert.throws(() => buildRealtimeSessionEvent(broken, defaultRealtimeInput(broken)));
  }
  assert.equal(realtimeInputSchema.safeParse({ ...defaultRealtimeInput(model), inputTranscriptionModel: "synthetic-client-override" }).success, false);
});

test("Realtimeの入力とAI字幕はWSとRTCの既存nativeイベントから役割別に取り出す", () => {
  const model = getRealtimeModels().find((item) => item.family === "realtime" && item.transport === "websocket")!;
  const protocol = new RealtimeProtocol(model);
  protocol.accept({ type: "session.updated", session: { audio: { input: { transcription: { model: model.inputTranscriptionModel } } } } }, 0);
  const input = { type: "conversation.item.input_audio_transcription.completed", transcript: "合成された利用者の発話" };
  const output = { type: "response.output_audio_transcript.done", transcript: "合成されたAIの応答" };
  for (const [event, role] of [[input, "input"], [output, "output"]] as const) {
    const ws = protocol.accept(event, 1).filter((value) => value.type === "transcript");
    const rtc = normalizeRealtimeBrowserEvent(JSON.stringify(event), 1);
    assert.deepEqual(ws, [{ type: "transcript", role, text: event.transcript, final: true }]);
    assert.deepEqual(rtc.filter((value) => value.type === "transcript"), ws);
  }
});
