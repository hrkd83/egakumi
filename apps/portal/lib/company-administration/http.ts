import "server-only";
import { ApiKeyError, type ApiKeyActor } from "../api-keys/contracts.ts";
import { createCompanySchema, updateCompanySchema, type PortalCompany, type CreateCompanyInput, type UpdateCompanyInput } from "./contracts.ts";

/** HTTPから公開する会社操作です。管理者認可は各サービス操作で必ず行います。 */
export type CompanyOperations={available(actor:ApiKeyActor):Promise<{companies:PortalCompany[]}>;
  overview(actor:ApiKeyActor):Promise<{companies:PortalCompany[]}>;
  create(actor:ApiKeyActor,input:CreateCompanyInput):Promise<PortalCompany>;
  update(actor:ApiKeyActor,id:string,input:UpdateCompanyInput):Promise<PortalCompany>};
/** HTTP入口の依存を分離し、未認証やCSRFをDBへ到達前に拒否します。 */
export type CompanyHttpDependencies={readActor():Promise<ApiKeyActor|null>;readOrigin():string;getService():CompanyOperations};
const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer"};
/** 本文ヘッダーに依存せず、小さな管理JSONだけを受け付けます。 */
async function readJson(request:Request):Promise<unknown> {
  if (request.headers.get("content-type")?.split(";",1)[0].trim().toLowerCase()!=="application/json") throw new ApiKeyError("InvalidRequest",400);
  const reader=request.body?.getReader(); if (!reader) throw new ApiKeyError("InvalidRequest",400);
  const decoder=new TextDecoder("utf-8",{fatal:true}); let text="",bytes=0;
  try { while(true) { const part=await reader.read(); if(part.done)break; bytes+=part.value.byteLength;
    if(bytes>2048)throw new Error(); text+=decoder.decode(part.value,{stream:true}); }
    return JSON.parse(text+decoder.decode());
  } catch { throw new ApiKeyError("InvalidRequest",400); }
  finally { await reader.cancel().catch(()=>undefined);reader.releaseLock(); }
}
/** 一般利用者の有効一覧と、基盤管理者の会社管理を同じ安全なHTTP契約で分けます。 */
export async function handleCompanies(request:Request,dependencies:CompanyHttpDependencies,management=false,id?:string):Promise<Response> {
  try {
    const allowed=id?["PATCH"]:management?["GET","POST"]:["GET"];
    if(!allowed.includes(request.method)) return Response.json({error:{code:"MethodNotAllowed"}},{status:405,headers:{...headers,Allow:allowed.join(", ")}});
    const actor=await dependencies.readActor(); if(!actor)throw new ApiKeyError("AuthenticationRequired",401);
    if(request.headers.get("sec-fetch-site")==="cross-site" || (request.method!=="GET" && request.headers.get("origin")!==dependencies.readOrigin())) throw new ApiKeyError("OriginNotAllowed",403);
    if(new URL(request.url).search)throw new ApiKeyError("InvalidRequest",400);
    const service=dependencies.getService();
    if(request.method==="GET")return Response.json(await (management?service.overview(actor):service.available(actor)),{headers});
    const body=await readJson(request);
    if(id){ const parsed=updateCompanySchema.safeParse(body);if(!parsed.success)throw new ApiKeyError("InvalidRequest",400);
      return Response.json({company:await service.update(actor,id,parsed.data)},{headers}); }
    const parsed=createCompanySchema.safeParse(body);if(!parsed.success)throw new ApiKeyError("InvalidRequest",400);
    return Response.json({company:await service.create(actor,parsed.data)},{status:201,headers});
  }catch(error){const safe=error instanceof ApiKeyError?error:new ApiKeyError("ServiceUnavailable",503);
    return Response.json({error:{code:safe.code}},{status:safe.status,headers});}
}
