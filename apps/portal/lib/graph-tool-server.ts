import "server-only";

import { ApiKeyError, type ApiKeyActor, type ApiKeyOverview } from "./api-keys/contracts.ts";
import { getApiKeyService } from "./api-keys/service.ts";
import { ConnectionSessionClient, ConnectionSessionError, type SharedStateStore } from "./connection-session/client.ts";
import { createConnectionSessionStore } from "./connection-session/store.ts";
import { GRAPH_TOOL_PATH, graphToolCompleteSchema, graphToolConnectionInputSchema, graphToolInputSchema,
  graphToolKeysSchema, graphToolLoginSchema, graphToolProfileSchema, graphToolStatusSchema } from "./graph-tool-contracts.ts";
import { getPublicModelGatewayOrigin } from "./model-public-config.ts";

const apiId = "graph-profile-v1";
const responseHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };
const maximumBodyBytes = 4096;

/** 画面や任意のHTTPヘッダーではなく、サーバーの設定から決めるGraph接続先です。 */
export type GraphToolEnvironment = { origin: string; profile: string };

/** 実際の共有セッションクライアントを通したまま、認証・認可・SQL・上流通信を検証する境界です。 */
export type GraphToolDependencies = {
  readActor: () => Promise<ApiKeyActor | null>;
  readPortalOrigin: () => string;
  readChoices?: (actor: ApiKeyActor, apiId: string) => Promise<ApiKeyOverview>;
  resolveCredential?: (actor: ApiKeyActor, apiKeyId: string, apiId: string) => Promise<{ subscriptionId: string; key: string }>;
  readGateway?: () => GraphToolEnvironment;
  readStore?: () => SharedStateStore;
  fetch?: typeof globalThis.fetch;
};

/** Graphの公開Gatewayと論理プロファイルだけを読み、未設定や任意URLへの迂回を拒否します。 */
export function getGraphToolEnvironment(environment: Readonly<Record<string, string | undefined>> = process.env): GraphToolEnvironment {
  const origin = getPublicModelGatewayOrigin(environment);
  const profile = environment.PORTAL_GRAPH_PROFILE ?? "graph-delegated";
  if (!origin || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(profile)) throw new ConnectionSessionError("InvalidConfiguration");
  return { origin, profile };
}

/** 固定エラーだけを返し、SDK例外・上流本文・接続セッションを公開しません。 */
function failure(code: string, status: number, requestId?: string): Response {
  return Response.json({ error: { code }, ...(requestId ? { requestId } : {}) }, { status, headers: responseHeaders });
}

/** 対応時に突合できる安全なAPIM要求IDだけを取り出します。 */
function requestId(headers: Headers): string | undefined {
  const value = headers.get("apim-request-id");
  return value && /^[A-Za-z0-9_-]{1,128}$/.test(value) ? value : undefined;
}

/** 宣言したContent-Lengthだけに頼らず、実際に読み込む本文量を制限します。 */
async function readBody(body: ReadableStream<Uint8Array> | null, maximum: number): Promise<unknown> {
  if (!body) throw new Error("Missing body");
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const item = await reader.read();
      if (item.done) break;
      size += item.value.length;
      if (size > maximum) throw new Error("Body too large");
      chunks.push(item.value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

/** SQL障害や更新競合をMicrosoft再認証と混同せず、公開エラーへ分類します。 */
function connectionFailure(error: unknown): Response {
  if (!(error instanceof ConnectionSessionError)) return failure("ConnectionStateUnavailable", 503);
  if (error.code === "IdentityMismatch") return failure("ConnectionIdentityMismatch", 403);
  if (error.code === "GatewayUnavailable") return failure("GatewayUnavailable", 503);
  if (["SessionUpdateRequired", "SessionRotationSuperseded"].includes(error.code)) return failure("SessionUpdateRequired", 409);
  if (error.code === "InvalidConfiguration") return failure("GatewayNotConfigured", 503);
  if (error.code.startsWith("SessionStore") || error.code === "SessionStateInvalid") return failure("ConnectionStateUnavailable", 503);
  if (["AuthenticationStatusFailed", "SessionCommitFailed"].includes(error.code)) return failure("GatewayUnavailable", 503);
  return failure("GatewayResponseInvalid", 502);
}

/** Portalの本人認証を先に確認し、ブラウザーが申告した本人IDは利用しません。 */
async function actor(dependencies: GraphToolDependencies): Promise<ApiKeyActor | Response> {
  try { return await dependencies.readActor() ?? failure("AuthenticationRequired", 401); }
  catch { return failure("AuthenticationUnavailable", 503); }
}

/** 更新を伴うBFF要求は、認証Cookieに加えて同一Originを必須とします。 */
async function input(request: Request, dependencies: GraphToolDependencies, poll: boolean): Promise<{ apiKeyId: string } | Response> {
  let origin: string;
  try { origin = new URL(dependencies.readPortalOrigin()).origin; }
  catch { return failure("GatewayNotConfigured", 503); }
  const fetchSite = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== origin || (fetchSite && fetchSite !== "same-origin")) {
    return failure("RequestNotAllowed", 403);
  }
  if (new URL(request.url).search) return failure("InvalidRequest", 400);
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") return failure("InvalidRequest", 415);
  if (Number(request.headers.get("content-length") ?? 0) > maximumBodyBytes) return failure("InvalidRequest", 413);
  try {
    const value = await readBody(request.body, maximumBodyBytes);
    return (poll ? graphToolConnectionInputSchema : graphToolInputSchema).parse(value);
  } catch { return failure("InvalidRequest", 400); }
}

/** 所属・台帳・APIM実購読の状態を毎回確認し、秘密は共有クライアントへだけ渡します。 */
async function client(dependencies: GraphToolDependencies, owner: ApiKeyActor, apiKeyId: string): Promise<ConnectionSessionClient | Response> {
  let credential: { subscriptionId: string; key: string };
  try {
    credential = await (dependencies.resolveCredential ?? ((person, id, target) => getApiKeyService().executionCredential(person, id, target)))(owner, apiKeyId, apiId);
  } catch (error) {
    const denied = error instanceof ApiKeyError && error.status < 500;
    return failure(denied ? "ApiKeyUnavailable" : "ApiKeyAccessUnavailable", denied ? 403 : 503);
  }
  try {
    const environment = (dependencies.readGateway ?? getGraphToolEnvironment)();
    return new ConnectionSessionClient({ ...environment, apiKey: credential.key, subscriptionId: credential.subscriptionId,
      principal: { tenantId: owner.tenantId, ownerId: owner.objectId },
      store: (dependencies.readStore ?? createConnectionSessionStore)(), fetch: dependencies.fetch });
  } catch (error) { return connectionFailure(error); }
}

/** Graphを利用できるグループ所有の購読だけを、キー実値を含めず選択肢として返します。 */
export async function handleGraphToolKeys(request: Request, dependencies: GraphToolDependencies): Promise<Response> {
  if (request.method !== "GET") return new Response(null, { status: 405, headers: { ...responseHeaders, Allow: "GET" } });
  if (request.headers.get("sec-fetch-site") === "cross-site") return failure("RequestNotAllowed", 403);
  if (new URL(request.url).search) return failure("InvalidRequest", 400);
  const owner = await actor(dependencies);
  if (owner instanceof Response) return owner;
  try {
    const overview = await (dependencies.readChoices ?? ((person, target) => getApiKeyService().executionChoices(person, target)))(owner, apiId);
    const groups = new Map(overview.groups.map((group) => [group.id.toLowerCase(), group.displayName]));
    const keys = overview.keys.filter((key) => key.state === "active" && groups.has(key.groupId.toLowerCase())).map((key) => ({
      id: key.id, displayName: key.displayName, companyName: key.companyName, groupName: groups.get(key.groupId.toLowerCase())!,
    }));
    return Response.json(graphToolKeysSchema.parse({ keys }), { headers: responseHeaders });
  } catch (error) {
    const denied = error instanceof ApiKeyError && error.status < 500;
    return failure(denied ? "ApiKeyUnavailable" : "ApiKeyAccessUnavailable", denied ? 403 : 503);
  }
}

/** 業務APIを最初に呼び、本人接続が必要な場合だけMicrosoft認証へのURLを公開します。 */
export async function handleGraphToolProfile(request: Request, dependencies: GraphToolDependencies): Promise<Response> {
  const value = await input(request, dependencies, false);
  if (value instanceof Response) return value;
  const owner = await actor(dependencies);
  if (owner instanceof Response) return owner;
  const gateway = await client(dependencies, owner, value.apiKeyId);
  if (gateway instanceof Response) return gateway;
  const started = performance.now();
  try {
    const result = await gateway.get(GRAPH_TOOL_PATH);
    if (result.kind === "login") return Response.json(graphToolLoginSchema.parse({ status: "need_login", loginUrl: result.loginUrl, expiresIn: result.expiresIn }), { status: 409, headers: responseHeaders });
    const upstream = result.response;
    const identifier = requestId(upstream.headers);
    const body = await readBody(upstream.body, 65_536);
    if (!upstream.ok) {
      const code = body && typeof body === "object" && "error" in body && body.error && typeof body.error === "object" && "code" in body.error
        ? String(body.error.code) : "";
      if (upstream.status === 429) return failure("RateLimitExceeded", 429, identifier);
      if (["ConnectionClaimsRequired", "GraphClaimsChallenge", "ConnectionReauthenticationRequired", "insufficient_claims", "GraphReauthenticationRequired"].includes(code)) return failure("GraphReauthenticationRequired", 403, identifier);
      if (upstream.status === 403) return failure("GraphAccessDenied", 403, identifier);
      if (upstream.status === 401) return failure("ApiKeyUnavailable", 403, identifier);
      return failure("GatewayUnavailable", 503, identifier);
    }
    const profile = graphToolProfileSchema.parse(body);
    if (profile.id.toLowerCase() !== owner.objectId.toLowerCase()) return failure("ConnectionIdentityMismatch", 403, identifier);
    return Response.json(graphToolCompleteSchema.parse({ status: "complete", profile, durationMs: Math.max(0, Math.round(performance.now() - started)),
      sessionUpdatePending: result.sessionUpdatePending, ...(identifier ? { requestId: identifier } : {}) }), { headers: responseHeaders });
  } catch (error) {
    if (error instanceof ConnectionSessionError) return connectionFailure(error);
    return failure("GatewayResponseInvalid", 502);
  }
}

/** ポーリングは完了状態の確認だけを行い、Graphの再実行や有効期限の延長は行いません。 */
export async function handleGraphToolConnection(request: Request, dependencies: GraphToolDependencies): Promise<Response> {
  const value = await input(request, dependencies, true);
  if (value instanceof Response) return value;
  const owner = await actor(dependencies);
  if (owner instanceof Response) return owner;
  const gateway = await client(dependencies, owner, value.apiKeyId);
  if (gateway instanceof Response) return gateway;
  try { return Response.json(graphToolStatusSchema.parse({ status: await gateway.poll() }), { headers: responseHeaders }); }
  catch (error) {
    if (error instanceof ConnectionSessionError && ["AuthenticationTransactionExpired", "NoAuthenticationTransaction"].includes(error.code)) {
      return Response.json({ status: "expired" }, { headers: responseHeaders });
    }
    return connectionFailure(error);
  }
}
