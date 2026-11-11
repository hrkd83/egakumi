import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { handleSpeech, type SpeechDependencies } from "../lib/speech-gateway.ts";
import { buildSpeechRequest, defaultSpeechInput, getSpeechEndpoint, getSpeechHeaders, getSpeechIntegrationExample, getSpeechRequestSchema, getSpeechResponseSchema, SAMPLE_SPEECH_TEXT } from "../lib/speech-integration.ts";
import { MAX_SPEECH_AUDIO_BYTES, MAX_SPEECH_JSON_BYTES, speechInputSchema } from "../lib/speech-request.ts";
import { inspectSpeechWav, parseSpeechTranscript, readSpeechResponse, speechAudioBytes } from "../lib/speech-response.ts";
import type { SpeechModel } from "../lib/model-types.ts";
import { AZURE_SPEECH_VOICES, AZURE_SPEECH_VOICE_IDS, AZURE_SPEECH_TTS_VOICES, AZURE_SPEECH_TTS_VOICE_IDS } from "../lib/speech-voices.ts";
import { buildSpeechExecutionRequest } from "../lib/speech-client.ts";

const tts: SpeechModel = { id: "azure-speech-tts", label: "Azure Speech TTS", providerId: "azure-ai-speech", provider: "Azure AI Speech", apiId: "azure-speech-v1", operationId: "tts", kind: "tts", path: "/models/azure-speech/v1/synthesize", maxRequestBytes: 8192, language: "ja-JP" };
const stt: SpeechModel = { ...tts, id: "azure-speech-stt", label: "Azure Speech STT", operationId: "stt", kind: "stt", path: "/models/azure-speech/v1/recognize", maxRequestBytes: 1_048_576 };
const openai: SpeechModel = { ...stt, id: "gpt-transcribe", label: "GPT Transcribe", providerId: "openai", provider: "OpenAI", apiId: "openai-audio-v1", operationId: "audio-transcriptions", modelId: "gpt-transcribe", path: "/models/openai-audio/v1/deployments/gpt-transcribe/transcriptions", maxRequestBytes: 4_194_304 };
const models = [tts, stt, openai]; const portalOrigin = "https://portal.example.invalid"; const gatewayOrigin = "https://gateway.example.invalid";
const secret = "test-only-private-speech-key"; const apiKeyId = "portal-11111111-1111-4111-8111-111111111111";
const actor = { tenantId: "22222222-2222-4222-8222-222222222222", objectId: "33333333-3333-4333-8333-333333333333" };

/** PCMフレームを含むテスト用WAVです。実APIの固定音声とは分けて扱います。 */
function wav(samples = 2000): Uint8Array {
  const bytes = Buffer.alloc(44 + samples * 2); bytes.write("RIFF", 0); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write("WAVEfmt ", 8); bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(1, 22); bytes.writeUInt32LE(16000, 24); bytes.writeUInt32LE(32000, 28); bytes.writeUInt16LE(2, 32); bytes.writeUInt16LE(16, 34); bytes.write("data", 36); bytes.writeUInt32LE(samples * 2, 40); return bytes;
}
const audio = wav();
/** 本人・購読認可・実際の本文検証を通し、外部通信だけを差し替えます。 */
function dependencies(overrides: Partial<SpeechDependencies> = {}): SpeechDependencies {
  return { readSession: async () => actor, readPortalOrigin: () => portalOrigin, readModels: () => models, readGatewayEnvironment: async () => ({ origin: gatewayOrigin }), resolveSubscriptionKey: async () => secret,
    fetch: async () => new Response(new Uint8Array(audio).buffer, { headers: { "Content-Type": "audio/wav", "apim-request-id": "speech-request-123" } }), ...overrides };
}
/** ブラウザーが選択できるのは公開設定と購読の台帳IDだけです。 */
function request(model: SpeechModel = tts, extra: Record<string, unknown> = {}, headers: Record<string, string> = {}, signal?: AbortSignal, bytes = audio): Request {
  const input = { ...defaultSpeechInput(model), apiKeyId, ...extra };
  if (model.kind === "stt") {
    const form = new FormData(); form.append("input", JSON.stringify(input)); form.append("file", new Blob([new Uint8Array(bytes).buffer], { type: "audio/wav" }), "user-recording.wav");
    return new Request(`${portalOrigin}/api/models/speech`, { method: "POST", headers: { origin: portalOrigin, ...headers }, body: form, signal });
  }
  return new Request(`${portalOrigin}/api/models/speech`, { method: "POST", headers: { origin: portalOrigin, "Content-Type": "application/json", ...headers }, body: JSON.stringify(input), signal });
}

test("音声入力は文章を受け付け、任意SSML・URL・キー実値は拒否する", () => {
  assert.ok(speechInputSchema.safeParse(defaultSpeechInput(tts)).success);
  for (const extra of [{ ssml: "<speak/>" }, { file: "audio.wav" }, { url: "https://other.invalid" }, { apiKey: secret }, { voice: 'x"><audio src="https://other.invalid"/>' }, { rate: "100%" }]) assert.equal(speechInputSchema.safeParse({ ...defaultSpeechInput(tts), ...extra }).success, false);
});

test("Voice Live共有の日本語7音声と既定Nanami・日英の表示名を維持する", () => {
  assert.deepEqual(AZURE_SPEECH_VOICE_IDS, [
    "ja-JP-NanamiNeural", "ja-JP-KeitaNeural", "ja-JP-AoiNeural", "ja-JP-DaichiNeural",
    "ja-JP-MayuNeural", "ja-JP-NaokiNeural", "ja-JP-ShioriNeural",
  ]);
  assert.equal(defaultSpeechInput(tts).voice, "ja-JP-NanamiNeural");
  for (const voice of AZURE_SPEECH_VOICES) {
    assert.ok(voice.label.ja.length > 0 && voice.label.en.length > 0);
    assert.equal(speechInputSchema.safeParse({ ...defaultSpeechInput(tts), voice: voice.id }).success, true);
  }
  for (const voice of ["unapproved", "custom-voice", "en-US-AvaNeural", "ja-JP-Nanami:DragonHDLatestNeural"])
    assert.equal(speechInputSchema.safeParse({ ...defaultSpeechInput(tts), voice }).success, false);
});

test("REST TTSは代表12音声の識別子・localeを固定し、未知音声を拒否する", () => {
  assert.equal(AZURE_SPEECH_TTS_VOICES.length, 12);
  assert.equal(new Set(AZURE_SPEECH_TTS_VOICE_IDS).size, 12);
  assert.deepEqual(AZURE_SPEECH_TTS_VOICE_IDS.slice(0, 7), AZURE_SPEECH_VOICE_IDS);
  assert.deepEqual(AZURE_SPEECH_TTS_VOICES.slice(7).map((voice) => [voice.id, voice.locale]), [
    ["en-US-JennyNeural", "en-US"], ["en-US-GuyNeural", "en-US"], ["zh-CN-XiaoyanNeural", "zh-CN"], ["ko-KR-SunHiNeural", "ko-KR"], ["es-ES-ElviraNeural", "es-ES"],
  ]);
  for (const voice of AZURE_SPEECH_TTS_VOICES) {
    assert.ok(voice.id.startsWith(voice.locale + "-"));
    assert.ok(voice.label.ja && voice.label.en);
    assert.equal(speechInputSchema.safeParse({ ...defaultSpeechInput(tts), voice: voice.id }).success, true);
  }
  assert.equal(speechInputSchema.safeParse({ ...defaultSpeechInput(tts), voice: "en-US-unregistered" }).success, false);
});

test("代表12音声の選択とlocaleをSSML・5言語例・ブラウザーから上流への送信へ反映する", async () => {
  const text = "同じ文章で声を比較します & <テスト>";
  for (const selected of AZURE_SPEECH_TTS_VOICES) {
    const voice = selected.id;
    const input = { ...defaultSpeechInput(tts), voice, text };
    const xml = String(buildSpeechRequest(tts, input));
    assert.ok(xml.includes(`<voice name="${voice}">`));
    assert.ok(xml.includes(`xml:lang="${selected.locale}"`));
    assert.ok(xml.includes("同じ文章で声を比較します &amp; &lt;テスト&gt;"));
    for (const language of ["typescript", "python", "php", "csharp", "curl"] as const) {
      const code = getSpeechIntegrationExample(language, tts, input, gatewayOrigin);
      assert.ok(code.includes(voice), language);
      assert.ok(code.includes(selected.locale), language);
      assert.ok(code.includes("同じ文章で声を比較します &amp; &lt;テスト&gt;"), language);
    }
    let calls = 0;
    const init = buildSpeechExecutionRequest(tts, input, apiKeyId, null);
    const headers = new Headers(init.headers); headers.set("origin", portalOrigin);
    const response = await handleSpeech(new Request(`${portalOrigin}/api/models/speech`, { ...init, headers }), dependencies({
      fetch: async (_url, options) => {
        calls++;
        assert.equal(options?.body, xml);
        assert.equal(new Headers(options?.headers).get("Content-Type"), "application/ssml+xml");
        return new Response(new Uint8Array(audio).buffer, { headers: { "Content-Type": "audio/wav" } });
      },
    }));
    assert.equal(response.status, 200);
    assert.equal(calls, 1);
  }
});

test("SSMLには入力文・許可音声・話速を使い、STTの本文と区別する", () => {
  const input = { ...defaultSpeechInput(tts), voice: "ja-JP-KeitaNeural" as const, rate: "+20%" as const };
  const xml = buildSpeechRequest(tts, input) as string; assert.ok(xml.includes(SAMPLE_SPEECH_TEXT)); assert.ok(xml.includes('name="ja-JP-KeitaNeural"')); assert.ok(xml.includes('rate="+20%"'));
  assert.equal((buildSpeechRequest(stt, defaultSpeechInput(stt)) as Record<string, unknown>).contentType, "audio/wav");
  assert.equal((buildSpeechRequest(openai, defaultSpeechInput(openai)) as Record<string, unknown>).formField, "file");
  assert.equal(getSpeechHeaders(openai)["Content-Type"], undefined); assert.equal(getSpeechHeaders(stt)["Content-Type"], "audio/wav"); assert.equal(getSpeechHeaders(tts)["X-Microsoft-OutputFormat"], undefined);
});

test("入力文をXML本文としてエスケープし、音声要素や外部URLとして解釈させない", async () => {
  const text = '任意の文\n<audio src="https://untrusted.invalid">音声</audio> & \'引用\'';
  const input = { ...defaultSpeechInput(tts), text };
  const xml = String(buildSpeechRequest(tts, input));
  assert.ok(xml.includes("&lt;audio src=&quot;https://untrusted.invalid&quot;&gt;")); assert.ok(!xml.includes("<audio")); assert.ok(xml.includes("&amp;")); assert.ok(xml.includes("&apos;引用&apos;"));
  const response = await handleSpeech(request(tts, { text }), dependencies({ fetch: async (_url, options) => {
    assert.equal(options?.body, xml); return new Response(new Uint8Array(audio).buffer, { headers: { "Content-Type": "audio/wav" } });
  } }));
  assert.equal(response.status, 200);
});

test("STTは選択ファイルの実WAV形式を検査し、重複フィールドやJSONだけの実行を拒否する", async () => {
  let calls = 0; const context = dependencies({ fetch: async () => { calls++; return Response.json({ text: "ok" }); } });
  const invalid = Buffer.from(audio); invalid.writeUInt32LE(48_000, 24);
  assert.equal((await handleSpeech(request(stt, {}, {}, undefined, invalid), context)).status, 400);
  const jsonOnly = new Request(`${portalOrigin}/api/models/speech`, { method: "POST", headers: { origin: portalOrigin, "Content-Type": "application/json" }, body: JSON.stringify({ ...defaultSpeechInput(openai), apiKeyId }) });
  assert.equal((await handleSpeech(jsonOnly, context)).status, 400);
  const form = new FormData(); form.append("input", JSON.stringify({ ...defaultSpeechInput(openai), apiKeyId })); form.append("file", new Blob([new Uint8Array(audio).buffer]), "input.wav"); form.append("file", new Blob([new Uint8Array(audio).buffer]), "second.wav");
  assert.equal((await handleSpeech(new Request(`${portalOrigin}/api/models/speech`, { method: "POST", headers: { origin: portalOrigin }, body: form }), context)).status, 400); assert.equal(calls, 0);
});

test("TTSの空欄を保ったSTT切替は実行でき、TTSへ戻した場合は空欄を拒否する", async () => {
  const response = await handleSpeech(request(openai, { text: "" }), dependencies({ fetch: async () => Response.json({ text: "認識しました" }) }));
  assert.equal(response.status, 200);
  assert.equal((await handleSpeech(request(tts, { text: "" }), dependencies())).status, 400);
});

test("5言語の例は選択に追従し、実際のWAV・multipartと固定配備名を使う", () => {
  for (const model of models) for (const language of ["typescript", "python", "php", "csharp", "curl"] as const) {
    const code = getSpeechIntegrationExample(language, model, { ...defaultSpeechInput(model), rate: "+20%" }, gatewayOrigin);
    assert.ok(code.includes(gatewayOrigin + model.path)); assert.ok(code.includes("GATEWAY_API_KEY")); assert.ok(code.includes("egakumi-portal-speech/1.0")); assert.ok(!code.includes(secret));
    if (model.kind === "tts") { assert.ok(code.includes(SAMPLE_SPEECH_TEXT)); assert.ok(code.includes("+20%")); assert.ok(code.includes("speech-result.wav")); }
    else { assert.ok(code.includes("input.wav")); assert.ok(!code.includes(SAMPLE_SPEECH_TEXT)); }
  }
  assert.equal(getSpeechRequestSchema(tts).contentMediaType, "application/ssml+xml"); assert.equal(getSpeechRequestSchema(stt).contentMediaType, "audio/wav"); assert.equal(getSpeechRequestSchema(openai).contentMediaType, "multipart/form-data"); assert.equal(getSpeechResponseSchema(tts).contentMediaType, "audio/wav");
  assert.throws(() => getSpeechEndpoint(tts, "http://gateway.example.invalid")); assert.throws(() => getSpeechEndpoint({ ...openai, path: "//elsewhere.invalid/a" }, gatewayOrigin)); assert.throws(() => getSpeechEndpoint({ ...openai, modelId: "other" }, gatewayOrigin));
});

test("本人認証と同一オリジンを確認し、拒否時は課金対象の通信をしない", async () => {
  let calls = 0; const context = dependencies({ fetch: async () => { calls++; throw new Error("not called"); } });
  assert.equal((await handleSpeech(request(tts, {}, { origin: "https://other.invalid" }), context)).status, 403);
  assert.equal((await handleSpeech(request(tts, {}, { "sec-fetch-site": "cross-site" }), context)).status, 403);
  const noOrigin = request(); noOrigin.headers.delete("origin"); assert.equal((await handleSpeech(noOrigin, context)).status, 403);
  assert.equal((await handleSpeech(request(), { ...context, readSession: async () => null })).status, 401);
  assert.equal((await handleSpeech(request(), { ...context, readSession: async () => { throw new Error(secret); } })).status, 503);
  assert.equal((await handleSpeech(request(tts, {}, { "Content-Type": "audio/wav" }), context)).status, 415); assert.equal(calls, 0);
});

test("パス・配備名・サイズ・設定の改変をキーの取得より先に拒否する", async () => {
  let keys = 0; const context = dependencies({ resolveSubscriptionKey: async () => { keys++; return secret; } });
  for (const extra of [{ modelId: "unknown" }, { text: "" }, { rate: "100%" }, { voice: "unapproved" }]) assert.equal((await handleSpeech(request(tts, extra), context)).status, 400);
  assert.equal((await handleSpeech(request(), { ...context, readModels: () => [{ ...tts, path: "/models/other" }] })).status, 400);
  assert.equal((await handleSpeech(request(), { ...context, readModels: () => [{ ...tts, maxRequestBytes: 1 }] })).status, 413);
  assert.equal((await handleSpeech(request(tts, {}, { "Content-Length": "524288001" }), context)).status, 413);
  assert.equal((await handleSpeech(request(stt, {}, {}, undefined, new Uint8Array([1, 2, 3])), context)).status, 400);
  assert.equal(keys, 0);
});

test("TTSは本人が選んだキーを1回だけ使い、検証済みWAVと形式情報を返す", async () => {
  let calls = 0;
  const response = await handleSpeech(request(), dependencies({ resolveSubscriptionKey: async (owner, id, apiId) => { assert.deepEqual(owner, actor); assert.equal(id, apiKeyId); assert.equal(apiId, tts.apiId); return secret; }, fetch: async (url, init) => {
    calls++; assert.equal(String(url), gatewayOrigin + tts.path); assert.equal(init?.redirect, "error"); assert.equal(init?.cache, "no-store");
    const headers = new Headers(init?.headers); assert.equal(headers.get("api-key"), secret); assert.equal(headers.get("User-Agent"), "egakumi-portal-speech/1.0"); assert.equal(headers.get("Content-Type"), "application/ssml+xml"); assert.equal(headers.get("cookie"), null); assert.equal(init?.body, buildSpeechRequest(tts, defaultSpeechInput(tts))); return dependencies().fetch!(url, init);
  } }));
  assert.equal(response.status, 200); assert.equal(response.headers.get("cache-control"), "no-store"); const result = await readSpeechResponse(response); assert.equal(result.kind, "tts"); if (result.kind !== "tts") return;
  assert.equal(result.audioBase64, Buffer.from(audio).toString("base64")); assert.equal(result.audioDurationMs, 125); assert.equal(result.requestId, "speech-request-123"); assert.equal(result.rawResponse.format, "riff-16khz-16bit-mono-pcm"); assert.equal(calls, 1); assert.ok(!JSON.stringify(result).includes(secret));
});

test("Azure STTは音声のバイト列を直接送り、上流の詳細JSONを保持する", async () => {
  const raw = { RecognitionStatus: "Success", NBest: [{ Display: SAMPLE_SPEECH_TEXT, Confidence: 0.98 }], Duration: 1_250_000 };
  const response = await handleSpeech(request(stt), dependencies({ fetch: async (url, init) => { assert.equal(String(url), gatewayOrigin + stt.path); assert.equal(new Headers(init?.headers).get("Content-Type"), "audio/wav"); assert.deepEqual(new Uint8Array(init?.body as ArrayBuffer), new Uint8Array(audio)); return Response.json(raw); } }));
  assert.equal(response.status, 200); const result = await readSpeechResponse(response); assert.equal(result.kind, "stt"); if (result.kind === "stt") { assert.equal(result.transcript, SAMPLE_SPEECH_TEXT); assert.deepEqual(result.rawResponse, raw); }
});

test("OpenAI STTはfileのみをmultipartにし、modelの本文上書きを送らない", async () => {
  const response = await handleSpeech(request(openai), dependencies({ fetch: async (url, init) => {
    assert.equal(String(url), gatewayOrigin + openai.path); const forwarded = new Request(String(url), init); const form = await forwarded.formData(); assert.deepEqual([...form.keys()], ["file"]); const file = form.get("file") as File; assert.equal(file.name, "input.wav"); assert.equal(file.type, "audio/wav"); assert.deepEqual(new Uint8Array(await file.arrayBuffer()), new Uint8Array(audio)); return Response.json({ text: SAMPLE_SPEECH_TEXT });
  } }));
  assert.equal(response.status, 200); assert.equal((await response.json()).transcript, SAMPLE_SPEECH_TEXT);
});

test("購読の失効や認可不能を秘密値や内部エラーを含めずに返す", async () => {
  for (const error of [new ApiKeyError("Forbidden", 403), new Error(secret)]) { const response = await handleSpeech(request(), dependencies({ resolveSubscriptionKey: async () => { throw error; } })); assert.equal(response.status, error instanceof ApiKeyError ? 403 : 503); assert.ok(!(await response.text()).includes(secret)); }
});

test("上流エラーと応答型・サイズ違反は成功扱いしない", async () => {
  for (const status of [302, 400, 401, 403, 500]) { const response = await handleSpeech(request(), dependencies({ fetch: async () => new Response(secret, { status }) })); assert.equal(response.status, 502); assert.ok(!(await response.text()).includes(secret)); }
  const rate = await handleSpeech(request(), dependencies({ fetch: async () => new Response(secret, { status: 429, headers: { "retry-after": "15" } }) })); assert.deepEqual(await rate.json(), { error: { code: "RateLimitExceeded" }, retryAfterSeconds: 15 });
  const rejectedHeaders: Record<string, string>[] = [{ "Content-Type": "text/html" }, { "Content-Type": "audio/wav", "Content-Length": String(MAX_SPEECH_AUDIO_BYTES + 1) }];
  for (const headers of rejectedHeaders) assert.equal((await handleSpeech(request(), dependencies({ fetch: async () => new Response("not audio", { headers }) }))).status, 502);
  assert.equal((await handleSpeech(request(stt), dependencies({ fetch: async () => Response.json({ text: "wrong provider shape" }) }))).status, 502);
  assert.equal((await handleSpeech(request(stt), dependencies({ fetch: async () => new Response("invalid json", { headers: { "Content-Type": "application/json" } }) }))).status, 502);
});

test("キーがJSONやWAVに反射されてもブラウザーへ戻さない", async () => {
  const response = await handleSpeech(request(openai), dependencies({ fetch: async () => Response.json({ text: "ok", metadata: { reflectedKey: secret } }) })); assert.equal(response.status, 502); assert.ok(!(await response.text()).includes(secret));
  const reflected = Buffer.from(audio); reflected.write(secret, 44); const binary = await handleSpeech(request(), dependencies({ fetch: async () => new Response(reflected, { headers: { "Content-Type": "audio/wav" } }) })); assert.equal(binary.status, 502); assert.ok(!(await binary.text()).includes(secret));
});

test("音声の読取り期限・取消を監視し、自動再試行しない", async () => {
  const keepAlive = setTimeout(() => undefined, 1000); let calls = 0;
  try {
    const timeout = await handleSpeech(request(), dependencies({ timeoutMs: 10, fetch: async () => { calls++; return new Response(new ReadableStream({ start() {} }), { headers: { "Content-Type": "audio/wav" } }); } })); assert.equal(timeout.status, 504); assert.equal(calls, 1);
    const abort = new AbortController(); const response = handleSpeech(request(tts, {}, {}, abort.signal), dependencies({ fetch: async () => { setTimeout(() => abort.abort(), 10); return new Response(new ReadableStream({ start() {} }), { headers: { "Content-Type": "audio/wav" } }); } })); assert.equal((await response).status, 499);
  } finally { clearTimeout(keepAlive); }
});

test("両STTのJSONでUnicodeエスケープされた購読キーも画面へ返さない", async () => {
  for (const model of [stt, openai]) {
    const value = model === stt ? { RecognitionStatus: "Success", NBest: [{ Display: "synthetic transcript", Confidence: 0.9 }] } : { text: "synthetic transcript" };
    const encoded = [...secret].map((character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`).join("");
    const raw = JSON.stringify({ ...value, metadata: { reflected: secret } }).replace(secret, encoded);
    assert.equal(raw.includes(secret), false);
    let calls = 0;
    const response = await handleSpeech(request(model), dependencies({ fetch: async () => { calls++; return new Response(raw, { headers: { "content-type": "application/json" } }); } }));
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error: { code: "GenerationFailed" } });
    assert.equal(calls, 1);
  }
});

test("両STTで引用符付きキーの値・プロパティ名への反射を拒否する", async () => {
  const credential = 'synthetic-speech-"key"-\\-value';
  for (const model of [stt, openai]) {
    const value = model === stt ? { RecognitionStatus: "Success", NBest: [{ Display: "synthetic transcript", Confidence: 0.9 }] } : { text: "synthetic transcript" };
    for (const reflected of [{ metadata: { reflected: credential } }, { [credential]: "marker" }]) {
      const raw = JSON.stringify({ ...value, ...reflected }); assert.equal(raw.includes(credential), false);
      const response = await handleSpeech(request(model), dependencies({ resolveSubscriptionKey: async () => credential, fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) }));
      assert.equal(response.status, 502); assert.deepEqual(await response.json(), { error: { code: "GenerationFailed" } });
    }
  }
});

test("RIFF長・チャンク長・チャンネル・サンプルレート・PCM形式を検証する", () => {
  assert.equal(inspectSpeechWav(audio).audioDurationMs, 125);
  for (const [offset, value] of [[4, 10], [20, 3], [22, 2], [24, 48000], [32, 4], [34, 24], [40, audio.length]] as const) { const invalid = Buffer.from(audio); invalid.writeUInt32LE(value, offset); assert.throws(() => inspectSpeechWav(invalid)); }
  assert.throws(() => inspectSpeechWav(new Uint8Array(46))); assert.throws(() => inspectSpeechWav(audio.subarray(0, audio.length - 1)));
});

test("大きなBase64を線形に検証し、不正なパディングや非WAVを拒否する", () => {
  const large = wav(600000); const base64 = Buffer.from(large).toString("base64"); assert.equal(speechAudioBytes(base64).length, large.length);
  for (const value of [base64 + "=", "=" + base64.slice(1), base64.slice(0, -5) + "!AAAA", Buffer.from("<svg>bad</svg>").toString("base64")]) assert.throws(() => speechAudioBytes(value));
});

test("AzureのNoMatchを認識状態付きの空文字として保持し、成功を捏造しない", () => {
  const result = parseSpeechTranscript({ RecognitionStatus: "NoMatch", NBest: null, Duration: 10 }, stt, 25); assert.equal(result.kind, "stt"); if (result.kind === "stt") { assert.equal(result.transcript, ""); assert.equal(result.rawResponse.RecognitionStatus, "NoMatch"); assert.equal(result.rawResponse.NBest, null); }
  assert.throws(() => parseSpeechTranscript({ RecognitionStatus: "Success" }, stt, 25)); assert.throws(() => parseSpeechTranscript({ error: { code: "bad" }, text: "ok" }, openai, 25));
});

test("AzureのSuccessに明示された空文字を無音として保持し、欠損とは区別する", async () => {
  for (const raw of [{ RecognitionStatus: "Success", DisplayText: "" },
    { RecognitionStatus: "Success", NBest: [{ Display: "", Confidence: 0 }] }]) {
    let calls = 0;
    const response = await handleSpeech(request(stt), dependencies({ fetch: async () => {
      calls++;
      return Response.json(raw);
    } }));
    assert.equal(response.status, 200);
    const result = await readSpeechResponse(response);
    assert.equal(result.kind, "stt");
    if (result.kind === "stt") {
      assert.equal(result.transcript, "");
      assert.deepEqual(result.rawResponse, raw);
    }
    assert.equal(calls, 1);
  }
  for (const raw of [{ RecognitionStatus: "Success" },
    { RecognitionStatus: "Success", NBest: [] }]) {
    assert.throws(() => parseSpeechTranscript(raw, stt, 25));
  }
});

test("ブラウザーの読取りも認可エラー・壊れた音声・中断を正しく扱う", async () => {
  await assert.rejects(readSpeechResponse(Response.json({ error: { code: "ApiKeyUnavailable" } }, { status: 403 })), (error: unknown) => error instanceof Error && error.message === "ApiKeyUnavailable");
  await assert.rejects(readSpeechResponse(Response.json({ error: { code: secret } }, { status: 500 })), (error: unknown) => error instanceof Error && !error.message.includes(secret));
  const good = await (await handleSpeech(request(), dependencies())).json(); await assert.rejects(readSpeechResponse(Response.json({ ...good, byteLength: 1 })));
  const abort = new AbortController(); const keepAlive = setTimeout(() => abort.abort(), 10); try { await assert.rejects(readSpeechResponse(new Response(new ReadableStream({ start() {} }), { headers: { "Content-Type": "application/json" } }), abort.signal)); } finally { clearTimeout(keepAlive); }
});
