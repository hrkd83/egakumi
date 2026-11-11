import { MAX_BUFFERED_BODY_BYTES } from "./model-runtime-limits.ts";
import { decisionInputSchema, decisionResultSchema } from "./decision-request.ts";
import { readEmbeddingBody } from "./embedding-response.ts";
import { ModelResponseError } from "./model-response.ts";
import type { IntegrationLanguage } from "./model-integration.ts";
import type { DecisionInput, DecisionModel, DecisionResult } from "./decision-types.ts";
import type { ModelGatewayErrorCode } from "./model-request.ts";
import type { Locale } from "./i18n/config.ts";

/** 状況と3種類の質問を同時に試せる、自由に編集可能な初期例です。 */
export function defaultDecisionInput(model: DecisionModel, locale: Locale = "ja"): DecisionInput {
  if (locale === "en") return { modelId: model.id,
    state: { message: "The service charged me twice. Please refund the duplicate payment.", contact_count: 2 },
    questions: {
      refund: { type: "noul", instructions: "Is the customer requesting a refund?" },
      team: { type: "choice", instructions: "Which team should handle this request?", criteria: { billing: "Payments, duplicate charges, and refunds", engineering: "Software errors and outages", support: "General help" } },
      severity: { type: "score", instructions: "How urgent is this issue?", criteria: ["Routine", "Important", "Urgent"] },
    },
  };
  return {
    modelId: model.id,
    state: { message: "同じサービスの料金が二重に引き落とされました。重複した分を返金してください。", contact_count: 2 },
    questions: {
      refund: { type: "noul", instructions: "利用者は返金を求めていますか？" },
      team: { type: "choice", instructions: "どの担当へ振り分けますか？", criteria: { billing: "料金、重複請求、返金", engineering: "システム障害、不具合", support: "一般的な使い方の問い合わせ" } },
      severity: { type: "score", instructions: "対応の緊急度を評価してください。", criteria: ["通常", "重要", "緊急"] },
    },
  };
}

/** 正本の配備名と公開制約を確認し、編集した状況と質問から上流JSONを作ります。 */
export function buildDecisionRequest(model: DecisionModel, input: DecisionInput): Record<string, unknown> {
  const parsed = decisionInputSchema.parse(input);
  if (parsed.modelId !== model.id) throw new Error("Invalid decision model");
  const body = { model: model.id, state: parsed.state, questions: parsed.questions };
  if (new TextEncoder().encode(JSON.stringify(body)).byteLength > model.maxRequestBytes) throw new RangeError("Decision request exceeds limit");
  return body;
}

/** 公開された意思決定の固定パスだけを、表示用のGateway URLへ結び付けます。 */
export function getDecisionEndpoint(model: DecisionModel, gatewayOrigin: string): string {
  const origin = new URL(gatewayOrigin);
  if (origin.protocol !== "https:" || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash || model.path !== "/models/microsoft-decision/v1/systemone") throw new Error("Invalid decision endpoint");
  return new URL(model.path, origin).href;
}

/** コード例は環境変数の参照だけを持ち、実際の購読キーを受け取りません。 */
export function getDecisionHeaders(): Record<string, string> { return { "Content-Type": "application/json", Accept: "application/json", "api-key": "${GATEWAY_API_KEY}" }; }

/** 3種類の質問と、選択肢・段階評価を表す部分スキーマです。 */
export function getDecisionRequestSchema(model: DecisionModel, locale: Locale = "ja"): Record<string, unknown> {
  const ja = locale === "ja";
  const instruction = { instructions: { type: "string", minLength: 1, description: ja ? "判断する内容を自然言語で指定します。" : "Describe the decision to make in natural language." } };
  return { $schema: "https://json-schema.org/draft/2020-12/schema", title: "Microsoft Decision request", type: "object", required: ["model", "state", "questions"], properties: {
    model: { type: "string", const: model.id, description: ja ? "基礎モデル名ではなく、Gatewayで許可された配備名です。" : "The deployment name allowed by the Gateway, rather than the underlying model name." },
    state: { type: "object", description: ja ? "判断対象の状況や事実をJSONオブジェクトで渡します。" : "The situation and facts to evaluate, represented as a JSON object.", additionalProperties: true },
    questions: { type: "object", minProperties: 1, description: ja ? "質問名と型付き質問の対応表です。応答でも同じ質問名を使います。" : "Named typed questions. Answers use the same question names.", additionalProperties: { oneOf: [
      { type: "object", required: ["type", "instructions"], properties: { type: { const: "noul" }, ...instruction }, additionalProperties: false },
      { type: "object", required: ["type", "instructions", "criteria"], properties: { type: { const: "choice" }, ...instruction, criteria: { type: "object", minProperties: 2, additionalProperties: { type: "string", minLength: 1 } } }, additionalProperties: false },
      { type: "object", required: ["type", "instructions", "criteria"], properties: { type: { const: "score" }, ...instruction, criteria: { type: "array", minItems: 2, items: { type: "string", minLength: 1 } } }, additionalProperties: false },
    ] } },
  }, additionalProperties: false };
}

/** 回答型ごとの意味と確率の構造を説明する部分スキーマです。 */
export function getDecisionResponseSchema(locale: Locale = "ja"): Record<string, unknown> {
  const ja = locale === "ja";
  const probability = { type: "number", minimum: 0, maximum: 1 };
  return { $schema: "https://json-schema.org/draft/2020-12/schema", title: "Microsoft Decision response (partial)", type: "object", required: ["model", "answers"], properties: {
    model: { type: "string", description: ja ? "応答の基礎モデル名です。配備名decision-1とは異なります。" : "The underlying model name, which differs from the decision-1 deployment name." },
    answers: { type: "object", description: ja ? "質問名をキーに、noulは肯定の確率、choiceは選択肢、scoreは確率加重の段階評価を返します。" : "Named answers: noul returns the probability of yes, choice returns a candidate, and score returns the weighted average of levels.", additionalProperties: { oneOf: [
      { type: "object", required: ["type", "noul"], properties: { type: { const: "noul" }, noul: probability } },
      { type: "object", required: ["type", "choice", "probabilities"], properties: { type: { const: "choice" }, choice: { type: "string" }, probabilities: { type: "object", additionalProperties: probability }, confidence: probability } },
      { type: "object", required: ["type", "score", "probabilities"], properties: { type: { const: "score" }, score: { type: "number", minimum: 0 }, probabilities: { type: "object", additionalProperties: probability }, confidence: probability } },
    ] } },
    usage: { type: "object", properties: { input_tokens: { type: "integer", minimum: 0 }, output_tokens: { type: "integer", minimum: 0 }, total_tokens: { type: "integer", minimum: 0 } }, additionalProperties: true },
  }, additionalProperties: true };
}

/** 選択した言語で、同じ公開APIと入力を使うサーバー側の組み込み例を示します。 */
export function getDecisionIntegrationExample(language: IntegrationLanguage, model: DecisionModel, input: DecisionInput, origin: string): string {
  const url = JSON.stringify(getDecisionEndpoint(model, origin));
  const body = JSON.stringify(buildDecisionRequest(model, input), null, 2);
  if (language === "typescript") return `const apiKey = process.env.GATEWAY_API_KEY;\nif (!apiKey) throw new Error("GATEWAY_API_KEY is required");\n\nconst response = await fetch(${url}, {\n  method: "POST",\n  headers: { "Content-Type": "application/json", Accept: "application/json", "api-key": apiKey },\n  body: JSON.stringify(JSON.parse(${JSON.stringify(body)})),\n  redirect: "error",\n});\nif (!response.ok) throw new Error(\`HTTP \${response.status}\`);\nconsole.log(await response.json());\n`;
  if (language === "python") return `import json\nimport os\nimport urllib.request\n\nclass NoRedirect(urllib.request.HTTPRedirectHandler):\n    def redirect_request(self, req, fp, code, msg, headers, newurl):\n        return None\n\npayload = json.loads(${JSON.stringify(body)})\nrequest = urllib.request.Request(\n    ${url}, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),\n    headers={"Content-Type": "application/json", "Accept": "application/json", "api-key": os.environ["GATEWAY_API_KEY"]},\n    method="POST",\n)\nwith urllib.request.build_opener(NoRedirect()).open(request) as response:\n    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))\n`;
  if (language === "php") return `<?php\n$apiKey = getenv('GATEWAY_API_KEY');\nif (!$apiKey) { throw new RuntimeException('GATEWAY_API_KEY is required'); }\n$body = ${JSON.stringify(body).replaceAll("$", "\\$")};\n$curl = curl_init(${url});\ncurl_setopt_array($curl, [\n    CURLOPT_POST => true, CURLOPT_POSTFIELDS => $body, CURLOPT_RETURNTRANSFER => true,\n    CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'Accept: application/json', 'api-key: ' . $apiKey],\n    CURLOPT_FOLLOWLOCATION => false, CURLOPT_TIMEOUT => 0,\n]);\n$result = curl_exec($curl);\n$status = curl_getinfo($curl, CURLINFO_RESPONSE_CODE);\nif ($result === false || $status >= 300) { throw new RuntimeException('Gateway request failed: HTTP ' . $status); }\necho $result;\ncurl_close($curl);\n`;
  if (language === "csharp") return `using System.Net.Http;\nusing System.Text;\n\nvar apiKey = Environment.GetEnvironmentVariable("GATEWAY_API_KEY")\n    ?? throw new InvalidOperationException("GATEWAY_API_KEY is required");\nusing var client = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = Timeout.InfiniteTimeSpan };\nusing var request = new HttpRequestMessage(HttpMethod.Post, ${url});\nrequest.Headers.Add("api-key", apiKey);\nrequest.Headers.Add("Accept", "application/json");\nrequest.Content = new StringContent(${JSON.stringify(body)}, Encoding.UTF8, "application/json");\nusing var response = await client.SendAsync(request);\nresponse.EnsureSuccessStatusCode();\nConsole.WriteLine(await response.Content.ReadAsStringAsync());\n`;
  const quoted = getDecisionEndpoint(model, origin).replaceAll("'", "'\\''");
  // JSONを単一引用符で囲むため、入力中の$やバッククォートはシェルで展開されません。
  return `: "\${GATEWAY_API_KEY:?GATEWAY_API_KEY is required}"\ncurl --silent --show-error --fail-with-body \\\n  --connect-timeout 10 \\\n  '${quoted}' \\\n  --header 'Content-Type: application/json' \\\n  --header 'Accept: application/json' \\\n  --header "api-key: $GATEWAY_API_KEY" \\\n  --data-binary '${body.replaceAll("'", "'\\''")}'\n`;
}

/** BFFの限定された失敗コードだけを、利用者向けの固定エラーへ変換します。 */
export async function readDecisionResponse(response: Response, signal?: AbortSignal): Promise<DecisionResult> {
  try {
    if (response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") throw new Error("Invalid content type");
    const value = JSON.parse(await readEmbeddingBody(response.body, MAX_BUFFERED_BODY_BYTES, signal));
    if (!response.ok) {
      const allowed: ModelGatewayErrorCode[] = ["InvalidRequest", "AuthenticationRequired", "RequestNotAllowed", "ModelUnavailable", "GatewayNotConfigured", "GatewayUnauthorized", "RateLimitExceeded", "GatewayTimeout", "GenerationFailed", "AuthenticationUnavailable", "RequestCancelled", "ApiKeyUnavailable", "ApiKeyAccessUnavailable"];
      const code: ModelGatewayErrorCode = allowed.includes(value?.error?.code) ? value.error.code : "GenerationFailed";
      throw new ModelResponseError({ error: { code }, ...(typeof value?.requestId === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(value.requestId) ? { requestId: value.requestId } : {}), ...(Number.isInteger(value?.retryAfterSeconds) && value.retryAfterSeconds > 0 && value.retryAfterSeconds <= 3600 ? { retryAfterSeconds: value.retryAfterSeconds } : {}) });
    }
    return decisionResultSchema.parse(value);
  } catch (error) { if (error instanceof ModelResponseError) throw error; throw new ModelResponseError({ error: { code: "GenerationFailed" } }); }
}
