import { Suspense } from "react";
import Link from "next/link";
import { ArrowLeft, ChevronRight } from "lucide-react";
import { notFound, redirect } from "next/navigation";
import { PortalShell } from "@/components/portal-shell";
import { ModelDocument } from "@/components/model-document";
import { CatalogDocument } from "@/components/catalog-document";
import { ProviderMark } from "@/components/provider-mark";
import { catalogItems, gatewaySchema } from "@/lib/catalog";
import { hasLocale } from "@/lib/i18n/config";
import { getDictionary } from "@/lib/i18n/dictionaries";
import { getGraphToolDictionary } from "@/lib/i18n/graph-tool";
import { requirePortalSession } from "@/lib/auth-session";
import { canAdministerApiKeys } from "@/lib/api-key-administration/page-access";
import { getDecisionModels, getEmbeddingModels, getImageGenerationModels, getModelCatalog, getRealtimeModels, getRerankModels, getSpeechModels, getTextGenerationModels } from "@/lib/model-catalog";
import { getPublicModelGatewayOrigin } from "@/lib/model-public-config";
import { groupRealtimeSeries } from "@/lib/realtime-series";
import Loading from "../../../loading";

/** 認証前にAPIカタログの本文を表示しない、専用ドキュメントページです。 */
export default function ApiDocumentPage({ params }: { params: Promise<{ lang: string; gateway: string; category: string; provider: string }> }) {
  return <Suspense fallback={<Loading />}><ApiDocumentContent params={params} /></Suspense>;
}

/** URLを公開カタログに照合し、ガイド・仕様・試用を同じAPIの文脈で表示します。 */
async function ApiDocumentContent({ params }: { params: Promise<{ lang: string; gateway: string; category: string; provider: string }> }) {
  const { lang, gateway, category, provider } = await params;
  const parsed = gatewaySchema.safeParse(gateway);
  if (!hasLocale(lang) || !parsed.success) notFound();
  if (gateway === "agents") redirect(`/${lang}/agents`);
  const catalog = getModelCatalog().find((entry) => entry.id === category);
  const isRealtime = gateway === "models" && category === "realtime";
  const seriesGroups = isRealtime ? groupRealtimeSeries(getRealtimeModels()) : [];
  // 以前の提供元URLも、対応する会話シリーズの詳細へ案内します。
  if (isRealtime && (provider === "openai" || provider === "azure-ai-speech")) {
    const target = provider === "openai" ? "gpt-realtime" : "voice-live-gpt";
    if (!seriesGroups.some((entry) => entry.id === target)) notFound();
    redirect(`/${lang}/models/realtime/${target}`);
  }
  const series = seriesGroups.find((entry) => entry.id === provider);
  const seriesApiIds = new Set(series?.models.flatMap((model) => [model.apiId, ...(model.observer ? [model.observer.apiId] : [])]));
  const operations = catalog?.operations.filter((operation) => isRealtime ? seriesApiIds.has(operation.apiId) : operation.providerId === provider) ?? [];
  const models = getTextGenerationModels().filter((model) => model.providerId === provider);
  const item = catalogItems.find((entry) => entry.gateway === gateway && entry.capability === category && entry.id === provider);
  if (gateway === "models" ? !catalog || !operations.length && !(category === "text" && models.length) : !item) notFound();
  const account = await requirePortalSession(lang, `/${lang}/${gateway}/${category}/${provider}`);
  const dictionary = await getDictionary(lang);
  const title = gateway === "models" ? series?.label ?? operations[0]?.provider ?? models[0]?.provider : item!.title;
  const subtitle = gateway === "models" ? dictionary.modelGateway.categories[catalog!.id] : item!.protocol.toUpperCase();
  const description = series ? dictionary.modelGateway.realtimeSeriesDescriptions[series.id] : gateway === "models" ? dictionary.modelGateway.descriptions[catalog!.id] : "";
  return <PortalShell locale={lang} dictionary={dictionary} gateway={parsed.data} account={account} canAdministerApiKeys={await canAdministerApiKeys()}>
    <div className="mx-auto max-w-[1440px]">
      <nav aria-label={lang === "ja" ? "パンくず" : "Breadcrumb"} className="mb-8 flex flex-wrap items-center gap-2 text-sm text-muted-foreground"><Link href={`/${lang}/${gateway}`} className="inline-flex items-center gap-2 hover:text-primary"><ArrowLeft className="size-4" />{dictionary.catalog.titles[parsed.data]}</Link><ChevronRight className="size-3" /><span>{subtitle}</span><ChevronRight className="size-3" /><span className="font-medium text-foreground">{title}</span></nav>
      {gateway === "models" && <header className="mb-9"><div className="flex items-center gap-4"><ProviderMark providerId={series?.providerId ?? provider} /><div><p className="mb-1 text-sm font-semibold text-primary">{subtitle}</p><h1 className="text-4xl font-semibold tracking-tight">{title}</h1></div></div><p className="mt-4 max-w-4xl text-base leading-8 text-muted-foreground">{description}</p></header>}
      {gateway === "models" ? <ModelDocument key={`${category}/${provider}`} category={catalog!.id} models={models} embeddingModels={getEmbeddingModels().filter((model) => model.providerId === provider)} imageModels={getImageGenerationModels().filter((model) => model.providerId === provider)} speechModels={getSpeechModels().filter((model) => model.providerId === provider)} realtimeModels={series?.models ?? []} rerankModels={getRerankModels().filter((model) => model.providerId === provider)} decisionModels={getDecisionModels().filter((model) => model.providerId === provider)} operations={operations} dictionary={dictionary} locale={lang} gatewayOrigin={getPublicModelGatewayOrigin()} /> : <CatalogDocument item={item!} dictionary={dictionary} locale={lang} gatewayOrigin={getPublicModelGatewayOrigin()} graph={item!.id === "graph-profile" ? { text: getGraphToolDictionary(lang), gatewayOrigin: getPublicModelGatewayOrigin() } : undefined} />}
    </div>
  </PortalShell>;
}
