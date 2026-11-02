import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleSpeech } from "@/lib/speech-gateway";

/** 認証済みPortalで選んだ購読を使い、アップロード音声または入力文をAPIM経由で処理します。 */
export async function POST(request: Request): Promise<Response> {
  return handleSpeech(request, { readSession: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL });
}
