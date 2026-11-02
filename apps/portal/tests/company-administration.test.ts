import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiKeyError } from "../lib/api-keys/contracts.ts";
import { createCompanySchema, updateCompanySchema, type PortalCompany } from "../lib/company-administration/contracts.ts";
import { CompanyAdministrationService } from "../lib/company-administration/service.ts";
import { handleCompanies } from "../lib/company-administration/http.ts";

const tenantId="11111111-1111-4111-8111-111111111111",actor={tenantId,objectId:"22222222-2222-4222-8222-222222222222"};
const company:PortalCompany={id:"33333333-3333-4333-8333-333333333333",name:"Example",isActive:true,createdAt:"2026-10-30T00:00:00.000Z",updatedAt:"2026-10-30T00:00:00.000Z"};
const errorCode=(code:string)=>(error:unknown)=>error instanceof ApiKeyError&&error.code===code;
/** 永続化や認可を通過した呼出しだけを記録します。 */
function fixture(){let admin=true,unavailable=false;const calls:string[]=[];
 const service=new CompanyAdministrationService({tenantId,isPlatformAdmin:async()=>{calls.push("authorize");if(unavailable)throw new ApiKeyError("AuthorizationUnavailable",503);return admin;},
 repository:{list:async(_tenant,active)=>{calls.push(active?"active-list":"all-list");return [company];},
 create:async(_actor,input)=>{calls.push("create");return {...company,...input};},update:async(_actor,_id,input)=>{calls.push("update");return {...company,name:input.name,isActive:input.isActive};},assertReady:async()=>undefined}});
 return {service,calls,set admin(value:boolean){admin=value;},set unavailable(value:boolean){unavailable=value;}};
}
test("会社名は正規化し、不明な入力や不正な版を受け付けない",()=>{
 assert.equal(createCompanySchema.parse({name:"  Ａ社　 国内   "}).name,"A社 国内");
 assert.equal(createCompanySchema.safeParse({name:"   "}).success,false);
 assert.equal(createCompanySchema.safeParse({name:"x".repeat(161)}).success,false);
 assert.equal(createCompanySchema.safeParse({name:"Test",tenantId}).success,false);
 assert.equal(updateCompanySchema.safeParse({name:"Test",isActive:false,expectedUpdatedAt:"invalid"}).success,false);
});
test("認証済み利用者は有効会社だけ、現在の管理者は全件を参照できる",async()=>{
 const app=fixture();assert.deepEqual(await app.service.available(actor),{companies:[company]});assert.deepEqual(app.calls,["active-list"]);
 await app.service.overview(actor);assert.deepEqual(app.calls.slice(-2),["authorize","all-list"]);
 app.admin=false;await assert.rejects(app.service.overview(actor),errorCode("AccessDenied"));
 await assert.rejects(app.service.create(actor,{name:"Blocked"}),errorCode("AccessDenied"));
 await assert.rejects(app.service.update(actor,company.id,{name:company.name,isActive:false,expectedUpdatedAt:company.updatedAt}),errorCode("AccessDenied"));
 assert.equal(app.calls.includes("create"),false);assert.equal(app.calls.includes("update"),false);
});
test("管理グループ脱退とGraph障害を操作ごとに反映し、DBへ書き込まない",async()=>{
 const app=fixture();await app.service.create(actor,{name:"Initial"});app.unavailable=true;
 await assert.rejects(app.service.update(actor,company.id,{name:"Changed",isActive:false,expectedUpdatedAt:company.updatedAt}),errorCode("AuthorizationUnavailable"));
 assert.equal(app.calls.includes("update"),false);
 await assert.rejects(app.service.available({...actor,tenantId:actor.objectId}),errorCode("AccessDenied"));
});
test("会社HTTPは未認証・CSRF・入力注入を拒否し、一覧・登録・版付き更新を分ける",async()=>{
 const app=fixture(),origin="https://portal.example.invalid";let signedIn=true;
 const dependencies={readActor:async()=>signedIn?actor:null,readOrigin:()=>origin,getService:()=>app.service};
 const request=(method:string,body?:unknown,override?:string)=>new Request(origin+"/api/company-administration",{method,headers:{origin:override??origin,"Content-Type":"application/json"},...(body?{body:JSON.stringify(body)}:{})});
 signedIn=false;assert.equal((await handleCompanies(request("POST",{name:"Test"}),dependencies,true)).status,401);assert.deepEqual(app.calls,[]);
 signedIn=true;assert.equal((await handleCompanies(request("POST",{name:"Test"},"https://foreign.invalid"),dependencies,true)).status,403);
 assert.equal((await handleCompanies(request("POST",{name:"Test",isActive:true}),dependencies,true)).status,400);
 assert.equal((await handleCompanies(request("GET"),dependencies)).status,200);
 assert.equal((await handleCompanies(request("POST",{name:"Test"}),dependencies,true)).status,201);
 assert.equal((await handleCompanies(request("PATCH",{name:"New",isActive:false,expectedUpdatedAt:company.updatedAt}),dependencies,true,company.id)).status,200);
 assert.equal((await handleCompanies(request("DELETE"),dependencies,true,company.id)).status,405);
});
