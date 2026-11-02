import { Suspense } from "react";
import { notFound } from "next/navigation";
import { PortalShell } from "@/components/portal-shell";
import { ApiKeyAdministrationDashboard } from "@/components/api-key-administration/dashboard";
import { requireApiKeyAdministrator } from "@/lib/api-key-administration/page-access";
import { hasLocale } from "@/lib/i18n/config";
import { getDictionary } from "@/lib/i18n/dictionaries";
import Loading from "../loading";

/** 管理権限の検証が終わるまで、グループ情報を含む画面は返しません。 */
export default function ApiKeyAdministrationPage({ params }: { params: Promise<{ lang: string }> }) {
  return <Suspense fallback={<Loading />}><AdministrationContent params={params} /></Suspense>;
}

/** 全組織の所有グループの情報は、現在の基盤管理メンバーだけに表示します。 */
async function AdministrationContent({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const account = await requireApiKeyAdministrator(lang);
  const dictionary = await getDictionary(lang);
  return <PortalShell locale={lang} dictionary={dictionary} section="api-key-administration" account={account} canAdministerApiKeys>
    <ApiKeyAdministrationDashboard dictionary={dictionary} locale={lang} />
  </PortalShell>;
}
