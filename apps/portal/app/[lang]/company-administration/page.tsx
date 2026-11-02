import { Suspense } from "react";
import { notFound } from "next/navigation";
import { PortalShell } from "@/components/portal-shell";
import { CompanyAdministrationDashboard } from "@/components/company-administration/dashboard";
import { requireApiKeyAdministrator } from "@/lib/api-key-administration/page-access";
import { hasLocale } from "@/lib/i18n/config";
import { getDictionary } from "@/lib/i18n/dictionaries";
import { getCompanyAdministrationText } from "@/lib/i18n/company-administration";
import Loading from "../loading";

/** 会社マスタの画面は、現在の基盤管理グループ所属を検証してから返します。 */
export default function CompanyAdministrationPage({ params }: { params: Promise<{ lang: string }> }) {
  return <Suspense fallback={<Loading />}><CompanyAdministrationContent params={params} /></Suspense>;
}

/** 会社はアプリ内の管理機能で登録し、環境paramsやEntra属性から自動生成しません。 */
async function CompanyAdministrationContent({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const account = await requireApiKeyAdministrator(lang, `/${lang}/company-administration`);
  const dictionary = await getDictionary(lang);
  return <PortalShell locale={lang} dictionary={dictionary} section="company-administration" account={account} canAdministerApiKeys>
    <CompanyAdministrationDashboard locale={lang} text={getCompanyAdministrationText(lang)} />
  </PortalShell>;
}
