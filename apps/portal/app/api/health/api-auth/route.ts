import { apiAuthenticationEnabled } from "@/lib/api-auth/settings";
import { getPortalApiAuthentication } from "@/lib/api-auth/service";
import { connection } from "next/server";

const responseHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
let expiresAt = 0;
let pending: Promise<boolean> | undefined;

function readiness(): Promise<boolean> {
  if (pending && Date.now() < expiresAt) return pending;
  expiresAt = Infinity;
  pending = Promise.resolve().then(async () => {
    await getPortalApiAuthentication().assertReady();
    return true;
  }).catch(() => false).finally(() => { expiresAt = Date.now() + 5_000; });
  return pending;
}

/** 配備時に試用用SQLと暗号鍵だけを確認します。Portal本体のACAプローブは独立しています。 */
export async function GET(): Promise<Response> {
  await connection();
  if (!apiAuthenticationEnabled()) {
    return Response.json({ status: "disabled" }, { status: 404, headers: responseHeaders });
  }
  const available = await readiness();
  return Response.json({ status: available ? "ready" : "unavailable" }, {
    status: available ? 200 : 503, headers: responseHeaders,
  });
}
