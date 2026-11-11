import { mkdir, readFile, readdir, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { z } from "zod";

const definitionSchema = z.object({
  name: z.string().min(1),
  path: z.string().startsWith("models/"),
  operations: z.array(z.object({
    name: z.string().min(1), displayName: z.string(), method: z.string(), urlTemplate: z.string(),
  }).passthrough()).min(1),
  authorization: z.object({ operationConstraints: z.record(z.string(), z.unknown()) }).passthrough(),
}).passthrough();
const capabilityEntrySchema = z.object({
  apiIds: z.array(z.string().min(1)).min(1), operationIds: z.array(z.string().min(1)).min(1), modelId: z.string().min(1),
  maxOutputTokens: z.number().int().min(1).safe().nullable(), limitKind: z.enum(["documented", "dynamic", "unsupported", "unverified"]),
  sourceUrls: z.array(z.url().refine((value) => { const url = new URL(value); return url.protocol === "https:" && !url.username && !url.password; })).min(1),
  checkedAt: z.string().regex(/^\d{4}-\d{2}-\d{2}$/), numericMaxOutputTokens: z.number().int().positive().optional(), supportsMaximumOutput: z.boolean().optional(),
}).strict().refine((entry) => entry.limitKind === "documented" ? entry.maxOutputTokens !== null : entry.maxOutputTokens === null)
  .refine((entry) => entry.numericMaxOutputTokens === undefined || entry.maxOutputTokens !== null && entry.numericMaxOutputTokens <= entry.maxOutputTokens);
const capabilitiesSchema = z.object({ schemaVersion: z.literal(1), entries: z.array(capabilityEntrySchema) }).strict().superRefine((document, context) => {
  const seen = new Set<string>();
  for (const entry of document.entries) for (const api of entry.apiIds) for (const operation of entry.operationIds) {
    const key = `${api}/${operation}/${entry.modelId}`; if (seen.has(key)) context.addIssue({ code: "custom", message: "Duplicate model capability target" }); seen.add(key);
  }
});
const snapshotSchema = z.object({
  schemaVersion: z.literal(1),
  definitions: z.array(definitionSchema).min(1),
  modelCapabilities: capabilitiesSchema.optional(),
});
const portalRoot = fileURLToPath(new URL("../", import.meta.url));
const publicBindingKeys = ["VOICE_LIVE_MODEL", "VOICE_LIVE_CALLS_MODEL", "VOICE_LIVE_API_VERSION", "VOICE_LIVE_CALLS_API_VERSION"] as const;
const publicBindingsSchema = z.object({ schemaVersion: z.literal(1), bindings: z.record(z.enum(publicBindingKeys), z.string().regex(/^[a-zA-Z0-9._-]{1,80}$/)) }).strict();

/** カタログ生成元と出力先です。検証時は一時フォルダーに差し替えられます。 */
export type ModelCatalogPaths = { sourceDirectory?: string; snapshotFile?: string; publicBindingsFile?: string; capabilitiesFile?: string };

/** PythonのGateway展開と同じ正本から、profile IDを変えずモデル別上限を付けます。 */
export function applyModelCapabilities(definitions: z.infer<typeof definitionSchema>[], capabilities: z.infer<typeof capabilitiesSchema>) {
  const indexed = new Map<string, z.infer<typeof capabilityEntrySchema>>(), covered = new Set<string>();
  for (const entry of capabilities.entries) for (const api of entry.apiIds) for (const operation of entry.operationIds) { indexed.set(`${api}/${operation}/${entry.modelId}`, entry); covered.add(`${api}/${operation}`); }
  const result = structuredClone(definitions);
  for (const api of result) for (const [operation, raw] of Object.entries(api.authorization.operationConstraints)) {
    if (!covered.has(`${api.name}/${operation}`)) continue;
    const constraint = raw as Record<string, unknown>;
    const selected = Array.isArray(constraint.profiles) ? constraint.profiles as Record<string, unknown>[] : [constraint];
    for (const profile of selected) {
      const websocket = api.websocket as { allowedModels?: string[]; session?: { models: string[] }; queryParameters?: { model?: string } } | undefined;
      const models = profile.allowedModels as string[] | undefined ?? (typeof profile.model === "string" ? [profile.model] : websocket?.allowedModels ?? websocket?.session?.models ?? [websocket?.queryParameters?.model]);
      if (!models.length || models.some((model) => !model || !indexed.has(`${api.name}/${operation}/${model}`))) throw new Error("A published model has no verified capability entry");
      const values = models.map((model) => indexed.get(`${api.name}/${operation}/${model}`)!);
      profile.maxOutputTokensByModel = Object.fromEntries(values.map((entry) => [entry.modelId, entry.maxOutputTokens]));
      profile.outputTokenLimitKinds = Object.fromEntries(values.map((entry) => [entry.modelId, entry.limitKind]));
      const maximum = values.map((entry) => entry.maxOutputTokens).filter((value): value is number => value !== null);
      profile.maxOutputTokens = maximum.length ? Math.max(...maximum) : null;
    }
    if (Array.isArray(constraint.profiles)) {
      constraint.maxOutputTokensByModel = Object.assign({}, ...selected.map((profile) => profile.maxOutputTokensByModel));
      constraint.outputTokenLimitKinds = Object.assign({}, ...selected.map((profile) => profile.outputTokenLimitKinds));
      const maximum = Object.values(constraint.maxOutputTokensByModel as Record<string, number | null>).filter((value): value is number => value !== null);
      constraint.maxOutputTokens = maximum.length ? Math.max(...maximum) : null;
    }
  }
  return result;
}

/** ファイルの不存在だけを識別し、権限不足等で古い情報へ退避しないようにします。 */
function isMissing(error: unknown): boolean {
  return error instanceof Error && "code" in error && error.code === "ENOENT";
}

/**
 * Gatewayの正本から、Portalのビルド範囲内へカタログのスナップショットを生成します。
 * @remarks Dockerでは配備スクリプトが同じスナップショットを同梱します。
 * 正本のないコンテナー内だけは、同梱内容を検査して再利用します。
 * @returns 生成したか再利用したかと、含まれるModel API数。
 */
export async function prepareModelCatalog(paths: ModelCatalogPaths = {}) {
  const sourceDirectory = paths.sourceDirectory ?? resolve(portalRoot, "../gateway/apim/apis");
  const snapshotFile = paths.snapshotFile ?? resolve(portalRoot, "lib/generated/model-api-definitions.json");
  let files: string[];
  try {
    files = await readdir(sourceDirectory);
  } catch (error) {
    if (!isMissing(error)) throw error;
    const snapshot = snapshotSchema.parse(JSON.parse(await readFile(snapshotFile, "utf8")));
    return { mode: "bundled" as const, apiCount: snapshot.definitions.length };
  }

  const definitions = [];
  let publicBindings: Partial<Record<typeof publicBindingKeys[number], string>> = {};
  try { publicBindings = publicBindingsSchema.parse(JSON.parse(await readFile(paths.publicBindingsFile ?? resolve(sourceDirectory, "../public-bindings.json"), "utf8"))).bindings; }
  catch (error) { if (!isMissing(error)) throw error; }
  for (const file of files.filter((name) => name.endsWith(".json")).sort()) {
    const raw: unknown = JSON.parse(await readFile(resolve(sourceDirectory, file), "utf8"), (_key, value: unknown) => {
      if (typeof value !== "string") return value;
      const key = publicBindingKeys.find((name) => value === `__${name}__`);
      return key ? publicBindings[key] ?? value : value;
    });
    if (raw && typeof raw === "object" && "path" in raw && typeof raw.path === "string" && raw.path.startsWith("models/")) {
      definitions.push(definitionSchema.parse(raw));
    }
  }
  let capabilities: z.infer<typeof capabilitiesSchema> | undefined;
  try { capabilities = capabilitiesSchema.parse(JSON.parse(await readFile(paths.capabilitiesFile ?? resolve(sourceDirectory, "../model-capabilities.json"), "utf8"))); }
  catch (error) { if (!isMissing(error) || !paths.sourceDirectory) throw error; }
  const snapshot = snapshotSchema.parse({ schemaVersion: 1, definitions: capabilities ? applyModelCapabilities(definitions, capabilities) : definitions, ...(capabilities ? { modelCapabilities: capabilities } : {}) });
  if (new Set(snapshot.definitions.map((definition) => definition.name)).size !== snapshot.definitions.length) {
    throw new Error("Model API names must be unique");
  }
  const serialized = `${JSON.stringify(snapshot, null, 2)}\n`;
  let previous: string | undefined;
  try { previous = await readFile(snapshotFile, "utf8"); }
  catch (error) { if (!isMissing(error)) throw error; }
  if (previous !== serialized) {
    await mkdir(dirname(snapshotFile), { recursive: true });
    const temporary = `${snapshotFile}.${process.pid}.tmp`;
    await writeFile(temporary, serialized, "utf8");
    await rename(temporary, snapshotFile);
  }
  return { mode: "generated" as const, apiCount: snapshot.definitions.length };
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try { console.log(JSON.stringify(await prepareModelCatalog())); }
  catch {
    console.error("Model catalog preparation failed: verify Gateway definitions or the bundled snapshot.");
    process.exitCode = 1;
  }
}
