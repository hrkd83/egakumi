import { getGatewayProfileSettings } from "@/lib/api-keys/gateway-profile";
import { getApiKeyLimitRepository } from "@/lib/api-keys/limit-repository";
import { getCompanyRepository } from "@/lib/company-administration/repository";
import { assertConnectionSessionStoreReady } from "@/lib/connection-session/store";
import { connection } from "next/server";

let expiresAt = 0;
let check: Promise<boolean> | undefined;

/** 同時プローブをまとめ、外部から連続で呼ばれてもSQL照会を増幅させません。 */
function readiness(): Promise<boolean> {
  if (check && Date.now() < expiresAt) return check;
  expiresAt = Number.POSITIVE_INFINITY;
  check = (async () => {
    try {
      const settings = getGatewayProfileSettings();
      await getApiKeyLimitRepository().getGatewayProfile(settings.tenantId, "profile-readiness");
      await getCompanyRepository().assertReady();
      await assertConnectionSessionStoreReady();
      return true;
    } catch { return false; }
  })().finally(() => { expiresAt = Date.now() + 5_000; });
  return check;
}

/** 実行IDによるSQL照会と専用鍵の準備を確認し、接続情報は返しません。 */
export async function GET(): Promise<Response> {
  await connection();
  if (await readiness()) {
    return Response.json({ status: "ready" }, { headers: { "Cache-Control": "no-store" } });
  } else {
    return Response.json({ status: "unavailable" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
