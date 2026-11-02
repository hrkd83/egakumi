import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import ts from "typescript";

/** TypeScript原本からブラウザー専用Workletを生成し、手書きJSとの二重管理を避けます。 */
async function prepareRealtimeWorklet(): Promise<void> {
  const source = await readFile(new URL("../lib/realtime-capture-worklet.ts", import.meta.url), "utf8");
  const built = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext, removeComments: false }, reportDiagnostics: true });
  if (built.diagnostics?.some((entry) => entry.category === ts.DiagnosticCategory.Error)) throw new Error("Realtime worklet compilation failed");
  const output = "// Generated from lib/realtime-capture-worklet.ts.\n" + built.outputText + '\nregisterProcessor("egakumi-microphone", createMicrophoneProcessor(AudioWorkletProcessor, sampleRate, () => currentTime));\n';
  await writeFile(fileURLToPath(new URL("../public/realtime-audio-worklet.js", import.meta.url)), output, "utf8");
}
await prepareRealtimeWorklet();
