import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleSubscriptionSync } from "@/lib/subscription-sync/http";
import { getSubscriptionSyncService } from "@/lib/subscription-sync/factory";

const dependencies = { readActor: readPortalPrincipal, readOrigin: () => getAuthEnvironment().baseURL, getService: getSubscriptionSyncService };
/** 基盤管理者へSQLとAPIMの全購読登録状況を返します。キー値は取得しません。 */
export async function GET(request: Request): Promise<Response> { return handleSubscriptionSync(request, dependencies); }
/** 確認内容を署名し、確定時に再照合してからSQLを基準に反映します。 */
export async function POST(request: Request): Promise<Response> { return handleSubscriptionSync(request, dependencies); }
