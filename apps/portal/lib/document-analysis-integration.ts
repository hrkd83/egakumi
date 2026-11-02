import { DOCUMENT_MEDIA_TYPES, MAX_DOCUMENT_BYTES, getDocumentAnalysisEndpoint, type DocumentAnalysisModel } from "./document-analysis-contracts.ts";
import type { IntegrationLanguage } from "./model-integration.ts";
import type { Locale } from "./i18n/config.ts";

/** 公開APIはファイル本文を受け取り、Portal内のmultipartとは分けて案内します。 */
export function getDocumentAnalysisExample(language: IntegrationLanguage, origin: string, model: DocumentAnalysisModel): string {
  const url = getDocumentAnalysisEndpoint(origin, model);
  if (language === "curl") return `# Local file -> Gateway. The API key remains on your backend.
curl --fail-with-body --max-redirs 0 --max-time 100 '${url}' \\
  --header "api-key: \${GATEWAY_API_KEY}" \\
  --header 'Content-Type: application/pdf' \\
  --data-binary '@input.pdf'

# HTTP 200: { "analyzeResult": { ... }, "requestId": "..." }
# The Gateway waits for analysis; do not poll Azure result URLs.`;
  if (language === "typescript") return `import { readFile } from "node:fs/promises";

const document = await readFile("input.pdf");
const response = await fetch("${url}", {
  method: "POST",
  redirect: "error",
  headers: { "api-key": process.env.GATEWAY_API_KEY!, "Content-Type": "application/pdf" },
  body: document,
  signal: AbortSignal.timeout(100_000),
});
if (!response.ok) throw new Error(\`Gateway returned \${response.status}\`);
const { analyzeResult } = await response.json();
console.log(analyzeResult.content, analyzeResult.pages);
// Use your backend. Never expose the subscription key to browser code.`;
  if (language === "python") return `import os
from pathlib import Path
import requests

response = requests.post(
    "${url}",
    headers={"api-key": os.environ["GATEWAY_API_KEY"], "Content-Type": "application/pdf"},
    data=Path("input.pdf").read_bytes(),
    allow_redirects=False,
    timeout=(10, 100),
)
response.raise_for_status()
result = response.json()["analyzeResult"]
print(result["content"], result["pages"])
# Keep the key and document on your backend. Do not retry automatically.`;
  if (language === "php") return `<?php
$curl = curl_init('${url}');
curl_setopt_array($curl, [
    CURLOPT_POST => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_FOLLOWLOCATION => false,
    CURLOPT_TIMEOUT => 100,
    CURLOPT_HTTPHEADER => ['api-key: ' . getenv('GATEWAY_API_KEY'), 'Content-Type: application/pdf'],
    CURLOPT_POSTFIELDS => file_get_contents('input.pdf'),
]);
$body = curl_exec($curl);
$status = curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
if ($body === false || $status !== 200) throw new RuntimeException('Document analysis failed');
$result = json_decode($body, true, 512, JSON_THROW_ON_ERROR)['analyzeResult'];
echo $result['content'];
curl_close($curl);`;
  return `using System.Net.Http;

using var handler = new HttpClientHandler { AllowAutoRedirect = false };
using var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(100) };
using var request = new HttpRequestMessage(HttpMethod.Post, "${url}");
request.Headers.Add("api-key", Environment.GetEnvironmentVariable("GATEWAY_API_KEY"));
request.Content = new ByteArrayContent(await File.ReadAllBytesAsync("input.pdf"));
request.Content.Headers.ContentType = new("application/pdf");
using var response = await client.SendAsync(request);
response.EnsureSuccessStatusCode();
Console.WriteLine(await response.Content.ReadAsStringAsync());
// The Gateway handles asynchronous Azure analysis internally.`;
}

/** ファイル形式と固定モデルを含む、Gateway向けのHTTP要求仕様です。 */
export function getDocumentAnalysisRequestSchema(locale: Locale) {
  return { type: "object", required: ["method", "model", "contentType", "file"], properties: {
    method: { type: "string", const: "POST" },
    model: { type: "string", enum: ["prebuilt-read", "prebuilt-layout"], description: locale === "ja" ? "URLクエリーで指定する固定モデル" : "Model supplied in the URL query" },
    contentType: { type: "string", enum: DOCUMENT_MEDIA_TYPES },
    file: { type: "string", format: "binary", maxLength: MAX_DOCUMENT_BYTES, description: locale === "ja" ? "HTTP本文へファイルのバイナリを送信。1 MiBはGatewayの上限です。URL・base64・multipartは公開APIへ送りません。" : "Raw file bytes in the HTTP body. 1 MiB is the Gateway limit. No URLs, base64, or multipart in the public API." },
  } };
}

/** 完全なAzureの解析結果を返すことと、代表的な属性を案内します。 */
export function getDocumentAnalysisResponseSchema(locale: Locale) {
  return { type: "object", required: ["analyzeResult"], properties: {
    analyzeResult: { type: "object", description: locale === "ja" ? "Azure Document Intelligence 2024-11-30 の解析結果。ページ・段落・表・座標などの属性はモデルと文書に依存します。" : "Native Azure Document Intelligence 2024-11-30 result. Pages, paragraphs, tables, and coordinates depend on the model and document.", required: ["apiVersion", "modelId", "content", "pages"], additionalProperties: true, properties: {
      apiVersion: { type: "string", const: "2024-11-30" }, modelId: { type: "string", enum: ["prebuilt-read", "prebuilt-layout"] },
      content: { type: "string", description: locale === "ja" ? "検出された全文。空の文書では空文字もありえます。" : "Full detected text, which can be empty for a blank document." },
      pages: { type: "array", items: { type: "object" }, description: locale === "ja" ? "解析された全ページの結果" : "All analyzed pages" },
      tables: { type: "array", items: { type: "object" }, description: locale === "ja" ? "Layoutで検出された表。検出なしでは省略されます。" : "Tables detected by Layout; omitted when none are detected." },
    } },
    requestId: { type: "string", description: locale === "ja" ? "問い合わせ用の要求ID" : "Request ID for troubleshooting" },
  } };
}
