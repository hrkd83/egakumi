import { Suspense } from "react";
import { notFound } from "next/navigation";
import { PortalShell } from "@/components/portal-shell";
import { ApiKeyManagement } from "@/components/api-keys/api-key-management";
import { requirePortalSession } from "@/lib/auth-session";
import { hasLocale } from "@/lib/i18n/config";
import { getDictionary } from "@/lib/i18n/dictionaries";
import { canAdministerApiKeys } from "@/lib/api-key-administration/page-access";
import Loading from "../loading";

/** 認証済みの画面が完成するまで、購読の情報を描画しません。 */
export default function ApiKeysPage({ params }: { params: Promise<{ lang: string }> }) {
  return <Suspense fallback={<Loading />}><ApiKeysContent params={params} /></Suspense>;
}

/** グループ所有のAPIキー管理画面を、サービスカタログとは独立して表示します。 */
async function ApiKeysContent({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const account = await requirePortalSession(lang, `/${lang}/api-keys`);
  const dictionary = await getDictionary(lang);
  const isPlatformAdministrator = await canAdministerApiKeys();
  return <PortalShell locale={lang} dictionary={dictionary} section="api-keys" account={account} canAdministerApiKeys={isPlatformAdministrator}>
    <ApiKeyManagement locale={lang} dictionary={dictionary} isPlatformAdministrator={isPlatformAdministrator} />
  </PortalShell>;
}
