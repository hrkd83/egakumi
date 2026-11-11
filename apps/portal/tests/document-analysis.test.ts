import assert from "node:assert/strict";
import { test } from "node:test";
import { DOCUMENT_MODELS, MAX_DOCUMENT_BYTES, documentAnalysisInputSchema, getDocumentAnalysisEndpoint, inspectDocument, parseDocumentAnalysisResult, readDocumentAnalysisResponse, DocumentAnalysisResponseError } from "../lib/document-analysis-contracts.ts";
import { getDocumentAnalysisExample, getDocumentAnalysisRequestSchema, getDocumentAnalysisResponseSchema } from "../lib/document-analysis-integration.ts";

/** Azureの文書解析結果に含まれる主要属性を備えた、個人情報を含まない試験データです。 */
export const analysis = { analyzeResult: { apiVersion: "2024-11-30", modelId: "prebuilt-layout", content: "Title\nFirst\tSecond", pages: [{ pageNumber: 1, width: 210, height: 297, unit: "millimeter", words: [{ content: "Title", confidence: 0.99 }] }, { pageNumber: 2 }], paragraphs: [{ content: "Title", role: "title" }], tables: [{ rowCount: 1, columnCount: 2, cells: [{ rowIndex: 0, columnIndex: 0, content: "First", kind: "columnHeader" }, { rowIndex: 0, columnIndex: 1, content: "Second" }] }] }, requestId: "document-example-1" };

test("Office・HEIFとHTML断片を受理し、UTF8の1024バイト境界で拒否しない", () => {
  for (const mime of ["application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.openxmlformats-officedocument.presentationml.presentation"]) assert.equal(inspectDocument(new Uint8Array([0x50, 0x4b, 3, 4]), mime), mime);
  assert.equal(inspectDocument(new TextEncoder().encode("0000ftypheic"), "image/heic"), "image/heic");
  const html = "<p>" + "a".repeat(1020) + "日本語</p>";
  assert.equal(inspectDocument(new TextEncoder().encode(html), "text/html"), "text/html");
});

test("ファイルのMIMEとシグネチャが一致する5形式だけを受け付ける", () => {
  const cases = [
    ["application/pdf", [0x25, 0x50, 0x44, 0x46, 0x2d]], ["image/jpeg", [0xff, 0xd8, 0xff]],
    ["image/png", [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]], ["image/tiff", [0x49, 0x49, 0x2a, 0]], ["image/tiff", [0x4d, 0x4d, 0, 0x2a]], ["image/bmp", [0x42, 0x4d]],
  ] as const;
  for (const [mime, bytes] of cases) assert.equal(inspectDocument(new Uint8Array(bytes), mime), mime);
  for (const mime of ["application/octet-stream", "image/svg+xml"]) assert.throws(() => inspectDocument(new TextEncoder().encode("%PDF-1.7"), mime));
  assert.throws(() => inspectDocument(new Uint8Array([0x42, 0x4d]), "application/pdf"));
  assert.throws(() => inspectDocument(new Uint8Array(0), "image/png"));
  const larger = new Uint8Array(2 * 1_048_576); larger.set([0x25, 0x50, 0x44, 0x46, 0x2d]);
  assert.equal(inspectDocument(larger, "application/pdf"), "application/pdf");
});

test("モデルと接続先は固定し、URL入力・任意モデル・秘密の追加入力を拒否する", () => {
  const input = { model: "prebuilt-read", apiKeyId: "portal-11111111-1111-4111-8111-111111111111" };
  assert.equal(documentAnalysisInputSchema.safeParse(input).success, true);
  for (const extra of [{ model: "custom-model" }, { url: "https://untrusted.invalid/file" }, { apiKey: "secret" }, { pages: "1" }, { headers: {} }]) assert.equal(documentAnalysisInputSchema.safeParse({ ...input, ...extra }).success, false);
  for (const origin of ["http://gateway.example.invalid", "https://gateway.example.invalid/path", "https://user:pass@gateway.example.invalid", "https://gateway.example.invalid?other=1"]) assert.throws(() => getDocumentAnalysisEndpoint(origin, "prebuilt-read"));
  assert.equal(getDocumentAnalysisEndpoint("https://gateway.example.invalid", "prebuilt-layout"), "https://gateway.example.invalid/tools/documents/v1/analyze?model=prebuilt-layout");
});

test("全ページ・全文・表を保持し、構造化データを勝手に切り捨てない", () => {
  const result = parseDocumentAnalysisResult(analysis, "prebuilt-layout", 123);
  assert.deepEqual(result.pageNumbers, [1, 2]); assert.equal(result.content, analysis.analyzeResult.content);
  assert.equal(result.tables[0].columnCount, 2); assert.equal(result.tables[0].cells[1].content, "Second");
  assert.deepEqual(result.rawResponse, analysis); assert.equal(result.requestId, "document-example-1");
  assert.equal(result.durationMs, 123);
});

test("空文書・表なしを成功とし、HTML文字列も変換せずプレーンテキストとして扱う", () => {
  const native = { analyzeResult: { apiVersion: "2024-11-30", modelId: "prebuilt-read", content: "<script>alert(1)</script>", pages: [{ pageNumber: 1 }] } };
  const result = parseDocumentAnalysisResult(native, "prebuilt-read", 0);
  assert.deepEqual(result.tables, []); assert.equal(result.content, native.analyzeResult.content);
  assert.equal(parseDocumentAnalysisResult({ analyzeResult: { ...native.analyzeResult, content: "" } }, "prebuilt-read", 0).content, "");
});

test("不正モデル・重複ページ・範囲外の表・過剰な入れ子を拒否する", () => {
  assert.throws(() => parseDocumentAnalysisResult(analysis, "prebuilt-read", 0));
  assert.throws(() => parseDocumentAnalysisResult({ analyzeResult: { ...analysis.analyzeResult, pages: [{ pageNumber: 1 }, { pageNumber: 1 }] } }, "prebuilt-layout", 0));
  assert.throws(() => parseDocumentAnalysisResult({ analyzeResult: { ...analysis.analyzeResult, tables: [{ rowCount: 1, columnCount: 1, cells: [{ rowIndex: 2, columnIndex: 0, content: "bad" }] }] } }, "prebuilt-layout", 0));
  let nested: unknown = "value"; for (let i = 0; i < 40; i++) nested = { child: nested };
  assert.doesNotThrow(() => parseDocumentAnalysisResult({ analyzeResult: { ...analysis.analyzeResult, nested } }, "prebuilt-layout", 0));
});

test("ブラウザーはBFFの検証済み結果と固定失敗コードだけを受け取る", async () => {
  const result = parseDocumentAnalysisResult(analysis, "prebuilt-layout", 10);
  assert.deepEqual(await readDocumentAnalysisResponse(Response.json(result), "prebuilt-layout", new AbortController().signal), result);
  await assert.rejects(readDocumentAnalysisResponse(Response.json({ error: { code: "DocumentAnalysisTimeout" } }, { status: 504 }), "prebuilt-read", new AbortController().signal), (error) => error instanceof DocumentAnalysisResponseError && error.failure.error.code === "DocumentAnalysisTimeout");
});

test("5言語の例は選択モデル・raw file送信とバックエンド秘密を使う", () => {
  for (const model of DOCUMENT_MODELS) for (const language of ["typescript", "python", "php", "csharp", "curl"] as const) {
    const code = getDocumentAnalysisExample(language, "https://gateway.example.invalid", model);
    assert.ok(code.includes(`/tools/documents/v1/analyze?model=${model}`)); assert.ok(code.includes("GATEWAY_API_KEY")); assert.ok(code.includes("input.pdf")); assert.ok(code.includes("application/pdf"));
    assert.equal(code.includes("urlSource"), false);
  }
  assert.equal(getDocumentAnalysisRequestSchema("ja").properties.file.format, "binary");
  assert.equal(getDocumentAnalysisResponseSchema("en").properties.analyzeResult.properties.apiVersion.const, "2024-11-30");
  assert.ok(getDocumentAnalysisExample("typescript", "https://gateway.example.invalid", "prebuilt-read").includes('redirect: "error"'));
  assert.ok(getDocumentAnalysisExample("python", "https://gateway.example.invalid", "prebuilt-read").includes("allow_redirects=False"));
  assert.ok(getDocumentAnalysisExample("csharp", "https://gateway.example.invalid", "prebuilt-read").includes("AllowAutoRedirect = false"));
});
