import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handlePiiRedaction } from "@/lib/pii-gateway";

/** 本人が選択した購読で、日本語または英語の本文をマスキングします。 */
export async function POST(request: Request): Promise<Response> {
  return handlePiiRedaction(request, { readSession: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL });
}
