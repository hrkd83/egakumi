import { z } from "zod";

/** Gateway試用は、Portalが本人のBearerをサーバー内で取得します。 */
export const tryBearerFields = { authentication: z.literal("bearer") };
export const tryCredentialSchema = z.strictObject(tryBearerFields);

/** ブラウザーが送る認証方式です。アクセストークンは含めません。 */
export type TryCredential = z.infer<typeof tryCredentialSchema>;
