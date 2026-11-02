import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { sql } from "kysely";
import { createLocalSqlTestDatabase, localSqlTest } from "./helpers/local-sql.ts";
import { ApiKeyError,createApiKeySchema } from "../lib/api-keys/contracts.ts";
import { configureApiKeyGroups } from "../scripts/api-key-group-config.ts";
import { configureApiKeyAdministration } from "../scripts/api-key-administration-config.ts";
import { SqlCompanyRepository } from "../lib/company-administration/repository.ts";
import { SqlApiKeyRepository } from "../lib/api-keys/repository.ts";
const errorCode=(code:string)=>(error:unknown)=>error instanceof ApiKeyError&&error.code===code;

test("SQLの会社台帳・監査・利用属性・再試行・無効化を検証する",localSqlTest,async(context)=>{
 const {admin,runtime}=await createLocalSqlTestDatabase(context);
 const tenantId=randomUUID(),groupId=randomUUID(),actor={tenantId,objectId:randomUUID()},keys=new SqlApiKeyRepository(runtime),companies=new SqlCompanyRepository(runtime);
 await companies.assertReady();
 const verificationGroups=[{groupId,products:[{productId:"model-users",maxSubscriptions:5},{productId:"pii-users",maxSubscriptions:5},{productId:"connection-users",maxSubscriptions:5}]}];
 await configureApiKeyGroups(admin,tenantId,verificationGroups);
 await configureApiKeyAdministration(admin,{tenantId,platformGroupId:groupId,groupNamePattern:"^ORG_",products:[{productId:"released-apis",displayName:"Released APIs",maxSubscriptions:5}]});
 await keys.ensureRegistry(tenantId,[{id:groupId,displayName:"ORG_CompanyTest",securityEnabled:true}]);
 await keys.observeGroups(tenantId,[{id:groupId,displayName:"ORG_CompanyTest",securityEnabled:true,status:"active",checkedAt:new Date().toISOString(),errorCode:null}]);
 assert.ok((await keys.getGroups(tenantId))[0]?.products.some(p=>p.id==="released-apis"),"既存の配備グループにも通常製品を補充する");
 const company=await companies.create(actor,{name:" Company  One "});assert.equal(company.name,"Company One");
 await assert.rejects(companies.create(actor,{name:"Ｃｏｍｐａｎｙ One"}),errorCode("CompanyNameExists"));
 const input={groupId,productId:"released-apis" as const,displayName:"Test Key",idempotencyKey:randomUUID(),clientType:"custom-system" as const,companyId:company.id,contactEmail:"team@example.invalid",systemName:"HR",teamName:"Development"};
 assert.equal(createApiKeySchema.safeParse(input).success,false,"HTTPから製品IDを指定できない");
 const reserved=await keys.reserve(actor,input);assert.equal(reserved.record.companyName,company.name);assert.equal(reserved.record.clientType,"custom-system");
 assert.equal(reserved.record.contactEmail,input.contactEmail);assert.equal((await keys.reserve(actor,input)).created,false);
 for(const changed of [{clientType:"saas" as const},{systemName:"Changed"},{teamName:"Changed"},{companyId:randomUUID()},{contactEmail:"other@example.invalid"}]){
  await assert.rejects(keys.reserve(actor,{...input,...changed}),errorCode("IdempotencyConflict"));
 }
 // 検証用3製品の設定を再適用しても、通常製品の承認・既存キー・新規発行を失いません。
 await configureApiKeyGroups(admin,tenantId,verificationGroups);
 await keys.ensureRegistry(tenantId,[{id:groupId,displayName:"ORG_CompanyTest",securityEnabled:true}]);
 assert.ok((await keys.getGroups(tenantId))[0]?.products.some(p=>p.id==="released-apis"));
 await keys.withLockedKey(actor,reserved.record.id,async context=>{
  await context.confirmGroupApproval({id:groupId,displayName:"ORG_CompanyTest",securityEnabled:true,status:"active",checkedAt:new Date().toISOString(),errorCode:null});
  await context.updateState("active");await context.markReviewed();
 });
 assert.equal((await keys.reserve(actor,{...input,idempotencyKey:randomUUID()})).created,true);
 const renamed=await companies.update(actor,company.id,{name:"Renamed",isActive:true,expectedUpdatedAt:company.updatedAt});
 await assert.rejects(companies.update(actor,company.id,{name:"Stale",isActive:false,expectedUpdatedAt:company.updatedAt}),errorCode("CompanyVersionConflict"));
 assert.equal((await keys.getKey(tenantId,reserved.record.id))?.companyName,"Renamed");
 const disabled=await companies.update(actor,company.id,{name:renamed.name,isActive:false,expectedUpdatedAt:renamed.updatedAt});assert.equal(disabled.isActive,false);
 assert.deepEqual(await companies.list(tenantId,true),[]);assert.equal((await companies.list(tenantId,false)).length,1);
 await assert.rejects(keys.reserve(actor,{...input,idempotencyKey:randomUUID()}),errorCode("CompanyNotAvailable"));
 assert.equal((await keys.reserve(actor,input)).created,false,"既存予約の再試行は会社無効化で別キーを作らない");
 await assert.rejects(keys.reserve(actor,{...input,companyId:randomUUID(),idempotencyKey:randomUUID()}),errorCode("CompanyNotAvailable"));
 assert.deepEqual(await companies.list(randomUUID(),false),[]);
 await assert.rejects(companies.update({...actor,tenantId:randomUUID()},company.id,{name:"Wrong",isActive:true,expectedUpdatedAt:disabled.updatedAt}),errorCode("NotFound"));
 const audit=(await sql<{amount:number}>`SELECT COUNT(*) AS amount FROM dbo.portalCompanyAudit WHERE tenantId=${tenantId}`.execute(admin)).rows[0];assert.equal(audit?.amount,3);
 await assert.rejects(sql`DELETE dbo.portalCompanyAudit WHERE tenantId=${tenantId}`.execute(runtime));
 await assert.rejects(sql`DELETE dbo.portalCompany WHERE tenantId=${tenantId}`.execute(runtime));
});
