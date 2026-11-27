import "server-only";

import type { ApiKeyActor } from "../api-keys/contracts.ts";
import { getPortalApiAuthentication, type PortalBearer } from "./service.ts";
import { requireApiAuthSession } from "./session.ts";
import { ApiAuthenticationError } from "./settings.ts";

/** 現在の本人セッションに紐づく購読管理専用の委任トークンを取得します。 */
export async function readSubscriptionManagementBearer(request: Request | Headers, actor: ApiKeyActor): Promise<PortalBearer> {
  const session = await requireApiAuthSession(request instanceof Request ? request.headers : request);
  if (session.tenantId.toLowerCase() !== actor.tenantId.toLowerCase() ||
      session.objectId.toLowerCase() !== actor.objectId.toLowerCase()) {
    throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
  }
  return getPortalApiAuthentication("subscription-management").bearer(session);
}
