import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { chatModel as chat, compatibleModel as compatible, responsesModel as responses, textGenerationModels as models } from "./helpers/model-fixtures.ts";
import {
  buildTextGenerationRequest, getIntegrationExample, getTextGenerationEndpoint, getTextGenerationHeaders,
  getTextGenerationRequestSchema, getTextGenerationResponseSchema, INTEGRATION_LANGUAGES, SAMPLE_TEXT_PROMPT,
} from "../lib/model-integration.ts";

const origin = "https://gateway.example.invalid";

test("三種類の生成形式で入力メッセージと対応する出力パラメーターを送る", () => {
  for (const model of models) {
    for (const stream of [false, true]) {
      const body = buildTextGenerationRequest(model, { message: SAMPLE_TEXT_PROMPT, maxOutputTokens: model.maxOutputTokens, stream });
      assert.equal(body.model, model.id);
      assert.equal(body.stream, stream);
      assert.equal(body[model.outputTokenParameter], model.maxOutputTokens);
      const presentTokenParameters = ["max_tokens", "max_completion_tokens", "max_output_tokens"].filter((key) => key in body);
      assert.deepEqual(presentTokenParameters, [model.outputTokenParameter]);
      if (model.operationId === "responses-create") {
        assert.equal(body.input, SAMPLE_TEXT_PROMPT);
        assert.equal(body.store, false);
        assert.equal("messages" in body, false);
      } else {
        assert.deepEqual(body.messages, [{ role: "user", content: SAMPLE_TEXT_PROMPT }]);
        assert.equal("input" in body, false);
        assert.equal("store" in body, false);
      }
    }
  }
});

test("同じモデル名でも選択したAPIの本文・パス・ヘッダー・スキーマを全言語へ反映する", () => {
  const variants = [chat, { ...responses, id: chat.id }];
  const options = { message: "選択したAPIで\n\"引用\"を含む文章を処理する", maxOutputTokens: 128, stream: true };
  for (const model of variants) {
    const body = buildTextGenerationRequest(model, options);
    const schema = getTextGenerationRequestSchema(model) as { required: string[]; properties: Record<string, unknown> };
    const isResponses = model.operationId === "responses-create";
    assert.equal(body.model, chat.id);
    assert.equal(isResponses ? body.input : (body.messages as { content: string }[])[0].content, options.message);
    assert.equal("store" in body, isResponses); if (isResponses) assert.equal(body.store, false);
    assert.equal("max_output_tokens" in body, isResponses); assert.equal("max_completion_tokens" in body, !isResponses);
    assert.equal(schema.required.includes(isResponses ? "input" : "messages"), true);
    assert.equal("store" in schema.properties, isResponses);
    assert.equal(getTextGenerationHeaders(model, true)["X-Egakumi-Profile"], model.profileId);
    for (const language of INTEGRATION_LANGUAGES) {
      const example = getIntegrationExample(language, model, options, origin);
      assert.ok(example.includes(`${origin}${model.path}`));
      assert.ok(example.includes(model.outputTokenParameter));
      assert.ok(example.includes(model.profileId));
      assert.ok(!example.includes(isResponses ? "/chat/completions" : "/responses"));
    }
  }
});

test("公開APIのモデル上限を超える設定や不正なstream値からリクエストを作らない", () => {
  for (const maxOutputTokens of [0, -1, 1.5, NaN, Infinity, chat.maxOutputTokens! + 1]) {
    assert.throws(() => buildTextGenerationRequest(chat, { message: SAMPLE_TEXT_PROMPT, maxOutputTokens, stream: false }), RangeError);
  }
  assert.throws(() => buildTextGenerationRequest(chat, { message: SAMPLE_TEXT_PROMPT, maxOutputTokens: 128, stream: "true" as unknown as boolean }), TypeError);
});

test("URLやヘッダーの例には公開パスと環境変数だけを載せる", () => {
  assert.equal(getTextGenerationEndpoint(chat, origin), `${origin}${chat.path}`);
  assert.equal(getTextGenerationEndpoint(chat, `${origin}/`), `${origin}${chat.path}`);
  assert.equal(getTextGenerationHeaders(chat, false)["api-key"], "${GATEWAY_API_KEY}");
  assert.equal(getTextGenerationHeaders(chat, true).Accept, "text/event-stream");
  assert.equal(getTextGenerationHeaders(compatible, false)["X-Egakumi-Profile"], "compatible");
  for (const unsafeOrigin of ["http://gateway.example.invalid", "https://user:secret@gateway.example.invalid", `${origin}/path`, `${origin}?key=secret`, `${origin}#secret`]) {
    assert.throws(() => getTextGenerationEndpoint(chat, unsafeOrigin));
  }
  for (const path of ["//other.invalid/models", "/models/../auth", "/models/test?secret=value", "/models/test#secret", "/models/\\test"]) {
    assert.throws(() => getTextGenerationEndpoint({ ...chat, path }, origin));
  }
});

test("全言語の例が選択モデル・プロファイル・パラメーターとストリーム方式へ連動する", () => {
  for (const language of INTEGRATION_LANGUAGES) {
    for (const model of [chat, compatible, responses]) {
      const normal = getIntegrationExample(language, model, { message: SAMPLE_TEXT_PROMPT, maxOutputTokens: 123, stream: false }, origin);
      const streaming = getIntegrationExample(language, model, { message: SAMPLE_TEXT_PROMPT, maxOutputTokens: 321, stream: true }, origin);
      for (const sample of [normal, streaming]) {
        assert.ok(sample.includes(`${origin}${model.path}`), language);
        assert.ok(sample.includes("GATEWAY_API_KEY"), language);
        assert.ok(sample.includes("X-Egakumi-Profile"), language);
        assert.ok(sample.includes(model.profileId), language);
        assert.ok(sample.includes(model.id), language);
        assert.ok(sample.includes(SAMPLE_TEXT_PROMPT), language);
        assert.equal(sample.includes("localhost:3000/api"), false);
      }
      assert.match(normal, new RegExp(`${model.outputTokenParameter}[^\\n]+123`));
      assert.match(streaming, new RegExp(`${model.outputTokenParameter}[^\\n]+321`));
      assert.ok(normal.includes("application/json"));
      assert.ok(streaming.includes("text/event-stream"));
      assert.notEqual(normal, streaming);
    }
  }
});

test("curl例のJSON本文は共通ビルダーが実際に送る本文と一致する", () => {
  for (const model of models) {
    for (const stream of [false, true]) {
      const options = { message: SAMPLE_TEXT_PROMPT, maxOutputTokens: 128, stream };
      const sample = getIntegrationExample("curl", model, options, origin);
      const json = sample.split("<<'JSON'\n")[1].split("\nJSON\n")[0];
      assert.deepEqual(JSON.parse(json), buildTextGenerationRequest(model, options));
      assert.equal(sample.includes("--no-buffer"), stream);
    }
  }
});

test("Python例の本文を複数行で読みやすく表示し、単一引用符がraw文字列を閉じない", () => {
  const model = { ...chat, id: "example'''model" };
  const options = { message: SAMPLE_TEXT_PROMPT, maxOutputTokens: 128, stream: true };
  const source = getIntegrationExample("python", model, options, origin);
  const body = source.split("payload = json.loads(r'''\n")[1].split("\n''')")[0];
  assert.ok(body.includes("\n"));
  assert.equal(body.includes("'"), false);
  assert.ok(body.includes("\\u0027\\u0027\\u0027"));
  assert.deepEqual(JSON.parse(body), buildTextGenerationRequest(model, options));
});

test("JSON SchemaはChatとResponsesの違い・出力上限・JSONとSSEの違いを示す", () => {
  const chatSchema = getTextGenerationRequestSchema(chat);
  const responseSchema = getTextGenerationRequestSchema(responses);
  const chatProperties = chatSchema.properties as Record<string, Record<string, unknown>>;
  const responseProperties = responseSchema.properties as Record<string, Record<string, unknown>>;
  assert.equal(chatProperties.max_completion_tokens.maximum, chat.maxOutputTokens);
  assert.ok("messages" in chatProperties);
  assert.equal("input" in chatProperties, false);
  assert.equal(responseProperties.max_output_tokens.maximum, responses.maxOutputTokens);
  assert.equal(responseProperties.store.const, false);
  assert.ok("input" in responseProperties);
  assert.equal("messages" in responseProperties, false);
  assert.equal(getTextGenerationResponseSchema(chat, false).type, "object");
  assert.match(String(getTextGenerationResponseSchema(chat, true).description), /\[DONE\]/);
  assert.ok(Array.isArray(getTextGenerationResponseSchema(responses, true).oneOf));
});

test("ChatのSSEスキーマは通常チャンクと空objectのAzureフィルター注釈を区別して示す", () => {
  const schema = getTextGenerationResponseSchema(chat, true);
  const branches = schema.anyOf as { title: string; required: string[]; properties: Record<string, Record<string, unknown>> }[];
  assert.equal(branches.length, 2);
  const chunk = branches.find((branch) => branch.title === "Completion chunk")!;
  const annotation = branches.find((branch) => branch.title === "Azure prompt-filter annotation")!;
  assert.ok(chunk.required.includes("choices"));
  assert.equal(chunk.properties.object.const, "chat.completion.chunk");
  assert.ok(annotation.required.includes("prompt_filter_results"));
  assert.equal(annotation.properties.object.type, "string");
  assert.equal("const" in annotation.properties.object, false);
  assert.equal(annotation.properties.choices.maxItems, 0);
  assert.equal(annotation.properties.prompt_filter_results.minItems, 1);
  assert.match(String(schema.description), /prompt-filter annotation/);
  assert.match(String(schema.description), /\[DONE\].*not JSON/);
});

test("TypeScript例はJSON応答を読み、ストリーム時には分割されたUTF-8を欠損させず受け取る", async () => {
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  for (const stream of [false, true]) {
    const options = { message: "引用\"と改行\n'''、${value}、JSONの文字を保持する。", maxOutputTokens: 128, stream };
    const text: string[] = [];
    const log: unknown[] = [];
    const fakeFetch = async (url: string, init: RequestInit) => {
      assert.equal(url, `${origin}${chat.path}`);
      assert.equal(new Headers(init.headers).get("api-key"), "test-only-value");
      assert.equal(init.redirect, "error");
      assert.deepEqual(JSON.parse(String(init.body)), buildTextGenerationRequest(chat, options));
      if (!stream) return Response.json({ choices: [{ message: { content: "確認" } }] });
      const data = new TextEncoder().encode('data: {"text":"確認"}\n\ndata: [DONE]\n\n');
      return new Response(new ReadableStream({ start(controller) {
        for (const byte of data) controller.enqueue(new Uint8Array([byte]));
        controller.close();
      } }));
    };
    const source = getIntegrationExample("typescript", chat, options, origin).replace('import process from "node:process";\n', "");
    const run = new AsyncFunction("process", "fetch", "console", source);
    await run({ env: { GATEWAY_API_KEY: "test-only-value" }, stdout: { write: (value: string) => text.push(value) } }, fakeFetch, { log: (value: unknown) => log.push(value) });
    if (stream) assert.equal(text.join(""), 'data: {"text":"確認"}\n\ndata: [DONE]\n\n');
    else assert.match(String(log[0]), /確認/);
  }
});

test("TypeScript例はNext.js推奨構成のdom・dom.iterableでも型検査を通る", () => {
  const sources = new Map<string, string>();
  for (const model of [chat, compatible, responses]) {
    for (const stream of [false, true]) {
      const path = fileURLToPath(new URL(`../.integration-${model.id}-${stream}.ts`, import.meta.url)).replaceAll("\\", "/");
      sources.set(path, getIntegrationExample("typescript", model, { message: SAMPLE_TEXT_PROMPT, maxOutputTokens: 128, stream }, origin));
    }
  }
  const options: ts.CompilerOptions = {
    target: ts.ScriptTarget.ES2017,
    module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    lib: ["lib.dom.d.ts", "lib.dom.iterable.d.ts", "lib.esnext.d.ts"],
    types: ["node"], strict: true, noEmit: true, skipLibCheck: true, esModuleInterop: true,
  };
  const host = ts.createCompilerHost(options);
  const getSourceFile = host.getSourceFile.bind(host);
  const fileExists = host.fileExists.bind(host);
  host.fileExists = (fileName) => sources.has(fileName.replaceAll("\\", "/")) || fileExists(fileName);
  host.getSourceFile = (fileName, languageVersion, onError, shouldCreateNewSourceFile) => {
    const source = sources.get(fileName.replaceAll("\\", "/"));
    return source === undefined ? getSourceFile(fileName, languageVersion, onError, shouldCreateNewSourceFile)
      : ts.createSourceFile(fileName, source, languageVersion, true);
  };
  const diagnostics = ts.getPreEmitDiagnostics(ts.createProgram([...sources.keys()], options, host))
    .filter((diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error);
  assert.deepEqual(diagnostics.map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n")), []);
});

test("C#例はヘッダー受信後の本文とSSEにも同じ600秒のキャンセルを適用する", () => {
  for (const stream of [false, true]) {
    const source = getIntegrationExample("csharp", chat, { message: SAMPLE_TEXT_PROMPT, maxOutputTokens: 128, stream }, origin);
    assert.ok(source.includes("new CancellationTokenSource(TimeSpan.FromSeconds(600))"));
    assert.ok(source.includes("HttpCompletionOption.ResponseHeadersRead, cancellation.Token"));
    if (stream) {
      assert.ok(source.includes("ReadAsStreamAsync(cancellation.Token)"));
      assert.ok(source.includes("ReadLineAsync(cancellation.Token)"));
    } else assert.ok(source.includes("ReadAsStringAsync(cancellation.Token)"));
  }
});
