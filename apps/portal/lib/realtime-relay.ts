import "server-only";

import type { IncomingMessage } from "node:http";
import type { Duplex } from "node:stream";
import WebSocket, { WebSocketServer, type RawData } from "ws";
import { z } from "zod";
import { getRealtimeEndpoint } from "./realtime-integration.ts";
import { runRealtimeConversation, type RealtimeConversationCommand } from "./realtime-gateway.ts";
import { handleRealtimeWebRtc } from "./realtime-webrtc.ts";
import { realtimeStreamEventSchema, resolveRealtimeOutputTokens, REALTIME_MAX_DURATION_MS, REALTIME_MAX_STREAM_BYTES, REALTIME_MAX_EVENTS, type RealtimeStreamEvent } from "./realtime-request.ts";
import { REALTIME_RELAY_PATH, realtimeRelayStartSchema, verifyRealtimeInternalProof, type RealtimeRelayContext } from "./realtime-relay-contract.ts";
export { REALTIME_RELAY_PATH, realtimeRelayStartSchema, verifyRealtimeInternalProof } from "./realtime-relay-contract.ts";
export type { RealtimeRelayContext } from "./realtime-relay-contract.ts";
/** 音声フレームは単一チャンネルのPCM16・24kHzに限ります。 */
const commandSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("input_audio"), delta: z.string().min(4).max(32_768).regex(/^[A-Za-z0-9+/]*={0,2}$/), sampleRate: z.literal(24000).optional() }).strict(),
  z.object({ type: z.literal("stop") }).strict(), z.object({ type: z.literal("cancel_response") }).strict(),
]);

/** NextのURL復号とprivate-folder規則の前に、内部パスの全表記を外側で遮断します。 */
export function isRealtimeInternalPath(rawUrl: string): boolean {
  try {
    let path = new URL(rawUrl, "http://loopback.invalid").pathname;
    for (let count = 0; count < 4; count++) { const decoded = decodeURIComponent(path); if (decoded === path) break; path = decoded; }
    path = path.replaceAll("\\", "/").replace(/\/+/g, "/").toLowerCase();
    return path === "/api/_internal" || path.startsWith("/api/_internal/") || path.includes("\0");
  } catch { return true; }
}

/** 通信や通話生成を差し替え、外部サービスを呼ばずにrelayの認可と回収を検証できます。 */
export type RealtimeRelayDependencies = {
  portalOrigin: string; authorizeUrl: string; internalProof: string;
  authorize?: (input: z.infer<typeof realtimeRelayStartSchema>, cookie: string, signal: AbortSignal) => Promise<RealtimeRelayContext>;
  runConversation?: typeof runRealtimeConversation; runWebRtc?: typeof handleRealtimeWebRtc;
  firstFrameTimeoutMs?: number; heartbeatMs?: number; maximumDurationMs?: number; maxConnections?: number; stopTimeoutMs?: number;
};

/** HTTP応答のバッファリングを使わず、検証済みJSONをブラウザーWSへ即時中継します。 */
export function createRealtimeRelay(dependencies: RealtimeRelayDependencies): { upgrade: (request: IncomingMessage, socket: Duplex, head: Buffer) => void; shutdown: () => Promise<void> } {
  const origin = new URL(dependencies.portalOrigin).origin;
  const connectionLimit = dependencies.maxConnections ?? 32;
  if (!Number.isInteger(connectionLimit) || connectionLimit < 1 || connectionLimit > 512) throw new Error("Invalid realtime connection limit");
  const target = new URL(dependencies.authorizeUrl);
  if (target.protocol !== "http:" || target.hostname !== "127.0.0.1" || target.pathname !== "/api/_internal/realtime/authorize" || target.search || target.hash || target.username || target.password
    || !verifyRealtimeInternalProof(dependencies.internalProof, dependencies.internalProof)) throw new Error("Invalid realtime internal configuration");
  const server = new WebSocketServer({ noServer: true, maxPayload: 262_144, perMessageDeflate: false, clientTracking: true });
  const active = new Map<WebSocket, { abort: AbortController; done: Promise<void> }>(); let shuttingDown = false;
  const authorize = dependencies.authorize ?? (async (input, cookie, signal) => {
    const response = await fetch(target, { method: "POST", headers: { "Content-Type": "application/json", "X-Portal-Realtime-Internal": dependencies.internalProof, Cookie: cookie, Origin: origin, "Sec-Fetch-Site": "same-origin" },
      body: JSON.stringify(input), redirect: "error", cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(75_000)]) });
    if (!response.ok) { void response.body?.cancel().catch(() => undefined); throw new Error(response.status === 401 ? "AuthenticationRequired" : response.status === 403 ? "ApiKeyUnavailable" : "ApiKeyAccessUnavailable"); }
    // このloopback応答だけにキー実値が含まれます。ログやクライアントフレームへ転記しません。
    const text = await response.text(); if (Buffer.byteLength(text) > 20_000) throw new Error("ApiKeyAccessUnavailable");
    const context = JSON.parse(text) as RealtimeRelayContext;
    if (context.portalOrigin !== origin || context.apiKeyId !== input.apiKeyId || context.model.id !== input.modelId || !/^[\x21-\x7e]{16,512}$/.test(context.key)
      || !z.uuid().safeParse(context.actor.tenantId).success || !z.uuid().safeParse(context.actor.objectId).success) throw new Error("ApiKeyAccessUnavailable");
    getRealtimeEndpoint(context.model, context.gatewayOrigin); return context;
  });

  const upgrade = (request: IncomingMessage, socket: Duplex, head: Buffer) => {
    socket.on("error", () => socket.destroy());
    const countHeader = (name: string) => request.rawHeaders.filter((_, index) => index % 2 === 0 && request.rawHeaders[index].toLowerCase() === name).length;
    const cookie = request.headers.cookie;
    if (shuttingDown || server.clients.size >= connectionLimit || request.url !== REALTIME_RELAY_PATH || request.method !== "GET" || request.headers.origin !== origin || countHeader("origin") !== 1 || countHeader("cookie") !== 1
      || !cookie || cookie.length > 16_384 || !cookie.includes("egakumi.portal") || request.headers["sec-websocket-protocol"]
      || request.headers["sec-fetch-site"] && request.headers["sec-fetch-site"] !== "same-origin") {
      socket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"); return;
    }
    server.handleUpgrade(request, socket, head, (client) => {
      const cancellation = new AbortController(); let started = false; let stopped = false; let command: ((value: RealtimeConversationCommand) => void) | undefined;
      let context: RealtimeRelayContext | undefined; let totalBytes = 0; let incomingBytes = 0; let incomingFrames = 0; let alive = true; let work: Promise<void> | undefined;
      let stopDeadline: ReturnType<typeof setTimeout> | undefined;
      let doneResolve: () => void = () => undefined; const done = new Promise<void>((resolve) => { doneResolve = resolve; }); active.set(client, { abort: cancellation, done });
      const closeSoon = () => { if (client.readyState === WebSocket.OPEN) client.close(1000); const timer = setTimeout(() => client.terminate(), 1000); timer.unref(); };
      const emit = (value: RealtimeStreamEvent) => {
        if (client.readyState !== WebSocket.OPEN) return;
        const encoded = JSON.stringify(realtimeStreamEventSchema.parse(value)); totalBytes += Buffer.byteLength(encoded);
        if (totalBytes > REALTIME_MAX_STREAM_BYTES || client.bufferedAmount > 1_048_576 || context && encoded.includes(context.key)) { cancellation.abort(); throw new Error("Stream consumer unavailable"); }
        client.send(encoded, (error) => { if (error) cancellation.abort(); });
      };
      const fail = (code: "InvalidRequest" | "GenerationFailed" | "AuthenticationRequired" | "ApiKeyUnavailable" | "ApiKeyAccessUnavailable" = "GenerationFailed") => { try { emit({ type: "error", error: { code } }); } catch { /* 受信側が遅いときも回収処理を優先します。 */ } cancellation.abort(); };
      const firstFrame = setTimeout(() => { fail("InvalidRequest"); closeSoon(); }, dependencies.firstFrameTimeoutMs ?? 5000); firstFrame.unref();
      const maximum = setTimeout(() => cancellation.abort(), Math.min(dependencies.maximumDurationMs ?? REALTIME_MAX_DURATION_MS, REALTIME_MAX_DURATION_MS)); maximum.unref();
      const heartbeat = setInterval(() => { if (!alive) { cancellation.abort(); client.terminate(); return; } alive = false; if (client.readyState === WebSocket.OPEN) client.ping(); }, dependencies.heartbeatMs ?? 15_000); heartbeat.unref();
      client.on("pong", () => { alive = true; });
      client.on("error", () => cancellation.abort());
      client.on("close", () => { clearTimeout(firstFrame); clearTimeout(maximum); clearTimeout(stopDeadline); clearInterval(heartbeat); cancellation.abort(); if (!work) { active.delete(client); doneResolve(); } });
      const execute = async (input: z.infer<typeof realtimeRelayStartSchema>) => {
        try {
          context = await authorize(input, cookie, cancellation.signal); if (cancellation.signal.aborted) return;
          resolveRealtimeOutputTokens(context.model, input);
          if ((context.model.transport === "webrtc") !== Boolean(input.sdp)) throw new Error("InvalidRequest");
          if (context.model.transport === "webrtc") {
            const allowed = new Set([context.model.apiId, ...(context.model.observer ? [context.model.observer.apiId] : [])]);
            const response = await (dependencies.runWebRtc ?? handleRealtimeWebRtc)(new Request(`${origin}/api/models/realtime/webrtc`, { method: "POST", headers: { Origin: origin, "Content-Type": "application/json" },
              body: JSON.stringify({ modelId: input.modelId, apiKeyId: input.apiKeyId, maxOutputTokens: input.maxOutputTokens, sdp: input.sdp }), signal: cancellation.signal }), {
              readSession: async () => context!.actor, readPortalOrigin: () => origin, readModels: () => [context!.model], readGatewayEnvironment: async () => ({ origin: context!.gatewayOrigin }),
              resolveSubscriptionKey: async (actor, id, apiId) => { if (id !== context!.apiKeyId || actor.tenantId !== context!.actor.tenantId || actor.objectId !== context!.actor.objectId || !allowed.has(apiId)) throw new Error("Invalid subscription binding"); return context!.key; },
            });
            if (!response.ok || !response.body) throw new Error("GenerationFailed");
            const reader = response.body.getReader(); const decoder = new TextDecoder("utf-8", { fatal: true }); let pending = "";
            try { while (true) { const chunk = await reader.read(); if (chunk.done) break; pending += decoder.decode(chunk.value, { stream: true }); if (pending.length > 262_144) throw new Error("Invalid stream frame");
              let split: number; while ((split = pending.indexOf("\n\n")) >= 0) { const frame = pending.slice(0, split); pending = pending.slice(split + 2); if (!frame.startsWith("data: ")) throw new Error("Invalid stream frame"); emit(realtimeStreamEventSchema.parse(JSON.parse(frame.slice(6)))); }
            } } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
          } else {
            await (dependencies.runConversation ?? runRealtimeConversation)(context.model, { modelId: input.modelId, maxOutputTokens: input.maxOutputTokens }, getRealtimeEndpoint(context.model, context.gatewayOrigin), context.key, cancellation.signal, emit, (receive) => { command = receive; return () => { command = undefined; }; });
          }
        } catch (error) {
          if (!stopped && client.readyState === WebSocket.OPEN) fail(error instanceof Error && ["InvalidRequest", "AuthenticationRequired", "ApiKeyUnavailable", "ApiKeyAccessUnavailable"].includes(error.message) ? error.message as "InvalidRequest" : "GenerationFailed");
        } finally { cancellation.abort(); clearTimeout(firstFrame); clearTimeout(maximum); clearTimeout(stopDeadline); clearInterval(heartbeat); context = undefined; closeSoon(); active.delete(client); doneResolve(); }
      };
      client.on("message", (data: RawData, binary: boolean) => {
        try {
          if (binary) throw new Error("Invalid frame"); const text = data.toString(); incomingBytes += Buffer.byteLength(text);
          if (incomingBytes > REALTIME_MAX_STREAM_BYTES || ++incomingFrames > REALTIME_MAX_EVENTS) throw new Error("Input limit");
          const value: unknown = JSON.parse(text);
          if (!started) { const input = realtimeRelayStartSchema.parse(value); started = true; clearTimeout(firstFrame); work = execute(input); return; }
          const action = commandSchema.parse(value); if (action.type === "stop") {
            if (stopped) return; stopped = true;
            if (context?.model.transport === "websocket" && command) {
              // Liveはsession.close/session.closedを確認する猶予を取り、応答がなくても有限時間で回収します。
              command(action); stopDeadline = setTimeout(() => cancellation.abort(), dependencies.stopTimeoutMs ?? 6500); stopDeadline.unref();
            } else cancellation.abort();
            return;
          }
          if (!context || context.model.transport !== "websocket" || !command || stopped || cancellation.signal.aborted) throw new Error("Invalid frame sequence");
          command(action);
        } catch { fail("InvalidRequest"); if (!work) closeSoon(); }
      });
    });
  };
  const shutdown = async () => {
    shuttingDown = true; const running = [...active.values()];
    for (const [client, session] of active) { session.abort.abort(); client.close(1001); }
    await Promise.race([Promise.allSettled(running.map((session) => session.done)), new Promise<void>((resolve) => { const timer = setTimeout(resolve, 15_000); timer.unref(); })]);
    for (const client of server.clients) client.terminate(); server.close();
  };
  return { upgrade, shutdown };
}
