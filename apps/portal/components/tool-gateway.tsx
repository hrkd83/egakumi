"use client";

import Link from "next/link";
import { ArrowUpRight, FileSearch, ShieldCheck, UserRound, Network, type LucideIcon } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { usePortalStore } from "@/components/portal-store-provider";
import { catalogItems, type CatalogItem } from "@/lib/catalog";
import type { Locale } from "@/lib/i18n/config";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { GraphToolDictionary } from "@/lib/i18n/graph-tool";

/** 各ツールの用途を示すアイコンです。会社名の補足表記は重ねません。 */
const icons: Partial<Record<CatalogItem["capability"], LucideIcon>> = {
  identity: UserRound, privacy: ShieldCheck, document: FileSearch,
};

/** Model Gatewayと同じ幅とカード構成で、各ツールのガイドへ案内します。 */
export function ToolGateway({ locale, dictionary }: { locale: Locale; dictionary: Dictionary; graphText: GraphToolDictionary; gatewayOrigin: string | null }) {
  const items = catalogItems.filter((item) => item.gateway === "tools");
  const protocol = usePortalStore((state) => state.toolProtocol);
  const setProtocol = usePortalStore((state) => state.setToolProtocol);
  const ja = locale === "ja";
  return <div className="mx-auto w-full min-w-0 max-w-[1440px]">
    <header className="mb-10 max-w-4xl">
      <p className="mb-5 text-sm font-semibold tracking-[.22em] text-primary">API DOCUMENTATION</p>
      <h1 className="text-4xl font-semibold tracking-tight">Tool Gateway</h1>
      <p className="mt-5 max-w-3xl text-base leading-8 text-muted-foreground">{ja
        ? "ユーザー情報の取得、個人情報のマスキング、文書からの情報抽出。各APIの使い方と仕様を確認し、実際のリクエストを試せます。"
        : "Retrieve user information, redact personal data, and extract document content. Explore each API's guide and specification, then try a request."}</p>
    </header>
    <Tabs value={protocol} onValueChange={(value) => setProtocol(value as "rest" | "mcp" | "a2a")}>
    <TabsList aria-label={ja ? "Tool Gatewayの接続方式" : "Tool Gateway protocols"} className="mb-7 h-auto w-full justify-start gap-2 rounded-none border-b border-border bg-transparent p-0">
      {(["rest", "mcp", "a2a"] as const).map((value) => <TabsTrigger key={value} value={value} className="rounded-none border-b-2 border-transparent px-4 py-3 text-base shadow-none data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:text-primary data-[state=active]:shadow-none">{value.toUpperCase()}</TabsTrigger>)}
    </TabsList>
    {(["rest", "mcp", "a2a"] as const).map((value) => <TabsContent key={value} value={value} className="mt-0 w-full min-w-0">
    <p className="mb-6 text-sm leading-7 text-muted-foreground">{value === "rest"
      ? (ja ? "HTTPのリクエストと応答を使って、アプリケーションから呼び出します。" : "Call tools using HTTP requests and responses from your application.")
      : value === "mcp" ? (ja ? "Model Context Protocolでツールを見つけ、対応クライアントから呼び出します。" : "Discover and call tools from clients that support Model Context Protocol.")
      : (ja ? "Agent2Agentプロトコルで接続するAPIは、今後こちらに掲載します。" : "APIs using the Agent2Agent protocol will appear here when available.")}</p>
    {value === "a2a" ? <div className="rounded-3xl border border-border bg-white p-10 text-center"><Network className="mx-auto mb-4 size-8 text-primary" aria-hidden="true" /><h2 className="text-xl font-semibold">{ja ? "準備中" : "Coming soon"}</h2><p className="mt-3 text-sm text-muted-foreground">{ja ? "現在、公開しているA2A APIはありません。" : "No A2A APIs are currently available."}</p></div> : <>
    <div className="mb-6 flex items-center justify-between gap-4">
      <h2 className="text-lg font-semibold">{ja ? "利用できるAPI" : "Available APIs"}</h2>
      <span className="text-sm text-muted-foreground">{items.filter((item) => item.protocol === value).length} {ja ? "API" : "APIs"}</span>
    </div>
    <div className="grid w-full min-w-0 gap-5 md:grid-cols-2 xl:grid-cols-3">
      {items.filter((item) => item.protocol === value).map((item) => {
        const Icon = icons[item.capability] ?? FileSearch;
        return <Link key={item.id} href={`/${locale}/tools/${item.capability}/${item.id}`}
          className="group flex min-w-0 flex-col rounded-3xl border border-border bg-white p-6 transition-colors hover:border-primary/40 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-primary">
          <div className="mb-6 flex items-center justify-between">
            <span className="inline-flex size-14 items-center justify-center rounded-2xl bg-white text-primary ring-1 ring-inset ring-slate-200"><Icon className="size-7" strokeWidth={1.6} aria-hidden="true" /></span>
            <ArrowUpRight className="size-4 text-muted-foreground/60 transition-colors group-hover:text-primary" aria-hidden="true" />
          </div>
          <h3 className="text-xl font-semibold tracking-tight">{item.title}</h3>
          <p className="mt-3 flex-1 text-sm leading-7 text-muted-foreground">{item.description[locale]}</p>
          <div className="mt-6 flex flex-wrap items-center gap-2 font-mono text-sm text-muted-foreground"><span className="font-semibold text-primary">{item.method}</span><span className="break-all">{item.endpoint}</span></div>
          <div className="mt-6 flex items-center justify-between gap-3 border-t border-border pt-4 text-sm font-semibold text-primary"><span>{ja ? "ガイドとAPI仕様を見る" : "View guide and API reference"}</span><ArrowUpRight className="size-4 shrink-0" aria-hidden="true" /></div>
        </Link>;
      })}
    </div>
    </>}
    </TabsContent>)}
    </Tabs>
    <p className="mt-7 text-sm leading-7 text-muted-foreground">{ja
      ? "APIキーによる認可を共通で利用します。Microsoft Graph /meでは、アクセスする本人のMicrosoft認証も必要です。"
      : "All APIs use API-key authorization. Microsoft Graph /me also requires the requesting user's Microsoft authentication."}</p>
    <span className="sr-only">{dictionary.catalog.titles.tools}</span>
  </div>;
}
