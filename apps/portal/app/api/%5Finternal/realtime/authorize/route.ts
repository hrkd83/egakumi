import { getAuthEnvironment } from "@/lib/auth-environment";
import { readPortalPrincipal } from "@/lib/auth-session";
import { ApiKeyError } from "@/lib/api-keys/contracts";
import { getApiKeyService } from "@/lib/api-keys/service";
import { getRealtimeModels } from "@/lib/model-catalog";
import { getPublicModelGatewayOrigin } from "@/lib/model-public-config";
import { getRealtimeEndpoint } from "@/lib/realtime-integration";
import { realtimeRelayStartSchema, verifyRealtimeInternalProof } from "@/lib/realtime-relay-contract";
import { resolveRealtimeOutputTokens } from "@/lib/realtime-request";
import { resolveRealtimeVoice } from "@/lib/realtime-voices";
import { readSpeechBody } from "@/lib/speech-response";
import { MAX_BUFFERED_BODY_BYTES } from "@/lib/model-runtime-limits";

/** 外側のサーバーが遮断したloopback専用口で、SQL本人と最新のGraph所属・購読権限を照合します。 */
export async function POST(request: Request): Promise<Response> {
  const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
  const failure = (status: number) => new Response(null, { status, headers });
  if (!verifyRealtimeInternalProof(process.env.PORTAL_REALTIME_INTERNAL_PROOF, request.headers.get("X-Portal-Realtime-Internal"))) return failure(404);
  try {
    const portalOrigin = getAuthEnvironment().baseURL;
    if (request.headers.get("origin") !== portalOrigin || request.headers.get("content-type")?.split(";", 1)[0] !== "application/json") return failure(403);
    const actor = await readPortalPrincipal(); if (!actor) return failure(401);
    const input = realtimeRelayStartSchema.parse(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await readSpeechBody(request.body, MAX_BUFFERED_BODY_BYTES, AbortSignal.any([request.signal, AbortSignal.timeout(10_000)])))));
    const model = getRealtimeModels().find((entry) => entry.id === input.modelId);
    if (!model || (model.transport === "webrtc") !== Boolean(input.sdp)) return failure(400);
    try { resolveRealtimeOutputTokens(model, input); resolveRealtimeVoice(model, input); } catch { return failure(400); }
    const gatewayOrigin = getPublicModelGatewayOrigin(); if (!gatewayOrigin) return failure(503); getRealtimeEndpoint(model, gatewayOrigin);
    const key = await getApiKeyService().executionKey(actor, input.apiKeyId, model.apiId);
    if (model.observer && await getApiKeyService().executionKey(actor, input.apiKeyId, model.observer.apiId) !== key) return failure(403);
    // 公開frontはこのURLを全拒否します。キーの送信先は同じコンテナのloopbackプロセスだけです。
    return Response.json({ actor: { tenantId: actor.tenantId, objectId: actor.objectId }, model, key, apiKeyId: input.apiKeyId, gatewayOrigin, portalOrigin }, { headers });
  } catch (error) { return failure(error instanceof ApiKeyError && error.status < 500 ? 403 : 503); }
}
