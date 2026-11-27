import "server-only";
import { z } from "zod";

/** GatewayのチケットをPortalの本人の認可セッションへ束縛する、サーバー専用の状態です。 */
export const documentOperationStateSchema = z.object({
  operationId: z.uuid(), model: z.enum(["prebuilt-read", "prebuilt-layout"]),
  statusPath: z.string().regex(/^\/tools\/documents\/v1\/operations\/[0-9a-f-]{36}$/i),
  ticket: z.string().regex(/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/),
  expiresAt: z.iso.datetime({ offset: true }), startedAt: z.number().finite().nonnegative(),
}).strict().refine((value) => value.statusPath.endsWith(`/${value.operationId}`));
export type DocumentOperationState = z.infer<typeof documentOperationStateSchema>;
