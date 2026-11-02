import "server-only";

import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { z } from "zod";
import { apiKeyIdSchema, type ApiKeyActor } from "./api-keys/contracts.ts";

const handleSchema = z.object({
  version: z.literal(1), tenantId: z.uuid(), objectId: z.uuid(), apiKeyId: apiKeyIdSchema,
  modelId: z.string().min(1).max(200), callId: z.string().regex(/^[A-Za-z0-9_-]{1,160}$/),
  ticket: z.string().regex(/^[A-Za-z0-9_.-]{1,4096}$/), expiresAt: z.number().int().positive(),
}).strict();
const associatedData = Buffer.from("egakumi.portal.realtime.cleanup.v1");

/** 通話停止の再試行に必要な最小限の情報です。購読キーそのものは含めません。 */
export type RealtimeCallHandle = z.infer<typeof handleSchema>;

/** Cookie署名鍵とは用途を分けたAES鍵を導出し、通話情報を画面から読み取れない参照へ変換します。 */
function encryptionKey(secret: string): Buffer {
  if (typeof secret !== "string" || secret.length < 32) throw new Error("Realtime cleanup secret is unavailable");
  return createHash("sha256").update(associatedData).update("\0").update(secret).digest();
}

/**
 * 検証済み本人・購読・通話に束縛した短期の停止参照を発行します。
 * @remarks 参照は機密情報を暗号化したものです。ログ・コピー用コード・永続ストレージへ保存しません。
 */
export function sealRealtimeCallHandle(value: RealtimeCallHandle, secret: string, now = Date.now()): string {
  const validated = handleSchema.parse(value);
  if (validated.expiresAt <= now || validated.expiresAt > now + 600_000) throw new Error("Invalid cleanup lifetime");
  const nonce = randomBytes(12); const cipher = createCipheriv("aes-256-gcm", encryptionKey(secret), nonce);
  cipher.setAAD(associatedData);
  const encrypted = Buffer.concat([cipher.update(JSON.stringify(validated), "utf8"), cipher.final()]);
  return `r1.${Buffer.concat([nonce, cipher.getAuthTag(), encrypted]).toString("base64url")}`;
}

/** 暗号・期限・ログイン本人をすべて確認し、不正な参照から送信先や所有権を作らせません。 */
export function openRealtimeCallHandle(handle: string, secret: string, actor: ApiKeyActor, now = Date.now()): RealtimeCallHandle {
  if (!/^r1\.[A-Za-z0-9_-]{40,15997}$/.test(handle)) throw new Error("Invalid cleanup handle");
  const packed = Buffer.from(handle.slice(3), "base64url");
  if (packed.length < 29) throw new Error("Invalid cleanup handle");
  const decipher = createDecipheriv("aes-256-gcm", encryptionKey(secret), packed.subarray(0, 12));
  decipher.setAAD(associatedData); decipher.setAuthTag(packed.subarray(12, 28));
  const value = handleSchema.parse(JSON.parse(Buffer.concat([decipher.update(packed.subarray(28)), decipher.final()]).toString("utf8")));
  if (value.expiresAt <= now || value.expiresAt > now + 600_000
    || value.tenantId.toLowerCase() !== actor.tenantId.toLowerCase() || value.objectId.toLowerCase() !== actor.objectId.toLowerCase()) {
    throw new Error("Cleanup handle is unavailable");
  }
  return value;
}
