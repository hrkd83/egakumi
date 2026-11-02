import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleCompanies } from "@/lib/company-administration/http";
import { getCompanyAdministrationService } from "@/lib/company-administration/service";
const dependencies={readActor:readPortalPrincipal,readOrigin:()=>getAuthEnvironment().baseURL,getService:getCompanyAdministrationService};
/** 既存の会社参照を保持したまま、改名・無効化します。 */
export async function PATCH(request:Request,context:{params:Promise<{id:string}>}):Promise<Response>{
  return handleCompanies(request,dependencies,true,(await context.params).id);
}
