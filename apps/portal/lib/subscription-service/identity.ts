import "server-only";

import { createRemoteJWKSet, jwtVerify, type JWTVerifyGetKey } from "jose";
import { z } from "zod";
import { ApiKeyError, type ApiKeyActor } from "../api-keys/contracts.ts";

const uuid = z.uuid().transform((value) => value.toLowerCase());
const settingsSchema = z.object({ tenant: uuid, audience: uuid, client: uuid });
const claimSchema = z.object({
  tid: uuid, oid: uuid, azp: uuid, ver: z.literal("2.0"), scp: z.string(),
});

/** 購読管理スコープを公開する既存APIと、許可された専用Portalクライアントを固定します。 */
export function subscriptionIdentitySettings(env: Readonly<Record<string, string | undefined>> = process.env) {
  const parsed = settingsSchema.safeParse({
    tenant: env.MICROSOFT_TENANT_ID, audience: env.PORTAL_SUBSCRIPTION_SERVICE_AUDIENCE, client: env.MICROSOFT_CLIENT_ID,
  });
  if (!parsed.success) throw new ApiKeyError("ConfigurationUnavailable", 503);
  return parsed.data;
}

/** PortalのCookieや転送ヘッダーを使わず、Entra署名付きの委任本人を毎要求検証します。 */
export function createSubscriptionActorVerifier(settings: z.output<typeof settingsSchema>, verificationKey?: JWTVerifyGetKey) {
  const authority = `https://login.microsoftonline.com/${settings.tenant}`;
  const key = verificationKey ?? createRemoteJWKSet(new URL(`${authority}/discovery/v2.0/keys`), { timeoutDuration: 5000 });
  return async (request: Request): Promise<ApiKeyActor> => {
    const authorization = request.headers.get("Authorization") ?? "";
    const match = authorization.length <= 16_391 && /^Bearer ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)$/.exec(authorization);
    if (!match) throw new ApiKeyError("AuthenticationRequired", 401);
    try {
      const { payload } = await jwtVerify(match[1], key, {
        issuer: `${authority}/v2.0`, audience: settings.audience, algorithms: ["RS256"],
        requiredClaims: ["exp", "iat", "nbf"], clockTolerance: 30,
      });
      if (payload.aud !== settings.audience) throw new Error();
      const claims = claimSchema.parse(payload);
      const scopes = claims.scp.split(" ");
      if (claims.tid !== settings.tenant || claims.azp !== settings.client
          || !scopes.includes("Subscription.Manage") || scopes.includes("Gateway.Invoke")) throw new Error();
      return { tenantId: claims.tid, objectId: claims.oid };
    } catch {
      throw new ApiKeyError("AuthenticationRequired", 401);
    }
  };
}
