import { z } from "zod";
import { apiKeyIdSchema } from "./api-keys/contracts.ts";
import { ModelResponseError } from "./model-response.ts";

/** 実行時の選択肢に必要な台帳情報です。購読キーの実値は含めません。 */
export const modelKeyChoicesSchema = z.object({
  keys: z.array(z.object({
    id: apiKeyIdSchema,
    displayName: z.string().min(1),
    companyName: z.string().nullable(),
    groupName: z.string().min(1),
  }).strict()),
}).strict();

/** 対象APIを利用できる、所属グループ所有のキー候補です。 */
export type ModelKeyChoice = z.infer<typeof modelKeyChoicesSchema>["keys"][number];

/** 所属とAPIの利用可否をサーバーで確認した、秘密値を含まない一覧だけを読みます。 */
export async function loadModelKeyChoices(apiId: string, signal: AbortSignal): Promise<ModelKeyChoice[]> {
  try {
    const response = await fetch(`/api/models/keys?${new URLSearchParams({ apiId })}`, {
      credentials: "same-origin", cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(45_000)]),
    });
    if (response.status === 401) throw new ModelResponseError({ error: { code: "AuthenticationRequired" } });
    if (!response.ok) throw new ModelResponseError({ error: { code: "ApiKeyAccessUnavailable" } });
    return modelKeyChoicesSchema.parse(await response.json()).keys;
  } catch (error) {
    if (signal.aborted || error instanceof ModelResponseError) throw error;
    throw new ModelResponseError({ error: { code: "ApiKeyAccessUnavailable" } });
  }
}
