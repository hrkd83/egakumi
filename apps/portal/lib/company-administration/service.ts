import "server-only";
import { z } from "zod";
import { getAuthEnvironment } from "../auth-environment.ts";
import { getApiKeyAdministrationService } from "../api-key-administration/service.ts";
import { ApiKeyError, type ApiKeyActor } from "../api-keys/contracts.ts";
import { getCompanyRepository, type CompanyRepository } from "./repository.ts";
import { createCompanySchema, updateCompanySchema, type PortalCompany, type CreateCompanyInput, type UpdateCompanyInput } from "./contracts.ts";
/** Graphの現在所属とSQLを単独で検証できる依存です。 */
export type CompanyAdministrationDependencies = { tenantId:string; repository:CompanyRepository; isPlatformAdmin(actor:ApiKeyActor):Promise<boolean> };
/** 会社を統計用の選択肢として管理し、本人所属とキー所有権を変更しません。 */
export class CompanyAdministrationService {
  private readonly dependencies:CompanyAdministrationDependencies;
  constructor(dependencies:CompanyAdministrationDependencies) {
    if (!z.uuid().safeParse(dependencies.tenantId).success) throw new ApiKeyError("ConfigurationUnavailable",503);
    this.dependencies=dependencies;
  }
  /** クライアント申告のテナントや別人IDを管理処理へ渡しません。 */
  private actor(input:ApiKeyActor):ApiKeyActor {
    if (!z.uuid().safeParse(input.tenantId).success || !z.uuid().safeParse(input.objectId).success
      || input.tenantId.toLowerCase()!==this.dependencies.tenantId.toLowerCase()) throw new ApiKeyError("AccessDenied",403);
    return {tenantId:input.tenantId.toLowerCase(),objectId:input.objectId.toLowerCase()};
  }
  /** 管理操作のたびに現在の管理グループ所属を確認します。 */
  private async authorize(input:ApiKeyActor):Promise<ApiKeyActor> {
    const actor=this.actor(input);
    if (!await this.dependencies.isPlatformAdmin(actor)) throw new ApiKeyError("AccessDenied",403);
    return actor;
  }
  /** 認証済みの利用者へ、新規発行で選択できる有効会社だけを返します。 */
  async available(input:ApiKeyActor):Promise<{companies:PortalCompany[]}> {
    const actor=this.actor(input); return {companies:await this.dependencies.repository.list(actor.tenantId,true)};
  }
  /** 管理者へ無効会社を含む一覧を返します。 */
  async overview(input:ApiKeyActor):Promise<{companies:PortalCompany[]}> {
    const actor=await this.authorize(input); return {companies:await this.dependencies.repository.list(actor.tenantId,false)};
  }
  /** 管理者が実際の会社名を登録します。構築時の会社seedは使いません。 */
  async create(input:ApiKeyActor,values:CreateCompanyInput):Promise<PortalCompany> {
    const actor=await this.authorize(input), parsed=createCompanySchema.safeParse(values);
    if (!parsed.success) throw new ApiKeyError("InvalidRequest",400);
    return this.dependencies.repository.create(actor,parsed.data);
  }
  /** 既存参照を維持したまま、名前と有効状態を更新します。 */
  async update(input:ApiKeyActor,id:string,values:UpdateCompanyInput):Promise<PortalCompany> {
    const actor=await this.authorize(input), parsed=updateCompanySchema.safeParse(values);
    if (!z.uuid().safeParse(id).success) throw new ApiKeyError("NotFound",404);
    if (!parsed.success) throw new ApiKeyError("InvalidRequest",400);
    return this.dependencies.repository.update(actor,id.toLowerCase(),parsed.data);
  }
}
let service:CompanyAdministrationService|undefined;
/** 認証済みリクエストでだけ、現在所属を確認するサービスを構成します。 */
export function getCompanyAdministrationService():CompanyAdministrationService {
  return service??=new CompanyAdministrationService({tenantId:getAuthEnvironment().microsoftTenantId,
    repository:getCompanyRepository(),isPlatformAdmin:(actor)=>getApiKeyAdministrationService().isAdmin(actor)});
}
