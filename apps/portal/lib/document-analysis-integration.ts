import { DOCUMENT_ANALYSIS_API_VERSION, getDocumentAnalysisEndpoint, type DocumentAnalysisModel } from "./document-analysis-contracts.ts";
import type { IntegrationLanguage } from "./model-integration.ts";
import type { Locale } from "./i18n/config.ts";

/** 公開APIで利用できる原本ファイル送信の例を、Portal内のmultipart入口とは分けて案内します。 */
export function getDocumentAnalysisExample(language: IntegrationLanguage, origin: string, model: DocumentAnalysisModel): string {
  const url = getDocumentAnalysisEndpoint(origin, model);
  if (language === "curl") return `# Run on your backend; never expose the access token or ticket to a browser.
response=$(curl --silent --show-error --fail-with-body --max-redirs 0 '${url}' \\
  --header "Authorization: Bearer \${GATEWAY_ACCESS_TOKEN}" --header 'Content-Type: application/pdf' --data-binary '@input.pdf')
path=$(printf '%s' "$response" | jq -r '.statusPath')
ticket=$(printf '%s' "$response" | jq -r '.ticket')
# Validate that path is /tools/documents/v1/operations/<UUID> before using it.
while true; do
  sleep 2
  result=$(curl --silent --show-error --fail-with-body --max-redirs 0 "${origin}$path" \\
    --header "Authorization: Bearer \${GATEWAY_ACCESS_TOKEN}" --header "X-Core-Document-Ticket: $ticket") || exit 1
  printf '%s' "$result" | jq -e '.analyzeResult' && break
done`;
  if (language === "typescript") return `import { readFile } from "node:fs/promises";
import { setTimeout as wait } from "node:timers/promises";

const headers = { "Authorization": "Bearer " + process.env.GATEWAY_ACCESS_TOKEN! };
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

headers = {"Authorization": "Bearer " + os.environ["GATEWAY_ACCESS_TOKEN"]}
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
        CURLOPT_HTTPHEADER => array_merge(['Authorization: Bearer ' . getenv('GATEWAY_ACCESS_TOKEN')], $extra)]);
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
client.DefaultRequestHeaders.Add("Authorization", "Bearer " + Environment.GetEnvironmentVariable("GATEWAY_ACCESS_TOKEN"));
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

/** 原本ファイルと提供元のJSON入力を案内する、Gateway向けのHTTP要求仕様です。
 * @remarks Portalのアップロード画面やBFFの入力検証を変更するものではありません。
 */
export function getDocumentAnalysisRequestSchema(locale: Locale) {
  return { type: "object", required: ["method", "model"], description: locale === "ja" ? "本文は原本ファイル、またはurlSource／base64SourceのJSONです。提供元の仕様に沿ったContent-Typeを送ります。APIMは本文を解析・変換せず、Content-Lengthから500,000,000バイト超と分かる場合に413を返します。復号後やURL先の文書サイズは提供元が判定します。" : "Send raw document bytes or provider JSON containing urlSource or base64Source, with a Content-Type matching the provider contract. APIM forwards the body without parsing or conversion and returns 413 when Content-Length declares more than 500,000,000 bytes. The provider checks decoded or URL-fetched document sizes.", oneOf: [{ required: ["file"] }, { required: ["urlSource"] }, { required: ["base64Source"] }], properties: {
    method: { type: "string", const: "POST" },
    model: { type: "string", description: locale === "ja" ? "提供元のモデルIDをURLクエリーで1値指定します。modelは必須・非空で、.と..はURL構造の不備として400になります。利用可否は提供元が判定します。Portal画面の選択肢はprebuilt-read／prebuilt-layoutです。" : "Supply one nonempty provider model ID in the required model URL query. The values . and .. return 400 because they alter the URL hierarchy. The provider determines model availability. The Portal UI offers prebuilt-read and prebuilt-layout." },
    contentType: { type: "string", description: locale === "ja" ? "原本形式のMIME、またはJSON入力のapplication/json。APIM独自のMIME許可一覧との比較は行いません。" : "The raw document MIME type, or application/json for provider JSON input. APIM does not compare it against a Gateway MIME allowlist." },
    file: { type: "string", format: "binary", description: locale === "ja" ? "HTTP本文へ原本ファイルを送信します。Portal画面はこの方式です。容量とページ数は提供元の料金プランおよび通信経路の技術上限に従います。" : "Raw document bytes in the HTTP body. The Portal upload UI uses this method. Capacity and page count follow the provider tier and transport technical limits." },
    urlSource: { type: "string", format: "uri", description: locale === "ja" ? "JSON本文に文書URLを指定します。Document Intelligenceが文書を取得します。" : "Document URL supplied in the JSON body. Document Intelligence fetches the document." },
    base64Source: { type: "string", contentEncoding: "base64", description: locale === "ja" ? "JSON本文にBase64化した文書を指定します。送信する本文全体は元ファイルより大きくなります。" : "Base64-encoded document supplied in the JSON body. The transmitted body is larger than the original file." },
  } };
}

/** 完全なAzureの解析結果を返すことと、代表的な属性を案内します。 */
export function getDocumentAnalysisResponseSchema(locale: Locale) {
  return { type: "object", required: ["analyzeResult"], properties: {
    analyzeResult: { type: "object", description: locale === "ja" ? `Azure Document Intelligence ${DOCUMENT_ANALYSIS_API_VERSION} の解析結果。ページ・段落・表・座標などの属性はモデルと文書に依存します。` : `Native Azure Document Intelligence ${DOCUMENT_ANALYSIS_API_VERSION} result. Pages, paragraphs, tables, and coordinates depend on the model and document.`, required: ["apiVersion", "modelId", "content", "pages"], additionalProperties: true, properties: {
      apiVersion: { type: "string", const: DOCUMENT_ANALYSIS_API_VERSION }, modelId: { type: "string", description: locale === "ja" ? "受付時のmodelと一致する提供元のモデルID" : "Provider model ID matching the model accepted for this operation" },
      content: { type: "string", description: locale === "ja" ? "検出された全文。空の文書では空文字もありえます。" : "Full detected text, which can be empty for a blank document." },
      pages: { type: "array", items: { type: "object" }, description: locale === "ja" ? "解析された全ページの結果" : "All analyzed pages" },
      tables: { type: "array", items: { type: "object" }, description: locale === "ja" ? "Layoutで検出された表。検出なしでは省略されます。" : "Tables detected by Layout; omitted when none are detected." },
    } },
    requestId: { type: "string", description: locale === "ja" ? "問い合わせ用の要求ID" : "Request ID for troubleshooting" },
  } };
}
