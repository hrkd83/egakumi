import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createServer, request as requestHttp, type IncomingMessage } from "node:http";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync } from "node:fs";
import type { Socket } from "node:net";
import type { Duplex } from "node:stream";
import { createRealtimeRelay, isRealtimeInternalPath, REALTIME_RELAY_PATH } from "../lib/realtime-relay.ts";

/** 公開設定は1つのoriginに限定し、開発用HTTPもloopbackでだけ許可します。 */
function publicOrigin(development: boolean): string {
  const value = new URL(process.env.BETTER_AUTH_URL ?? "");
  if (value.username || value.password || value.pathname !== "/" || value.search || value.hash || value.protocol !== "https:" && !(development && value.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(value.hostname))) throw new Error("Invalid Portal origin");
  return value.origin;
}

/** インストール済みNextが使う開発専用HMRだけを、同じoriginから受け付けます。 */
export function isPortalDevelopmentUpgrade(url: string | undefined, requestOrigin: string | undefined, expectedOrigin: string): boolean {
  return requestOrigin === expectedOrigin && typeof url === "string" && /^\/_next\/hmr(?:\?|$)/.test(url);
}

/** Next standaloneを改造せずloopbackで起動し、同一公開ポートにHTTPと所有者付き音声WSを収容します。 */
export async function servePortal(development = false): Promise<void> {
  const port = Number(process.env.PORT ?? 3000); const nextPort = Number(process.env.PORTAL_NEXT_PORT ?? 3001);
  if (!Number.isInteger(port) || !Number.isInteger(nextPort) || port < 1024 || port > 65535 || nextPort < 1024 || nextPort > 65535 || port === nextPort) throw new Error("Invalid Portal port");
  const origin = publicOrigin(development); const proof = randomBytes(32).toString("hex");
  // 社内proxyを利用しても、秘密を含む内部認可通信は必ず同一PCのloopbackへ直結します。
  const noProxy = [...new Set(`${process.env.NO_PROXY ?? process.env.no_proxy ?? ""},localhost,127.0.0.1,::1`.split(",").map((entry) => entry.trim()).filter(Boolean))].join(",");
  process.env.NO_PROXY = noProxy; process.env.no_proxy = noProxy;
  const relay = createRealtimeRelay({ portalOrigin: origin, authorizeUrl: `http://127.0.0.1:${nextPort}/api/_internal/realtime/authorize`, internalProof: proof, maxConnections: Number(process.env.PORTAL_REALTIME_MAX_CONNECTIONS ?? 32) });
  // react-server条件はNext自身へ継承しません。通常Reactを使うNextの起動条件を保ちます。
  const childEnvironment: NodeJS.ProcessEnv = { ...process.env, PORT: String(nextPort), HOSTNAME: "127.0.0.1", PORTAL_REALTIME_INTERNAL_PROOF: proof, NODE_ENV: development ? "development" : "production" };
  delete childEnvironment.NODE_OPTIONS;
  const args = development ? [resolve("node_modules/next/dist/bin/next"), "dev", "--hostname", "127.0.0.1", "--port", String(nextPort)] : [resolve(existsSync("server.js") ? "server.js" : ".next/standalone/server.js")];
  const proxyFlags = process.execArgv.includes("--use-env-proxy") ? ["--use-env-proxy"] : [];
  const child = spawn(process.execPath, [...proxyFlags, ...args], { cwd: process.cwd(), env: childEnvironment, stdio: "inherit", windowsHide: true });
  const pending = new Set<IncomingMessage>();
  const connections = new Set<Socket>();
  let stopping = false; let signalCount = 0;
  const server = createServer((incoming, outgoing) => {
    if (stopping) { outgoing.writeHead(503, { "Content-Length": "0", Connection: "close" }); outgoing.end(); return; }
    if (isRealtimeInternalPath(incoming.url ?? "/")) { outgoing.writeHead(404, { "Content-Length": "0", "Cache-Control": "no-store" }); outgoing.end(); return; }
    if (incoming.url === REALTIME_RELAY_PATH) { outgoing.writeHead(426, { "Content-Length": "0", Upgrade: "websocket", Connection: "Upgrade" }); outgoing.end(); return; }
    const headers = { ...incoming.headers }; delete headers["x-portal-realtime-internal"];
    const forwarded = requestHttp({ hostname: "127.0.0.1", port: nextPort, path: incoming.url, method: incoming.method, headers }, (response) => {
      // pipeはSSEも含めて逐次転送します。応答全体をメモリーへ保持しません。
      outgoing.writeHead(response.statusCode ?? 502, response.headers); response.pipe(outgoing);
      response.on("error", () => outgoing.destroy());
    });
    pending.add(incoming); forwarded.on("close", () => pending.delete(incoming));
    forwarded.on("error", () => { if (!outgoing.headersSent) outgoing.writeHead(503, { "Content-Length": "0" }); outgoing.end(); });
    incoming.on("aborted", () => forwarded.destroy()); outgoing.on("close", () => { if (!outgoing.writableFinished) forwarded.destroy(); });
    incoming.pipe(forwarded);
  });
  server.on("connection", (socket) => { connections.add(socket); socket.once("close", () => connections.delete(socket)); });
  /** 開発時のNext HMRだけは同一originで中継し、任意のupgrade先は作りません。 */
  const upgradeDevelopment = (incoming: IncomingMessage, socket: Duplex, head: Buffer) => {
    const headers = { ...incoming.headers }; delete headers["x-portal-realtime-internal"];
    const upstream = requestHttp({ hostname: "127.0.0.1", port: nextPort, path: incoming.url, headers });
    upstream.on("upgrade", (response, peer, nextHead) => {
      socket.write(`HTTP/1.1 ${response.statusCode} Switching Protocols\r\n${Object.entries(response.headers).map(([name, value]) => `${name}: ${Array.isArray(value) ? value.join(", ") : value}`).join("\r\n")}\r\n\r\n`);
      if (head.length) peer.write(head); if (nextHead.length) socket.write(nextHead); socket.pipe(peer); peer.pipe(socket);
      socket.on("error", () => peer.destroy()); peer.on("error", () => socket.destroy()); socket.on("close", () => peer.destroy()); peer.on("close", () => socket.destroy());
    });
    upstream.on("response", (response) => { response.destroy(); socket.destroy(); }); upstream.on("error", () => socket.destroy()); upstream.end();
  };
  server.on("upgrade", (incoming, socket, head) => {
    if (development && isPortalDevelopmentUpgrade(incoming.url, incoming.headers.origin, origin) && !stopping) upgradeDevelopment(incoming, socket, head);
    else relay.upgrade(incoming, socket, head);
  });
  server.on("clientError", (_error, socket) => socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"));
  const shutdown = async (code: number) => {
    if (stopping) return; stopping = true;
    // Nextは通話回収後まで保持し、loopbackでの処理と上流hangupを完了できるようにします。
    server.close(); server.closeIdleConnections();
    await relay.shutdown(); for (const request of pending) request.destroy(); server.closeAllConnections(); for (const socket of connections) socket.destroy();
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
    await Promise.race([new Promise<void>((finish) => { if (child.exitCode !== null || child.signalCode !== null) finish(); else child.once("exit", () => finish()); }), new Promise<void>((finish) => { const timer = setTimeout(() => { child.kill("SIGKILL"); finish(); }, 5000); timer.unref(); })]);
    process.exitCode = code;
  };
  const terminated = () => { signalCount++; if (signalCount > 1) { child.kill("SIGKILL"); process.exit(1); } void shutdown(0); };
  process.on("SIGTERM", terminated); process.on("SIGINT", terminated);
  child.once("error", () => { console.error("Portal Next process could not start"); void shutdown(1); });
  child.once("exit", (code) => { if (!stopping) { console.error("Portal Next process ended"); void shutdown(code ?? 1); } });
  server.once("error", () => { console.error("Portal front server could not start"); void shutdown(1); });
  await new Promise<void>((finish) => server.listen(port, process.env.HOSTNAME ?? "0.0.0.0", finish));
  console.info(`Portal front server listening on port ${port}`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  void servePortal(process.argv.includes("--dev")).catch(() => { console.error("Portal front server configuration failed"); process.exitCode = 1; });
}
