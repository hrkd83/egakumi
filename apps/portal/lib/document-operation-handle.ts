import "server-only";
import { createCipheriv, createDecipheriv, hkdfSync, randomBytes } from "node:crypto";
import { z } from "zod";
import type { ApiKeyActor } from "./api-keys/contracts.ts";

/** GatewayのチケットをPortal利用者と選択購読へ束縛する、サーバー専用の状態です。 */
export const documentOperationStateSchema = z.object({
  operationId: z.uuid(), model: z.enum(["prebuilt-read", "prebuilt-layout"]),
  statusPath: z.string().regex(/^\/tools\/documents\/v1\/operations\/[0-9a-f-]{36}$/i),
  ticket: z.string().regex(/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/),
  expiresAt: z.iso.datetime({ offset: true }), startedAt: z.number().finite().nonnegative(),
}).strict().refine((value) => value.statusPath.endsWith(`/${value.operationId}`));
export type DocumentOperationState = z.infer<typeof documentOperationStateSchema>;

/** 用途別の鍵を導出し、Cookie署名や他の暗号化データと鍵の用途を分離します。 */
function key(secret: string): Buffer {
  if (secret.length < 32) throw new Error("Document operation configuration");
  return Buffer.from(hkdfSync("sha256", secret, "egakumi.portal.document-operation.v1", "aes-256-gcm", 32));
}

/** 現在の本人と購読を、改ざんできない追加認証データにします。 */
function binding(actor: ApiKeyActor, apiKeyId: string): Buffer {
  return Buffer.from(JSON.stringify([actor.tenantId, actor.objectId, apiKeyId]));
}

/** Azure URLやキーを含まない状態を封印し、別の利用者が結果取得へ流用できないようにします。 */
export function sealDocumentOperation(state: DocumentOperationState, actor: ApiKeyActor, apiKeyId: string, secret: string): string {
  const parsed = documentOperationStateSchema.parse(state);
  const iv = randomBytes(12); const cipher = createCipheriv("aes-256-gcm", key(secret), iv);
  cipher.setAAD(binding(actor, apiKeyId));
  const body = Buffer.concat([cipher.update(JSON.stringify(parsed), "utf8"), cipher.final()]);
  return Buffer.concat([Buffer.from([1]), iv, cipher.getAuthTag(), body]).toString("base64url");
}

/** 解析の保持期限を過ぎた参照を、署名や所有者の不一致と区別します。 */
export class DocumentOperationExpiredError extends Error {}

/** 本人・購読・署名と提供元の保持期限を確認して、固定Gatewayパスだけを復元します。 */
export function openDocumentOperation(handle: string, actor: ApiKeyActor, apiKeyId: string, secret: string, now = Date.now()): DocumentOperationState {
  if (!/^[A-Za-z0-9_-]{40,16000}$/.test(handle)) throw new Error("Invalid document operation");
  const bytes = Buffer.from(handle, "base64url");
  if (bytes.length < 30 || bytes[0] !== 1) throw new Error("Invalid document operation");
  const decipher = createDecipheriv("aes-256-gcm", key(secret), bytes.subarray(1, 13));
  decipher.setAAD(binding(actor, apiKeyId)); decipher.setAuthTag(bytes.subarray(13, 29));
  const state = documentOperationStateSchema.parse(JSON.parse(Buffer.concat([decipher.update(bytes.subarray(29)), decipher.final()]).toString("utf8")));
  if (Date.parse(state.expiresAt) <= now) throw new DocumentOperationExpiredError("Expired document operation");
  return state;
}
