import assert from "node:assert/strict";
import { test } from "node:test";
import { isAllowedPortalQuery } from "../lib/portal-query.ts";
import { handleTextGeneration } from "../lib/model-gateway.ts";
import { handleEmbeddings } from "../lib/embedding-gateway.ts";
import { handleDecision } from "../lib/decision-gateway.ts";
import { handleImageGeneration } from "../lib/image-gateway.ts";
import { handleRerank } from "../lib/rerank-gateway.ts";
import { handleSpeech } from "../lib/speech-gateway.ts";
import { handlePiiRedaction } from "../lib/pii-gateway.ts";
import { handlePiiMcp } from "../lib/pii-mcp-gateway.ts";
import { handleDocumentAnalysis, handleDocumentAnalysisStatus } from "../lib/document-analysis-gateway.ts";
import { handleRealtimeStop } from "../lib/realtime-webrtc.ts";
import { handleGraphToolProfile } from "../lib/graph-tool-server.ts";
import { defaultEmbeddingInput } from "../lib/embedding-integration.ts";
import { defaultDecisionInput } from "../lib/decision-integration.ts";
import { defaultRerankInput } from "../lib/rerank-integration.ts";
import { defaultImageInput } from "../lib/image-integration.ts";
import { defaultSpeechInput } from "../lib/speech-integration.ts";
import { getImageGenerationModels, getSpeechModels } from "../lib/model-catalog.ts";
import { chatModel } from "./helpers/model-fixtures.ts";
import { smallEmbedding } from "./helpers/embedding-fixtures.ts";
import type { DecisionModel } from "../lib/decision-types.ts";
import type { RerankModel } from "../lib/rerank-types.ts";

const origin = "https://portal.example.invalid";
const marker = "SYNTHETIC_PRIVATE_QUERY_MARKER";
const actor = { tenantId: "11111111-1111-4111-8111-111111111111", objectId: "22222222-2222-4222-8222-222222222222" };
const decision: DecisionModel = { id: "synthetic-decision", label: "Synthetic decision", providerId: "microsoft", provider: "Microsoft", apiId: "microsoft-decision-v1", operationId: "systemone", path: "/models/microsoft-decision/v1/systemone", maxRequestBytes: 8192, responseModel: "Synthetic-Decision" };
const rerank: RerankModel = { id: "synthetic-rerank", label: "Synthetic rerank", providerId: "cohere", provider: "Cohere", apiId: "cohere-inference-v1", operationId: "rerank", path: "/models/cohere/v1/rerank", maxRequestBytes: 65_536, maxDocuments: 8, minTopN: 1, maxTopN: 3 };

/** 認証の回数と、query拒否後に開始してはならない外部処理を数えます。 */
function boundary(authenticated = true) {
  const counts = { authentication: 0, external: 0 };
  const forbidden = (): never => { counts.external++; throw new Error(marker); };
  const readActor = async () => { counts.authentication++; return authenticated ? actor : null; };
  return { counts, readActor, readSession: readActor, readOrigin: () => origin, readPortalOrigin: () => origin,
    readModels: forbidden, readGatewayEnvironment: forbidden, readGateway: forbidden, resolveBearer: forbidden, fetch: forbidden };
}
type Boundary = ReturnType<typeof boundary>;
/** 実HTTPハンドラーと独立合成入力を結び付け、外部サービスを呼ばずに入口の契約を通します。 */
type Target = { path: string; names: readonly string[]; request(query?: string): Request; handle(request: Request, dependencies: Boundary): Promise<Response> };

/** 正規JSON本文を保ったまま、URLだけに負例のqueryを付けます。 */
function jsonRequest(path: string, body: unknown, query = "", method = "POST"): Request {
  return new Request(`${origin}${path}${query}`, { method, headers: { origin, "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
/** 既存の公開入力から組み立てた正規JSONを、同じハンドラーの正常対照にも使います。 */
function jsonTarget(path: string, names: readonly string[], body: unknown, handle: Target["handle"]): Target {
  return { path, names, handle, request: (query) => jsonRequest(path, body, query) };
}
/** 本文内の文書メタデータとファイルを使い、multipart入口にはqueryが不要なことを検証します。 */
function documentRequest(query = ""): Request {
  const form = new FormData();
  form.append("input", JSON.stringify({ authentication: "bearer", model: "prebuilt-read" }));
  form.append("file", new Blob(["%PDF-1.7\nsynthetic document"], { type: "application/pdf" }), "synthetic.pdf");
  return new Request(`${origin}/api/tools/documents${query}`, { method: "POST", headers: { origin }, body: form });
}

const piiInput = { authentication: "bearer", text: "Synthetic contact data", language: "ja" };
const targets: Target[] = [
  jsonTarget("/api/models/decision", ["criteria", "instructions", "message", "questions", "state", "value", "state.message", "state.nested.items.value", "questions.q.instructions", "questions.q.criteria.a", "questions.q.criteria", "synthetic-unapproved-name", "synthetic[unapproved]"], { authentication: "bearer", ...defaultDecisionInput(decision) }, handleDecision),
  jsonTarget("/api/models/embeddings", ["texts", "texts"], { authentication: "bearer", ...defaultEmbeddingInput(smallEmbedding) }, handleEmbeddings),
  jsonTarget("/api/models/generate", ["message", "message"], { authentication: "bearer", modelId: chatModel.id, message: "Synthetic text", maxOutputTokens: 128, stream: false }, handleTextGeneration),
  jsonTarget("/api/models/images", ["prompt", "prompt"], { authentication: "bearer", ...defaultImageInput(getImageGenerationModels()[0]) }, handleImageGeneration),
  jsonTarget("/api/models/rerank", ["documents", "documents", "query", "query"], { authentication: "bearer", ...defaultRerankInput(rerank) }, handleRerank),
  jsonTarget("/api/models/speech", ["input.text", "text", "text"], { authentication: "bearer", ...defaultSpeechInput(getSpeechModels().find((model) => model.kind === "tts")!) }, handleSpeech),
  { path: "/api/tools/documents", names: ["input"], request: documentRequest, handle: handleDocumentAnalysis },
  jsonTarget("/api/tools/pii", ["text", "text"], piiInput, handlePiiRedaction),
  jsonTarget("/api/tools/pii-mcp", ["text", "text"], piiInput, handlePiiMcp),
  jsonTarget("/api/models/realtime/stop", ["handle"], { handle: "r2." + "a".repeat(70) }, handleRealtimeStop),
  jsonTarget("/api/tools/graph/profile", ["profile"], { authentication: "bearer" }, handleGraphToolProfile),
  jsonTarget("/api/tools/documents/status", ["handle"], { authentication: "bearer", handle: "b".repeat(70) }, handleDocumentAnalysisStatus),
];

test("Portalの公開試用APIは許可外queryを認証後かつ本文・下流処理前に拒否する", async (t) => {
  for (const target of targets) {
    for (const [index, name] of target.names.entries()) {
      await t.test(`${target.path} / ${name} / ${index}`, async () => {
        const context = boundary();
        const query = `?${new URLSearchParams({ [name]: marker })}`;
        const request = target.request(query);
        const response = await target.handle(request, context);
        assert.equal(response.status, 400);
        assert.deepEqual(await response.json(), { error: { code: "InvalidRequest" } });
        assert.equal(response.headers.get("cache-control"), "no-store");
        assert.equal(request.bodyUsed, false);
        assert.deepEqual(context.counts, { authentication: 1, external: 0 });
        assert.equal(request.url, `${origin}${target.path}${query}`);
      });
    }
    const control = boundary();
    const normal = target.request();
    await target.handle(normal, control);
    assert.equal(control.counts.authentication, 1);
    assert.ok(control.counts.external > 0, `${target.path}: queryなしの正規入力は後続処理へ進む`);
  }
});

test("未認証query要求では本文や外部依存に触れず、既存401を維持する", async () => {
  for (const target of targets) {
    const context = boundary(false);
    const request = target.request("?unused=synthetic");
    const response = await target.handle(request, context);
    assert.equal(response.status, 401, target.path);
    assert.deepEqual(await response.json(), { error: { code: "AuthenticationRequired" } });
    assert.equal(request.bodyUsed, false);
    assert.deepEqual(context.counts, { authentication: 1, external: 0 });
  }
});

test("空名・名前のエンコード・重複・空の区切りを拒否し、末尾の疑問符だけは許容する", () => {
  for (const query of ["?=synthetic", "?unused", "?%75nused=synthetic", "?unused=one&unused=two", "?_rsc=synthetic", "?&&"]) {
    assert.equal(isAllowedPortalQuery(new Request(`${origin}/api/models/generate${query}`)), false);
  }
  for (const query of ["", "?"]) assert.equal(isAllowedPortalQuery(new Request(`${origin}/api/models/generate${query}`)), true);
  for (const query of ["?model=prebuilt-read", "?%6dodel=prebuilt-read"]) assert.equal(isAllowedPortalQuery(new Request(`${origin}/api/tools/documents${query}`), ["model"]), true);
  for (const query of ["?model=one&model=two", "?model=prebuilt-read&unused=synthetic"]) assert.equal(isAllowedPortalQuery(new Request(`${origin}/api/tools/documents${query}`), ["model"]), false);
});

test("ツールAPIの空query区切りは本文の判定前に拒否する", async () => {
  for (const target of targets.filter((target) => target.path.startsWith("/api/tools/"))) {
    const context = boundary();
    const request = target.request("?&&");
    const response = await target.handle(request, context);
    assert.equal(response.status, 400, target.path);
    assert.deepEqual(await response.json(), { error: { code: "InvalidRequest" } });
    assert.equal(request.bodyUsed, false);
    assert.deepEqual(context.counts, { authentication: 1, external: 0 });
  }
});
