import { z } from "zod";
import { apiKeyIdSchema } from "../api-keys/contracts.ts";

/** 従来のキー指定も受け付ける、キー方式の本文フィールドです。 */
export const tryApiKeyFields = { authentication: z.literal("api-key").optional(), apiKeyId: apiKeyIdSchema };
/** サーバーが資格情報を解決する、Bearer方式の本文フィールドです。 */
export const tryBearerFields = { authentication: z.literal("bearer"), apiKeyId: z.never().optional() };

/** ブラウザーは認証方式と台帳IDだけを送信し、資格情報は送信しません。 */
export const tryCredentialSchema = z.union([z.strictObject(tryApiKeyFields), z.strictObject(tryBearerFields)]);

/** 購読IDはキー方式でだけ選択できます。 */
export type TryCredential = z.infer<typeof tryCredentialSchema>;
