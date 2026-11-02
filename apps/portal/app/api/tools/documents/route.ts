import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleDocumentAnalysis } from "@/lib/document-analysis-gateway";

/** 本人が選択した購読と文書で、Gatewayの文書解析APIを実行します。 */
export async function POST(request: Request): Promise<Response> {
  return handleDocumentAnalysis(request, { readSession: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL });
}
