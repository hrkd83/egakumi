import { DOCUMENT_MEDIA_TYPES, MAX_DOCUMENT_BYTES, getDocumentAnalysisEndpoint, type DocumentAnalysisModel } from "./document-analysis-contracts.ts";
import type { IntegrationLanguage } from "./model-integration.ts";
import type { Locale } from "./i18n/config.ts";

/** 公開APIはファイル本文を受け取り、Portal内のmultipartとは分けて案内します。 */
export function getDocumentAnalysisExample(language: IntegrationLanguage, origin: string, model: DocumentAnalysisModel): string {
  const url = getDocumentAnalysisEndpoint(origin, model);
  if (language === "curl") return `# Run on your backend; never expose the subscription key or ticket to a browser.
response=$(curl --silent --show-error --fail-with-body --max-redirs 0 '${url}' \\
  --header "api-key: \${GATEWAY_API_KEY}" --header 'Content-Type: application/pdf' --data-binary '@input.pdf')
path=$(printf '%s' "$response" | jq -r '.statusPath')
ticket=$(printf '%s' "$response" | jq -r '.ticket')
# Validate that path is /tools/documents/v1/operations/<UUID> before using it.
while true; do
  sleep 2
  result=$(curl --silent --show-error --fail-with-body --max-redirs 0 "${origin}$path" \\
    --header "api-key: \${GATEWAY_API_KEY}" --header "X-Core-Document-Ticket: $ticket") || exit 1
  printf '%s' "$result" | jq -e '.analyzeResult' && break
done`;
  if (language === "typescript") return `import { readFile } from "node:fs/promises";
import { setTimeout as wait } from "node:timers/promises";

const headers = { "api-key": process.env.GATEWAY_API_KEY! };
let response = await fetch("${url}", { method: "POST", redirect: "error", headers: { ...headers, "Content-Type": "application/pdf" }, body: await readFile("input.pdf") });
if (!response.ok) throw new Error(\`HTTP \${response.status}\`);
const job = await response.json();
if (!/^\\/tools\\/documents\\/v1\\/operations\\/[0-9a-f-]{36}$/i.test(job.statusPath)) throw new Error("Invalid operation path");
const statusUrl = new URL(job.statusPath, "${origin}");
do {
  const seconds = Number(response.headers.get("retry-after"));
  await wait(Number.isInteger(seconds) && seconds > 0 ? seconds * 1000 : 2000);
  response = await fetch(statusUrl, { redirect: "error", headers: { ...headers, "X-Core-Document-Ticket": job.ticket } });
  if (!response.ok) throw new Error(\`HTTP \${response.status}\`);
} while (response.status === 202);
console.log((await response.json()).analyzeResult);
// Persist the protected operation reference on your backend if resuming later.`;
  if (language === "python") return `import os, re, time
from pathlib import Path
import requests

headers = {"api-key": os.environ["GATEWAY_API_KEY"]}
response = requests.post("${url}", headers={**headers, "Content-Type": "application/pdf"}, data=Path("input.pdf").read_bytes(), allow_redirects=False)
response.raise_for_status()
job = response.json()
if not re.fullmatch(r"/tools/documents/v1/operations/[0-9a-fA-F-]{36}", job["statusPath"]):
    raise ValueError("Invalid operation path")
while response.status_code == 202:
    seconds = response.headers.get("Retry-After", "2")
    time.sleep(int(seconds) if seconds.isdecimal() and int(seconds) > 0 else 2)
    response = requests.get("${origin}" + job["statusPath"], headers={**headers, "X-Core-Document-Ticket": job["ticket"]}, allow_redirects=False)
    response.raise_for_status()
print(response.json()["analyzeResult"])
# Keep the key and operation ticket on your backend.`;
  if (language === "php") return `<?php
function callGateway(string $url, ?string $body, array $extra = []): array {
    $curl = curl_init($url);
    curl_setopt_array($curl, [CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_HTTPHEADER => array_merge(['api-key: ' . getenv('GATEWAY_API_KEY')], $extra)]);
    if ($body !== null) curl_setopt_array($curl, [CURLOPT_POST => true, CURLOPT_POSTFIELDS => $body]);
    $raw = curl_exec($curl); $status = curl_getinfo($curl, CURLINFO_RESPONSE_CODE); curl_close($curl);
    if ($raw === false || $status < 200 || $status >= 300) throw new RuntimeException('Gateway request failed');
    return [$status, json_decode($raw, true, 512, JSON_THROW_ON_ERROR)];
}
[$status, $job] = callGateway('${url}', file_get_contents('input.pdf'), ['Content-Type: application/pdf']);
if (!preg_match('~^/tools/documents/v1/operations/[0-9a-fA-F-]{36}$~', $job['statusPath'])) throw new RuntimeException('Invalid operation path');
while ($status === 202) {
    sleep(2);
    [$status, $result] = callGateway('${origin}' . $job['statusPath'], null, ['X-Core-Document-Ticket: ' . $job['ticket']]);
}
echo json_encode($result['analyzeResult'], JSON_PRETTY_PRINT);`;
  return `using System.Net.Http;
using System.Text.Json;

using var handler = new HttpClientHandler { AllowAutoRedirect = false };
using var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
client.DefaultRequestHeaders.Add("api-key", Environment.GetEnvironmentVariable("GATEWAY_API_KEY"));
using var content = new ByteArrayContent(await File.ReadAllBytesAsync("input.pdf"));
content.Headers.ContentType = new("application/pdf");
var response = await client.PostAsync("${url}", content);
response.EnsureSuccessStatusCode();
var job = JsonDocument.Parse(await response.Content.ReadAsStringAsync()).RootElement;
var path = job.GetProperty("statusPath").GetString()!;
if (!System.Text.RegularExpressions.Regex.IsMatch(path, @"^/tools/documents/v1/operations/[0-9a-fA-F-]{36}$")) throw new Exception("Invalid operation path");
while ((int)response.StatusCode == 202) {
    var delay = response.Headers.RetryAfter?.Delta ?? TimeSpan.FromSeconds(2);
    response.Dispose(); await Task.Delay(delay);
    using var poll = new HttpRequestMessage(HttpMethod.Get, new Uri(new Uri("${origin}"), path));
    poll.Headers.Add("X-Core-Document-Ticket", job.GetProperty("ticket").GetString());
    response = await client.SendAsync(poll); response.EnsureSuccessStatusCode();
}
Console.WriteLine(await response.Content.ReadAsStringAsync());
response.Dispose();`;
}

/** ファイル形式と固定モデルを含む、Gateway向けのHTTP要求仕様です。 */
export function getDocumentAnalysisRequestSchema(locale: Locale) {
  return { type: "object", required: ["method", "model", "contentType", "file"], properties: {
    method: { type: "string", const: "POST" },
    model: { type: "string", enum: ["prebuilt-read", "prebuilt-layout"], description: locale === "ja" ? "URLクエリーで指定する固定モデル" : "Model supplied in the URL query" },
    contentType: { type: "string", enum: DOCUMENT_MEDIA_TYPES },
    file: { type: "string", format: "binary", maxLength: MAX_DOCUMENT_BYTES, description: locale === "ja" ? "HTTP本文へファイルを送信します。容量とページ数は提供元の料金プランおよび通信経路の技術上限に従います。" : "Raw file bytes. Capacity and page count follow the provider tier and transport technical limits." },
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
