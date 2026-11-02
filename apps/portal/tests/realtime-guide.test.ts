import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { once } from "node:events";
import { test } from "node:test";
import { ModuleKind, ScriptTarget, transpileModule } from "typescript";
import WebSocket, { WebSocketServer } from "ws";
import { getRealtimeIntegrationCode, defaultRealtimeInput } from "../lib/realtime-integration.ts";
import { getRealtimeModels } from "../lib/model-catalog.ts";

/** 実際に表示するPython例を、ネットワークなしの非同期WSで実行します。 */
const pythonFixture = `
import asyncio, json, sys, types
mode = sys.argv[1]
class Socket:
    def __init__(self): self.queue = asyncio.Queue(); self.frames = 0
    async def __aenter__(self): return self
    async def __aexit__(self, *args): await self.close()
    def __aiter__(self): return self
    async def __anext__(self):
        event = await self.queue.get()
        if event is None: raise StopAsyncIteration
        return json.dumps(event)
    async def send(self, raw):
        event = json.loads(raw)
        if event['type'] in ('session.start', 'session.update'):
            if mode == 'initial-error': await self.queue.put({'type':'error','error':{'message':'private-upstream-text'}})
            elif mode == 'initial-close': await self.queue.put(None)
            else: await self.queue.put({'type':'session.started' if event['type']=='session.start' else 'session.updated','session':{}})
        elif event['type'] in ('input_audio_buffer.append','session.input_audio.append'):
            self.frames += 1
            if mode == 'active-error': await self.queue.put({'type':'error','error':{'message':'private-upstream-text'}})
            elif self.frames in (3, 6):
                await self.queue.put({'type':'response.done'})
                await self.queue.put({'type':'fixture.utterance','turn':self.frames//3})
        elif event['type'] == 'session.close': await self.queue.put({'type':'session.closed'})
    async def close(self): await self.queue.put(None)
fake = types.ModuleType('websockets.asyncio.client')
fake.connect = lambda *args, **kwargs: Socket()
sys.modules['websockets'] = types.ModuleType('websockets')
sys.modules['websockets.asyncio'] = types.ModuleType('websockets.asyncio')
sys.modules['websockets.asyncio.client'] = fake
`;

/** 失敗した送信/受信タスクを、利用者停止まで待たずに呼び出し元へ返すかを観測します。 */
const runFixture = `
async def main():
    stop = asyncio.Event(); events = []
    async def frames():
        while not stop.is_set():
            await asyncio.sleep(.001)
            yield bytes(1 if mode == 'invalid-audio' else 4800)
    async def consume(event):
        events.append(event['type'])
        if event['type'] == 'fixture.utterance' and event['turn'] == 2: stop.set()
    error = None
    try: await asyncio.wait_for(converse(frames(), stop, consume), .5)
    except Exception as cause: error = type(cause).__name__
    print(json.dumps({'error':error,'turns':events.count('fixture.utterance'),'stopped':stop.is_set()}))
asyncio.run(main())
`;

for (const mode of ["conversation", "initial-error", "initial-close", "active-error", "invalid-audio"]) {
  test(`Python音声ガイドは${mode}を停止待ちで放置しない`, (context) => {
    const python = process.env.PORTAL_TEST_PYTHON ?? "python3";
    const probe = spawnSync(python, ["--version"], { encoding: "utf8", timeout: 5000 });
    if (probe.error || probe.status !== 0) { context.skip("Python 3実行環境が必要です。PORTAL_TEST_PYTHONでも指定できます。"); return; }
    for (const model of getRealtimeModels().filter((candidate) => candidate.transport === "websocket")) {
      const source = getRealtimeIntegrationCode(model, defaultRealtimeInput(model), "https://gateway.example.invalid", "python");
      const result = spawnSync(python, ["-c", pythonFixture + "\n" + source + "\n" + runFixture, mode], {
        encoding: "utf8", timeout: 5000, env: { ...process.env, GATEWAY_API_KEY: "isolated-guide-fixture" },
      });
      assert.equal(result.status, 0, `${model.id}: ${result.stderr}`);
      const observed = JSON.parse(result.stdout.trim()) as { error: string | null; turns: number; stopped: boolean };
      if (mode === "conversation") assert.deepEqual(observed, { error: null, turns: 2, stopped: true }, model.id);
      else assert.equal(observed.error, mode === "invalid-audio" ? "ValueError" : "RuntimeError", model.id);
    }
  });
}

for (const scenario of ["user-stop", "service-error"]) {
  test(`TypeScript音声ガイドは無音で入力待ちでも${scenario}で終了する`, { timeout: 10_000 }, async () => {
    for (const model of getRealtimeModels().filter((candidate) => candidate.transport === "websocket")) {
      const server = new WebSocketServer({ host: "127.0.0.1", port: 0 });
      await once(server, "listening");
      const address = server.address(); assert.ok(address && typeof address !== "string");
      const controller = new AbortController(); let inputWaits = 0; let returned = false;
      const input: AsyncIterable<Uint8Array> = { [Symbol.asyncIterator]: () => ({
        next: () => { inputWaits++; return new Promise(() => undefined); },
        return: async () => { returned = true; return { done: true, value: undefined }; },
      }) };
      server.on("connection", (socket) => socket.on("message", (raw) => {
        const event = JSON.parse(raw.toString()) as { type: string };
        if (["session.start", "session.update"].includes(event.type)) {
          socket.send(JSON.stringify({ type: model.family === "live" ? "session.started" : "session.updated", session: {} }));
          setTimeout(() => {
            if (scenario === "user-stop") controller.abort();
            else socket.send(JSON.stringify({ type: "error", error: { message: "private-fixture-upstream" } }));
          }, 30);
        }
        if (event.type === "session.close") { socket.send(JSON.stringify({ type: "session.closed" })); socket.close(); }
      }));
      const generated = getRealtimeIntegrationCode(model, defaultRealtimeInput(model), "https://gateway.example.invalid", "typescript")
        .replace(/^import WebSocket from "ws";\n/m, "")
        .replaceAll(/wss:\/\/gateway\.example\.invalid/g, `ws://127.0.0.1:${address.port}`)
        .replace("export async function", "async function");
      const javascript = transpileModule(generated, { compilerOptions: { module: ModuleKind.None, target: ScriptTarget.ES2022 } }).outputText;
      const create = new Function("WebSocket", "process", javascript + "\nreturn converse;") as
        (webSocket: typeof WebSocket, environment: { env: { GATEWAY_API_KEY: string } }) => (frames: AsyncIterable<Uint8Array>, stop: AbortSignal, consume: () => void) => Promise<void>;
      let deadline: ReturnType<typeof setTimeout> | undefined;
      try {
        const converse = create(WebSocket, { env: { GATEWAY_API_KEY: "isolated-guide-fixture" } });
        await Promise.race([converse(input, controller.signal, () => undefined), new Promise<never>((_resolve, reject) => {
          deadline = setTimeout(() => reject(new Error("Guide waited for a stalled microphone")), 1000);
        })]);
        assert.ok(inputWaits > 0, model.id); assert.equal(returned, true, model.id);
      } finally {
        if (deadline) clearTimeout(deadline);
        controller.abort(); for (const socket of server.clients) socket.terminate();
        await new Promise<void>((resolve) => server.close(() => resolve()));
      }
    }
  });
}
