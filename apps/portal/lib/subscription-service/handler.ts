import "server-only";

import { z } from "zod";
import { ApiKeyError, type ApiKeyActor } from "../api-keys/contracts.ts";
import { handleGatewayProfile, type GatewayProfileDependencies } from "../api-keys/gateway-profile.ts";
import { subscriptionOperations, type SubscriptionOperation } from "./operations.ts";

const operationRequest = z.object({ operation: z.string(), input: z.unknown() }).strict();
const responseHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };

export type SubscriptionServiceRole = "management" | "profile";
type SubscriptionServiceDependencies = {
  readActor(request: Request): Promise<ApiKeyActor>;
  profile: GatewayProfileDependencies;
};

/** 各アプリは自身の用途だけを公開し、保有する権限に対応する入口を限定します。 */
export async function handleSubscriptionService(request: Request, role: SubscriptionServiceRole,
  dependencies: SubscriptionServiceDependencies): Promise<Response> {
  const url = new URL(request.url);
  if (url.search) return Response.json({ error: { code: "NotFound" } }, { status: 404, headers: responseHeaders });
  if (request.method === "GET" && url.pathname === "/api/health") {
    return Response.json({ status: "ok" }, { headers: responseHeaders });
  }
  if (role === "profile" && url.pathname === "/api/internal/subscription-profile") {
    return handleGatewayProfile(request, dependencies.profile);
  }
  if (role !== "management" || url.pathname !== "/api/internal/subscriptions" || request.method !== "POST") {
    return Response.json({ error: { code: "NotFound" } }, { status: 404, headers: responseHeaders });
  }

  try {
    const actor = await dependencies.readActor(request);
    const parsed = operationRequest.safeParse(await request.json().catch(() => null));
    if (!parsed.success) throw new ApiKeyError("InvalidRequest", 400);
    const { operation, input } = parsed.data;
    if (!Object.hasOwn(subscriptionOperations, operation)) throw new ApiKeyError("NotFound", 404);
    const value = await subscriptionOperations[operation as SubscriptionOperation](actor, input);
    return Response.json(value, { headers: responseHeaders });
  } catch (error) {
    const safe = error instanceof ApiKeyError ? error : new ApiKeyError("ConfigurationUnavailable", 503);
    return Response.json({ error: { code: safe.code } }, { status: safe.status, headers: responseHeaders });
  }
}
