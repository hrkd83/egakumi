import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { handleCompanies } from "@/lib/company-administration/http";
import { getCompanyAdministrationService } from "@/lib/company-administration/service";
const dependencies={readActor:readPortalPrincipal,readOrigin:()=>getAuthEnvironment().baseURL,getService:getCompanyAdministrationService};
/** 基盤管理者にだけ無効会社を含む一覧を返します。 */
export async function GET(request:Request):Promise<Response>{return handleCompanies(request,dependencies,true);}
/** 現在の基盤管理者が、会社を登録します。 */
export async function POST(request:Request):Promise<Response>{return handleCompanies(request,dependencies,true);}
