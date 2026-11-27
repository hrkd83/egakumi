import { z } from "zod";
import { apiAuthPurposeHeader, subscriptionManagementRequiredCode, type ApiAuthPurpose } from "./purpose.ts";

export const subscriptionManagementRequiredEvent = "egakumi:subscription-management-authentication-required";

/** APIが購読管理用の再認証を求めたときに、現在の画面へ知らせます。 */
export function notifySubscriptionManagementRequired(code: string): void {
  if (code === subscriptionManagementRequiredCode && typeof window !== "undefined") {
    window.dispatchEvent(new Event(subscriptionManagementRequiredEvent));
  }
}

/** 試用・購読管理とも、同じPortalから固定のEntra認可先へ移動します。 */
export async function startPortalApiAuthorization(returnTo: string, purpose: ApiAuthPurpose = "gateway", claims?: string): Promise<void> {
  const headers = new Headers({ "Content-Type": "application/x-www-form-urlencoded" });
  if (purpose !== "gateway") headers.set(apiAuthPurposeHeader, purpose);
  const fields = new URLSearchParams({ returnTo });
  if (claims !== undefined) fields.set("claims", claims);
  const response = await fetch("/api/api-auth/start", {
    method: "POST", headers, body: fields,
  });
  if (!response.ok) throw new Error();
  const { authorizationURL } = z.object({ authorizationURL: z.url() }).parse(await response.json());
  const target = new URL(authorizationURL);
  if (target.origin !== "https://login.microsoftonline.com" || !target.pathname.endsWith("/oauth2/v2.0/authorize")) throw new Error();
  window.location.assign(target);
}
