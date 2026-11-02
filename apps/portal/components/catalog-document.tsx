"use client";

import { GraphToolPanel } from "@/components/tools/graph-tool-panel";
import { PiiRedactionPanel } from "@/components/tools/pii-redaction-panel";
import { PiiMcpPanel } from "@/components/tools/pii-mcp-panel";
import { DocumentAnalysisPanel } from "@/components/tools/document-analysis-panel";
import type { CatalogItem } from "@/lib/catalog";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";
import type { GraphToolDictionary } from "@/lib/i18n/graph-tool";

/** 各ツールの専用URLに、同じ構成のガイド・API仕様・実行画面を表示します。 */
export function CatalogDocument({ item, locale, graph, dictionary, gatewayOrigin }: {
  item: CatalogItem; dictionary: Dictionary; locale: Locale;
  gatewayOrigin: string | null;
  graph?: { text: GraphToolDictionary; gatewayOrigin: string | null };
}) {
  const isLiveGraph = item.gateway === "tools" && item.id === "graph-profile" && graph;

  return <div className="mx-auto max-w-[1500px] space-y-8">
    <header className="space-y-5 border-b border-border pb-7">
      <div className="space-y-3">
        <p className="text-sm font-semibold text-primary">{item.protocol.toUpperCase()} · {dictionary.catalog.capabilities[item.capability]}</p>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">{item.title}</h1>
        <p className="max-w-3xl text-base leading-8 text-muted-foreground">{item.description[locale]}</p>
      </div>
    </header>
    {isLiveGraph ? <GraphToolPanel text={graph.text} locale={locale} gatewayOrigin={graph.gatewayOrigin} />
      : item.id === "pii-redact" ? <PiiRedactionPanel dictionary={dictionary} locale={locale} gatewayOrigin={gatewayOrigin} />
      : item.id === "pii-redact-mcp" ? <PiiMcpPanel dictionary={dictionary} locale={locale} gatewayOrigin={gatewayOrigin} />
      : item.id === "document-analysis" ? <DocumentAnalysisPanel dictionary={dictionary} locale={locale} gatewayOrigin={gatewayOrigin} />
      : <p>{locale === "ja" ? "準備中です。" : "Coming soon."}</p>}
  </div>;
}
