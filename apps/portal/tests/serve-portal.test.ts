import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve, sep } from "node:path";
import { test } from "node:test";
import { isPortalDevelopmentUpgrade } from "../scripts/serve-portal.ts";

/** 他プロセスへ接続しないloopbackの空きポートを試験専用に確保します。 */
async function freePort(): Promise<number> { const server = createServer(); server.listen(0, "127.0.0.1"); await once(server, "listening"); const address = server.address(); assert.ok(address && typeof address !== "string"); const port = address.port; await new Promise<void>((finish) => server.close(() => finish())); return port; }

test("Next 16のHMRパスを同一originで許可し、旧パスや類似パスへupgradeしない", () => {
  const origin = "http://localhost:3000";
  for (const path of ["/_next/hmr", "/_next/hmr?id=fixture"]) assert.equal(isPortalDevelopmentUpgrade(path, origin, origin), true);
  for (const path of ["/_next/webpack-hmr", "/_next/hmr-other", "/api/_internal/realtime/authorize", undefined]) assert.equal(isPortalDevelopmentUpgrade(path, origin, origin), false);
  assert.equal(isPortalDevelopmentUpgrade("/_next/hmr", "https://other.invalid", origin), false);
});

test("正式frontは内部URLとproofを遮断し、HTTP逐次転送とNext子の障害終了を監督する", { timeout: 25_000 }, async () => {
  const folder = await mkdtemp(join(tmpdir(), "egakumi-portal-front-fixture-")); const port = await freePort(); let nextPort = await freePort(); while (nextPort === port) nextPort = await freePort();
  // Nextの代わりに外部通信しないHTTP子だけを起動し、front自体の公開境界を確認します。
  await writeFile(join(folder, "server.js"), `const http=require('node:http');
const server=http.createServer((req,res)=>{
 if(req.url==='/stream'){res.writeHead(200,{'Content-Type':'text/plain'});res.write('first\\n');setTimeout(()=>res.end('last\\n'),350);return;}
 if(req.url==='/exit'){res.end('stopping');setTimeout(()=>process.exit(17),20);return;}
 res.setHeader('Content-Type','application/json');res.end(JSON.stringify({proofForwarded:Boolean(req.headers['x-portal-realtime-internal']),loopback:process.env.HOSTNAME==='127.0.0.1',port:Number(process.env.PORT)}));
});server.listen(Number(process.env.PORT),'127.0.0.1');setTimeout(()=>process.exit(99),20000).unref();`);
  const child = spawn(process.execPath, ["--conditions=react-server", "--experimental-strip-types", resolve("scripts/serve-portal.ts")], { cwd: folder, windowsHide: true,
    env: { ...process.env, NODE_ENV: "production", BETTER_AUTH_URL: `https://portal.example.invalid`, PORT: String(port), PORTAL_NEXT_PORT: String(nextPort), HOSTNAME: "127.0.0.1" }, stdio: ["ignore", "pipe", "pipe"] });
  let output = ""; child.stdout.on("data", (bytes) => { output += bytes.toString(); }); child.stderr.on("data", (bytes) => { output += bytes.toString(); });
  const exited = once(child, "exit"); const endpoint = `http://127.0.0.1:${port}`;
  try {
    const deadline = Date.now() + 10_000;
    while (true) { try { const response = await fetch(endpoint); if (response.ok) break; } catch { /* 子とfrontの起動完了を待ちます。 */ } if (Date.now() > deadline) throw new Error(`Fixture did not start: ${output}`); await new Promise((finish) => setTimeout(finish, 30)); }
    for (const path of ["/api/_internal/realtime/authorize", "/api/%5Finternal/realtime/authorize", "/api/%255Finternal/realtime/authorize"]) assert.equal((await fetch(endpoint + path)).status, 404);
    const response = await fetch(endpoint, { headers: { "X-Portal-Realtime-Internal": "attempted-forgery" } }); assert.deepEqual(await response.json(), { proofForwarded: false, loopback: true, port: nextPort });
    const stream = await fetch(`${endpoint}/stream`); const reader = stream.body!.getReader(); const first = await reader.read(); assert.equal(new TextDecoder().decode(first.value), "first\n");
    const second = await reader.read(); assert.equal(new TextDecoder().decode(second.value), "last\n"); assert.equal((await reader.read()).done, true); reader.releaseLock();
    assert.equal((await fetch(`${endpoint}/api/models/realtime/socket`)).status, 426);
    await fetch(`${endpoint}/exit`); const [code] = await exited; assert.equal(code, 17);
  } finally { if (child.exitCode === null && child.signalCode === null) child.kill(); assert.ok(resolve(folder).startsWith(resolve(tmpdir()) + sep)); await rm(folder, { recursive: true, force: true }); }
});
