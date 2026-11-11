import type { ImageGenerationModel } from "./model-types.ts";
import { imageGenerationInputSchema, MAX_IMAGE_PROMPT_CHARACTERS, type ImageGenerationInput } from "./image-request.ts";
import type { IntegrationLanguage } from "./model-integration.ts";

/** 編集可能な入力欄の初期例です。実行時は利用者のpromptを使います。 */
export const SAMPLE_IMAGE_PROMPT = "A small white ceramic robot watering a green plant on a bright wooden desk, clean modern illustration, no text, square composition.";

/** モデル選択直後の設定をAPI正本の範囲から作ります。 */
export function defaultImageInput(model: ImageGenerationModel): ImageGenerationInput {
  return { modelId: model.id, prompt: SAMPLE_IMAGE_PROMPT, size: model.sizes[0] ?? "", quality: model.qualities[0] ?? "", width: 1024, height: 1024, count: model.minImages ?? 1, outputFormat: model.formats[0] ?? "png" };
}

/** プロバイダーの契約と正本のサイズ・品質・形式を検査して本文を組み立てます。 */
export function buildImageGenerationRequest(model: ImageGenerationModel, input: ImageGenerationInput): Record<string, unknown> {
  imageGenerationInputSchema.strip().parse(input);
  if (model.id !== input.modelId || !model.formats.includes(input.outputFormat)) throw new RangeError("Invalid image model or format");
  const count = input.count ?? 1;
  if (count < (model.minImages ?? 1) || count > (model.maxImages ?? (model.operationId === "image-generate" ? 10 : 1))) throw new RangeError("Invalid image count");
  if (model.operationId === "image-generate") {
    if (!/^(?:auto|[1-9][0-9]*x[1-9][0-9]*)$/.test(input.size) || !/^[a-z]+$/.test(input.quality)) throw new RangeError("Invalid image size or quality");
    return { model: model.id, prompt: input.prompt, n: count, size: input.size, quality: input.quality, output_format: input.outputFormat };
  }
  if (input.size !== "" || input.quality !== "" || !Number.isInteger(input.width) || !Number.isInteger(input.height)
    || input.width < model.minWidth || input.width > model.maxWidth || input.height < model.minHeight || input.height > model.maxHeight) throw new RangeError("Invalid image dimensions");
  return { model: model.id, prompt: input.prompt, num_images: count, width: input.width, height: input.height, output_format: input.outputFormat };
}

/** Gatewayの公開オリジンと登録済みパスだけをコード例に使います。 */
export function getImageGenerationEndpoint(model: ImageGenerationModel, origin: string): string {
  const base = new URL(origin);
  if (base.protocol !== "https:" || base.username || base.password || base.pathname !== "/" || base.search || base.hash) throw new TypeError("A public HTTPS origin is required");
  const expected = model.operationId === "image-generate" ? "/models/openai-images/v1/generations" : "/models/blackforestlabs/v1/flux-2/generations";
  if (model.path !== expected) throw new TypeError("An approved image generation path is required");
  return new URL(model.path, base).href;
}

/** FLUXのルーティングは、実際のGatewayポリシーが読むプロファイルヘッダーに合わせます。 */
export function getImageGenerationHeaders(model: ImageGenerationModel): Record<string, string> {
  return { "Content-Type": "application/json", Accept: "application/json", "api-key": "${GATEWAY_API_KEY}", ...(model.profileId ? { "X-Egakumi-Profile": model.profileId } : {}) };
}

/** パネルで使う主要フィールドのJSON Schemaです。全機能の仕様と誤認させない部分定義です。 */
export function getImageRequestSchema(model: ImageGenerationModel): Record<string, unknown> {
  return { $schema: "https://json-schema.org/draft/2020-12/schema", title: "Image generation request — panel fields", type: "object", additionalProperties: true,
    required: ["model", "prompt", ...(model.operationId === "image-generate" ? ["n", "size", "quality"] : ["num_images", "width", "height"]), "output_format"],
    properties: { model: { const: model.id }, prompt: { type: "string", minLength: 1, maxLength: MAX_IMAGE_PROMPT_CHARACTERS }, output_format: { enum: model.formats },
      ...(model.operationId === "image-generate" ? { n: { type: "integer", minimum: model.minImages ?? 1, maximum: model.maxImages ?? 10 }, size: { type: "string", examples: model.sizes }, quality: { type: "string", examples: model.qualities } }
        : { num_images: { type: "integer", minimum: model.minImages ?? 1, maximum: model.maxImages ?? 1 }, width: { type: "integer", minimum: model.minWidth, maximum: model.maxWidth }, height: { type: "integer", minimum: model.minHeight, maximum: model.maxHeight } }) } };
}

/** Azureの同期応答を説明し、BFL直販の非同期polling_urlとは区別します。 */
export function getImageResponseSchema(): Record<string, unknown> {
  return { $schema: "https://json-schema.org/draft/2020-12/schema", title: "Image generation success response — partial", type: "object", required: ["data"],
    properties: { created: { type: "integer" }, data: { type: "array", items: { type: "object", anyOf: [{ required: ["b64_json"] }, { required: ["url"] }], properties: { b64_json: { type: "string", contentEncoding: "base64" }, url: { type: "string", format: "uri", description: "Temporary HTTPS image URL when returned by the provider." }, revised_prompt: { type: "string" } }, additionalProperties: true } }, usage: { type: "object", additionalProperties: true } }, additionalProperties: true };
}

/** POSIXシェルで固定値をひとつの引数として扱います。 */
function quote(value: string): string { return `'${value.replaceAll("'", "'\\''")}'`; }

/** 秘密キーを環境変数から読む、サーバー側の5言語の例を作ります。 */
export function getImageIntegrationExample(language: IntegrationLanguage, model: ImageGenerationModel, input: ImageGenerationInput, origin: string): string {
  const url = getImageGenerationEndpoint(model, origin);
  const body = JSON.stringify(buildImageGenerationRequest(model, input), null, 2);
  const profile = model.profileId;
  switch (language) {
    case "typescript": return `// Node.js server; never expose the key to the browser.\nconst apiKey = process.env.GATEWAY_API_KEY;\nif (!apiKey) throw new Error("GATEWAY_API_KEY is required");\nconst response = await fetch(${JSON.stringify(url)}, {\n  method: "POST", redirect: "error",\n  headers: { "Content-Type": "application/json", Accept: "application/json", "api-key": apiKey${profile ? `, "X-Egakumi-Profile": ${JSON.stringify(profile)}` : ""} },\n  body: JSON.stringify(${body}),\n});\nif (!response.ok) throw new Error(\`HTTP \${response.status}\`);\nconsole.log(JSON.stringify(await response.json(), null, 2));\n`;
    case "python": return `import json\nimport os\nimport urllib.request\n\nclass NoRedirect(urllib.request.HTTPRedirectHandler):\n    def redirect_request(self, req, fp, code, msg, headers, newurl):\n        return None\n\npayload = json.loads(r'''\n${body.replaceAll("'", "\\u0027")}\n''')\nrequest = urllib.request.Request(\n    ${JSON.stringify(url)},\n    data=json.dumps(payload).encode("utf-8"),\n    headers={"Content-Type": "application/json", "Accept": "application/json", "api-key": os.environ["GATEWAY_API_KEY"]${profile ? `, "X-Egakumi-Profile": ${JSON.stringify(profile)}` : ""}},\n    method="POST",\n)\nwith urllib.request.build_opener(NoRedirect()).open(request) as response:\n    print(json.dumps(json.load(response), indent=2))\n`;
    case "php": return `<?php\n$apiKey = getenv('GATEWAY_API_KEY');\nif (!$apiKey) { throw new RuntimeException('GATEWAY_API_KEY is required'); }\n$body = <<<'JSON'\n${body}\nJSON;\n$curl = curl_init(${quote(url)});\ncurl_setopt_array($curl, [\n    CURLOPT_POST => true, CURLOPT_POSTFIELDS => $body,\n    CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'Accept: application/json', 'api-key: ' . $apiKey${profile ? `, 'X-Egakumi-Profile: ${profile}'` : ""}],\n    CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => false, CURLOPT_TIMEOUT => 0,\n]);\n$result = curl_exec($curl);\n$status = curl_getinfo($curl, CURLINFO_RESPONSE_CODE);\nif ($result === false || $status >= 400) { throw new RuntimeException('HTTP ' . $status); }\ncurl_close($curl);\necho json_encode(json_decode($result, true, 512, JSON_THROW_ON_ERROR), JSON_PRETTY_PRINT);\n`;
    case "csharp": return `using System.Net.Http;\nusing System.Text;\nusing System.Text.Json;\n\nvar key = Environment.GetEnvironmentVariable("GATEWAY_API_KEY")\n    ?? throw new InvalidOperationException("GATEWAY_API_KEY is required");\nusing var handler = new HttpClientHandler { AllowAutoRedirect = false };\nusing var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };\nusing var request = new HttpRequestMessage(HttpMethod.Post, ${JSON.stringify(url)});\nrequest.Headers.Add("api-key", key);\n${profile ? `request.Headers.Add("X-Egakumi-Profile", ${JSON.stringify(profile)});\n` : ""}request.Content = new StringContent("""\n${body}\n""", Encoding.UTF8, "application/json");\nusing var response = await client.SendAsync(request);\nresponse.EnsureSuccessStatusCode();\nConsole.WriteLine(await response.Content.ReadAsStringAsync());\n`;
    case "curl": return `: "\${GATEWAY_API_KEY:?GATEWAY_API_KEY is required}"\ncurl --silent --show-error --fail-with-body \\\n  --connect-timeout 10 \\\n  ${quote(url)} \\\n  --header 'Content-Type: application/json' \\\n  --header 'Accept: application/json' \\\n  --header "api-key: $GATEWAY_API_KEY" \\\n${profile ? `  --header ${quote(`X-Egakumi-Profile: ${profile}`)} \\\n` : ""}  --data-binary @- <<'JSON'\n${body}\nJSON\n`;
  }
}
