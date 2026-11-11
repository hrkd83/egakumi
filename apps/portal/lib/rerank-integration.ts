import type { IntegrationLanguage } from "./model-integration.ts";
import { getTextGenerationEndpoint } from "./model-integration.ts";
import { rerankInputSchema, type RerankInput } from "./rerank-request.ts";
import type { RerankModel } from "./rerank-types.ts";

/** 内容を編集してモデルを比較できる、初期表示用の質問と候補文書です。 */
export const RERANK_SAMPLE = {
  query: "AIゲートウェイは何のために使いますか？",
  documents: [
    "AIゲートウェイは複数のAIサービスを共通の認証・アクセス制御で安全に利用するための窓口です。",
    "社内カフェテリアの営業時間は平日の午前8時から午後7時までです。",
    "APIの利用状況を観測し、購読ごとの利用制限を適用できます。",
  ],
};

/** モデルの許可範囲と文書数の両方に収まる最大の返却件数を使います。 */
export function defaultRerankInput(model: RerankModel): RerankInput {
  const documents = RERANK_SAMPLE.documents.slice(0, model.maxDocuments);
  return { modelId: model.id, query: RERANK_SAMPLE.query, documents, topN: Math.min(model.maxTopN, documents.length) };
}

/** 画面・コード例・実通信を同じ本文にし、未許可モデルや件数を転送しません。 */
export function buildRerankRequest(model: RerankModel, input: RerankInput): Record<string, unknown> {
  rerankInputSchema.strip().parse(input);
  if (input.modelId !== model.id) throw new TypeError("Invalid rerank model");
  if (input.documents.length > model.maxDocuments || input.topN < model.minTopN || input.topN > Math.min(model.maxTopN, input.documents.length)) throw new RangeError("Invalid rerank count");
  return { model: model.id, query: input.query, documents: [...input.documents], top_n: input.topN };
}

/** このAPIの固定公開パスだけを、コード例へ組み込みます。 */
export function getRerankEndpoint(model: RerankModel, gatewayOrigin: string): string {
  if (model.path !== "/models/cohere/v1/rerank") throw new TypeError("Invalid rerank API path");
  return getTextGenerationEndpoint(model, gatewayOrigin);
}

/** APIキー実値を含まない、組み込み先で設定するヘッダー例です。 */
export function getRerankHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", Accept: "application/json", "api-key": "${GATEWAY_API_KEY}" };
}

/** 現行Gatewayで扱う文字列文書の入力契約を示す部分スキーマです。 */
export function getRerankRequestSchema(model: RerankModel): Record<string, unknown> {
  return { $schema: "https://json-schema.org/draft/2020-12/schema", title: "Cohere rerank request (partial)", type: "object", required: ["model", "query", "documents", "top_n"], properties: {
    model: { type: "string", const: model.id }, query: { type: "string", minLength: 1, description: "Question used to rank the candidate documents." },
    documents: { type: "array", minItems: 1, maxItems: model.maxDocuments, items: { type: "string", minLength: 1 }, description: "Candidate texts in their original order." },
    top_n: { type: "integer", minimum: model.minTopN, maximum: model.maxTopN, description: "Number of results. Must not exceed the number of candidate documents." },
  }, additionalProperties: true };
}

/** indexは入力配列の0始まりの位置です。順位と混同しないよう契約に明記します。 */
export function getRerankResponseSchema(): Record<string, unknown> {
  return { $schema: "https://json-schema.org/draft/2020-12/schema", title: "Cohere rerank response (partial)", type: "object", required: ["results"], properties: {
    results: { type: "array", description: "Results ordered by descending relevance_score.", items: { type: "object", required: ["index", "relevance_score"], properties: { index: { type: "integer", minimum: 0, description: "Zero-based position in the original documents array." }, relevance_score: { type: "number", minimum: 0, maximum: 1 } }, additionalProperties: true } },
    meta: { type: "object", properties: { billed_units: { type: "object", properties: { search_units: { type: "integer", minimum: 0 } }, additionalProperties: true } }, additionalProperties: true },
  }, additionalProperties: true };
}

/** 標準HTTP機能を使い、同じ入力をAPIMへ送るサーバー側のコード例です。 */
export function getRerankIntegrationExample(language: IntegrationLanguage, model: RerankModel, input: RerankInput, origin: string): string {
  const url = getRerankEndpoint(model, origin), body = JSON.stringify(buildRerankRequest(model, input), null, 2);
  switch (language) {
    case "typescript": return `const apiKey = process.env.GATEWAY_API_KEY;\nif (!apiKey) throw new Error("GATEWAY_API_KEY is required");\nconst response = await fetch(${JSON.stringify(url)}, {\n  method: "POST",\n  headers: { "Content-Type": "application/json", Accept: "application/json", "api-key": apiKey },\n  body: JSON.stringify(${body}),\n  redirect: "error",\n});\nif (!response.ok) throw new Error(\`HTTP \${response.status}\`);\nconsole.log(await response.json());\n`;
    case "python": return `import json\nimport os\nimport urllib.request\n\nclass NoRedirect(urllib.request.HTTPRedirectHandler):\n    def redirect_request(self, req, fp, code, msg, headers, newurl):\n        return None\n\npayload = json.loads(r'''\n${body.replaceAll("'", "\\u0027")}\n''')\nrequest = urllib.request.Request(\n    ${JSON.stringify(url)}, data=json.dumps(payload).encode("utf-8"),\n    headers={"Content-Type": "application/json", "Accept": "application/json", "api-key": os.environ["GATEWAY_API_KEY"]}, method="POST",\n)\nwith urllib.request.build_opener(NoRedirect()).open(request) as response:\n    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))\n`;
    case "php": return `<?php\n$apiKey = getenv('GATEWAY_API_KEY');\nif (!$apiKey) { throw new RuntimeException('GATEWAY_API_KEY is required'); }\n$body = '${body.replaceAll("\\", "\\\\").replaceAll("'", "\\'")}';\n$curl = curl_init(${JSON.stringify(url)});\ncurl_setopt_array($curl, [\n    CURLOPT_POST => true, CURLOPT_POSTFIELDS => $body, CURLOPT_RETURNTRANSFER => true,\n    CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'Accept: application/json', 'api-key: ' . $apiKey],\n    CURLOPT_FOLLOWLOCATION => false, CURLOPT_TIMEOUT => 0,\n]);\n$result = curl_exec($curl);\n$status = curl_getinfo($curl, CURLINFO_RESPONSE_CODE);\nif ($result === false || $status < 200 || $status >= 300) { throw new RuntimeException('Gateway request failed: HTTP ' . $status); }\necho $result;\ncurl_close($curl);\n`;
    case "csharp": return `using System.Net.Http;\nusing System.Text;\n\nvar apiKey = Environment.GetEnvironmentVariable("GATEWAY_API_KEY")\n    ?? throw new InvalidOperationException("GATEWAY_API_KEY is required");\nusing var handler = new HttpClientHandler { AllowAutoRedirect = false };\nusing var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };\nusing var request = new HttpRequestMessage(HttpMethod.Post, ${JSON.stringify(url)});\nrequest.Headers.Add("api-key", apiKey);\nrequest.Headers.Add("Accept", "application/json");\nrequest.Content = new StringContent(${JSON.stringify(body)}, Encoding.UTF8, "application/json");\nusing var response = await client.SendAsync(request);\nresponse.EnsureSuccessStatusCode();\nConsole.WriteLine(await response.Content.ReadAsStringAsync());\n`;
    case "curl": return `: "\${GATEWAY_API_KEY:?GATEWAY_API_KEY is required}"\ncurl --silent --show-error --fail-with-body \\\n  --connect-timeout 10 \\\n  '${url}' \\\n  --header 'Content-Type: application/json' \\\n  --header 'Accept: application/json' \\\n  --header "api-key: $GATEWAY_API_KEY" \\\n  --data-binary '${body.replaceAll("'", "'\\''")}'\n`;
  }
}

