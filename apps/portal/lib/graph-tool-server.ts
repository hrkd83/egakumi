import "server-only";
import { isAllowedPortalQuery } from "./portal-query.ts";

import { ApiAuthenticationError, apiAuthenticationFailure, assertGatewayCredential, resolveGatewayCredential, watchGatewayCredential,
  type GatewayActor, type GatewayCredential } from "./api-auth/gateway-credential.ts";
import { decodeApiClaims } from "./api-auth/oauth.ts";
import type { PortalBearer } from "./api-auth/service.ts";
import { GRAPH_TOOL_PATH, graphToolCompleteSchema, graphToolInputSchema, graphToolProfileSchema } from "./graph-tool-contracts.ts";
import { getPublicModelGatewayOrigin } from "./model-public-config.ts";

const responseHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };
const maximumBodyBytes = 4096;

/** 画面や任意のHTTPヘッダーではなく、サーバーの設定から決めるGraph接続先です。 */
export type GraphToolEnvironment = { origin: string };

/** 実際の共有セッションクライアントを通したまま、認証・認可・SQL・上流通信を検証する境界です。 */
export type GraphToolDependencies = {
  readActor: () => Promise<GatewayActor | null>;
  readPortalOrigin: () => string;
  resolveBearer?: (request: Request, actor: GatewayActor, origin: string) => Promise<PortalBearer>;
  authorizeBearer?: (credential: GatewayCredential) => Promise<void>;
  readGateway?: () => GraphToolEnvironment;
  fetch?: typeof globalThis.fetch;
};

/** Graphの公開Gatewayを読み、未設定や任意URLへの迂回を拒否します。 */
export function getGraphToolEnvironment(environment: Readonly<Record<string, string | undefined>> = process.env): GraphToolEnvironment {
  const origin = getPublicModelGatewayOrigin(environment);
  if (!origin) throw new Error("Invalid Gateway configuration");
  return { origin };
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

/** 固定APIMから返った同一テナントの追加認証要求だけを、OAuth開始用に整形します。 */
function graphClaimsChallenge(headers: Headers, tenantId: string): string | undefined {
  const value = headers.get("www-authenticate");
  if (!value || value.length > 4_400) return undefined;
  const prefix = `Bearer authorization_uri="https://login.microsoftonline.com/${tenantId.toLowerCase()}/oauth2/v2.0/authorize", error="insufficient_claims", claims="`;
  if (!value.startsWith(prefix) || !value.endsWith('"')) return undefined;
  const claims = value.slice(prefix.length, -1);
  if (!/^[A-Za-z0-9+/]{1,4096}={0,2}$/.test(claims)) return undefined;
  const decoded = Buffer.from(claims, "base64");
  if (decoded.length > 3_072 || decoded.toString("base64") !== claims) return undefined;
  try { return Buffer.from(decodeApiClaims(decoded.toString("base64url")), "utf8").toString("base64url"); }
  catch { return undefined; }
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

/** Portalの本人認証を先に確認し、ブラウザーが申告した本人IDは利用しません。 */
async function actor(dependencies: GraphToolDependencies): Promise<GatewayActor | Response> {
  try { return await dependencies.readActor() ?? failure("AuthenticationRequired", 401); }
  catch { return failure("AuthenticationUnavailable", 503); }
}

/** 更新を伴うBFF要求は、認証Cookieに加えて同一Originを必須とします。 */
async function input(request: Request, dependencies: GraphToolDependencies): Promise<GatewayActor | Response> {
  let origin: string;
  try { origin = new URL(dependencies.readPortalOrigin()).origin; }
  catch { return failure("GatewayNotConfigured", 503); }
  const fetchSite = request.headers.get("sec-fetch-site");
  if (request.method !== "POST" || request.headers.get("origin") !== origin || (fetchSite && fetchSite !== "same-origin")) {
    return failure("RequestNotAllowed", 403);
  }
  const owner = await actor(dependencies);
  if (owner instanceof Response) return owner;
  if (!isAllowedPortalQuery(request)) return failure("InvalidRequest", 400);
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") return failure("InvalidRequest", 415);
  if (Number(request.headers.get("content-length") ?? 0) > maximumBodyBytes) return failure("InvalidRequest", 413);
  try {
    const value = await readBody(request.body, maximumBodyBytes);
    graphToolInputSchema.parse(value);
    return owner;
  } catch { return failure("InvalidRequest", 400); }
}

/** 本人のBearerを固定Graph OBO Gatewayへ送り、追加認証が必要なら構造化した要求を返します。 */
export async function handleGraphToolProfile(request: Request, dependencies: GraphToolDependencies): Promise<Response> {
  const owner = await input(request, dependencies);
  if (owner instanceof Response) return owner;
  let environment: GraphToolEnvironment;
  try { environment = (dependencies.readGateway ?? getGraphToolEnvironment)(); }
  catch { return failure("GatewayNotConfigured", 503); }
  const started = performance.now();
  const lifetime = new AbortController();
  try {
    const resolveBearer = dependencies.resolveBearer;
    const credential = await resolveGatewayCredential(request, owner, environment.origin,
      resolveBearer && (() => resolveBearer(request, owner, environment.origin)));
    const signal = watchGatewayCredential(credential, AbortSignal.any([request.signal, lifetime.signal]));
    const authorize = async () => {
      try { await (dependencies.authorizeBearer ?? assertGatewayCredential)(credential); }
      catch (error) {
        if (error instanceof ApiAuthenticationError) throw error;
        throw new ApiAuthenticationError("ApiAuthenticationUnavailable", 503);
      }
      if (signal.aborted) throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
    };
    await authorize();
    let upstream: Response;
    try {
      upstream = await (dependencies.fetch ?? fetch)(new URL(GRAPH_TOOL_PATH, environment.origin), {
        method: "GET", headers: credential.headers, redirect: "error", cache: "no-store",
        signal: AbortSignal.any([signal, AbortSignal.timeout(90_000)]),
      });
    } catch {
      await authorize();
      return failure("GatewayUnavailable", 503);
    }
    const identifier = requestId(upstream.headers);
    const body = await readBody(upstream.body, 65_536);
    await authorize();
    if (identifier?.includes(credential.value) || JSON.stringify(body).includes(credential.value)) {
      return failure("GatewayResponseInvalid", 502);
    }
    if (!upstream.ok) {
      const code = body && typeof body === "object" && "error" in body && body.error && typeof body.error === "object" && "code" in body.error
        ? String(body.error.code) : "";
      if (upstream.status === 429) return failure("RateLimitExceeded", 429, identifier);
      if (upstream.status === 401 && code === "GRAPH_REAUTHENTICATION_REQUIRED") {
        const claims = graphClaimsChallenge(upstream.headers, owner.tenantId);
        if (claims) {
          return Response.json({ error: { code: "GraphReauthenticationRequired" }, claims, ...(identifier ? { requestId: identifier } : {}) },
            { status: 403, headers: responseHeaders });
        }
      }
      if (["ConnectionClaimsRequired", "GraphClaimsChallenge", "ConnectionReauthenticationRequired", "insufficient_claims", "GraphReauthenticationRequired", "GRAPH_REAUTHENTICATION_REQUIRED"].includes(code)) return failure("GraphReauthenticationRequired", 403, identifier);
      if (upstream.status === 403) return failure("GraphAccessDenied", 403, identifier);
      if (upstream.status === 401) return failure("ApiAuthenticationRequired", 401, identifier);
      return failure("GatewayUnavailable", 503, identifier);
    }
    const profile = graphToolProfileSchema.parse(body);
    if (profile.id.toLowerCase() !== owner.objectId.toLowerCase()) return failure("ConnectionIdentityMismatch", 403, identifier);
    return Response.json(graphToolCompleteSchema.parse({ status: "complete", profile, durationMs: Math.max(0, Math.round(performance.now() - started)),
      ...(identifier ? { requestId: identifier } : {}) }), { headers: responseHeaders });
  } catch (error) {
    const denied = apiAuthenticationFailure(error);
    return denied ? failure(denied.code, denied.status) : failure("GatewayResponseInvalid", 502);
  } finally { lifetime.abort(); }
}
