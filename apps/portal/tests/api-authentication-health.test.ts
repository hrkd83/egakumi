import assert from "node:assert/strict";
import { test } from "node:test";

type ResolveContext = { parentURL?: string };
type Resolution = { url: string; shortCircuit?: boolean };
type Hooks = { registerHooks(hooks: { resolve(specifier: string, context: ResolveContext,
  next: (specifier: string, context: ResolveContext) => Resolution): Resolution }): { deregister(): void } };

/** 試用APIの暗号鍵障害を再現し、通常のPortalを稼働させる二つの実ルートを確認します。 */
test("試用OAuthの準備失敗はPortal本体を停止させず、同時プローブは一度だけ検査する", async () => {
  const bridgeName = "__egakumi_test_api_auth_health";
  const bridge = { enabled: false, probes: 0, cleanup: 0 };
  const globals = globalThis as typeof globalThis & Record<string, unknown>;
  globals[bridgeName] = bridge;
  const access = `globalThis[${JSON.stringify(bridgeName)}]`;
  const generic = new URL("../app/api/health/ready/route.ts", import.meta.url).href;
  const oauth = new URL("../app/api/health/api-auth/route.ts", import.meta.url).href;
  const imports: Record<string, string> = {
    "next/server": "export const connection=async()=>{}",
    "@/lib/api-keys/gateway-profile": "export const getGatewayProfileSettings=()=>({tenantId:'example'})",
    "@/lib/api-keys/limit-repository": "export const getApiKeyLimitRepository=()=>({getGatewayProfile:async()=>{}})",
    "@/lib/company-administration/repository": "export const getCompanyRepository=()=>({assertReady:async()=>{}})",
    "@/lib/connection-session/store": "export const assertConnectionSessionStoreReady=async()=>{}",
    "@/lib/subscription-service/client": "export const subscriptionManagerOrigin=()=>null",
    "@/lib/api-auth/settings": `export const apiAuthenticationEnabled=()=>${access}.enabled`,
    "@/lib/api-auth/service": `export const scheduleApiAuthCleanup=()=>{${access}.cleanup++};
      export const getPortalApiAuthentication=()=>({assertReady:async()=>{${access}.probes++;throw new Error('synthetic key failure')}})`,
  };
  const { registerHooks } = await import("node:module") as unknown as Hooks;
  const hooks = registerHooks({ resolve(specifier, context, next) {
    const source = (context.parentURL === generic || context.parentURL === oauth) && imports[specifier];
    return source ? { url: `data:text/javascript,${encodeURIComponent(source)}`, shortCircuit: true } : next(specifier, context);
  } });
  try {
    const oauthHealth = (await import(oauth) as { GET(): Promise<Response> }).GET;
    const normalHealth = (await import(generic) as { GET(): Promise<Response> }).GET;
    const disabled = await oauthHealth();
    assert.equal(disabled.status, 404);
    assert.deepEqual(await disabled.json(), { status: "disabled" });
    assert.equal(bridge.probes, 0);
    bridge.enabled = true;
    const responses = await Promise.all([oauthHealth(), oauthHealth(), oauthHealth(), normalHealth()]);
    for (const response of responses.slice(0, 3)) {
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { status: "unavailable" });
    }
    assert.equal(responses[3].status, 200);
    assert.deepEqual(await responses[3].json(), { status: "ready" });
    assert.equal(bridge.probes, 1);
    assert.equal(bridge.cleanup, 1);
  } finally {
    hooks.deregister();
    delete globals[bridgeName];
  }
});
