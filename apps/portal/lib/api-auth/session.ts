import "server-only";

import { headers } from "next/headers";
import { connection } from "next/server";
import { getAuth } from "../auth";
import { getAuthEnvironment } from "../auth-environment.ts";
import { ApiAuthenticationError } from "./settings.ts";

/** SQLで検証したPortalセッションと本人です。ブラウザーには返しません。 */
export type ApiAuthSession = { sessionId: string; tenantId: string; objectId: string };

/** 通常のBetter Authセッションをキャッシュや延長なしで確認します。 */
export async function requireApiAuthSession(requestHeaders?: Headers): Promise<ApiAuthSession> {
  await connection();
  const provided = requestHeaders ?? await headers();
  if (!provided.get("cookie")?.includes("egakumi.portal")) throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
  const result = await getAuth().api.getSession({
    headers: provided, query: { disableCookieCache: true, disableRefresh: true },
  });
  const tenantId = getAuthEnvironment().microsoftTenantId;
  if (!result || !result.session.id || result.user.entraTenantId !== tenantId || !result.user.entraObjectId) {
    throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
  }
  return { sessionId: result.session.id, tenantId, objectId: result.user.entraObjectId };
}
