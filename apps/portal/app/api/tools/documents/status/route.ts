import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleDocumentAnalysisStatus } from "@/lib/document-analysis-gateway";

/** 本人に束縛した参照を使って解析状況を確認し、完了時は結果を返します。 */
export async function POST(request: Request): Promise<Response> {
  return handleDocumentAnalysisStatus(request, { readSession: readPortalPrincipal, readPortalOrigin: () => getAuthEnvironment().baseURL, readHandleSecret: () => getAuthEnvironment().secret });
}
