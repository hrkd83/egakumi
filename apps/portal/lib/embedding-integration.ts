import type { EmbeddingModel } from "./model-types.ts";
import { embeddingInputSchema, type EmbeddingInput } from "./embedding-request.ts";
import type { IntegrationLanguage } from "./model-integration.ts";

/** 編集可能な入力欄の初期例です。実行時は利用者のtextsを使います。 */
export const EMBEDDING_SAMPLE_TEXTS = [
  "AI gateways provide a secure, shared entry point for AI services.",
  "An embedding represents the meaning of text as a numeric vector.",
] as const;

/** 画面で提示する出力次元です。nullはパラメーターを省略し、モデルの既定値を使います。 */
export function getEmbeddingDimensionChoices(model: EmbeddingModel): number[] {
  if (model.providerId === "cohere") return [256, 512, 1_024, 1_536];
  return model.id === "text-embedding-3-large" ? [256, 512, 1_024, 1_536, 3_072] : [256, 512, 1_024, 1_536];
}

/** 提供元の公式仕様に基づく、パラメーター省略時の次元数です。 */
export function getEmbeddingDefaultDimensions(model: EmbeddingModel): number {
  return model.id === "text-embedding-3-large" ? 3_072 : 1_536;
}

/** モデル選択時に、別の提供元のオプションを持ち越さない初期値を作ります。 */
export function defaultEmbeddingInput(model: EmbeddingModel): EmbeddingInput {
  return { modelId: model.id, dimensions: null, inputType: model.providerId === "cohere" ? model.inputTypes[0] ?? "search_document" : null, texts: [EMBEDDING_SAMPLE_TEXTS[0]] };
}

/** 利用者の入力文と、選択した提供元に適合するパラメーターだけでリクエストを作ります。 */
export function buildEmbeddingRequest(model: EmbeddingModel, input: EmbeddingInput): Record<string, unknown> {
  embeddingInputSchema.strip().parse(input);
  if (model.id !== input.modelId) throw new TypeError("Invalid embedding model");
  const count = input.texts.length;
  if (count > model.maxInputs) throw new RangeError("The input count exceeds the API limit");
  if (input.dimensions !== null && (!Number.isInteger(input.dimensions) || input.dimensions < 1 || input.dimensions > getEmbeddingDefaultDimensions(model))) throw new RangeError("Invalid embedding dimensions");
  const texts = [...input.texts];
  if (model.providerId === "cohere") {
    if (!input.inputType || !model.inputTypes.includes(input.inputType) || (input.dimensions !== null && !getEmbeddingDimensionChoices(model).includes(input.dimensions))) throw new TypeError("Invalid Cohere embedding options");
    return { model: model.id, texts, input_type: input.inputType, embedding_types: ["float"], ...(input.dimensions === null ? {} : { output_dimension: input.dimensions }) };
  }
  if (input.inputType !== null) throw new TypeError("OpenAI embeddings have no input_type option");
  return { model: model.id, input: texts, encoding_format: "float", ...(input.dimensions === null ? {} : { dimensions: input.dimensions }) };
}

/** 公開された固定のパスだけで、組み込み先のURLを作ります。 */
export function getEmbeddingEndpoint(model: EmbeddingModel, gatewayOrigin: string): string {
  const origin = new URL(gatewayOrigin);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(origin.hostname);
  if ((origin.protocol !== "https:" && !(local && origin.protocol === "http:")) || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) throw new TypeError("A public Gateway origin is required");
  if (!model.path.startsWith("/models/") || /[?#\r\n\\]/.test(model.path)) throw new TypeError("An embedding API path is required");
  const endpoint = new URL(model.path, origin.origin);
  if (endpoint.origin !== origin.origin || endpoint.pathname !== model.path) throw new TypeError("An unchanged embedding API path is required");
  return endpoint.href;
}

/** 例のAPIキーは環境変数の表現だけとし、実値を受け取る引数を設けません。 */
export function getEmbeddingHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", Accept: "application/json", "api-key": "${GATEWAY_API_KEY}" };
}

/** このパネルが扱うテキスト埋め込みの入力を、提供元別のJSON Schemaで示します。 */
export function getEmbeddingRequestSchema(model: EmbeddingModel): Record<string, unknown> {
  const cohere = model.providerId === "cohere";
  return {
    $schema: "https://json-schema.org/draft/2020-12/schema", title: `${model.provider} text embeddings request (partial)`,
    type: "object", required: cohere ? ["model", "texts", "input_type", "embedding_types"] : ["model", "input", "encoding_format"],
    properties: {
      model: { const: model.id }, [cohere ? "texts" : "input"]: { type: "array", minItems: 1, maxItems: model.maxInputs, items: { type: "string", minLength: 1 } },
      ...(cohere ? { input_type: { enum: model.inputTypes }, embedding_types: { const: ["float"] }, output_dimension: { type: "integer", enum: getEmbeddingDimensionChoices(model) } }
        : { encoding_format: { const: "float" }, dimensions: { type: "integer", minimum: 1, maximum: getEmbeddingDefaultDimensions(model) } }),
    }, additionalProperties: true,
  };
}

/** 出力ベクトルと使用量を読むための部分スキーマです。実際の追加フィールドは生JSONで確認できます。 */
export function getEmbeddingResponseSchema(model: EmbeddingModel): Record<string, unknown> {
  const vector = { type: "array", items: { type: "number" } };
  return {
    $schema: "https://json-schema.org/draft/2020-12/schema", title: `${model.provider} embeddings response (partial)`, type: "object",
    ...(model.providerId === "cohere" ? {
      required: ["embeddings"], properties: { embeddings: { type: "object", required: ["float"], properties: { float: { type: "array", items: vector } }, additionalProperties: true }, meta: { type: "object", properties: { billed_units: { type: "object", properties: { input_tokens: { type: "integer", minimum: 0 } }, additionalProperties: true } }, additionalProperties: true } },
    } : {
      required: ["data", "model"], properties: { model: { type: "string" }, data: { type: "array", items: { type: "object", required: ["index", "embedding"], properties: { index: { type: "integer", minimum: 0 }, embedding: vector }, additionalProperties: true } }, usage: { type: "object", properties: { prompt_tokens: { type: "integer", minimum: 0 }, total_tokens: { type: "integer", minimum: 0 } }, additionalProperties: true } },
    }), additionalProperties: true,
  };
}

/** POSIXシェルやPHPの単一引用符内に、固定値を安全に埋め込みます。 */
function quote(value: string): string { return `'${value.replaceAll("'", "'\\''")}'`; }

/** 各言語の標準HTTP機能を使い、同じ本文をAPIMへ直接送るサーバー側の例を作ります。 */
export function getEmbeddingIntegrationExample(language: IntegrationLanguage, model: EmbeddingModel, input: EmbeddingInput, gatewayOrigin: string): string {
  const url = getEmbeddingEndpoint(model, gatewayOrigin);
  const body = JSON.stringify(buildEmbeddingRequest(model, input), null, 2);
  switch (language) {
    case "typescript": return `import process from "node:process";\n\nconst apiKey = process.env.GATEWAY_API_KEY;\nif (!apiKey) throw new Error("GATEWAY_API_KEY is required");\nconst response = await fetch(${JSON.stringify(url)}, {\n  method: "POST",\n  headers: { "Content-Type": "application/json", Accept: "application/json", "api-key": apiKey },\n  body: JSON.stringify(${body}),\n  redirect: "error", signal: AbortSignal.timeout(90_000),\n});\nif (!response.ok) throw new Error(\`HTTP \${response.status}\`);\nconsole.log(JSON.stringify(await response.json(), null, 2));\n`;
    case "python": return `import json\nimport os\nimport urllib.request\n\nclass NoRedirect(urllib.request.HTTPRedirectHandler):\n    def redirect_request(self, req, fp, code, msg, headers, newurl):\n        return None\n\npayload = json.loads(r'''\n${body.replaceAll("'", "\\u0027")}\n''')\nrequest = urllib.request.Request(\n    ${JSON.stringify(url)},\n    data=json.dumps(payload).encode("utf-8"),\n    headers={"Content-Type": "application/json", "Accept": "application/json", "api-key": os.environ["GATEWAY_API_KEY"]},\n    method="POST",\n)\nwith urllib.request.build_opener(NoRedirect()).open(request, timeout=90) as response:\n    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))\n`;
    case "php": return `<?php\n$apiKey = getenv('GATEWAY_API_KEY');\nif (!$apiKey) { throw new RuntimeException('GATEWAY_API_KEY is required'); }\n$body = <<<'JSON'\n${body}\nJSON;\n$curl = curl_init(${JSON.stringify(url)});\ncurl_setopt_array($curl, [\n    CURLOPT_POST => true, CURLOPT_POSTFIELDS => $body, CURLOPT_RETURNTRANSFER => true,\n    CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'Accept: application/json', 'api-key: ' . $apiKey],\n    CURLOPT_FOLLOWLOCATION => false, CURLOPT_TIMEOUT => 90,\n]);\n$result = curl_exec($curl);\n$status = curl_getinfo($curl, CURLINFO_RESPONSE_CODE);\nif ($result === false || $status >= 300) { throw new RuntimeException('Gateway request failed: HTTP ' . $status); }\necho json_encode(json_decode($result, true, 512, JSON_THROW_ON_ERROR), JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE);\ncurl_close($curl);\n`;
    case "csharp": return `using System.Net.Http;\nusing System.Text;\nusing System.Text.Json;\n\nvar apiKey = Environment.GetEnvironmentVariable("GATEWAY_API_KEY")\n    ?? throw new InvalidOperationException("GATEWAY_API_KEY is required");\nusing var handler = new HttpClientHandler { AllowAutoRedirect = false };\nusing var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(90) };\nusing var request = new HttpRequestMessage(HttpMethod.Post, ${JSON.stringify(url)});\nrequest.Headers.Add("api-key", apiKey);\nrequest.Headers.Add("Accept", "application/json");\nrequest.Content = new StringContent("""\n${body}\n""", Encoding.UTF8, "application/json");\nusing var response = await client.SendAsync(request);\nresponse.EnsureSuccessStatusCode();\nusing var result = JsonDocument.Parse(await response.Content.ReadAsStringAsync());\nConsole.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));\n`;
    case "curl": return `: "\${GATEWAY_API_KEY:?GATEWAY_API_KEY is required}"\ncurl --silent --show-error --fail-with-body \\\n  --connect-timeout 10 --max-time 90 \\\n  ${quote(url)} \\\n  --header 'Content-Type: application/json' \\\n  --header 'Accept: application/json' \\\n  --header "api-key: $GATEWAY_API_KEY" \\\n  --data-binary @- <<'JSON'\n${body}\nJSON\n`;
  }
}
