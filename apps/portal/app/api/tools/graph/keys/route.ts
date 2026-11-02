import { readPortalPrincipal } from "@/lib/auth-session";
import { getAuthEnvironment } from "@/lib/auth-environment";
import { handleGraphToolKeys } from "@/lib/graph-tool-server";

/** Graphを利用できる本人所属の購読候補だけを返し、キー実値は公開しません。 */
export async function GET(request: Request): Promise<Response> {
  return handleGraphToolKeys(request, { readActor: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL });
}
