import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { getApiKeyAdministrationRepository } from "../api-key-administration/repository.ts";
import { getApiKeyEnvironment } from "../api-keys/environment.ts";
import { getGatewayProfileSettings } from "../api-keys/gateway-profile.ts";
import { getApiKeyLimitRepository } from "../api-keys/limit-repository.ts";
import { getAuthEnvironment } from "../auth-environment.ts";
import { getCompanyRepository } from "../company-administration/repository.ts";
import { handleSubscriptionService, type SubscriptionServiceRole } from "./handler.ts";
import { createSubscriptionActorVerifier, subscriptionIdentitySettings } from "./identity.ts";

const maxBodyBytes = 65_536;

/** 信頼済み内部ルートに必要なHTTP情報だけを受け取り、受信本文を制限します。 */
async function requestFrom(incoming: IncomingMessage): Promise<Request | null> {
  if (!incoming.url?.startsWith("/") || incoming.url.startsWith("//") || incoming.url.includes("\\")) return null;
  const method = incoming.method ?? "GET";
  const headers = new Headers();
  for (const [name, value] of Object.entries(incoming.headers)) {
    if (Array.isArray(value)) for (const item of value) headers.append(name, item);
    else if (typeof value === "string") headers.set(name, value);
  }
  const chunks: Buffer[] = [];
  let length = 0;
  for await (const chunk of incoming) {
    const data = Buffer.from(chunk);
    length += data.length;
    if (length > maxBodyBytes) return null;
    chunks.push(data);
  }
  if (length && method !== "POST") return null;
  return new Request(new URL(incoming.url, "https://subscription-service.invalid"), {
    method, headers, ...(length ? { body: Buffer.concat(chunks).toString("utf8") } : {}),
  });
}

/** 内部応答の秘密をログに出さず、検証済みポリシーだけを実行するアプリを起動します。 */
export function serveSubscriptionService(role: SubscriptionServiceRole) {
  const port = Number(process.env.PORT ?? 3000);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid service port");
  const verify = role === "management" ? createSubscriptionActorVerifier(subscriptionIdentitySettings()) : async () => {
    throw new Error("Invalid service role");
  };
  const profile = {
    settings: getGatewayProfileSettings,
    readProfile: (tenantId: string, subscriptionId: string) => getApiKeyLimitRepository().getGatewayProfile(tenantId, subscriptionId),
  };
  let readyUntil = 0;
  let ready: Promise<boolean> | undefined;
  const checkReady = () => {
    if (ready && Date.now() < readyUntil) return ready;
    readyUntil = Number.POSITIVE_INFINITY;
    ready = (async () => {
      if (role === "profile") {
        const settings = profile.settings();
        await profile.readProfile(settings.tenantId, "profile-readiness");
      } else {
        const auth = getAuthEnvironment();
        getApiKeyEnvironment();
        await getApiKeyAdministrationRepository().getSettings(auth.microsoftTenantId);
        await getCompanyRepository().assertReady();
      }
      return true;
    })().catch(() => false).finally(() => { readyUntil = Date.now() + 5000; });
    return ready;
  };
  const send = async (response: ServerResponse, result: Response) => {
    response.writeHead(result.status, Object.fromEntries(result.headers.entries()));
    response.end(await result.text());
  };
  const server = createServer({ requestTimeout: 20_000, headersTimeout: 10_000 }, (incoming, outgoing) => {
    void (async () => {
      const request = await requestFrom(incoming);
      if (!request) {
        await send(outgoing, Response.json({ error: { code: "InvalidRequest" } }, { status: 400, headers: { "Cache-Control": "no-store" } }));
        return;
      }
      if (request.method === "GET" && incoming.url === "/api/health/ready") {
        const ok = await checkReady();
        await send(outgoing, Response.json({ status: ok ? "ready" : "unavailable" },
          { status: ok ? 200 : 503, headers: { "Cache-Control": "no-store" } }));
        return;
      }
      await send(outgoing, await handleSubscriptionService(request, role, { readActor: verify, profile }));
    })().catch(() => {
      if (!outgoing.headersSent) outgoing.writeHead(503, { "Cache-Control": "no-store" });
      outgoing.end();
    });
  });
  server.on("error", () => { console.error("Subscription service could not listen"); process.exitCode = 1; });
  for (const signal of ["SIGINT", "SIGTERM"] as const) process.once(signal, () => server.close());
  return server.listen(port, "0.0.0.0");
}

export function runSubscriptionService(role: SubscriptionServiceRole) {
  try {
    if (process.env.PORTAL_SUBSCRIPTION_SERVICE_ROLE !== role) throw new Error("Invalid service role");
    serveSubscriptionService(role);
  } catch {
    console.error("Subscription service configuration failed");
    process.exitCode = 1;
  }
}
