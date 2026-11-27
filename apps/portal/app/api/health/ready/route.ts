import { getApiAuthStore } from "@/lib/api-auth/store";
import { scheduleApiAuthCleanup } from "@/lib/api-auth/service";
import { connection } from "next/server";

let expiresAt = 0;
let check: Promise<boolean> | undefined;

/** 同時プローブをまとめ、外部から連続で呼ばれてもSQL照会を増幅させません。 */
function readiness(): Promise<boolean> {
  if (check && Date.now() < expiresAt) return check;
  expiresAt = Number.POSITIVE_INFINITY;
  check = (async () => {
    try {
      await getApiAuthStore().assertReady();
      scheduleApiAuthCleanup();
      return true;
    } catch { return false; }
  })().finally(() => { expiresAt = Date.now() + 5_000; });
  return check;
}

/** 実行IDで認可スキーマを照会してSQLへの接続を確認します。 */
export async function GET(): Promise<Response> {
  await connection();
  if (await readiness()) {
    return Response.json({ status: "ready" }, { headers: { "Cache-Control": "no-store" } });
  } else {
    return Response.json({ status: "unavailable" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
