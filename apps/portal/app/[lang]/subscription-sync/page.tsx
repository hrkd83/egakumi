import { Suspense } from "react";
import { notFound } from "next/navigation";
import { PortalShell } from "@/components/portal-shell";
import { SubscriptionSyncDashboard } from "@/components/subscription-sync/dashboard";
import { requireApiKeyAdministrator } from "@/lib/api-key-administration/page-access";
import { hasLocale } from "@/lib/i18n/config";
import { getDictionary } from "@/lib/i18n/dictionaries";
import { getSubscriptionSyncText } from "@/lib/i18n/subscription-sync";
import Loading from "../loading";

/** 全購読のメタデータは、基盤管理者をサーバーで確認してから表示します。 */
export default function SubscriptionSyncPage({ params }: { params: Promise<{ lang: string }> }) {
  return <Suspense fallback={<Loading />}><SubscriptionSyncContent params={params} /></Suspense>;
}
/** ローカルでは参照のみ、Azureの正しい台帳へ接続した実行環境だけで反映できます。 */
async function SubscriptionSyncContent({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const account = await requireApiKeyAdministrator(lang, `/${lang}/subscription-sync`);
  return <PortalShell locale={lang} dictionary={await getDictionary(lang)} section="subscription-sync" account={account} canAdministerApiKeys>
    <SubscriptionSyncDashboard text={getSubscriptionSyncText(lang)} />
  </PortalShell>;
}
