import { isAllowedAuthRoute, parsePortalSignIn } from "./auth-routes.ts";

/** 認証ライブラリの起動と信頼する公開originを、HTTP境界の検査から分離します。 */
export type PortalAuthHttpDependencies = {
  readOrigin(): string;
  handle(request: Request): Promise<Response>;
};

const responseHeaders = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff" };

/** 秘密や内部例外を含めず、認証HTTP境界の拒否理由だけを返します。 */
function failure(code: string, status: number): Response {
  return Response.json({ error: { code } }, { status, headers: responseHeaders });
}

/**
 * 公開する認証操作・戻り先・POSTの送信元を検証してからBetter Authへ渡します。
 * @remarks Cookieがない初回ログインでもOriginを必須にします。Fetch Metadataがある場合は
 * same-originだけを受理し、信頼するoriginをHostや転送ヘッダーから推測しません。
 * MicrosoftからのGETコールバックには、ブラウザーPOST向けのOrigin制約を適用しません。
 */
export async function handlePortalAuth(request: Request, dependencies: PortalAuthHttpDependencies): Promise<Response> {
  const pathname = new URL(request.url).pathname;
  if (!isAllowedAuthRoute(pathname, request.method)) return failure("NotFound", 404);
  try {
    if (request.method === "POST") {
      const fetchSite = request.headers.get("sec-fetch-site");
      if (request.headers.get("origin") !== dependencies.readOrigin() || (fetchSite !== null && fetchSite !== "same-origin")) {
        return failure("OriginNotAllowed", 403);
      }
    }
    if (pathname === "/api/auth/sign-in/social") {
      let body;
      try { body = parsePortalSignIn(await request.json()); } catch { body = null; }
      if (!body) return failure("InvalidSignInRequest", 400);
      const headers = new Headers(request.headers);
      headers.delete("Content-Length");
      headers.set("Content-Type", "application/json");
      request = new Request(request.url, { method: request.method, headers, body: JSON.stringify(body), signal: request.signal });
    }
    const response = await dependencies.handle(request);
    const headers = new Headers(response.headers);
    for (const [name, value] of Object.entries(responseHeaders)) headers.set(name, value);
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  } catch { return failure("AuthenticationUnavailable", 503); }
}
