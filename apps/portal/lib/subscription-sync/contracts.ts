import { z } from "zod";
import { apiKeyIdSchema, apimSubscriptionIdSchema } from "../api-keys/contracts.ts";

/** 同期は同じSQL行の最終状態だけを扱い、発行・削除途中の処理を横取りしません。 */
export type CanonicalSubscription = {
  id: string; apimSubscriptionId: string; productId: string; purpose: string;
  companyName: string | null; groupName: string; state: string; creationSource: string;
};
/** 管理画面に表示する差分です。キー値は契約に含めません。 */
export type SubscriptionDifference = {
  id: string; sql: CanonicalSubscription | null;
  apim: { id: string; displayName: string; scope: string; state: string } | null;
  desired: { displayName: string; productId: string; state: "active" | "suspended" } | null;
  action: "none" | "update" | "recreate" | "delete" | "delete-unregistered" | "blocked";
  reactivates: boolean;
};
/** 実行履歴は追記だけで残し、中断時も実行開始が失われないようにします。 */
export type SyncAudit = { id: string; planId: string; subscriptionId: string; action: string; phase: string; code: string | null; recordedAt: string };
/** 対象DBの種別と反映可否を必ず一覧と一緒に返します。 */
export type SyncInventory = { rows: SubscriptionDifference[]; writable: boolean; source: "azure" | "local";
  database: string; server: string; apimResourceId: string; audit: SyncAudit[] };
/** 一回に最大10件とし、再作成・削除・再有効化の内容を確認できるサイズにします。 */
export const syncItemIdSchema = z.union([apiKeyIdSchema, z.string().startsWith("apim:").refine((id) => apimSubscriptionIdSchema.safeParse(id.slice(5)).success)]);
export const previewRequestSchema = z.object({ action: z.literal("preview"), ids: z.array(syncItemIdSchema).min(1).max(10)
  .refine((ids) => new Set(ids).size === ids.length) }).strict();
export const applyRequestSchema = z.object({ action: z.literal("apply"), token: z.string().min(1).max(60_000), confirmed: z.literal(true) }).strict();
export const syncRequestSchema = z.discriminatedUnion("action", [previewRequestSchema, applyRequestSchema]);
/** 有効期限付き確認画面の値です。変更対象はサーバー側で署名します。 */
export type SyncPreview = { token: string; expiresAt: string; rows: SubscriptionDifference[] };
/** 一括処理の一部が失敗しても、各購読の結果を隠しません。 */
export type SyncResult = { id: string; outcome: "succeeded" | "pending" | "failed"; code: string | null };
