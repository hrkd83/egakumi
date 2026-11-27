import { z } from "zod";
import { tryCredentialSchema } from "./api-auth/try-credential.ts";

/** Graph疎通確認で固定して呼び出す、公開済みのGateway APIです。 */
export const GRAPH_TOOL_PATH = "/tools/graph/v1/me";

/** 本人の属性として公開する項目と表示順です。Gatewayの固定 $select と合わせます。 */
export const GRAPH_TOOL_PROFILE_FIELDS = [
  "displayName", "givenName", "surname", "userPrincipalName", "id", "jobTitle",
  "companyName", "department", "employeeId", "officeLocation", "mail", "accountEnabled",
] as const;

/** このAPIで取得する属性です。呼び出し側から任意の属性を指定することはできません。 */
export const GRAPH_TOOL_PROFILE_SELECT = GRAPH_TOOL_PROFILE_FIELDS.join(",");

/** ブラウザーはトークンを含めず、本人のBearer認可を指定します。 */
export const graphToolInputSchema = tryCredentialSchema;

/** Graphの固定 $select に対応する公開項目です。未設定・権限などによる省略とnullを区別せず受け取ります。 */
export const graphToolProfileSchema = z.object({
  id: z.string().min(1).max(256), "@odata.context": z.string().max(2048).optional(),
  userPrincipalName: z.string().max(320).nullable().optional(), displayName: z.string().max(256).nullable().optional(),
  givenName: z.string().max(256).nullable().optional(), surname: z.string().max(256).nullable().optional(),
  jobTitle: z.string().max(256).nullable().optional(), companyName: z.string().max(256).nullable().optional(),
  department: z.string().max(256).nullable().optional(), employeeId: z.string().max(256).nullable().optional(),
  officeLocation: z.string().max(256).nullable().optional(), mail: z.string().max(320).nullable().optional(),
  accountEnabled: z.boolean().nullable().optional(),
}).strict();

/** 本人確認済みのユーザー属性です。idは接続セッションの本人IDとの照合にも使います。 */
export type GraphToolProfile = z.infer<typeof graphToolProfileSchema>;

/** 空値は表示用の記号へ、真偽値は有効・無効の説明へ変換し、元のJSON値は変更しません。 */
export function graphToolProfileValue(profile: GraphToolProfile, field: typeof GRAPH_TOOL_PROFILE_FIELDS[number], labels: {
  unavailableField: string; accountEnabled: string; accountDisabled: string;
}): string {
  const value = profile[field];
  if (typeof value === "boolean") return value ? labels.accountEnabled : labels.accountDisabled;
  return typeof value === "string" && value.trim() ? value : labels.unavailableField;
}

/** Graph取得に成功したときの公開応答です。セッション更新の秘密ヘッダーは含めません。 */
export const graphToolCompleteSchema = z.object({
  status: z.literal("complete"), profile: graphToolProfileSchema, requestId: z.string().max(256).optional(),
  durationMs: z.number().finite().nonnegative(),
}).strict();

/** 画面へ表示する失敗の固定コードと問い合わせ用IDです。 */
export const graphToolErrorCodeSchema = z.enum([
  "AuthenticationRequired", "AuthenticationUnavailable", "RequestNotAllowed", "InvalidRequest", "GatewayNotConfigured",
  "ApiAuthenticationRequired", "ApiAuthenticationBusy", "ApiAuthenticationUnavailable",
  "GatewayUnavailable", "GatewayResponseInvalid", "GraphAccessDenied",
  "GraphReauthenticationRequired", "RateLimitExceeded", "ConnectionIdentityMismatch",
]);

/** 画面へ返せる失敗コードです。上流の生の例外や詳細は含めません。 */
export type GraphToolErrorCode = z.infer<typeof graphToolErrorCodeSchema>;

/** 画面へ表示する失敗の固定コードと問い合わせ用IDです。 */
export const graphToolErrorSchema = z.object({
  error: z.object({ code: graphToolErrorCodeSchema }).strict(),
  requestId: z.string().max(256).optional(),
  claims: z.string().max(4_096).regex(/^[A-Za-z0-9_-]+$/).optional(),
}).strict().refine(({ claims, error }) => claims === undefined || error.code === "GraphReauthenticationRequired");

/** 実行に成功した、プロフィールと計測情報です。 */
export type GraphToolComplete = z.infer<typeof graphToolCompleteSchema>;
/** 公開可能な失敗情報です。 */
export type GraphToolFailure = z.infer<typeof graphToolErrorSchema>;
