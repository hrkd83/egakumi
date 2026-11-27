import "server-only";

import { isPortalReturnPath } from "../auth-navigation.ts";
import { decodeApiClaims } from "./oauth.ts";
import { ApiAuthenticationError, readLimitedAuthBody } from "./settings.ts";
import { getPortalApiAuthentication } from "./service.ts";
import { requireApiAuthSession } from "./session.ts";

const correlationCookie = "__Secure-egakumi.api-auth";
const cookiePath = "/api/api-auth";
const responseHeaders = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff" };
const correlationFormat = /^[A-Za-z0-9_-]{43}$/;

/** OAuth受付の各ルートを別に保ち、許可したHTTP操作だけへ結び付けます。 */
type ApiAuthAction = "start" | "callback" | "complete" | "status" | "disconnect";

function errorResponse(code: string, status: number): Response {
  return Response.json({ error: { code } }, { headers: responseHeaders, status });
}

function cookie(value: string, seconds: number): string {
  return `${correlationCookie}=${value}; Path=${cookiePath}; Max-Age=${seconds}; Secure; HttpOnly; SameSite=None`;
}

function getCorrelation(request: Request): string | undefined {
  const values = (request.headers.get("cookie") ?? "").split(";").map((part) => part.trim())
    .filter((part) => part.startsWith(`${correlationCookie}=`));
  const value = values.length === 1 ? values[0].slice(correlationCookie.length + 1) : "";
  return correlationFormat.test(value) ? value : undefined;
}

function redirect(url: URL, correlation: string, seconds: number): Response {
  return new Response(null, { status: 303, headers: { ...responseHeaders, Location: url.href, "Set-Cookie": cookie(correlation, seconds) } });
}

function returnURL(origin: string, path: string, connected: boolean): URL {
  const result = new URL(safePath(path) ?? "/ja/models", origin);
  result.searchParams.set("api-auth", connected ? "connected" : "failed");
  return result;
}

function safePath(value: string | null): string | undefined {
  const locale = value?.split("/")[1];
  return (locale === "ja" || locale === "en") && isPortalReturnPath(value ?? undefined, locale) ? value! : undefined;
}

async function form(request: Request, length: number): Promise<URLSearchParams> {
  const type = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (type !== "application/x-www-form-urlencoded" || Number(request.headers.get("content-length") ?? 0) > length) {
    throw new ApiAuthenticationError("ApiAuthorizationInvalid", 400);
  }
  try { return new URLSearchParams(await readLimitedAuthBody(request.body, length, AbortSignal.any([request.signal, AbortSignal.timeout(10_000)]))); }
  catch { throw new ApiAuthenticationError("ApiAuthorizationInvalid", 400); }
}

/** OAuth callback以外の変更は固定Portal originに限定します。 */
function sameOrigin(request: Request, origin: string): boolean {
  const metadata = request.headers.get("sec-fetch-site");
  return request.headers.get("origin") === origin && (metadata === null || metadata === "same-origin");
}

/** 同一オリジンの操作と、相関値を要求するEntraのform_postを独立に検査します。 */
export async function handleApiAuthentication(request: Request, action: ApiAuthAction): Promise<Response> {
  try {
    if (new URL(request.url).search) return errorResponse("ApiAuthorizationInvalid", 400);
    const correlation = getCorrelation(request);
    if ((action === "callback" || action === "complete") && !correlation) return errorResponse("ApiAuthorizationInvalid", 400);
    const service = getPortalApiAuthentication();
    const origin = service.settings.auth.baseURL;
    if (action === "callback") {
      const incomingOrigin = request.headers.get("origin");
      // ブラウザーが参照元を隠す場合も、state・Cookie・完了時のSQL本人照合は必須です。
      if (incomingOrigin !== null && incomingOrigin !== "null" && incomingOrigin !== new URL(service.settings.entra).origin) {
        return errorResponse("ApiAuthorizationInvalid", 403);
      }
      const fields = await form(request, 32_768);
      const state = fields.get("state");
      const code = fields.get("code");
      const errors = fields.getAll("error");
      if (!state || !correlationFormat.test(state) || fields.getAll("state").length !== 1 ||
          fields.getAll("code").length > 1 || errors.length > 1 || Boolean(code) === Boolean(errors.length) ||
          [...fields.keys()].some((key) => !["state", "code", "error", "error_description", "error_uri", "session_state", "client_info"].includes(key))) {
        return errorResponse("ApiAuthorizationInvalid", 400);
      }
      const outcome = await service.callback(state, correlation!, code);
      if (outcome.complete) return redirect(new URL(`${cookiePath}/complete`, origin), correlation!, 60);
      return redirect(returnURL(origin, outcome.returnTo, false), "", 0);
    }
    if ((action === "start" || action === "disconnect") && !sameOrigin(request, origin)) {
      return errorResponse("ApiAuthorizationInvalid", 403);
    }
    if (action === "status") {
      const metadata = request.headers.get("sec-fetch-site");
      if (metadata && metadata !== "same-origin") return errorResponse("ApiAuthorizationInvalid", 403);
    }
    const session = await requireApiAuthSession(request.headers);
    if (action === "complete") {
      const outcome = await service.complete(session, correlation!);
      return redirect(returnURL(origin, outcome.returnTo, outcome.connected), "", 0);
    }
    if (action === "start") {
      const fields = await form(request, 8192);
      const returnTo = safePath(fields.get("returnTo"));
      const challenges = fields.getAll("claims");
      if (!returnTo || fields.getAll("returnTo").length !== 1 || challenges.length > 1 ||
        fields.size !== 1 + challenges.length) return errorResponse("ApiAuthorizationInvalid", 400);
      const claims = challenges.length ? decodeApiClaims(challenges[0]) : undefined;
      const started = await service.start(session, returnTo, claims);
      return Response.json({ authorizationURL: started.authorizationURL.href }, {
        headers: { ...responseHeaders, "Set-Cookie": cookie(started.correlation, 600) },
      });
    }
    if (action === "disconnect") {
      await service.disconnect(session);
      const headers = new Headers(responseHeaders);
      headers.set("Set-Cookie", cookie("", 0));
      return Response.json({ status: "disconnected" }, { headers });
    }
    return Response.json({ status: await service.status(session) }, { headers: responseHeaders });
  } catch (error) {
    if (error instanceof ApiAuthenticationError) return errorResponse(error.code, error.status);
    return errorResponse("ApiAuthenticationUnavailable", 503);
  }
}
