import { z } from "zod";

/** 試用の追加認証を開始し、固定のEntra認可先へ移動します。 */
export async function startPortalApiAuthorization(returnTo: string, claims?: string): Promise<void> {
  const headers = new Headers({ "Content-Type": "application/x-www-form-urlencoded" });
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
