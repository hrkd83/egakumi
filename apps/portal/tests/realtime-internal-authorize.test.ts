import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { test } from "node:test";
import { ApiKeyError, type ApiKeyActor } from "../lib/api-keys/contracts.ts";
import type { RealtimeModel } from "../lib/model-types.ts";

const portalOrigin = "https://portal.example.invalid", gatewayOrigin = "https://gateway.example.invalid";
const proof = "a".repeat(64), key = "synthetic-internal-realtime-key-only";
const actor = { tenantId: randomUUID(), objectId: randomUUID() }, apiKeyId = `portal-${randomUUID()}`;
const websocket: RealtimeModel = { id: "synthetic-realtime-ws", label: "Synthetic Realtime", providerId: "openai", provider: "OpenAI",
  family: "realtime", transport: "websocket", apiId: "openai-realtime-ws-v1", operationId: "invoke",
  path: "/models/openai-realtime/v1/ws/", modelId: "gpt-realtime-2.1", voice: "marin", maxRequestBytes: 65536, maxOutputTokens: 128 };
const webrtc: RealtimeModel = { ...websocket, id: "synthetic-realtime-rtc", transport: "webrtc", apiId: "openai-realtime-control-v1",
  operationId: "realtime-create", path: "/models/openai-realtime/v1/calls", maxRequestBytes: 131072,
  observer: { apiId: "openai-realtime-observer-v1", path: "/models/openai-realtime/v1/observer/" } };
const start = { type: "start", modelId: websocket.id, apiKeyId, maxOutputTokens: 16 };

type Session = { user: { entraTenantId: string; entraObjectId: string; name: string; email: string } } | null;
type KeyCall = { actor: ApiKeyActor; id: string; apiId: string };
type SessionCall = { headers: Headers; query: { disableCookieCache: boolean; disableRefresh: boolean } };

/** 認証DBとGraph/キー照会だけを制御し、本番routeと本人抽出の配線を通します。 */
async function routeFixture() {
  const bridgeId = "__egakumi_realtime_internal_authorize_fixture";
  const bridge = {
    headers: new Headers(), connectionCalls: 0, sessionCalls: [] as SessionCall[], keyCalls: [] as KeyCall[],
    session: null as Session, sessionError: false, models: [websocket, webrtc] as RealtimeModel[],
    authorize: async (_actor: ApiKeyActor, _id: string, _apiId: string): Promise<string> => key,
    async getSession(value: SessionCall): Promise<Session> {
      this.sessionCalls.push(value);
      if (this.sessionError) throw new Error(key);
      return this.session;
    },
    async executionKey(value: ApiKeyActor, id: string, apiId: string): Promise<string> {
      this.keyCalls.push({ actor: value, id, apiId });
      return this.authorize(value, id, apiId);
    },
  };
  const globals = globalThis as typeof globalThis & Record<string, unknown>;
  assert.equal(globals[bridgeId], undefined);
  globals[bridgeId] = bridge;
  const app = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const moduleUrl = (name: string) => pathToFileURL(resolve(app, name)).href;
  const routeUrl = moduleUrl("app/api/%5Finternal/realtime/authorize/route.ts");
  const sessionUrl = moduleUrl("lib/auth-session.ts");
  const access = `globalThis[${JSON.stringify(bridgeId)}]`;
  const environment = `export const getAuthEnvironment = () => ({baseURL:${JSON.stringify(portalOrigin)},microsoftTenantId:${JSON.stringify(actor.tenantId)}})`;
  const data = (source: string) => `data:text/javascript,${encodeURIComponent(source)}`;
  const routeAdapters: Record<string, string> = {
    "@/lib/auth-environment": data(environment),
    "@/lib/auth-session": sessionUrl,
    "@/lib/api-keys/service": data(`export const getApiKeyService=()=>({executionKey:(...args)=>${access}.executionKey(...args)})`),
    "@/lib/model-catalog": data(`export const getRealtimeModels=()=>${access}.models`),
  };
  const sessionAdapters: Record<string, string> = {
    "next/headers": data(`export const headers=async()=>${access}.headers`),
    "next/server": data(`export const connection=async()=>{${access}.connectionCalls++}`),
    "next/navigation": data("export const redirect=()=>{throw new Error('Unexpected fixture redirect')}"),
    "@/lib/auth": data(`export const getAuth=()=>({api:{getSession:(args)=>${access}.getSession(args)}})`),
    "@/lib/auth-environment": data(environment),
    "@/lib/auth-navigation": moduleUrl("lib/auth-navigation.ts"),
  };
  type Resolution = { url: string; shortCircuit?: boolean };
  type Hooks = { registerHooks(hooks: { resolve(specifier: string, context: { parentURL?: string },
    next: (specifier: string, context: { parentURL?: string }) => Resolution): Resolution }): { deregister(): void } };
  const { registerHooks } = await import("node:module") as unknown as Hooks;
  const hooks = registerHooks({ resolve(specifier, context, next) {
    const adapters = context.parentURL === routeUrl ? routeAdapters : context.parentURL === sessionUrl ? sessionAdapters : null;
    if (adapters?.[specifier]) return { url: adapters[specifier], shortCircuit: true };
    if (context.parentURL === routeUrl && specifier.startsWith("@/lib/")) {
      return { url: moduleUrl(`${specifier.slice(2)}.ts`), shortCircuit: true };
    }
    return next(specifier, context);
  } });
  const { POST } = await import(routeUrl) as { POST(request: Request): Promise<Response> };
  // このtest worker内だけの合成設定。既存の秘密値を読み取って復元用に保持しません。
  process.env.PORTAL_REALTIME_INTERNAL_PROOF = proof;
  process.env.PORTAL_GATEWAY_URL = gatewayOrigin;
  return {
    bridge,
    reset() {
      bridge.headers = new Headers(); bridge.connectionCalls = 0; bridge.sessionCalls = []; bridge.keyCalls = [];
      bridge.session = { user: { entraTenantId: actor.tenantId, entraObjectId: actor.objectId, name: "Synthetic account", email: "account@example.invalid" } };
      bridge.sessionError = false; bridge.models = [websocket, webrtc]; bridge.authorize = async () => key;
      process.env.PORTAL_REALTIME_INTERNAL_PROOF = proof; process.env.PORTAL_GATEWAY_URL = gatewayOrigin;
    },
    async post(request: Request) { bridge.headers = request.headers; return POST(request); },
    close() { hooks.deregister(); delete globals[bridgeId]; delete process.env.PORTAL_REALTIME_INTERNAL_PROOF; delete process.env.PORTAL_GATEWAY_URL; },
  };
}

/** 外部ソケットを開かず、同じコンテナの内部口へ渡すRequestだけを組み立てます。 */
function request(body: BodyInit | null = JSON.stringify(start), headers: Record<string, string> = {}, signal?: AbortSignal): Request {
  const init: RequestInit & { duplex?: "half" } = { method: "POST", headers: {
    "X-Portal-Realtime-Internal": proof, Origin: portalOrigin, "Content-Type": "application/json",
    Cookie: "egakumi.portal.session_token=synthetic-only", ...headers,
  }, body, signal };
  if (body instanceof ReadableStream) init.duplex = "half";
  return new Request("http://127.0.0.1:3001/api/_internal/realtime/authorize", init);
}

/** すべての拒否は本文を持たず、Cookie・キー・例外文字列を返さないことを検査します。 */
async function emptyFailure(response: Response, status: number): Promise<void> {
  assert.equal(response.status, status);
  assert.equal(await response.text(), "");
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  for (const header of ["set-cookie", "location", "access-control-allow-origin"]) assert.equal(response.headers.get(header), null);
}

test("内部Realtime認可の実POST配線をRequestから検証し、外部認証と上流通信を開始しない", async (t) => {
  const f = await routeFixture();
  try {
    await t.test("内部proofなし・改変・不正設定は、Originやセッションや本文を読む前に404", async () => {
      for (const candidate of ["", "b".repeat(64), "a".repeat(63), "é".repeat(64)]) {
        f.reset(); await emptyFailure(await f.post(request("not-json", { "X-Portal-Realtime-Internal": candidate })), 404);
        assert.equal(f.bridge.connectionCalls, 0); assert.equal(f.bridge.sessionCalls.length, 0); assert.equal(f.bridge.keyCalls.length, 0);
      }
      for (const value of [undefined, "invalid"]) {
        f.reset(); if (value === undefined) delete process.env.PORTAL_REALTIME_INTERNAL_PROOF; else process.env.PORTAL_REALTIME_INTERNAL_PROOF = value;
        await emptyFailure(await f.post(request()), 404); assert.equal(f.bridge.connectionCalls, 0);
      }
      f.reset(); const missing = request(); missing.headers.delete("X-Portal-Realtime-Internal");
      await emptyFailure(await f.post(missing), 404); assert.equal(f.bridge.connectionCalls, 0);
    });
    await t.test("OriginとJSON媒体型の不一致を本人照会前に403にする", async () => {
      const variants: Record<string, string>[] = [{ Origin: "" }, { Origin: "https://other.invalid" }, { Origin: `${portalOrigin}/` },
        { "Content-Type": "text/plain" }, { "Content-Type": "multipart/form-data" }];
      for (const headers of variants) {
        f.reset(); await emptyFailure(await f.post(request(undefined, headers)), 403);
        assert.equal(f.bridge.connectionCalls, 0); assert.equal(f.bridge.keyCalls.length, 0);
      }
    });
    await t.test("実本人抽出はCookieのない申告・期限切れ・別tenantを拒否し、SQL障害を成功にしない", async () => {
      f.reset(); await emptyFailure(await f.post(request(undefined, { Cookie: "", "X-Entra-Object-Id": randomUUID() })), 401);
      assert.equal(f.bridge.sessionCalls.length, 0);
      for (const session of [null, { user: { entraTenantId: randomUUID(), entraObjectId: actor.objectId, name: "Other", email: "other@example.invalid" } },
        { user: { entraTenantId: actor.tenantId, entraObjectId: "", name: "Missing", email: "missing@example.invalid" } }]) {
        f.reset(); f.bridge.session = session; await emptyFailure(await f.post(request()), 401); assert.equal(f.bridge.keyCalls.length, 0);
      }
      f.reset(); f.bridge.sessionError = true; await emptyFailure(await f.post(request()), 503); assert.equal(f.bridge.keyCalls.length, 0);
    });
    await t.test("不正JSON・UTF8・厳格入力は空の固定エラーとなりキー照会へ進まない", async () => {
      const bodies: BodyInit[] = ["{", new Uint8Array([0xff]), " ".repeat(135_001), JSON.stringify({ ...start, url: "https://other.invalid" }),
        JSON.stringify({ ...start, apiKey: key }), JSON.stringify({ ...start, tenantId: actor.tenantId }), JSON.stringify({ ...start, type: "other" }),
        JSON.stringify({ ...start, apiId: "unapproved-api" }), JSON.stringify({ ...start, headers: { "api-key": key } }),
        JSON.stringify({ ...start, maxOutputTokens: 0 }), JSON.stringify({ ...start, apiKeyId: "master" })];
      for (const body of bodies) {
        f.reset(); await emptyFailure(await f.post(request(body)), 503); assert.equal(f.bridge.keyCalls.length, 0);
      }
      let cancelled = false; const controller = new AbortController(); controller.abort();
      const pending = new ReadableStream<Uint8Array>({ cancel() { cancelled = true; } });
      f.reset(); await emptyFailure(await f.post(request(pending, {}, controller.signal)), 503);
      assert.equal(cancelled, true); assert.equal(f.bridge.keyCalls.length, 0);
    });
    await t.test("未知model・transportとSDP・音声・token境界を本番validatorで照合する", async () => {
      for (const body of [{ ...start, modelId: "unpublished" }, { ...start, sdp: "synthetic-sdp-offer-0000" },
        { ...start, modelId: webrtc.id }, { ...start, maxOutputTokens: 129 }, { ...start, voice: "unpublished-voice" }]) {
        f.reset(); await emptyFailure(await f.post(request(JSON.stringify(body))), 400); assert.equal(f.bridge.keyCalls.length, 0);
      }
    });
    await t.test("接続先設定とcatalogのAPI/path改変をキー照会前に拒否する", async () => {
      for (const value of [undefined, "http://gateway.example.invalid", `${gatewayOrigin}/private`, `${gatewayOrigin}/?token=synthetic`]) {
        f.reset(); if (value === undefined) delete process.env.PORTAL_GATEWAY_URL; else process.env.PORTAL_GATEWAY_URL = value;
        await emptyFailure(await f.post(request()), 503); assert.equal(f.bridge.keyCalls.length, 0);
      }
      for (const model of [{ ...websocket, apiId: "unapproved-api" }, { ...websocket, path: "/unapproved-path" }, { ...websocket, modelId: "unpublished-native" }]) {
        f.reset(); f.bridge.models = [model]; await emptyFailure(await f.post(request()), 503); assert.equal(f.bridge.keyCalls.length, 0);
      }
    });
    await t.test("mainとobserverを現在本人・同じキーIDで順に照会し、不許可と秘密不一致を拒否する", async () => {
      const body = JSON.stringify({ ...start, modelId: webrtc.id, sdp: "synthetic-sdp-offer-0000" });
      for (const [failedApi, error, status] of [[webrtc.apiId, new ApiKeyError(key, 404), 403],
        [webrtc.observer!.apiId, new ApiKeyError(key, 403), 403], [webrtc.observer!.apiId, new ApiKeyError(key, 503), 503],
        [webrtc.apiId, new Error(key), 503]] as const) {
        f.reset(); f.bridge.authorize = async (_actor, _id, api) => { if (api === failedApi) throw error; return key; };
        await emptyFailure(await f.post(request(body)), status);
        assert.deepEqual(f.bridge.keyCalls.map((entry) => entry.apiId), failedApi === webrtc.apiId ? [webrtc.apiId] : [webrtc.apiId, webrtc.observer!.apiId]);
        assert.ok(f.bridge.keyCalls.every((entry) => entry.id === apiKeyId && entry.actor.objectId === actor.objectId && entry.actor.tenantId === actor.tenantId));
      }
      f.reset(); f.bridge.authorize = async (_actor, _id, api) => api === webrtc.apiId ? key : "different-synthetic-key";
      await emptyFailure(await f.post(request(body)), 403); assert.equal(f.bridge.keyCalls.length, 2);
      const foreignKeyId = `portal-${randomUUID()}`;
      f.reset(); f.bridge.authorize = async (_actor, id) => { assert.equal(id, foreignKeyId); throw new ApiKeyError(key, 404); };
      await emptyFailure(await f.post(request(JSON.stringify({ ...start, apiKeyId: foreignKeyId }))), 403);
      assert.deepEqual(f.bridge.keyCalls.map((entry) => entry.id), [foreignKeyId], "許可済みの別キーへfallbackしない");
    });
    await t.test("認可成功だけがloopback DTOへキーを返し、申告本人・Cookie・proofは返さない", async () => {
      for (const model of [websocket, webrtc]) {
        f.reset(); const extra = model.transport === "webrtc" ? { sdp: "synthetic-sdp-offer-0000" } : {};
        const response = await f.post(request(JSON.stringify({ ...start, modelId: model.id, ...extra }), {
          "X-Entra-Tenant-Id": randomUUID(), "X-Entra-Object-Id": randomUUID(), "X-MS-CLIENT-PRINCIPAL-ID": randomUUID(),
        }));
        assert.equal(response.status, 200); assert.equal(response.headers.get("cache-control"), "no-store");
        assert.equal(response.headers.get("x-content-type-options"), "nosniff");
        for (const header of ["set-cookie", "location", "access-control-allow-origin"]) assert.equal(response.headers.get(header), null);
        const context = await response.json();
        assert.deepEqual(context, { actor, model, key, apiKeyId, gatewayOrigin, portalOrigin });
        assert.deepEqual(f.bridge.keyCalls.map((entry) => entry.apiId), model.observer ? [model.apiId, model.observer.apiId] : [model.apiId]);
        assert.ok(f.bridge.keyCalls.every((entry) => entry.id === apiKeyId && entry.actor.tenantId === actor.tenantId && entry.actor.objectId === actor.objectId));
        assert.equal(f.bridge.sessionCalls.length, 1);
        assert.deepEqual(f.bridge.sessionCalls[0].query, { disableCookieCache: true, disableRefresh: true });
        assert.equal(f.bridge.sessionCalls[0].headers.get("cookie"), "egakumi.portal.session_token=synthetic-only");
        const text = JSON.stringify(context); assert.equal(text.includes(proof), false); assert.equal(text.includes("Synthetic account"), false); assert.equal(text.includes("session_token"), false);
      }
      f.reset(); const body = JSON.stringify({ ...start, modelId: webrtc.id, sdp: "synthetic-sdp-offer-" + "a".repeat(150_000) });
      assert.equal((await f.post(request(body))).status, 200, "135KBを超える開始要求でも本人・購読認可を通す");
      assert.deepEqual(f.bridge.keyCalls.map((entry) => entry.apiId), [webrtc.apiId, webrtc.observer!.apiId]);
    });
  } finally { f.close(); }
});
