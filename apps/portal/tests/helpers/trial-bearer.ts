import { randomUUID } from "node:crypto";
import type { GatewayActor, GatewayCredential } from "../../lib/api-auth/gateway-credential.ts";
import type { PortalBearer } from "../../lib/api-auth/service.ts";

/** サーバー側の認可を模す合成資格情報です。外部サービスでは使用できません。 */
export function trialBearer(value: string, actor: GatewayActor = { tenantId: randomUUID(), objectId: randomUUID() }): PortalBearer {
  const now = Date.now();
  return { value, generation: 1, expiresAt: now + 300_000, accessExpiresAt: now + 360_000,
    binding: { ...actor, sessionId: randomUUID(), hash: "c".repeat(64) } };
}

/** 固定の上流へ転送する合成Bearerヘッダーを構築します。 */
export function trialCredential(value: string): GatewayCredential {
  return { value, headers: { Authorization: `Bearer ${value}` }, bearer: trialBearer(value) };
}

/** JSON転送後にデコードされる合成資格情報の反射を再現します。 */
export function unicodeEscaped(value: string): string {
  return [...value].map((character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`).join("");
}
