import "server-only";
import { notFound, redirect } from "next/navigation";
import { readPortalPrincipal, type PortalAccount } from "../auth-session";
import type { Locale } from "../i18n/config";
import { getApiKeyAdministrationService } from "./service";

/** ナビゲーションはサーバーで確認できた管理権限だけを使い、確認失敗時は表示しません。 */
export async function canAdministerApiKeys(): Promise<boolean> {
  const actor = await readPortalPrincipal();
  if (!actor) return false;
  try { return await getApiKeyAdministrationService().isAdmin(actor); }
  catch { return false; }
}

/** 管理ページのHTMLや全組織の台帳を返す前に、現在の基盤管理グループ所属を確認します。 */
export async function requireApiKeyAdministrator(locale: Locale, nextPath = `/${locale}/api-key-administration`): Promise<PortalAccount> {
  const actor = await readPortalPrincipal();
  if (!actor) redirect(`/${locale}/login?next=${encodeURIComponent(nextPath)}`);
  if (!await getApiKeyAdministrationService().isAdmin(actor)) notFound();
  return { name: actor.name, email: actor.email };
}
