import assert from "node:assert/strict";
import { test } from "node:test";
import type { RealtimeModel } from "../lib/model-types.ts";
import { getRealtimeIntegrationCode } from "../lib/realtime-integration.ts";

const model: RealtimeModel = { id: "voice-rtc-fixture", label: "Voice RTC", providerId: "azure-ai-speech", provider: "Azure AI Speech", family: "voice-live", transport: "webrtc", apiId: "azure-voice-live-webrtc-v1", operationId: "invoke", path: "/models/voice-live/v1/webrtc/", modelId: "gpt-6.1-sol", voice: "ja-JP-NanamiNeural", maxRequestBytes: 131072, maxOutputTokens: 128000, numericMaxOutputTokens: 4096, supportsMaximumOutput: true };
const origin = "https://gateway.example.invalid";
const instructions = "日本語で自然に会話してください。利用者の発話を聞き、簡潔で分かりやすく応答してください。ツールの呼び出しや外部の操作は行わないでください。";
const php = (voice = "ja-JP-NanamiNeural", maxOutputTokens = 128) => getRealtimeIntegrationCode(model, { modelId: model.id, voice, maxOutputTokens }, origin, "php");

test("PHP Voice Live RTC uses the same verified Swoole transport as conversation guides", () => {
  const code = php();
  assert.ok(code.includes(String.raw`use Swoole\Coroutine\Http\Client;`));
  assert.ok(code.includes(String.raw`use Swoole\Coroutine\Channel;`));
  assert.match(code, /new Client\(\$url\["host"\], \$url\["port"\] \?\? 443, true\)/);
  assert.match(code, /"ssl_verify_peer" => true/);
  assert.match(code, /"ssl_host_name" => \$url\["host"\]/);
  assert.match(code, /"ssl_allow_self_signed" => false/);
  assert.match(code, /setHeaders\(\["api-key" => \$key\]\)/);
  assert.ok(code.includes('parse_url("wss://gateway.example.invalid/models/voice-live/v1/webrtc/")'));
  assert.doesNotMatch(code, /phrity|vendor\/autoload|new WebSocket\\Client/);
});

test("PHP SDP creation serializes selected settings without changing the fixed control contract", () => {
  for (const [voice, maxOutputTokens] of [["ja-JP-NanamiNeural", 128], ["ja-JP-KeitaNeural", 256]] as const) {
    const code = php(voice, maxOutputTokens);
    const match = code.match(/\$(?:socket->push|client->send)\(("(?:\\.|[^"\\])*")\)/);
    assert.ok(match);
    assert.deepEqual(JSON.parse(JSON.parse(match[1])), {
      type: "rtc.call.sdp.create", sdp_offer: "<browser-generated audio-only SDP offer>",
      session: { modalities: ["text", "audio"], instructions, voice: { type: "azure-standard", name: voice }, turn_detection: { type: "server_vad", threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 500 }, max_response_output_tokens: maxOutputTokens, tools: [] },
    });
  }
});

test("PHP delivers a bounded SDP answer then retains the socket until owner stop or deadline", () => {
  const code = php();
  assert.match(code, /function negotiateSdp\(Channel \$ownerStopped, callable \$deliverAnswer\): void/);
  assert.match(code, /strlen\(\$frame->data\) > 262144/);
  assert.match(code, /strlen\(\$answer\) > 65536/);
  assert.ok(code.includes(String.raw`str_starts_with($answer, "v=0\r\n")`));
  assert.match(code, /\$deadline = microtime\(true\) \+ 60/);
  const stages = ["$socket->upgrade(", "$socket->push(", "$socket->recv(", "$deliverAnswer($answer)", "$ownerStopped->pop(", "finally { $socket->close(); }"];
  const positions = stages.map((text) => code.indexOf(text));
  assert.ok(positions.every((position) => position >= 0));
  assert.deepEqual(positions, [...positions].sort((a, b) => a - b));
  assert.match(code, /application owns browser audio tracks and RTCPeerConnection cleanup/);
});
