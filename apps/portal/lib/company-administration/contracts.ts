import { z } from "zod";

/** 会社は統計用の属性です。認可・所有グループ・テナントを切り替えません。 */
export type PortalCompany = { id: string; name: string; isActive: boolean; createdAt: string; updatedAt: string };
/** 表記揺れと制御文字を整理し、運用時に識別しやすい会社名を受け付けます。 */
export const companyNameSchema = z.string().transform((value) => value.normalize("NFKC").trim().replace(/\s+/gu, " "))
  .pipe(z.string().min(1).max(160).refine((value) => !/[\u0000-\u001f\u007f]/.test(value)));
/** 新規会社は有効状態で登録し、名前以外の設定をHTTPから持ち込みません。 */
export const createCompanySchema = z.object({ name: companyNameSchema }).strict();
/** 読込時の版を必須にし、別の管理者が行った変更を上書きしません。 */
export const updateCompanySchema = z.object({ name: companyNameSchema, isActive: z.boolean(), expectedUpdatedAt: z.iso.datetime() }).strict();
/** 会社登録の入力です。 */
export type CreateCompanyInput = z.infer<typeof createCompanySchema>;
/** 会社改名・無効化の入力です。 */
export type UpdateCompanyInput = z.infer<typeof updateCompanySchema>;
