import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleImageGeneration } from "@/lib/image-gateway";

/** 認証済みポータルの画像生成要求を、本人が選んだ購読の権限でAPIMへ渡します。 */
export async function POST(request: Request): Promise<Response> {
  return handleImageGeneration(request, { readSession: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL });
}
