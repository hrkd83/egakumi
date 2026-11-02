import { z } from "zod";

/** Entraで確認した本人です。クライアントの入力からは作りません。 */
export type ApiKeyActor = { tenantId: string; objectId: string };

/** グループに承認された製品と、作成可能な購読の上限です。 */
export type ApiKeyProduct = { id: string; displayName: string; maxSubscriptions: number };

/** 個人ではなく、キーの所有者として登録したEntraグループです。 */
export type ApiKeyGroup = { id: string; displayName: string; products: ApiKeyProduct[];
  directoryStatus?: "active" | "deleted" | "ineligible" | "unknown"; lastCheckedAt?: string | null };

/** 通常発行キーが利用する、公開済みAPIをまとめた製品です。 */
export const RELEASED_API_PRODUCT_ID = "released-apis";

/** 統計用の接続元分類です。認可や所有権の判定には使いません。 */
export const apiKeyClientTypeSchema = z.enum(["custom-system", "saas", "oss", "low-code", "other"]);
/** 接続元クライアントの分類です。 */
export type ApiKeyClientType = z.infer<typeof apiKeyClientTypeSchema>;

/** 秘密値を含まない、グループ所有の購読台帳です。 */
export type ApiKeyRecord = {
  id: string;
  apimSubscriptionId: string;
  groupId: string;
  productId: string;
  displayName: string;
  state: "creating" | "active" | "suspended" | "deleting" | "deleted";
  createdAt: string;
  updatedAt: string;
  lastReviewedAt: string | null;
  budgetMode: "unlimited";
  clientType: ApiKeyClientType | null;
  systemName: string | null;
  teamName: string | null;
  companyId: string | null;
  companyName: string | null;
  contactEmail: string | null;
};

/** 所属が確認できたグループだけを表示する一覧です。 */
export type ApiKeyOverview = { groups: ApiKeyGroup[]; keys: ApiKeyRecord[] };

/** 明示的な表示操作でだけ返し、永続化しないAPIMキーです。 */
export type ApiKeySecrets = { primaryKey: string; secondaryKey: string };

/** 任意の補足名は空欄を未設定へ揃え、制御文字を受け付けません。 */
const optionalName = z.string().trim().max(120).refine((value) => !/[\u0000-\u001f\u007f]/.test(value)).optional();

/** 作成時は所有グループ・利用者情報を指定し、製品はサーバー側で固定します。 */
export const createApiKeySchema = z.object({
  groupId: z.uuid(),
  displayName: z.string().trim().min(1).max(80),
  idempotencyKey: z.uuid(),
  clientType: apiKeyClientTypeSchema,
  systemName: optionalName,
  teamName: optionalName,
  companyId: z.uuid(),
  contactEmail: z.email().trim().max(254),
}).strict();

/** 永続化層だけが使用する、固定製品を付加した発行契約です。 */
export const reserveApiKeySchema = createApiKeySchema.extend({ productId: z.literal(RELEASED_API_PRODUCT_ID) });
/** 本人・グループ認可後にSQLへ渡す発行予約です。 */
export type ReserveApiKeyInput = z.infer<typeof reserveApiKeySchema>;

/** 一覧やURLから受け取る台帳IDは、このポータルが発行する購読に限定します。 */
export const apiKeyIdSchema = z.string().regex(/^portal-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);

/** 固定APIM内の購読名です。URL区切り・エンコード・クエリ・制御文字は許可しません。 */
export const apimSubscriptionIdSchema = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$/);

/** サーバーカタログが指定するAPI識別子です。任意のパスやリビジョン指定は受け付けません。 */
export const apimApiIdSchema = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$/);

/** 購読の状態変更と棚卸は専用操作で受け付けます。 */
export const apiKeyActionSchema = z.object({ action: z.enum(["suspend", "resume", "review", "delete", "reveal"]) }).strict();

/** 所有元の読取時点を明示し、並行する移管で別の所有者を上書きしません。 */
export const transferApiKeyOwnershipSchema = z.object({
  targetGroupId: z.uuid(), expectedSourceGroupId: z.uuid(),
}).strict();

/** 所有元メンバーが選んだ、検証前の移管指定です。 */
export type TransferApiKeyOwnershipInput = z.infer<typeof transferApiKeyOwnershipSchema>;

/** 移管先候補にはGraphで確認した所属の有無を必ず付け、未確認を未所属と表示しません。 */
export type ApiKeyOwnerTarget = ApiKeyGroup & { isMember: boolean };

/** 検証済みの発行入力です。 */
export type CreateApiKeyInput = z.infer<typeof createApiKeySchema>;

/** UIへ返してよい失敗理由だけを定義します。SDKやGraphの本文を露出しません。 */
export class ApiKeyError extends Error {
  public readonly code: string;
  public readonly status: number;

  constructor(code: string, status: number) {
    super(code);
    this.name = "ApiKeyError";
    this.code = code;
    this.status = status;
  }
}
