import "server-only";

import { z } from "zod";

/** API接続の失効世代に束縛して保存する、通話停止に必要な情報です。 */
export const bearerRealtimeCallSchema = z.strictObject({
  version: z.literal(2), modelId: z.string().min(1).max(200),
  callId: z.string().regex(/^[A-Za-z0-9_-]{1,160}$/),
  ticket: z.string().regex(/^[A-Za-z0-9_.-]{1,4096}$/), expiresAt: z.number().int().positive(),
});

export type RealtimeCall = z.infer<typeof bearerRealtimeCallSchema>;
