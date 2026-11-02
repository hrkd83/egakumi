import "server-only";
import { ApiKeyError, type ApiKeyActor } from "../api-keys/contracts.ts";
import { syncRequestSchema } from "./contracts.ts";
import type { SubscriptionSyncService } from "./service.ts";

/** 同期HTTP入口に必要な、署名済み本人・公開Origin・サービスだけの依存です。 */
export type SyncHttpDependencies = { readActor(): Promise<ApiKeyActor | null>; readOrigin(): string;
  getService(): Pick<SubscriptionSyncService, "overview" | "preview" | "apply"> };
const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };
/** 本文を上限付きで読み取り、任意の状態・ARM宛先の指定を受け付けません。 */
async function readInput(request: Request) {
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") throw new ApiKeyError("InvalidRequest", 400);
  const reader = request.body?.getReader();
  if (!reader) throw new ApiKeyError("InvalidRequest", 400);
  let bytes = 0, text = "";
  const decoder = new TextDecoder("utf-8", { fatal: true });
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      if ((bytes += part.value.byteLength) > 65_536) throw new Error();
      text += decoder.decode(part.value, { stream: true });
    }
    text += decoder.decode();
    return syncRequestSchema.parse(JSON.parse(text));
  } catch { throw new ApiKeyError("InvalidRequest", 400); }
  finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}
/** GETは照会だけ、POSTは同一Originの管理者によるプレビューと確定だけを実行します。 */
export async function handleSubscriptionSync(request: Request, dependencies: SyncHttpDependencies): Promise<Response> {
  try {
    if (!["GET", "POST"].includes(request.method)) return Response.json({ error: { code: "MethodNotAllowed" } }, { status: 405, headers: { ...headers, Allow: "GET, POST" } });
    const actor = await dependencies.readActor();
    if (!actor) throw new ApiKeyError("AuthenticationRequired", 401);
    if (request.headers.get("sec-fetch-site") === "cross-site" || (request.method === "POST" && request.headers.get("origin") !== dependencies.readOrigin())) throw new ApiKeyError("OriginNotAllowed", 403);
    if (new URL(request.url).search) throw new ApiKeyError("InvalidRequest", 400);
    if (request.method === "GET") return Response.json(await dependencies.getService().overview(actor), { headers });
    const input = await readInput(request), service = dependencies.getService();
    return Response.json(input.action === "preview" ? await service.preview(actor, input.ids) : await service.apply(actor, input.token), { headers });
  } catch (error) {
    const safe = error instanceof ApiKeyError ? error : new ApiKeyError("ServiceUnavailable", 503);
    return Response.json({ error: { code: safe.code } }, { status: safe.status, headers });
  }
}
