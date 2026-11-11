import type { IntegrationLanguage } from "./model-integration.ts";
import { PII_API, buildPiiRequest, type PiiInput } from "./pii-contract.ts";

/** 固定の公開操作だけをコード例に使用し、任意の送信先を受け取りません。 */
export function getPiiEndpoint(origin: string): string {
  const url = new URL(origin);
  if (url.protocol !== "https:" || url.origin !== origin || url.username || url.password) throw new TypeError("Invalid gateway origin");
  return new URL(PII_API.path, url).href;
}

/** コード例の秘密値は環境変数として表記し、実キーは含めません。 */
export function getPiiHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", Accept: "application/json", "api-key": "${GATEWAY_API_KEY}" };
}

/** PII入力の公開契約です。文字要素の上限はサービス側で正確に検査します。 */
export function getPiiRequestSchema(): Record<string, unknown> {
  return { type: "object", additionalProperties: false, required: ["text", "language"], properties: {
    text: { type: "string", minLength: 1, description: "Single nonblank text, at most 5,120 Unicode 8.0 text elements, validated by Language. The provider request, including its Language API wrapper, must fit 1,000,000 UTF-8 bytes. JSON escaping and syntax whitespace do not change the compact budget. The text value is not Unicode-normalized, trimmed or truncated. No partial result is returned." },
    language: { type: "string", description: "Language of the input text. Automatic detection is not enabled." },
  } };
}

/** 検出した実値を含まないPII応答の公開契約です。 */
export function getPiiResponseSchema(): Record<string, unknown> {
  return { type: "object", additionalProperties: false, required: ["redactedText", "language", "detectedCount", "modelVersion", "warningCodes", "requestId"], properties: {
    redactedText: { type: "string", description: "Text with detected PII masked. Review it for your use case; detection is not guaranteed to find all personal information." },
    language: { type: "string", description: "The language requested by the caller." },
    detectedCount: { type: "integer", minimum: 0, description: "Number of detected entities. The original entity values are not returned." },
    modelVersion: { type: "string", const: PII_API.modelVersion, description: "The deployed PII model version." },
    warningCodes: { type: "array", uniqueItems: true, items: { type: "string" }, description: "Allowlisted warning codes only. Currently this array is empty." },
    requestId: { type: "string", format: "uuid", description: "Request identifier for troubleshooting without sharing the original text." },
  } };
}

/** 各言語の標準HTTPクライアントで同一の入力を送る、バックエンド向けの組み込み例です。 */
export function getPiiIntegrationExample(language: IntegrationLanguage, input: PiiInput, origin: string): string {
  const url = getPiiEndpoint(origin), body = JSON.stringify(buildPiiRequest(input), null, 2);
  switch (language) {
    case "typescript": return `const apiKey = process.env.GATEWAY_API_KEY;\nif (!apiKey) throw new Error("GATEWAY_API_KEY is required");\nconst response = await fetch(${JSON.stringify(url)}, {\n  method: "POST",\n  headers: { "Content-Type": "application/json", Accept: "application/json", "api-key": apiKey },\n  body: JSON.stringify(${body}),\n  redirect: "error",\n});\nif (!response.ok) throw new Error(\`HTTP \${response.status}\`);\nconst result = await response.json();\nconsole.log(result.redactedText);\n`;
    case "python": return `import json\nimport os\nimport urllib.request\n\nclass NoRedirect(urllib.request.HTTPRedirectHandler):\n    def redirect_request(self, req, fp, code, msg, headers, newurl):\n        return None\n\npayload = json.loads(r'''\n${body.replaceAll("'", "\\u0027")}\n''')\nrequest = urllib.request.Request(\n    ${JSON.stringify(url)}, data=json.dumps(payload).encode("utf-8"),\n    headers={"Content-Type": "application/json", "Accept": "application/json", "api-key": os.environ["GATEWAY_API_KEY"]}, method="POST",\n)\nwith urllib.request.build_opener(NoRedirect()).open(request) as response:\n    result = json.load(response)\n    print(result["redactedText"])\n`;
    case "php": return `<?php\n$apiKey = getenv('GATEWAY_API_KEY');\nif (!$apiKey) { throw new RuntimeException('GATEWAY_API_KEY is required'); }\n$curl = curl_init(${JSON.stringify(url)});\ncurl_setopt_array($curl, [\n    CURLOPT_POST => true, CURLOPT_POSTFIELDS => '${body.replaceAll("\\", "\\\\").replaceAll("'", "\\'")}',\n    CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => false, CURLOPT_TIMEOUT => 0,\n    CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'Accept: application/json', 'api-key: ' . $apiKey],\n]);\n$result = curl_exec($curl);\n$status = curl_getinfo($curl, CURLINFO_RESPONSE_CODE);\nif ($result === false || $status < 200 || $status >= 300) { throw new RuntimeException('Gateway request failed: HTTP ' . $status); }\necho json_decode($result, true, 512, JSON_THROW_ON_ERROR)['redactedText'];\ncurl_close($curl);\n`;
    case "csharp": return `using System.Net.Http;\nusing System.Text;\nusing System.Text.Json;\n\nvar apiKey = Environment.GetEnvironmentVariable("GATEWAY_API_KEY")\n    ?? throw new InvalidOperationException("GATEWAY_API_KEY is required");\nusing var handler = new HttpClientHandler { AllowAutoRedirect = false };\nusing var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };\nusing var request = new HttpRequestMessage(HttpMethod.Post, ${JSON.stringify(url)});\nrequest.Headers.Add("api-key", apiKey);\nrequest.Headers.Add("Accept", "application/json");\nrequest.Content = new StringContent(${JSON.stringify(body)}, Encoding.UTF8, "application/json");\nusing var response = await client.SendAsync(request);\nresponse.EnsureSuccessStatusCode();\nusing var result = JsonDocument.Parse(await response.Content.ReadAsStringAsync());\nConsole.WriteLine(result.RootElement.GetProperty("redactedText").GetString());\n`;
    case "curl": return `: "\${GATEWAY_API_KEY:?GATEWAY_API_KEY is required}"\ncurl --silent --show-error --fail-with-body \\\n  --connect-timeout 10 \\\n  '${url}' \\\n  --header 'Content-Type: application/json' \\\n  --header 'Accept: application/json' \\\n  --header "api-key: $GATEWAY_API_KEY" \\\n  --data-binary '${body.replaceAll("'", "'\\''")}'\n`;
  }
}
