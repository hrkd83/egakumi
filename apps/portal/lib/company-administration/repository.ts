import "server-only";
import { randomUUID } from "node:crypto";
import { Kysely, sql } from "kysely";
import { z } from "zod";
import { createAuthDatabase } from "../auth-database.ts";
import { getSqlEnvironment } from "../auth-environment.ts";
import { ApiKeyError, type ApiKeyActor } from "../api-keys/contracts.ts";
import { createCompanySchema, updateCompanySchema, type PortalCompany, type CreateCompanyInput, type UpdateCompanyInput } from "./contracts.ts";

/** 会社の物理削除を公開しない永続化窓口です。 */
export interface CompanyRepository {
  list(tenantId: string, activeOnly: boolean): Promise<PortalCompany[]>;
  create(actor: ApiKeyActor, input: CreateCompanyInput): Promise<PortalCompany>;
  update(actor: ApiKeyActor, id: string, input: UpdateCompanyInput): Promise<PortalCompany>;
  assertReady(): Promise<void>;
}
const guid = z.uuid().transform((value) => value.toLowerCase());
const dbGuid = (value: string) => sql`CAST(${value} AS char(36))`;
const columns = sql`id,name,isActive,CONVERT(varchar(23),createdAt,126)+'Z' AS createdAt,
  CONVERT(varchar(23),updatedAt,126)+'Z' AS updatedAt`;
/** 内部のSQL診断や個人情報を固定エラーへ置き換えます。 */
async function safe<T>(operation: () => Promise<T>): Promise<T> {
  try { return await operation(); } catch (error) {
    if (error instanceof ApiKeyError) throw error;
    throw new ApiKeyError("CompanyStoreUnavailable",503);
  }
}
/** 会社名の重複・並行編集・監査を同じSQLトランザクションで守ります。 */
export class SqlCompanyRepository implements CompanyRepository {
  private readonly database: Kysely<Record<string, unknown>>;
  constructor(database: Kysely<Record<string, unknown>>) { this.database=database; }
  /** 有効な会社だけの選択肢、または管理者用の全件を返します。 */
  async list(tenant: string, activeOnly: boolean): Promise<PortalCompany[]> {
    const tenantId=guid.parse(tenant);
    return safe(async () => (await sql<PortalCompany>`SELECT ${columns} FROM dbo.portalCompany
      WHERE tenantId=${dbGuid(tenantId)} ${activeOnly ? sql`AND isActive=1` : sql``} ORDER BY name,id`.execute(this.database)).rows);
  }
  /** 読取専用で、実行用IDのスキーマ版と会社台帳への接続を確認します。 */
  async assertReady(): Promise<void> {
    return safe(async () => {
      const row=(await sql<{version:number}>`SELECT MAX([version]) AS version FROM dbo.portalSchemaVersions`.execute(this.database)).rows[0];
      if (!row || row.version<8) throw new ApiKeyError("CompanyStoreUnavailable",503);
      await sql`SELECT TOP(0) id,name,isActive FROM dbo.portalCompany`.execute(this.database);
    });
  }
  /** 新しい会社を作り、登録した実本人のIDを監査へ残します。 */
  async create(actor: ApiKeyActor, values: CreateCompanyInput): Promise<PortalCompany> {
    const input=createCompanySchema.parse(values), tenantId=guid.parse(actor.tenantId), actorId=guid.parse(actor.objectId);
    return safe(() => this.database.transaction().execute(async (transaction) => {
      await sql`DECLARE @r int; EXEC @r=sp_getapplock @Resource=${`portal-company:${tenantId}`},
        @LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=15000;
        IF @r<0 THROW 51000,'Company operation busy',1;`.execute(transaction);
      const nameKey=input.name.toLowerCase();
      if ((await sql`SELECT id FROM dbo.portalCompany WHERE tenantId=${dbGuid(tenantId)} AND nameKey=${nameKey}`.execute(transaction)).rows.length) {
        throw new ApiKeyError("CompanyNameExists",409);
      }
      const id=randomUUID();
      await sql`INSERT dbo.portalCompany(tenantId,id,name,nameKey) VALUES(${tenantId},${id},${input.name},${nameKey})`.execute(transaction);
      const company=(await sql<PortalCompany>`SELECT ${columns} FROM dbo.portalCompany WHERE tenantId=${dbGuid(tenantId)} AND id=${dbGuid(id)}`.execute(transaction)).rows[0]!;
      await sql`INSERT dbo.portalCompanyAudit(id,tenantId,companyId,actorObjectId,action,previousName,previousIsActive,name,isActive)
        VALUES(${randomUUID()},${tenantId},${id},${actorId},'create',NULL,NULL,${company.name},${company.isActive})`.execute(transaction);
      return company;
    }));
  }
  /** 改名と無効化は監査を残し、既存キーの会社参照を壊しません。 */
  async update(actor: ApiKeyActor, companyId: string, values: UpdateCompanyInput): Promise<PortalCompany> {
    const input=updateCompanySchema.parse(values), tenantId=guid.parse(actor.tenantId), actorId=guid.parse(actor.objectId), id=guid.parse(companyId);
    return safe(() => this.database.transaction().execute(async (transaction) => {
      await sql`DECLARE @r int; EXEC @r=sp_getapplock @Resource=${`portal-company:${tenantId}`},
        @LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=15000;
        IF @r<0 THROW 51000,'Company operation busy',1;`.execute(transaction);
      const previous=(await sql<PortalCompany>`SELECT ${columns} FROM dbo.portalCompany WITH(UPDLOCK,HOLDLOCK)
        WHERE tenantId=${dbGuid(tenantId)} AND id=${dbGuid(id)}`.execute(transaction)).rows[0];
      if (!previous) throw new ApiKeyError("NotFound",404);
      if (previous.updatedAt!==input.expectedUpdatedAt) throw new ApiKeyError("CompanyVersionConflict",409);
      const nameKey=input.name.toLowerCase();
      if ((await sql`SELECT id FROM dbo.portalCompany WHERE tenantId=${dbGuid(tenantId)} AND nameKey=${nameKey} AND id<>${dbGuid(id)}`.execute(transaction)).rows.length) {
        throw new ApiKeyError("CompanyNameExists",409);
      }
      if (previous.name===input.name && previous.isActive===input.isActive) return previous;
      await sql`DECLARE @now datetime2(3)=SYSUTCDATETIME(); UPDATE dbo.portalCompany SET name=${input.name},nameKey=${nameKey},isActive=${input.isActive},
        updatedAt=CASE WHEN @now>updatedAt THEN @now ELSE DATEADD(millisecond,1,updatedAt) END
        WHERE tenantId=${dbGuid(tenantId)} AND id=${dbGuid(id)}`.execute(transaction);
      await sql`INSERT dbo.portalCompanyAudit(id,tenantId,companyId,actorObjectId,action,previousName,previousIsActive,name,isActive)
        VALUES(${randomUUID()},${tenantId},${id},${actorId},'update',${previous.name},${previous.isActive},${input.name},${input.isActive})`.execute(transaction);
      return (await sql<PortalCompany>`SELECT ${columns} FROM dbo.portalCompany WHERE tenantId=${dbGuid(tenantId)} AND id=${dbGuid(id)}`.execute(transaction)).rows[0]!;
    }));
  }
}
let repository: CompanyRepository | undefined;
/** ビルド時にはDBへ接続せず、最初の利用時に会社台帳を用意します。 */
export function getCompanyRepository(): CompanyRepository {
  return repository ??= new SqlCompanyRepository(new Kysely<Record<string,unknown>>(createAuthDatabase(getSqlEnvironment())));
}
