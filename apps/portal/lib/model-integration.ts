import type { TextGenerationModel } from "./model-types.ts";
import { getMinimumTextOutputTokens, MAX_TEXT_MESSAGE_CHARACTERS } from "./model-request.ts";

/** 編集可能な入力欄の初期例です。実行時は利用者が入力したmessageを使います。 */
export const SAMPLE_TEXT_PROMPT = "AIゲートウェイの役割を、日本語で3つの箇条書きで説明してください。";

/** 利用者が比較できる組み込み例の言語です。すべてサーバー側で秘密を取得します。 */
export const INTEGRATION_LANGUAGES = ["typescript", "python", "php", "csharp", "curl"] as const;

/** 組み込み例で選択できる言語の識別子です。 */
export type IntegrationLanguage = (typeof INTEGRATION_LANGUAGES)[number];

/** 画面の設定、実行テスト、組み込み例のリクエスト構造を一致させるための入力です。 */
export type TextGenerationOptions = { message: string; maxOutputTokens?: number | null; stream: boolean };

/** JSON Schema表示用の値です。実応答には、この部分スキーマにない追加フィールドも含まれます。 */
export type IntegrationJsonSchema = Record<string, unknown>;

/**
 * 選択したAPIプロファイルに適合する、利用者の入力を含むリクエスト本文を生成します。
 * @remarks サーバーと画面で同じ関数を使い、表示例と実際の送信内容のずれを防ぎます。
 */
export function buildTextGenerationRequest(model: TextGenerationModel, options: TextGenerationOptions): Record<string, unknown> {
  if (typeof options.message !== "string" || !options.message.trim() || options.message.length > MAX_TEXT_MESSAGE_CHARACTERS) throw new TypeError("A text message is required");
  if (model.maxOutputTokens === null ? options.maxOutputTokens != null : options.maxOutputTokens == null || !Number.isSafeInteger(options.maxOutputTokens) || options.maxOutputTokens < getMinimumTextOutputTokens(model) || options.maxOutputTokens > model.maxOutputTokens) {
    throw new RangeError("The output token limit is outside the model profile");
  }
  if (typeof options.stream !== "boolean") throw new TypeError("Stream must be a boolean");
  const body: Record<string, unknown> = {
    model: model.id,
    ...(model.operationId === "responses-create"
      ? { input: options.message, store: false }
      : { messages: [{ role: "user", content: options.message }] }),
    ...(options.maxOutputTokens == null ? {} : { [model.outputTokenParameter]: options.maxOutputTokens }),
    stream: options.stream,
  };
  return body;
}

/** 公開GatewayのルートとAPIパスだけから接続先を作り、秘密や任意クエリを例へ混入させません。 */
export function getTextGenerationEndpoint(model: Pick<TextGenerationModel, "path">, gatewayOrigin: string): string {
  const origin = new URL(gatewayOrigin);
  const isLocal = ["localhost", "127.0.0.1", "[::1]"].includes(origin.hostname);
  if ((origin.protocol !== "https:" && !(isLocal && origin.protocol === "http:")) || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) {
    throw new TypeError("A public Gateway origin is required");
  }
  if (!model.path.startsWith("/models/") || model.path.includes("?") || model.path.includes("#") || /[\r\n\\]/.test(model.path)) {
    throw new TypeError("A model API path is required");
  }
  const endpoint = new URL(model.path, origin.origin);
  if (endpoint.origin !== origin.origin || endpoint.pathname !== model.path) throw new TypeError("An unchanged model API path is required");
  return endpoint.href;
}

/** APIキーは例の中でも環境変数として表現し、実値を受け取る引数を設けません。 */
export function getTextGenerationHeaders(model: TextGenerationModel, stream: boolean): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Accept: stream ? "text/event-stream" : "application/json",
    "api-key": "${GATEWAY_API_KEY}",
    "X-Egakumi-Profile": model.profileId,
  };
}

/** モデルの公開制約と、今回の組み込み例が使用する入力フィールドをJSON Schemaで示します。 */
export function getTextGenerationRequestSchema(model: TextGenerationModel): IntegrationJsonSchema {
  const content = model.operationId === "responses-create"
    ? { input: { type: "string", minLength: 1, maxLength: MAX_TEXT_MESSAGE_CHARACTERS }, store: { type: "boolean", const: false } }
    : { messages: { type: "array", minItems: 1, items: {
      type: "object", required: ["role", "content"], properties: {
        role: { type: "string", enum: ["user", "assistant", "system", "developer"] }, content: { type: "string", minLength: 1, maxLength: MAX_TEXT_MESSAGE_CHARACTERS },
      }, additionalProperties: true,
    } } };
  return {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: model.operationId === "responses-create" ? "Stateless Responses — text request" : "Chat Completions — text request",
    description: "Partial schema of the text request used by this panel. It does not describe every field or capability of the API.",
    type: "object",
    required: ["model", model.operationId === "responses-create" ? "input" : "messages", ...(model.maxOutputTokens === null ? [] : [model.outputTokenParameter]), "stream", ...(model.operationId === "responses-create" ? ["store"] : [])],
    properties: {
      model: { type: "string", const: model.id }, ...content,
      ...(model.maxOutputTokens === null ? {} : { [model.outputTokenParameter]: { type: "integer", minimum: getMinimumTextOutputTokens(model), maximum: model.maxOutputTokens } }),
      stream: { type: "boolean", description: "Returns SSE when true and JSON when false." },
    },
    additionalProperties: true,
  };
}

/** 完了したJSON応答のうち、本文と使用量を読むための安定した部分を示します。 */
function jsonResponseSchema(model: TextGenerationModel): IntegrationJsonSchema {
  const tokenCount = { type: "integer", minimum: 0 };
  if (model.operationId === "responses-create") return {
    title: "Responses — JSON response (partial)", type: "object",
    properties: {
      id: { type: "string" }, object: { const: "response" }, model: { type: "string" }, status: { type: "string" },
      output: { type: "array", items: { type: "object", properties: {
        type: { type: "string" }, role: { type: "string" },
        content: { type: "array", items: { type: "object", properties: { type: { type: "string" }, text: { type: "string" } }, additionalProperties: true } },
      }, additionalProperties: true } },
      usage: { type: ["object", "null"], properties: { input_tokens: tokenCount, output_tokens: tokenCount, total_tokens: tokenCount }, additionalProperties: true },
    }, additionalProperties: true,
  };
  return {
    title: "Chat Completions — JSON response (partial)", type: "object",
    properties: {
      id: { type: "string" }, object: { const: "chat.completion" }, model: { type: "string" },
      choices: { type: "array", items: { type: "object", properties: {
        index: tokenCount, finish_reason: { type: ["string", "null"] },
        message: { type: "object", properties: { role: { type: "string" }, content: { type: ["string", "null"] } }, additionalProperties: true },
      }, additionalProperties: true } },
      usage: { type: ["object", "null"], properties: { prompt_tokens: tokenCount, completion_tokens: tokenCount, total_tokens: tokenCount }, additionalProperties: true },
    }, additionalProperties: true,
  };
}

/**
 * 非ストリームでは応答JSON、ストリームではSSEのdata行に現れる主要なJSONの形を返します。
 * @remarks SSE自体はJSON配列ではありません。イベントを受け取るたびにdataを解釈し、終端を確認します。
 */
export function getTextGenerationResponseSchema(model: TextGenerationModel, stream: boolean): IntegrationJsonSchema {
  const common = {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    description: "Partial schema of the main success response fields. Run the example to inspect additional model-specific fields.",
  };
  if (!stream) return { ...common, ...jsonResponseSchema(model) };
  if (model.operationId === "responses-create") return {
    ...common, title: "Responses — SSE data (selected events)",
    description: "Content-Type: text/event-stream. Selected JSON payloads from data lines are shown here. The stream also includes lifecycle events and terminal events such as response.completed.",
    oneOf: [
      { type: "object", required: ["type", "delta"], properties: { type: { const: "response.output_text.delta" }, delta: { type: "string" }, sequence_number: { type: "integer" }, output_index: { type: "integer" }, content_index: { type: "integer" } }, additionalProperties: true },
      { type: "object", required: ["type", "response"], properties: { type: { enum: ["response.completed", "response.failed", "response.incomplete"] }, response: jsonResponseSchema(model) }, additionalProperties: true },
    ],
  };
  return {
    ...common, title: "Chat Completions — SSE data (partial)",
    description: "Content-Type: text/event-stream. Each data line contains a completion chunk or an Azure prompt-filter annotation. The terminal data: [DONE] is not JSON. Usage may be absent depending on the model and request.",
    anyOf: [
      {
        title: "Completion chunk", type: "object", required: ["choices"], properties: {
          id: { type: "string" }, object: { const: "chat.completion.chunk" }, model: { type: "string" },
          choices: { type: "array", items: { type: "object", properties: {
            index: { type: "integer" }, finish_reason: { type: ["string", "null"] },
            delta: { type: "object", properties: { role: { type: "string" }, content: { type: "string" } }, additionalProperties: true },
          }, additionalProperties: true } },
          usage: { type: ["object", "null"], additionalProperties: true },
        }, additionalProperties: true,
      },
      {
        title: "Azure prompt-filter annotation", type: "object", required: ["prompt_filter_results"], properties: {
          id: { type: "string" }, object: { type: "string" }, model: { type: "string" }, created: { type: "integer" },
          choices: { type: "array", maxItems: 0 },
          prompt_filter_results: { type: "array", minItems: 1, items: {
            type: "object", required: ["prompt_index", "content_filter_results"], properties: {
              prompt_index: { type: "integer", minimum: 0 },
              content_filter_results: { type: "object", additionalProperties: true },
            }, additionalProperties: true,
          } },
        }, additionalProperties: true,
      },
    ],
  };
}

/** POSIXシェルの単一引数に、安全に固定値を埋め込みます。 */
function shellQuote(value: string): string { return `'${value.replaceAll("'", "'\\''")}'`; }

/** PHPの単一引用符リテラルで、固定のURLやヘッダーを表現します。 */
function phpQuote(value: string): string { return `'${value.replaceAll("\\", "\\\\").replaceAll("'", "\\'")}'`; }

/** TypeScriptのサーバー実装例です。SSEはチャンク単位のまま表示し、イベント境界を仮定しません。 */
function typescriptExample(url: string, profile: string, body: string, stream: boolean): string {
  return `import process from "node:process";

// Node.jsのサーバー側で実行します。キーをブラウザーへ渡さないでください。
const apiKey = process.env.GATEWAY_API_KEY;
if (!apiKey) throw new Error("GATEWAY_API_KEY is required");

const response = await fetch(${JSON.stringify(url)}, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Accept: "${stream ? "text/event-stream" : "application/json"}",
    "api-key": apiKey,
    "X-Egakumi-Profile": ${JSON.stringify(profile)},
  },
  body: JSON.stringify(${body}),
  redirect: "error",
  signal: AbortSignal.timeout(600_000),
});
if (!response.ok) throw new Error(\`HTTP \${response.status}\`);
${stream ? `if (!response.body) throw new Error("Response body is missing");
const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
try {
  // SSEの生データを確認します。実装時は空行でイベントを区切り、data行を解釈します。
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    process.stdout.write(value);
  }
} finally {
  reader.releaseLock();
}` : `const result = await response.json();
console.log(JSON.stringify(result, null, 2));`}
`;
}

/** Python標準ライブラリだけで実行できる、サーバー側の組み込み例です。 */
function pythonExample(url: string, profile: string, body: string, stream: boolean): string {
  const pythonJson = body.replaceAll("'", "\\u0027");
  return `import json
import os
${stream ? "import sys\n" : ""}import urllib.request

api_key = os.environ["GATEWAY_API_KEY"]
payload = json.loads(r'''
${pythonJson}
''')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

opener = urllib.request.build_opener(NoRedirect())
request = urllib.request.Request(
    ${JSON.stringify(url)},
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Accept": "${stream ? "text/event-stream" : "application/json"}",
        "api-key": api_key,
        "X-Egakumi-Profile": ${JSON.stringify(profile)},
    },
    method="POST",
)
with opener.open(request, timeout=600) as response:
${stream ? `    # SSEの生データを確認します。空行がイベントの区切りです。
    for line in response:
        sys.stdout.write(line.decode("utf-8"))
        sys.stdout.flush()` : `    result = json.load(response)
    print(json.dumps(result, ensure_ascii=False, indent=2))`}
`;
}

/** PHPのサーバー側で環境変数とcURLを使う組み込み例です。 */
function phpExample(url: string, profile: string, body: string, stream: boolean): string {
  return `<?php
$apiKey = getenv('GATEWAY_API_KEY');
if (!$apiKey) { throw new RuntimeException('GATEWAY_API_KEY is required'); }
$body = <<<'JSON'
${body}
JSON;
$curl = curl_init(${phpQuote(url)});
curl_setopt_array($curl, [
    CURLOPT_POST => true,
    CURLOPT_HTTPHEADER => [
        'Content-Type: application/json',
        'Accept: ${stream ? "text/event-stream" : "application/json"}',
        'api-key: ' . $apiKey,
        ${phpQuote(`X-Egakumi-Profile: ${profile}`)},
    ],
    CURLOPT_POSTFIELDS => $body,
    CURLOPT_FOLLOWLOCATION => false,
    CURLOPT_TIMEOUT => 600,
${stream ? `    // SSEの生データを確認します。チャンク境界とイベント境界は一致しません。
    CURLOPT_WRITEFUNCTION => static function ($handle, string $chunk): int {
        echo $chunk;
        flush();
        return strlen($chunk);
    },` : `    CURLOPT_RETURNTRANSFER => true,`}
]);
$result = curl_exec($curl);
$status = curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
if ($result === false || $status >= 400) { throw new RuntimeException('Gateway request failed: HTTP ' . $status); }
${stream ? "" : "echo json_encode(json_decode($result, true, 512, JSON_THROW_ON_ERROR), JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE);\n"}curl_close($curl);
`;
}

/** C#のサーバー側でHttpClientを使う組み込み例です。 */
function csharpExample(url: string, profile: string, body: string, stream: boolean): string {
  return `using System.Net.Http;
using System.Text;
using System.Threading;
${stream ? "" : "using System.Text.Json;\n"}
var apiKey = Environment.GetEnvironmentVariable("GATEWAY_API_KEY")
    ?? throw new InvalidOperationException("GATEWAY_API_KEY is required");
using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(600));
using var handler = new HttpClientHandler { AllowAutoRedirect = false };
using var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(600) };
using var request = new HttpRequestMessage(HttpMethod.Post, ${JSON.stringify(url)});
request.Headers.Add("api-key", apiKey);
request.Headers.Add("X-Egakumi-Profile", ${JSON.stringify(profile)});
request.Headers.Add("Accept", "${stream ? "text/event-stream" : "application/json"}");
request.Content = new StringContent("""
${body}
""", Encoding.UTF8, "application/json");
using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellation.Token);
response.EnsureSuccessStatusCode();
${stream ? `using var reader = new StreamReader(await response.Content.ReadAsStreamAsync(cancellation.Token));
// SSEの生データを確認します。空行を含めて読み、イベントを区切ります。
while (await reader.ReadLineAsync(cancellation.Token) is { } line)
{
    Console.WriteLine(line);
}` : `using var result = JsonDocument.Parse(await response.Content.ReadAsStringAsync(cancellation.Token));
Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));`}
`;
}

/** コマンドラインで同じ本文を送信する例です。キーは履歴に直書きしません。 */
function curlExample(url: string, profile: string, body: string, stream: boolean): string {
  return `: "\${GATEWAY_API_KEY:?GATEWAY_API_KEY is required}"
curl --silent --show-error --fail-with-body${stream ? " --no-buffer" : ""} \\
  --connect-timeout 10 --max-time 600 \\
  ${shellQuote(url)} \\
  --header 'Content-Type: application/json' \\
  --header 'Accept: ${stream ? "text/event-stream" : "application/json"}' \\
  --header "api-key: $GATEWAY_API_KEY" \\
  --header ${shellQuote(`X-Egakumi-Profile: ${profile}`)} \\
  --data-binary @- <<'JSON'
${body}
JSON
`;
}

/**
 * 選択言語に合わせて、公開APIMに直接送るサーバー側の組み込み例を生成します。
 * @remarks stream=trueの例は受信したSSEをそのまま表示します。プロバイダーごとのJSONを隠すSDKは使いません。
 */
export function getIntegrationExample(language: IntegrationLanguage, model: TextGenerationModel, options: TextGenerationOptions, gatewayOrigin: string): string {
  const url = getTextGenerationEndpoint(model, gatewayOrigin);
  const body = JSON.stringify(buildTextGenerationRequest(model, options), null, 2);
  switch (language) {
    case "typescript": return typescriptExample(url, model.profileId, body, options.stream);
    case "python": return pythonExample(url, model.profileId, body, options.stream);
    case "php": return phpExample(url, model.profileId, body, options.stream);
    case "csharp": return csharpExample(url, model.profileId, body, options.stream);
    case "curl": return curlExample(url, model.profileId, body, options.stream);
  }
}
