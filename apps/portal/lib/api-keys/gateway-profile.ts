import "server-only";

import { timingSafeEqual } from "node:crypto";
import { z } from "zod";
import { ApiKeyError } from "./contracts.ts";
import { apiKeyLimitProfileSchema, unlimitedApiKeyLimits, type ApiKeyLimitProfile } from "./limit-contracts.ts";
import { gatewaySubscriptionIdSchema } from "./limit-repository.ts";

/** Gateway専用の接続情報です。Portalのログイン鍵や利用者の購読キーと共用しません。 */
export type GatewayProfileSettings = { secret: string; tenantId: string };

/** 起動時には秘密を読まず、内部APIの呼び出し時にだけ設定を検証します。 */
export function getGatewayProfileSettings(environment: Readonly<Record<string, string | undefined>> = process.env): GatewayProfileSettings {
  const parsed = z.object({ secret: z.string().regex(/^[a-f0-9]{128}$/), tenantId: z.uuid() }).safeParse({
    secret: environment.PORTAL_SUBSCRIPTION_PROFILE_SECRET,
    tenantId: environment.MICROSOFT_TENANT_ID,
  });
  if (!parsed.success) throw new ApiKeyError("ProfileServiceUnavailable", 503);
  return { ...parsed.data, tenantId: parsed.data.tenantId.toLowerCase() };
}

/** SQL取得を差し替え、認証前に台帳へアクセスしないことを検証できる契約です。 */
export type GatewayProfileDependencies = {
  settings(): GatewayProfileSettings;
  readProfile(tenantId: string, subscriptionId: string): Promise<ApiKeyLimitProfile | null>;
};

const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };

/**
 * 認証済みAPIMだけへ、利用制限の公開値を返します。
 * @remarks 未登録の既存購読はSQL照会の成功後だけ無制限です。障害を無制限へ読み替えません。
 */
export async function handleGatewayProfile(request: Request, dependencies: GatewayProfileDependencies): Promise<Response> {
  try {
    if (request.method !== "GET") return Response.json({ error: { code: "MethodNotAllowed" } }, { status: 405, headers: { ...headers, Allow: "GET" } });
    const settings = dependencies.settings();
    const received = request.headers.get("X-Core-Profile-Key") ?? "";
    if (!/^[a-f0-9]{128}$/.test(received) || !timingSafeEqual(Buffer.from(received), Buffer.from(settings.secret))) {
      throw new ApiKeyError("AccessDenied", 403);
    }
    // WS/gRPCでもset-bodyを使わず照会できるよう、検証済みIDを専用ヘッダーで受けます。
    const parsed = gatewaySubscriptionIdSchema.safeParse(request.headers.get("X-Core-Profile-Subscription"));
    if (!parsed.success) throw new ApiKeyError("InvalidRequest", 400);
    const found = await dependencies.readProfile(settings.tenantId, parsed.data);
    const profile = apiKeyLimitProfileSchema.parse(found ?? unlimitedApiKeyLimits());
    return Response.json({ schemaVersion: 1, subscriptionId: parsed.data, ...profile }, { headers });
  } catch (error) {
    const safe = error instanceof ApiKeyError ? error : new ApiKeyError("ProfileServiceUnavailable", 503);
    return Response.json({ error: { code: safe.code } }, { status: safe.status, headers });
  }
}
