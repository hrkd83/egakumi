import "server-only";

import { ApiKeyError, apiKeyActionSchema, apiKeyIdSchema, createApiKeySchema } from "./contracts.ts";
import type { ApiKeyActor, ApiKeyOverview, ApiKeyRecord, ApiKeySecrets, CreateApiKeyInput } from "./contracts.ts";

/** HTTP層から使う操作だけを定義し、認可ロジックをサービス側へ集約します。 */
export type ApiKeyOperations = {
  overview(actor: ApiKeyActor): Promise<ApiKeyOverview>;
  create(actor: ApiKeyActor, input: CreateApiKeyInput): Promise<ApiKeyRecord>;
  act(actor: ApiKeyActor, id: string, action: "suspend" | "resume" | "review" | "delete" | "reveal"): Promise<ApiKeyRecord | ApiKeySecrets>;
};

/** セッションの検証と接続先設定をテスト可能にする、サーバー専用の依存です。 */
export type ApiKeyHttpDependencies = {
  readActor(): Promise<ApiKeyActor | null>;
  readOrigin(): string;
  getService(): ApiKeyOperations;
};

const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };

/** 小さな管理操作だけを受け付け、長さヘッダーのない本文にも上限を適用します。 */
async function readJson(request: Request): Promise<unknown> {
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
    throw new ApiKeyError("InvalidRequest", 400);
  }
  const reader = request.body?.getReader();
  if (!reader) throw new ApiKeyError("InvalidRequest", 400);
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let body = "";
  let bytes = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      bytes += part.value.byteLength;
      if (bytes > 8192) throw new ApiKeyError("InvalidRequest", 400);
      body += decoder.decode(part.value, { stream: true });
    }
    body += decoder.decode();
    return JSON.parse(body);
  } catch {
    throw new ApiKeyError("InvalidRequest", 400);
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

/**
 * グループ所有キーのHTTP入口です。認証・Origin・入力を確認後にだけ操作を呼びます。
 * @remarks 上流の例外やSDKの本文にはキーが含まれる可能性があるため、ログや応答に転送しません。
 */
export async function handleApiKeys(request: Request, dependencies: ApiKeyHttpDependencies, id?: string): Promise<Response> {
  try {
    if ((id && request.method !== "POST") || (!id && !["GET", "POST"].includes(request.method))) {
      return Response.json({ error: { code: "MethodNotAllowed" } }, { status: 405, headers: { ...headers, Allow: id ? "POST" : "GET, POST" } });
    }
    const actor = await dependencies.readActor();
    if (!actor) throw new ApiKeyError("AuthenticationRequired", 401);
    if (request.headers.get("sec-fetch-site") === "cross-site") throw new ApiKeyError("OriginNotAllowed", 403);
    if (request.method === "POST" && request.headers.get("origin") !== dependencies.readOrigin()) {
      throw new ApiKeyError("OriginNotAllowed", 403);
    }
    if (id && !apiKeyIdSchema.safeParse(id).success) throw new ApiKeyError("NotFound", 404);
    if (request.method === "GET") {
      return Response.json(await dependencies.getService().overview(actor), { headers });
    }
    const body = await readJson(request);
    if (id) {
      const parsed = apiKeyActionSchema.safeParse(body);
      if (!parsed.success) throw new ApiKeyError("InvalidRequest", 400);
      return Response.json(await dependencies.getService().act(actor, id, parsed.data.action), { headers });
    }
    const parsed = createApiKeySchema.safeParse(body);
    if (!parsed.success) throw new ApiKeyError("InvalidRequest", 400);
    return Response.json(await dependencies.getService().create(actor, parsed.data), { status: 201, headers });
  } catch (error) {
    const safe = error instanceof ApiKeyError ? error : new ApiKeyError("ServiceUnavailable", 503);
    return Response.json({ error: { code: safe.code } }, { status: safe.status, headers });
  }
}
