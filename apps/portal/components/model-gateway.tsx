"use client";

import Link from "next/link";
import { ArrowUpRight, AudioLines, GitBranch, ImageIcon, Layers3, ListOrdered, Radio, Sparkles, type LucideIcon } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { usePortalStore } from "@/components/portal-store-provider";
import { ProviderMark } from "@/components/provider-mark";
import { groupTextModels } from "@/lib/text-model-choices";
import type { EmbeddingModel, ImageGenerationModel, ModelCategory, ModelCategoryId, ModelOperation, RealtimeModel, SpeechModel, TextGenerationModel } from "@/lib/model-types";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";

/** 機能タブを識別するアイコンです。 */
const categoryIcons: Record<ModelCategoryId, LucideIcon> = {
  text: Sparkles, embeddings: Layers3, image: ImageIcon, speech: AudioLines,
  realtime: Radio, rerank: ListOrdered, decision: GitBranch,
};

/** 1枚の提供元カードにまとめる公開情報です。 */
type ProviderGroup = { id: string; name: string; models: string[]; operations: ModelOperation[] };

/** 個別モデルのカードを量産せず、現在のタブの提供元ごとに集約します。 */
function groupProviders(category: ModelCategory, textModels: TextGenerationModel[]): ProviderGroup[] {
  const groups = new Map<string, ProviderGroup>();
  if (category.id === "text") {
    for (const choice of groupTextModels(textModels)) {
      const model = choice.variants[0];
      const group = groups.get(model.providerId) ?? { id: model.providerId, name: model.provider, models: [], operations: [] };
      group.models.push(choice.label);
      groups.set(group.id, group);
    }
  } else {
    for (const operation of category.operations) {
      const group = groups.get(operation.providerId) ?? { id: operation.providerId, name: operation.provider, models: [], operations: [] };
      group.models = [...new Set([...group.models, ...operation.models])];
      group.operations.push(operation);
      groups.set(group.id, group);
    }
  }
  // OpenAIを先頭へ置き、ほかの提供元は公開定義の順序を保ちます。
  return [...groups.values()].sort((left, right) => Number(right.id === "openai") - Number(left.id === "openai"));
}

/** 用途と提供元から、使い方ガイド・API仕様の専用ページへ案内します。 */
export function ModelGateway({ categories, models, dictionary, locale }: { categories: ModelCategory[]; models: TextGenerationModel[]; embeddingModels: EmbeddingModel[]; imageModels: ImageGenerationModel[]; speechModels: SpeechModel[]; realtimeModels: RealtimeModel[]; dictionary: Dictionary; locale: Locale; gatewayOrigin: string | null }) {
  const text = dictionary.modelGateway;
  const categoryId = usePortalStore((state) => state.modelCategory);
  const setCategory = usePortalStore((state) => state.setModelCategory);
  const category = categories.find((item) => item.id === categoryId) ?? categories[0];
  const introduction = locale === "ja"
    ? "用途と提供元からAPIを選び、使い方ガイドとAPI仕様を確認できます。各詳細ページでは、アプリへの組み込み例と実際のリクエストも試せます。"
    : "Choose a capability and provider to read its guide and API reference. Each detail page includes integration examples and lets you try a real request.";
  const openLabel = locale === "ja" ? "ガイドとAPI仕様を見る" : "Read the guide and API reference";

  return (
    <div className="mx-auto w-full min-w-0 max-w-[1400px]">
      <section className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl">
          <p className="mb-3 text-sm font-bold tracking-[.22em] text-primary">API DOCUMENTATION</p>
          <h1 className="text-[30px] font-semibold tracking-[-.035em] sm:text-[34px]">Model Gateway</h1>
          <p className="mt-3 text-[15px] leading-8 text-muted-foreground">{introduction}</p>
        </div>
      </section>

      <Tabs value={category.id} onValueChange={(value) => { if (categories.some((item) => item.id === value)) setCategory(value as ModelCategoryId); }} className="gap-0">
        <div className="mb-7 border-b border-border pb-1">
          <TabsList variant="line" aria-label="Model Gateway" className="h-auto w-full flex-wrap justify-start gap-x-2 gap-y-2 p-0">
            {categories.map((item) => { const Icon = categoryIcons[item.id]; return <TabsTrigger key={item.id} value={item.id} className="h-11 flex-none gap-2 px-2 text-sm data-active:text-primary after:bg-primary"><Icon className="size-4" />{text.categories[item.id]}</TabsTrigger>; })}
          </TabsList>
        </div>
        {categories.map((item) => {
          const providers = groupProviders(item, models);
          return <TabsContent key={item.id} value={item.id} className="mt-0 w-full min-w-0">
            <div className="mb-6 flex items-center justify-between gap-4"><p className="text-sm leading-6 text-muted-foreground">{text.descriptions[item.id]}</p><span className="shrink-0 text-sm text-muted-foreground">{providers.length} {text.providers}</span></div>
            <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
              {providers.map((provider) => <Link key={provider.id} href={`/${locale}/models/${item.id}/${provider.id}`} className="catalog-card group flex min-h-64 min-w-0 flex-col rounded-2xl border border-border bg-white p-6 text-left focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-primary">
                <div className="mb-5 flex w-full items-center justify-between"><ProviderMark providerId={provider.id} /><ArrowUpRight className="size-4 text-muted-foreground/50 transition-colors group-hover:text-primary" /></div>
                <h2 className="text-lg font-semibold tracking-tight">{provider.name}</h2>
                <p className="mt-1 text-sm text-muted-foreground">{provider.models.length ? `${provider.models.length} ${text.models}` : `${provider.operations.length} ${text.operations}`}</p>
                <div className="mb-5 mt-4 flex flex-wrap gap-1.5">{provider.models.slice(0, 3).map((name) => <span key={name} className="max-w-full truncate rounded-md bg-background px-2 py-1 font-mono text-sm text-muted-foreground">{name}</span>)}{item.id === "speech" && !provider.models.length && provider.operations.map((operation) => <span key={operation.operationId} className="rounded-md bg-background px-2 py-1 font-mono text-sm text-muted-foreground">{operation.operationId.toUpperCase()}</span>)}{provider.models.length > 3 && <span className="px-1 py-1 text-sm text-muted-foreground">{text.moreModels.replace("{count}", String(provider.models.length - 3))}</span>}</div>
                <span className="mt-auto flex w-full items-center justify-between border-t border-border/70 pt-4 text-sm font-semibold text-primary">{openLabel}<ArrowUpRight className="size-3.5" /></span>
              </Link>)}
            </div>
          </TabsContent>;
        })}
      </Tabs>

    </div>
  );
}
