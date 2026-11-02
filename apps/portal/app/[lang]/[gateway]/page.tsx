import { Suspense } from "react";
import { notFound } from "next/navigation";
import { HostedAgentComingSoon } from "@/components/hosted-agent-coming-soon";
import { ModelGateway } from "@/components/model-gateway";
import { ToolGateway } from "@/components/tool-gateway";
import { PortalShell } from "@/components/portal-shell";
import { gatewaySchema } from "@/lib/catalog";
import { hasLocale } from "@/lib/i18n/config";
import { getDictionary } from "@/lib/i18n/dictionaries";
import { getGraphToolDictionary } from "@/lib/i18n/graph-tool";
import { requirePortalSession } from "@/lib/auth-session";
import { getEmbeddingModels, getImageGenerationModels, getModelCatalog, getRealtimeModels, getSpeechModels, getTextGenerationModels } from "@/lib/model-catalog";
import { getPublicModelGatewayOrigin } from "@/lib/model-public-config";
import Loading from "../loading";
import { canAdministerApiKeys } from "@/lib/api-key-administration/page-access";

/** ビルド時に用意する、カタログのサービス区分です。 */
export function generateStaticParams() {
  return gatewaySchema.options.map((gateway) => ({ gateway }));
}

/**
 * 無地の背景で認証確認を待ち、確認後にだけカタログを返します。
 * @param props - 言語とサービス区分を含むルートパラメーター。
 * @returns URLの値を読む子コンポーネントを包むSuspense境界。
 * @remarks パラメーターはここで解決せず、共通シェルの内側へ渡します。
 */
export default function GatewayPage({ params }: PageProps<"/[lang]/[gateway]">) {
  return (
    <Suspense fallback={<Loading />}>
      <GatewayContent params={params} />
    </Suspense>
  );
}

/**
 * URLで指定されたゲートウェイを検証し、対応するカタログを表示します。
 * @param props - Suspense境界内で解決する言語とサービス区分。
 * @returns Model・Tool・Hosted Agentのいずれかの画面。
 */
async function GatewayContent({ params }: Pick<PageProps<"/[lang]/[gateway]">, "params">) {
  const { lang, gateway } = await params;
  const parsed = gatewaySchema.safeParse(gateway);
  if (!hasLocale(lang) || !parsed.success) notFound();
  const account = await requirePortalSession(lang, `/${lang}/${parsed.data}`);
  const dictionary = await getDictionary(lang);
  return <PortalShell locale={lang} dictionary={dictionary} gateway={parsed.data} account={account} canAdministerApiKeys={await canAdministerApiKeys()}>
    {parsed.data === "models"
      ? <ModelGateway locale={lang} dictionary={dictionary} categories={getModelCatalog()} models={getTextGenerationModels()} embeddingModels={getEmbeddingModels()} imageModels={getImageGenerationModels()} speechModels={getSpeechModels()} realtimeModels={getRealtimeModels()} gatewayOrigin={getPublicModelGatewayOrigin()} />
      : parsed.data === "tools" ? <ToolGateway locale={lang} dictionary={dictionary} graphText={getGraphToolDictionary(lang)} gatewayOrigin={getPublicModelGatewayOrigin()} />
      : <HostedAgentComingSoon locale={lang} />}
  </PortalShell>;
}
