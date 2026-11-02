import "server-only";
import { z } from "zod";
import { ApiKeyError, apiKeyIdSchema, transferApiKeyOwnershipSchema,
  type ApiKeyActor, type ApiKeyOwnerTarget, type ApiKeyRecord, type TransferApiKeyOwnershipInput } from "./contracts.ts";

/** 所有元の認可を行う、移管の専用操作です。 */
export type ApiKeyOwnerOperations = {
  ownerTargets(actor: ApiKeyActor, id: string, search: string, cursor?: string): Promise<{ groups: ApiKeyOwnerTarget[]; nextCursor: string | null }>;
  transfer(actor: ApiKeyActor, id: string, input: TransferApiKeyOwnershipInput): Promise<ApiKeyRecord>;
};
/** HTTPの認証と実操作を、外部通信なしに検証するための依存です。 */
export type ApiKeyOwnerHttpDependencies = {
  readActor(): Promise<ApiKeyActor | null>; readOrigin(): string; getService(): ApiKeyOwnerOperations;
};
const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };

/** 移管は小さなJSONだけを受け、追加の本人IDや製品指定を受け付けません。 */
async function readInput(request: Request): Promise<unknown> {
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") throw new ApiKeyError("InvalidRequest", 400);
  const reader = request.body?.getReader();
  if (!reader) throw new ApiKeyError("InvalidRequest", 400);
  let bytes = 0; const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const part = await reader.read(); if (part.done) break;
      bytes += part.value.byteLength; if (bytes > 4096) throw new ApiKeyError("InvalidRequest", 400);
      chunks.push(part.value);
    }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks)));
  } catch { throw new ApiKeyError("InvalidRequest", 400); }
  finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}

/** 現在の所有元を検証してから、候補検索または直接移管を呼び出します。 */
export async function handleApiKeyOwner(request: Request, dependencies: ApiKeyOwnerHttpDependencies, id: string): Promise<Response> {
  try {
    if (!["GET", "POST"].includes(request.method)) return Response.json({ error: { code: "MethodNotAllowed" } }, { status: 405, headers: { ...headers, Allow: "GET, POST" } });
    const actor = await dependencies.readActor();
    if (!actor) throw new ApiKeyError("AuthenticationRequired", 401);
    if (request.headers.get("sec-fetch-site") === "cross-site"
        || (request.method === "POST" && request.headers.get("origin") !== dependencies.readOrigin())) throw new ApiKeyError("OriginNotAllowed", 403);
    if (!apiKeyIdSchema.safeParse(id).success) throw new ApiKeyError("NotFound", 404);
    if (request.method === "GET") {
      const parameters = new URL(request.url).searchParams;
      const search = z.string().trim().min(2).max(80).safeParse(parameters.get("search"));
      const cursor = parameters.get("cursor") ?? undefined;
      if (!search.success || parameters.getAll("search").length !== 1 || parameters.getAll("cursor").length > 1
          || (cursor !== undefined && (cursor.length < 1 || cursor.length > 24000))
          || [...parameters.keys()].some((key) => key !== "search" && key !== "cursor")) throw new ApiKeyError("InvalidRequest", 400);
      return Response.json(await dependencies.getService().ownerTargets(actor, id, search.data, cursor), { headers });
    }
    const input = transferApiKeyOwnershipSchema.safeParse(await readInput(request));
    if (!input.success) throw new ApiKeyError("InvalidRequest", 400);
    return Response.json(await dependencies.getService().transfer(actor, id, input.data), { headers });
  } catch (error) {
    const safe = error instanceof ApiKeyError ? error : new ApiKeyError("ServiceUnavailable", 503);
    return Response.json({ error: { code: safe.code } }, { status: safe.status, headers });
  }
}
