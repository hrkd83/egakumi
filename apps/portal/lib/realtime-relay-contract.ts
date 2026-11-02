import { timingSafeEqual } from "node:crypto";
import { z } from "zod";
import type { ApiKeyActor } from "./api-keys/contracts.ts";
import type { RealtimeModel } from "./model-types.ts";
import { realtimeExecutionSchema } from "./realtime-request.ts";

/** 公開する双方向接続はこのパスだけです。URLへキーや本人情報は含めません。 */
export const REALTIME_RELAY_PATH = "/api/models/realtime/socket";
/** 認可済みの接続情報です。この値はloopback内だけで使い、ブラウザーへ送信しません。 */
export type RealtimeRelayContext = { actor: ApiKeyActor; model: RealtimeModel; key: string; apiKeyId: string; gatewayOrigin: string; portalOrigin: string };
/** ブラウザーは接続開始時だけモデルと購読を選べます。任意URLや上流イベントを指定できません。 */
export const realtimeRelayStartSchema = realtimeExecutionSchema.extend({ type: z.literal("start"), sdp: z.string().min(20).max(120_000).optional() }).strict();

/** 内部認可口の証明を定時間で照合し、未設定・長さ違い・推測値を拒否します。 */
export function verifyRealtimeInternalProof(expected: string | undefined, actual: string | null | undefined): boolean {
  if (!expected || !/^[a-f0-9]{64}$/.test(expected) || !actual || !/^[a-f0-9]{64}$/.test(actual)) return false;
  return timingSafeEqual(Buffer.from(expected), Buffer.from(actual));
}
