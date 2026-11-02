import "server-only";

import { z } from "zod";
import { ApiKeyError, type ApiKeyActor } from "../api-keys/contracts.ts";
import type { ApiKeyAdministrationOverview, ApiKeyAdministrationPageRequest } from "./contracts.ts";

/** HTTP入口へ公開するのは、権限確認と観測の取得・更新だけです。 */
export type ApiKeyAdministrationOperations = {
  isAdmin(actor: ApiKeyActor): Promise<boolean>;
  overview(actor: ApiKeyActor, input?: ApiKeyAdministrationPageRequest): Promise<ApiKeyAdministrationOverview>;
  refresh(actor: ApiKeyActor): Promise<ApiKeyAdministrationOverview>;
};

/** 未認証や別Originの要求では、SQL・Graphのサービスを起動しません。 */
export type ApiKeyAdministrationHttpDependencies = {
  readActor(): Promise<ApiKeyActor | null>;
  readOrigin(): string;
  getService(): ApiKeyAdministrationOperations;
};
const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };
const refreshSchema = z.object({ action: z.literal("refresh") }).strict();

/** 更新操作の本文は固定の小さなJSONだけを受け付けます。任意IDリストは指定できません。 */
async function refreshRequest(request: Request): Promise<void> {
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
    throw new ApiKeyError("InvalidRequest", 400);
  }
  const reader = request.body?.getReader();
  if (!reader) throw new ApiKeyError("InvalidRequest", 400);
  let text = ""; let bytes = 0;
  const decoder = new TextDecoder("utf-8", { fatal: true });
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      bytes += part.value.byteLength;
      if (bytes > 1024) throw new Error();
      text += decoder.decode(part.value, { stream: true });
    }
    text += decoder.decode();
    if (!refreshSchema.safeParse(JSON.parse(text)).success) throw new Error();
  } catch { throw new ApiKeyError("InvalidRequest", 400); }
  finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}

/** 管理画面の所属判定・一覧・観測更新を、同一Originの認証済み要求へ限定します。 */
export async function handleApiKeyAdministration(request: Request, dependencies: ApiKeyAdministrationHttpDependencies, accessOnly = false): Promise<Response> {
  try {
    const allowed = accessOnly ? ["GET"] : ["GET", "POST"];
    if (!allowed.includes(request.method)) return Response.json({ error: { code: "MethodNotAllowed" } },
      { status: 405, headers: { ...headers, Allow: allowed.join(", ") } });
    const actor = await dependencies.readActor();
    if (!actor) throw new ApiKeyError("AuthenticationRequired", 401);
    if (request.headers.get("sec-fetch-site") === "cross-site"
        || (request.method === "POST" && request.headers.get("origin") !== dependencies.readOrigin())) {
      throw new ApiKeyError("OriginNotAllowed", 403);
    }
    if (accessOnly) return Response.json({ isAdmin: await dependencies.getService().isAdmin(actor) }, { headers });
    if (request.method === "GET") {
      const parameters = new URL(request.url).searchParams;
      const cursor = parameters.get("cursor") ?? undefined;
      if ([...parameters.keys()].some((key) => key !== "cursor") || parameters.getAll("cursor").length > 1
          || (cursor !== undefined && !z.uuid().safeParse(cursor).success)) throw new ApiKeyError("InvalidRequest", 400);
      return Response.json(await dependencies.getService().overview(actor, { cursor }), { headers });
    }
    if (new URL(request.url).search) throw new ApiKeyError("InvalidRequest", 400);
    await refreshRequest(request);
    return Response.json(await dependencies.getService().refresh(actor), { headers });
  } catch (error) {
    const safe = error instanceof ApiKeyError ? error : new ApiKeyError("ServiceUnavailable", 503);
    return Response.json({ error: { code: safe.code } }, { status: safe.status, headers });
  }
}
