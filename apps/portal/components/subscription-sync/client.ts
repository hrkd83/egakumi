import { z } from "zod";
import { ApiKeyClientError } from "../api-keys/client.ts";
import { syncItemIdSchema } from "../../lib/subscription-sync/contracts.ts";
import type { SyncInventory, SyncPreview, SyncResult } from "../../lib/subscription-sync/contracts.ts";

const metadata = z.object({ id: z.string().max(256), displayName: z.string().max(1000), scope: z.string().max(2048), state: z.string().max(80) });
const rowSchema = z.object({ id: syncItemIdSchema, sql: z.object({ id: z.string(), apimSubscriptionId: z.string(), productId: z.string(), purpose: z.string(),
  companyName: z.string().nullable(), groupName: z.string(), state: z.string(), creationSource: z.string() }).nullable(), apim: metadata.nullable(),
  desired: z.object({ displayName: z.string(), productId: z.string(), state: z.enum(["active", "suspended"]) }).nullable(),
  action: z.enum(["none", "update", "recreate", "delete", "delete-unregistered", "blocked"]), reactivates: z.boolean() });
const inventorySchema = z.object({ rows: z.array(rowSchema).max(200_000), writable: z.boolean(), source: z.enum(["azure", "local"]), database: z.string(), server: z.string(), apimResourceId: z.string(),
  audit: z.array(z.object({ id: z.uuid(), planId: z.uuid(), subscriptionId: z.string(), action: z.string(), phase: z.string(), code: z.string().nullable(), recordedAt: z.iso.datetime() })).max(100) });
const previewSchema = z.object({ token: z.string().max(60_000), expiresAt: z.iso.datetime(), rows: z.array(rowSchema).min(1).max(10) });
const resultsSchema = z.object({ results: z.array(z.object({ id: syncItemIdSchema, outcome: z.enum(["succeeded", "pending", "failed"]), code: z.string().nullable() })).max(10) });
/** 更新の自動再送は行いません。通信中断後は一覧で実際の状態を確認します。 */
async function request(body?: unknown, signal?: AbortSignal): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch("/api/subscription-sync", { method: body ? "POST" : "GET", cache: "no-store", credentials: "same-origin",
      ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(240_000)]) : AbortSignal.timeout(240_000) });
  } catch { throw new ApiKeyClientError("NetworkUnavailable"); }
  let payload: unknown;
  try { payload = await response.json(); } catch { throw new ApiKeyClientError("ServiceUnavailable"); }
  if (!response.ok) {
    const failure = z.object({ error: z.object({ code: z.string().max(80) }) }).safeParse(payload);
    throw new ApiKeyClientError(failure.success ? failure.data.error.code : "ServiceUnavailable");
  }
  return payload;
}
/** SQL・APIMの状態を同じレスポンスから取得し、途中失敗を空一覧へ変えません。 */
export async function loadSyncInventory(signal?: AbortSignal): Promise<SyncInventory> {
  return inventorySchema.parse(await request(undefined, signal));
}
/** 選択した行だけの差分を再照会し、署名付き確認内容を受け取ります。 */
export async function previewSync(ids: string[]): Promise<SyncPreview> { return previewSchema.parse(await request({ action: "preview", ids })); }
/** サーバーが署名した内容だけを確定し、値や宛先を送信しません。 */
export async function applySync(token: string): Promise<{ results: SyncResult[] }> { return resultsSchema.parse(await request({ action: "apply", token, confirmed: true })); }
