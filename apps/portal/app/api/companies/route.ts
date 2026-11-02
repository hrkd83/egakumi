import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleCompanies } from "@/lib/company-administration/http";
import { getCompanyAdministrationService } from "@/lib/company-administration/service";
const dependencies={readActor:readPortalPrincipal,readOrigin:()=>getAuthEnvironment().baseURL,getService:getCompanyAdministrationService};
/** 発行時に選択できる、有効な会社だけを返します。 */
export async function GET(request:Request):Promise<Response>{return handleCompanies(request,dependencies);}
