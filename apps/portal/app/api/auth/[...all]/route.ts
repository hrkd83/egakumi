import { getAuth } from "@/lib/auth";
import { getAuthEnvironment } from "@/lib/auth-environment";
import { handlePortalAuth } from "@/lib/auth-http";

/**
 * 認証のリクエストを、サーバー上のBetter Authへ受け渡します。
 * @param request - Microsoftからの戻り、ログイン開始、セッション確認などのリクエスト。
 * @returns Cookieを含む認証の応答。内部構成やSQLのエラー内容は外部へ返しません。
 */
async function handleAuth(request: Request) {
  return handlePortalAuth(request, {
    readOrigin: () => getAuthEnvironment().baseURL,
    handle: (validated) => getAuth().handler(validated),
  });
}

/** Better Authのセッション確認とMicrosoftコールバックを処理します。 */
export const GET = handleAuth;

/** Better Authのログイン開始・ログアウトを処理します。 */
export const POST = handleAuth;
