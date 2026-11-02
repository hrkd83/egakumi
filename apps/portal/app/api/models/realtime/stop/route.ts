import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleRealtimeStop } from "@/lib/realtime-webrtc";

/** 本人に束縛した暗号化参照と現在の購読権限を確認し、登録済み通話だけを停止します。 */
export async function POST(request: Request): Promise<Response> {
  return handleRealtimeStop(request, { readSession: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL });
}
