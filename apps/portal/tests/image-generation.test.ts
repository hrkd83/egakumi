import assert from "node:assert/strict";
import { test } from "node:test";
import { crc32, deflateSync } from "node:zlib";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { getImageGenerationModels } from "../lib/model-catalog.ts";
import { buildImageGenerationRequest, defaultImageInput, getImageGenerationEndpoint, getImageGenerationHeaders, getImageIntegrationExample, getImageRequestSchema, getImageResponseSchema, SAMPLE_IMAGE_PROMPT } from "../lib/image-integration.ts";
import { handleImageGeneration, type ImageGenerationDependencies } from "../lib/image-gateway.ts";
import { imageGenerationInputSchema, MAX_IMAGE_RESPONSE_BYTES } from "../lib/image-request.ts";
import { imageExternalUrl, imagePreviewSource, imageResponsePreview, parseImageGenerationResult, rasterBase64Mime, readImageResponse, safeImageUrl } from "../lib/image-response.ts";
import { getImageGenerationDictionary } from "../lib/i18n/image-generation.ts";

const models = getImageGenerationModels();
const openai = models.find((model) => model.operationId === "image-generate")!;
const flux = models.find((model) => model.operationId === "flux2-generate" && model.profileId === "pro")!;
const portalOrigin = "https://portal.example.invalid";
const gatewayOrigin = "https://gateway.example.invalid";
const secret = "test-only-private-image-key";
const apiKeyId = "portal-11111111-1111-4111-8111-111111111111";
const actor = { tenantId: "22222222-2222-4222-8222-222222222222", objectId: "33333333-3333-4333-8333-333333333333" };
/** 外部の画像やメタデータを埋め込まず、白い1画素のRGBA PNGを合成します。 */
function syntheticPng(): string {
  const chunk = (type: string, data: Buffer): Buffer => {
    const payload = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const checksum = Buffer.alloc(4);
    checksum.writeUInt32BE(crc32(payload));
    return Buffer.concat([length, payload, checksum]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(1, 0);
  header.writeUInt32BE(1, 4);
  header[8] = 8; // 各色8ビット。
  header[9] = 6; // RGBA。
  const pixels = Buffer.from([0, 255, 255, 255, 255]); // フィルターなし、白、不透明。
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header), chunk("IDAT", deflateSync(pixels)), chunk("IEND", Buffer.alloc(0)),
  ]).toString("base64");
}
const png = syntheticPng();
const jpeg = Buffer.from([255, 216, 255, 224, 0, 16, 74, 70, 73, 70, 0, 1, 255, 217]).toString("base64");

test("複数画像とネイティブWebPを受理し、元の応答を保持する", () => {
  const webp = Buffer.from("RIFF0000WEBPVP8 synthetic-raster", "ascii").toString("base64");
  assert.equal(rasterBase64Mime(webp), "image/webp");
  const raw = { data: [{ b64_json: png }, { b64_json: webp }] };
  const result = parseImageGenerationResult(raw, openai.id, 1);
  assert.equal(result.images.length, 2); assert.deepEqual(result.rawResponse, raw);
});

/** 実際のAPIと同じ入力制約を通し、外部通信と本人情報だけを固定します。 */
function dependencies(overrides: Partial<ImageGenerationDependencies> = {}): ImageGenerationDependencies {
  return { readSession: async () => actor, readPortalOrigin: () => portalOrigin, readModels: () => models,
    readGatewayEnvironment: async () => ({ origin: gatewayOrigin }), resolveSubscriptionKey: async () => secret,
    fetch: async () => Response.json({ created: 1, data: [{ b64_json: png }], usage: { total_tokens: 12 } }, { headers: { "apim-request-id": "image-request-123" } }), ...overrides };
}

/** 同一オリジンのJSON実行要求です。キー実値はブラウザーから送りません。 */
function request(input: unknown = defaultImageInput(openai), headers: Record<string, string> = {}, signal?: AbortSignal, query = ""): Request {
  return new Request(`${portalOrigin}/api/models/images${query}`, { method: "POST", headers: { origin: portalOrigin, "Content-Type": "application/json", ...headers }, body: JSON.stringify({ apiKeyId, ...(input as object) }), signal });
}

test("画像入力はプロンプトを受け付け、任意接続先・キー実値は拒否する", () => {
  const input = defaultImageInput(openai);
  assert.ok(imageGenerationInputSchema.safeParse(input).success);
  for (const extra of [{ apiKey: secret }, { url: "https://other.invalid" }, { n: 100 }, { stream: true },
    ...["image", "image_url", "input_image", "input_image_2", "mask"].map((field) => ({ [field]: "synthetic" }))]) assert.equal(imageGenerationInputSchema.safeParse({ ...input, ...extra }).success, false);
  for (const width of [0, -1, 512.5, "512", null]) assert.equal(imageGenerationInputSchema.safeParse({ ...input, width }).success, false);
});

test("OpenAIは複数枚・高品質・大きな寸法を提供元へ送信できる", () => {
  const input = { ...defaultImageInput(openai), count: 3, size: "2048x2048", quality: "high" };
  assert.deepEqual(buildImageGenerationRequest(openai, input), { model: openai.id, prompt: SAMPLE_IMAGE_PROMPT, n: 3, size: "2048x2048", quality: "high", output_format: "png" });
  for (const changed of [{ count: 11 }, { size: "bad" }, { quality: "<script>" }, { outputFormat: "svg" as "png" }]) assert.throws(() => buildImageGenerationRequest(openai, { ...input, ...changed }));
});

test("入力プロンプトの引用符・改行を保持し、任意の生成URLは受け付けない", async () => {
  const prompt = "白いロボットの\"絵\"\n文字は描かない。''' ${image}";
  const input = { ...defaultImageInput(openai), prompt };
  assert.equal(buildImageGenerationRequest(openai, input).prompt, prompt);
  const response = await handleImageGeneration(request(input), dependencies({ fetch: async (_url, options) => {
    assert.equal(JSON.parse(String(options?.body)).prompt, prompt); return Response.json({ data: [{ b64_json: png }] });
  } }));
  assert.equal(response.status, 200); assert.equal(imageGenerationInputSchema.safeParse({ ...input, url: "https://untrusted.invalid" }).success, false);
  assert.equal(imageGenerationInputSchema.safeParse({ ...input, prompt: " " }).success, false);
});

test("FLUXはサイズと形式だけを選び、API専用本文とproプロファイルを生成する", () => {
  const input = { ...defaultImageInput(flux), width: 512, height: 768, outputFormat: "jpeg" as const };
  assert.deepEqual(buildImageGenerationRequest(flux, input), { model: flux.id, prompt: SAMPLE_IMAGE_PROMPT, num_images: 1, width: 512, height: 768, output_format: "jpeg" });
  assert.equal(getImageGenerationHeaders(flux)["X-Egakumi-Profile"], "pro");
  assert.equal(getImageGenerationHeaders(openai)["X-Egakumi-Profile"], undefined);
  for (const extra of [{ width: 0 }, { count: 2 }, { width: 512.5 }, { quality: "high" }, { size: "auto" }]) assert.throws(() => buildImageGenerationRequest(flux, { ...input, ...extra }));
});

test("コード例は全言語で固定公開URLと現在の本文・プロファイルを使い、秘密実値を含めない", () => {
  for (const model of [openai, flux]) {
    for (const language of ["typescript", "python", "php", "csharp", "curl"] as const) {
      const code = getImageIntegrationExample(language, model, defaultImageInput(model), gatewayOrigin);
      assert.ok(code.includes(`${gatewayOrigin}${model.path}`));
      assert.ok(code.includes(SAMPLE_IMAGE_PROMPT));
      assert.ok(code.includes("GATEWAY_API_KEY"));
      assert.ok(!code.includes(secret));
      if (model.profileId) assert.ok(code.includes("X-Egakumi-Profile"));
      else assert.ok(!code.includes("X-Egakumi-Profile"));
    }
  }
  assert.throws(() => getImageGenerationEndpoint(openai, "http://gateway.example.com"));
  assert.throws(() => getImageGenerationEndpoint({ ...flux, path: "//elsewhere.example/secret" }, gatewayOrigin));
  assert.equal((getImageRequestSchema(flux).properties as Record<string, unknown>).num_images !== undefined, true);
  assert.equal((getImageResponseSchema().properties as Record<string, unknown>).data !== undefined, true);
});

test("同一オリジン・本人認証・JSONがない場合は画像を生成しない", async () => {
  let calls = 0;
  const context = dependencies({ fetch: async () => { calls++; throw new Error("not called"); } });
  const rejectedHeaders: Record<string, string>[] = [{ origin: "https://other.invalid" }, { "sec-fetch-site": "cross-site" }];
  for (const headers of rejectedHeaders) assert.equal((await handleImageGeneration(request(undefined, headers), context)).status, 403);
  const noOrigin = request(); noOrigin.headers.delete("origin");
  assert.equal((await handleImageGeneration(noOrigin, context)).status, 403);
  assert.equal((await handleImageGeneration(request(), { ...context, readSession: async () => null })).status, 401);
  assert.equal((await handleImageGeneration(request(), { ...context, readSession: async () => { throw new Error(secret); } })).status, 503);
  assert.equal((await handleImageGeneration(request(undefined, { "Content-Type": "text/plain" }), context)).status, 415);
  assert.equal(calls, 0);
});

test("画像APIの同名queryやAPI版queryは本文・購読・上流処理の前に拒否する", async () => {
  let calls = 0;
  const forbidden = () => { calls++; throw new Error("not called"); };
  const context = dependencies({ readModels: forbidden, resolveSubscriptionKey: forbidden, fetch: forbidden });
  for (const query of ["?prompt=synthetic", "?%70rompt=synthetic", "?prompt=one&prompt=two", "?api-version=preview", "?input_image=synthetic", "?unexpected", "?=synthetic"]) {
    const incoming = request(undefined, {}, undefined, query);
    const response = await handleImageGeneration(incoming, context);
    assert.equal(response.status, 400, query);
    assert.deepEqual(await response.json(), { error: { code: "InvalidRequest" } });
    assert.equal(incoming.bodyUsed, false);
    assert.equal(response.headers.get("cache-control"), "no-store");
  }
  const malformed = new Request(`${portalOrigin}/api/models/images?prompt=synthetic`, { method: "POST", headers: { origin: portalOrigin, "Content-Type": "application/json" }, body: "{" });
  assert.equal((await handleImageGeneration(malformed, context)).status, 400);
  assert.equal(malformed.bodyUsed, false);
  assert.equal(calls, 0);
});

test("query拒否を追加しても同一オリジン・本人認証・JSONの既存判定順序を維持する", async () => {
  let sessions = 0;
  let calls = 0;
  const context = dependencies({ readSession: async () => { sessions++; return actor; }, fetch: async () => { calls++; throw new Error("not called"); } });
  const query = "?prompt=synthetic";
  assert.equal((await handleImageGeneration(request(undefined, { origin: "https://other.invalid" }, undefined, query), context)).status, 403);
  assert.equal(sessions, 0);
  assert.equal((await handleImageGeneration(request(undefined, {}, undefined, query), { ...context, readSession: async () => null })).status, 401);
  assert.equal((await handleImageGeneration(request(undefined, { "Content-Type": "text/plain" }, undefined, query), context)).status, 415);
  assert.equal((await handleImageGeneration(request(undefined, {}, undefined, query), context)).status, 400);
  assert.equal(sessions, 2);
  assert.equal(calls, 0);
  assert.equal((await handleImageGeneration(request(undefined, { "Content-Type": "application/json; charset=utf-8" }), dependencies())).status, 200);
});

test("制約違反とパス改変は購読キーの取得・上流通信より先に拒否する", async () => {
  let calls = 0;
  const context = dependencies({ resolveSubscriptionKey: async () => { calls++; return secret; } });
  for (const input of [{ ...defaultImageInput(openai), modelId: "unknown-model" }, { ...defaultImageInput(openai), count: 11 }, { ...defaultImageInput(flux), width: 0 }, { ...defaultImageInput(openai), prompt: "" }]) assert.equal((await handleImageGeneration(request(input), context)).status, 400);
  assert.equal((await handleImageGeneration(request(), { ...context, readModels: () => [{ ...openai, path: "/models/other/generate" }] })).status, 400);
  assert.equal((await handleImageGeneration(request(undefined, { "Content-Length": "524288001" }), context)).status, 413);
  assert.equal((await handleImageGeneration(request(), { ...context, readModels: () => [{ ...openai, maxRequestBytes: 10 }] })).status, 413);
  assert.equal(calls, 0);
});

test("本人に認可された選択キーをサーバーで解決し、APIMへ1回だけ送る", async () => {
  let calls = 0;
  const response = await handleImageGeneration(request(), dependencies({ resolveSubscriptionKey: async (owner, id, apiId) => { assert.deepEqual(owner, actor); assert.equal(id, apiKeyId); assert.equal(apiId, openai.apiId); return secret; }, fetch: async (url, init) => {
    calls++; assert.equal(String(url), `${gatewayOrigin}${openai.path}`); assert.equal(init?.redirect, "error"); assert.equal(init?.cache, "no-store");
    const headers = new Headers(init?.headers); assert.equal(headers.get("api-key"), secret); assert.equal(headers.get("cookie"), null);
    assert.deepEqual(JSON.parse(String(init?.body)), buildImageGenerationRequest(openai, defaultImageInput(openai)));
    return dependencies().fetch!(url, init);
  } }));
  assert.equal(response.status, 200); assert.equal(response.headers.get("cache-control"), "no-store");
  const result = await response.json(); assert.equal(result.images[0].mimeType, "image/png"); assert.equal(result.rawResponse.data[0].b64_json, png); assert.equal(result.requestId, "image-request-123"); assert.ok(!JSON.stringify(result).includes(secret)); assert.equal(calls, 1);
});

test("購読の失効・権限不足は秘密や内部例外を表示せず拒否する", async () => {
  for (const error of [new ApiKeyError("Forbidden", 403), new Error(secret)]) {
    const response = await handleImageGeneration(request(), dependencies({ resolveSubscriptionKey: async () => { throw error; } }));
    assert.equal(response.status, error instanceof ApiKeyError ? 403 : 503); assert.ok(!(await response.text()).includes(secret));
  }
});

test("FLUX proの実行は定義済みの専用パスとプロファイルを使う", async () => {
  const response = await handleImageGeneration(request(defaultImageInput(flux)), dependencies({ fetch: async (url, init) => {
    assert.equal(String(url), `${gatewayOrigin}/models/blackforestlabs/v1/flux-2/generations`); assert.equal(new Headers(init?.headers).get("X-Egakumi-Profile"), "pro");
    assert.equal(JSON.parse(String(init?.body)).num_images, 1);
    return Response.json({ created: 1, data: [{ b64_json: jpeg }], request_meta: { cost: 0.01 } });
  } }));
  assert.equal(response.status, 200); assert.equal((await response.json()).images[0].mimeType, "image/jpeg");
});

test("正常な画像応答の追加フィールドへキーが反射されてもブラウザーへ返さない", async () => {
  const response = await handleImageGeneration(request(), dependencies({ fetch: async () => Response.json({ data: [{ b64_json: png }], metadata: { reflectedCredential: secret } }) }));
  assert.equal(response.status, 502);
  const body = await response.text();
  assert.ok(!body.includes(secret));
  assert.deepEqual(JSON.parse(body), { error: { code: "GenerationFailed" } });
});

test("上流エラー・不正Content-Type・大きすぎる画像を成功扱いしない", async () => {
  for (const status of [302, 400, 401, 403, 500]) {
    const response = await handleImageGeneration(request(), dependencies({ fetch: async () => new Response(secret, { status }) }));
    assert.equal(response.status, 502); assert.ok(!(await response.text()).includes(secret));
  }
  const rate = await handleImageGeneration(request(), dependencies({ fetch: async () => new Response(secret, { status: 429, headers: { "retry-after": "20" } }) }));
  assert.deepEqual(await rate.json(), { error: { code: "RateLimitExceeded" }, retryAfterSeconds: 20 });
  const rejectedResponseHeaders: Record<string, string>[] = [{ "Content-Type": "text/html" }, { "Content-Type": "application/json", "Content-Length": String(MAX_IMAGE_RESPONSE_BYTES + 1) }];
  for (const headers of rejectedResponseHeaders) assert.equal((await handleImageGeneration(request(), dependencies({ fetch: async () => new Response("{}", { headers }) }))).status, 502);
  for (const body of [{ data: [] }, { data: [{ b64_json: Buffer.from("<svg>unsafe</svg>").toString("base64") }] }, { data: [{ url: "http://127.0.0.1/test" }] }, { error: { code: "blocked" }, data: [{ b64_json: png }] }]) assert.equal((await handleImageGeneration(request(), dependencies({ fetch: async () => Response.json(body) }))).status, 502);
});

test("JSONのUnicodeエスケープで隠れた購読キーも画像の正常JSONへ含めない", async () => {
  const encoded = [...secret].map((character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`).join("");
  const raw = JSON.stringify({ data: [{ b64_json: png }], metadata: { reflected: secret } }).replace(secret, encoded);
  assert.equal(raw.includes(secret), false);
  let calls = 0;
  const response = await handleImageGeneration(request(), dependencies({ fetch: async () => { calls++; return new Response(raw, { headers: { "content-type": "application/json" } }); } }));
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: { code: "GenerationFailed" } });
  assert.equal(calls, 1);
});

test("画像のraw JSONでも引用符付きキーの値・プロパティ名への反射を拒否する", async () => {
  const credential = 'synthetic-image-"key"-\\-value';
  for (const reflected of [{ metadata: { reflected: credential } }, { [credential]: "marker" }]) {
    const raw = JSON.stringify({ data: [{ b64_json: png }], ...reflected });
    assert.equal(raw.includes(credential), false);
    const response = await handleImageGeneration(request(), dependencies({ resolveSubscriptionKey: async () => credential, fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) }));
    assert.equal(response.status, 502); assert.deepEqual(await response.json(), { error: { code: "GenerationFailed" } });
  }
});

test("画像読み取り中の期限切れ・キャンセルは再試行せず読取りを終了する", async () => {
  const keepAlive = setTimeout(() => undefined, 1000);
  try {
    let calls = 0;
    const timeout = await handleImageGeneration(request(), dependencies({ timeoutMs: 10, fetch: async () => { calls++; return new Response(new ReadableStream({ start() {} }), { headers: { "Content-Type": "application/json" } }); } }));
    assert.equal(timeout.status, 504); assert.equal(calls, 1);
    const abort = new AbortController();
    const result = handleImageGeneration(request(undefined, {}, abort.signal), dependencies({ fetch: async () => { setTimeout(() => abort.abort(), 10); return new Response(new ReadableStream({ start() {} }), { headers: { "Content-Type": "application/json" } }); } }));
    assert.equal((await result).status, 499);
  } finally { clearTimeout(keepAlive); }
});

test("PNG/JPEG以外とBase64の不正なパディングを拒否し、大きな実画像でもスタックを消費しない", () => {
  assert.equal(rasterBase64Mime(png), "image/png"); assert.equal(rasterBase64Mime(jpeg), "image/jpeg");
  for (const value of ["data:image/png;base64," + png, png + "=", "=" + png.slice(1), Buffer.from("<svg>unsafe</svg>").toString("base64"), "a".repeat(16 * 1_048_576 + 4)]) assert.equal(rasterBase64Mime(value), null);
  const large = Buffer.concat([Buffer.from(png, "base64"), Buffer.alloc(1_048_576, 100)]).toString("base64");
  assert.equal(rasterBase64Mime(large), "image/png");
  assert.equal(rasterBase64Mime(large.slice(0, -5) + "!AAAA"), null);
});

test("画像URLは自動取得せず、外部のHTTPS応答のみJSONとして扱う", async () => {
  for (const url of ["javascript:alert(1)", "http://images.example.org/a.png", "https://127.0.0.1/a.png", "https://[::1]/a.png", "https://private.local/a.png", "https://user:pass@images.example.org/a.png", "https://images.example.org:8443/a.png"]) assert.equal(safeImageUrl(url), null);
  const url = "https://images.example.org/result.png?signature=temporary";
  const parsed = parseImageGenerationResult({ data: [{ url }] }, flux.id, 100);
  assert.deepEqual(parsed.images, [{ index: 0, kind: "url" }]); assert.equal(imagePreviewSource(parsed, parsed.images[0]), null);
  assert.equal(imageExternalUrl(parsed, parsed.images[0]), url);
  const raster = parseImageGenerationResult({ data: [{ b64_json: png }] }, openai.id, 100);
  assert.equal(imageExternalUrl(raster, raster.images[0]), null);
  parsed.rawResponse.data = [{ url: "https://127.0.0.1/private.png" }];
  assert.equal(imageExternalUrl(parsed, parsed.images[0]), null);
  parsed.rawResponse.data = [{ url }];
  let calls = 0;
  const response = await handleImageGeneration(request(defaultImageInput(flux)), dependencies({ fetch: async () => { calls++; return Response.json({ data: [{ url }] }); } }));
  assert.equal(response.status, 200); assert.equal(calls, 1);
});

test("Base64の画面表示だけを省略し、コピーする実JSONと画像は保持する", async () => {
  const large = Buffer.concat([Buffer.from(png, "base64"), Buffer.alloc(1024)]).toString("base64");
  const raw = { created: 1, data: [{ b64_json: large }] };
  const preview = imageResponsePreview(raw); assert.ok(preview.truncated); assert.ok(preview.text.length < 1000); assert.equal(raw.data[0].b64_json, large);
  const result = parseImageGenerationResult(raw, openai.id, 100);
  assert.ok(imagePreviewSource(result, result.images[0])?.startsWith("data:image/png;base64,"));
  const received = await readImageResponse(Response.json(result)); assert.equal(received.rawResponse.data && JSON.stringify(received.rawResponse), JSON.stringify(raw));
});

test("クライアントの画像応答読取りも中断でき、エラー本文を公開しない", async () => {
  await assert.rejects(readImageResponse(Response.json({ error: { code: "ApiKeyUnavailable" }, requestId: "valid-id" }, { status: 403 })), (error: unknown) => error instanceof Error && error.message === "ApiKeyUnavailable");
  await assert.rejects(readImageResponse(Response.json({ error: { code: secret } }, { status: 500 })), (error: unknown) => error instanceof Error && !error.message.includes(secret));
  const abort = new AbortController(); const keepAlive = setTimeout(() => abort.abort(), 10);
  try { await assert.rejects(readImageResponse(new Response(new ReadableStream({ start() {} })), abort.signal)); } finally { clearTimeout(keepAlive); }
});

test("画像生成の日本語と英語の案内は同じ項目を持つ", () => { assert.deepEqual(Object.keys(getImageGenerationDictionary("ja")).sort(), Object.keys(getImageGenerationDictionary("en")).sort()); });
