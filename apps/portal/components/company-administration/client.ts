import { z } from "zod";
import { ApiKeyClientError } from "../api-keys/client.ts";

/** サーバーから受け取った会社の公開項目だけを採用します。 */
const companySchema = z.object({ id: z.uuid(), name: z.string().trim().min(1).max(160), isActive: z.boolean(),
  createdAt: z.iso.datetime(), updatedAt: z.iso.datetime() });
const companiesSchema = z.object({ companies: z.array(companySchema).max(1000).refine((records) =>
  new Set(records.map((record) => record.id.toLowerCase())).size === records.length) });

/** 会社名が変わっても同じ会社として識別する、管理画面向けのレコードです。 */
export type CompanyRecord = z.infer<typeof companySchema>;

/** クライアントで明示する更新入力です。読取時の版を送って競合を検出します。 */
const updateCompanySchema = z.object({ name: z.string().trim().min(1).max(160), isActive: z.boolean(), expectedUpdatedAt: z.iso.datetime() }).strict();

/** 管理APIの失敗本文を画面へ流さず、固定の失敗コードへ変換します。 */
async function request(url: string, method: "GET" | "POST" | "PATCH", body?: unknown, signal?: AbortSignal): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(url, { method, credentials: "same-origin", cache: "no-store",
      ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30_000)]) : AbortSignal.timeout(30_000) });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiKeyClientError("NetworkUnavailable");
  }
  let payload: unknown;
  try { payload = await response.json(); } catch { throw new ApiKeyClientError("ServiceUnavailable"); }
  if (!response.ok) {
    const failure = z.object({ error: z.object({ code: z.string().max(80) }) }).safeParse(payload);
    throw new ApiKeyClientError(failure.success ? failure.data.error.code : "ServiceUnavailable");
  }
  return payload;
}

/** 基盤管理者が登録した会社を、停止中も含めて取得します。 */
export async function loadCompanies(signal?: AbortSignal): Promise<CompanyRecord[]> {
  const parsed = companiesSchema.safeParse(await request("/api/company-administration", "GET", undefined, signal));
  if (!parsed.success) throw new ApiKeyClientError("ServiceUnavailable");
  return parsed.data.companies;
}

/** 新しい会社の名称だけを送ります。ID・登録者・登録日時はサーバーが決定します。 */
export async function createCompany(name: string, signal?: AbortSignal): Promise<CompanyRecord> {
  const input = z.object({ name: z.string().trim().min(1).max(160) }).safeParse({ name });
  if (!input.success) throw new ApiKeyClientError("InvalidRequest");
  const parsed = z.object({ company: companySchema }).safeParse(await request("/api/company-administration", "POST", input.data, signal));
  if (!parsed.success) throw new ApiKeyClientError("ServiceUnavailable");
  return parsed.data.company;
}

/** 会社を物理削除せず、名称と今後の選択可否を更新します。 */
export async function updateCompany(id: string, input: z.infer<typeof updateCompanySchema>, signal?: AbortSignal): Promise<CompanyRecord> {
  if (!z.uuid().safeParse(id).success) throw new ApiKeyClientError("InvalidRequest");
  const values = updateCompanySchema.safeParse(input);
  if (!values.success) throw new ApiKeyClientError("InvalidRequest");
  const parsed = z.object({ company: companySchema }).safeParse(await request(`/api/company-administration/${id}`, "PATCH", values.data, signal));
  if (!parsed.success) throw new ApiKeyClientError("ServiceUnavailable");
  return parsed.data.company;
}
