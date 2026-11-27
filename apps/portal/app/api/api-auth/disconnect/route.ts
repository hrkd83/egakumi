import { handleApiAuthentication } from "@/lib/api-auth/http";

/** Gateway試用の専用OAuth受付を処理します。 */
export async function POST(request: Request): Promise<Response> {
  return handleApiAuthentication(request, "disconnect");
}
