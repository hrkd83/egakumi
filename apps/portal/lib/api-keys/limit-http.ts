import "server-only";

import { ApiKeyError, apiKeyIdSchema, type ApiKeyActor } from "./contracts.ts";
import { updateApiKeyLimitsSchema, type ApiKeyLimitProfile, type UpdateApiKeyLimits } from "./limit-contracts.ts";

/** HTTP入口が使用する、認可を伴うプロファイル操作です。 */
export type ApiKeyLimitOperations = {
  get(actor: ApiKeyActor, id: string): Promise<ApiKeyLimitProfile>;
  update(actor: ApiKeyActor, id: string, values: UpdateApiKeyLimits): Promise<ApiKeyLimitProfile>;
};

/** 実セッションと公開Originを使いつつ、HTTPの境界を単体検証できる依存です。 */
export type ApiKeyLimitHttpDependencies = {
  readActor(): Promise<ApiKeyActor | null>;
  readOrigin(): string;
  getService(): ApiKeyLimitOperations;
};

const responseHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };

/** Content-Lengthに頼らず入力を8KiBに制限し、JSON以外を受け付けません。 */
async function readJson(request: Request): Promise<unknown> {
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
    throw new ApiKeyError("InvalidRequest", 400);
  }
  const reader = request.body?.getReader();
  if (!reader) throw new ApiKeyError("InvalidRequest", 400);
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let body = "", bytes = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      bytes += part.value.byteLength;
      if (bytes > 8192) throw new ApiKeyError("InvalidRequest", 400);
      body += decoder.decode(part.value, { stream: true });
    }
    return JSON.parse(body + decoder.decode());
  } catch { throw new ApiKeyError("InvalidRequest", 400); }
  finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}

/** 認証・同一Origin・厳密な入力を確認し、設定変更をCSRFや他グループの操作から守ります。 */
export async function handleApiKeyLimits(request: Request, dependencies: ApiKeyLimitHttpDependencies, id: string): Promise<Response> {
  try {
    if (request.method !== "GET" && request.method !== "PUT") {
      return Response.json({ error: { code: "MethodNotAllowed" } }, {
        status: 405, headers: { ...responseHeaders, Allow: "GET, PUT" },
      });
    }
    const actor = await dependencies.readActor();
    if (!actor) throw new ApiKeyError("AuthenticationRequired", 401);
    if (request.headers.get("sec-fetch-site") === "cross-site"
        || (request.method === "PUT" && request.headers.get("origin") !== dependencies.readOrigin())) {
      throw new ApiKeyError("OriginNotAllowed", 403);
    }
    if (!apiKeyIdSchema.safeParse(id).success) throw new ApiKeyError("NotFound", 404);
    if (request.method === "GET") {
      return Response.json(await dependencies.getService().get(actor, id), { headers: responseHeaders });
    }
    const parsed = updateApiKeyLimitsSchema.safeParse(await readJson(request));
    if (!parsed.success) throw new ApiKeyError("InvalidRequest", 400);
    return Response.json(await dependencies.getService().update(actor, id, parsed.data), { headers: responseHeaders });
  } catch (error) {
    const safe = error instanceof ApiKeyError ? error : new ApiKeyError("ServiceUnavailable", 503);
    return Response.json({ error: { code: safe.code } }, { status: safe.status, headers: responseHeaders });
  }
}
