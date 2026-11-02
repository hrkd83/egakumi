import assert from "node:assert/strict";
import { test } from "node:test";
import { buildSpeechExecutionRequest } from "../lib/speech-client.ts";
import type { SpeechModel } from "../lib/model-types.ts";

const tts: SpeechModel = { id: "tts", label: "TTS", provider: "Azure AI Speech", providerId: "azure-ai-speech", apiId: "azure-speech-v1", operationId: "tts", kind: "tts", path: "/models/azure-speech/v1/synthesize", maxRequestBytes: 65_536, language: "ja-JP" };
const input = { modelId: "tts", voice: "ja-JP-NanamiNeural" as const, rate: "0%" as const, text: "編集した本文 <test> & 日本語" };

test("TTSは編集文をJSONで送り、前のSTTファイルが残っていても添付しない", () => {
  const request = buildSpeechExecutionRequest(tts, input, "fixture-key", new File(["unused"], "previous.wav"));
  assert.deepEqual(JSON.parse(String(request.body)), { ...input, apiKeyId: "fixture-key" });
  assert.equal(new Headers(request.headers).get("Content-Type"), "application/json");
});

test("STTは選択ファイルの内容と入力設定を送り、multipart境界はブラウザーへ任せる", async () => {
  const stt: SpeechModel = { ...tts, id: "stt", kind: "stt", operationId: "stt", path: "/models/azure-speech/v1/recognize" };
  const file = new File([new Uint8Array([0, 1, 2, 3])], "会議.wav", { type: "audio/wav" });
  const request = buildSpeechExecutionRequest(stt, { ...input, modelId: "stt" }, "fixture-key", file);
  assert.ok(request.body instanceof FormData); assert.deepEqual([...request.body.keys()], ["input", "file"]);
  assert.deepEqual(JSON.parse(String(request.body.get("input"))), { ...input, modelId: "stt", apiKeyId: "fixture-key" });
  const uploaded = request.body.get("file"); assert.ok(uploaded instanceof File); assert.equal(uploaded.name, "会議.wav");
  assert.deepEqual(new Uint8Array(await uploaded.arrayBuffer()), new Uint8Array([0, 1, 2, 3]));
  assert.equal(new Headers(request.headers).has("Content-Type"), false);
  assert.throws(() => buildSpeechExecutionRequest(stt, input, "fixture-key", null));
});
