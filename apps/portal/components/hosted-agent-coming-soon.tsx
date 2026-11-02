import { Bot } from "lucide-react";
import type { Locale } from "@/lib/i18n/config";
import { getHostedAgentDictionary } from "@/lib/i18n/hosted-agent";

/** 未公開のサンプルAPIを並べず、提供準備中であることだけを案内します。 */
export function HostedAgentComingSoon({ locale }: { locale: Locale }) {
  const text = getHostedAgentDictionary(locale);

  return <div className="mx-auto w-full max-w-[1400px]">
    <header className="mb-8">
      <p className="mb-3 text-sm font-bold tracking-[.22em] text-primary">API DOCUMENTATION</p>
      <h1 className="text-[30px] font-semibold tracking-[-.035em] sm:text-[34px]">Hosted Agent API</h1>
    </header>
    <section aria-labelledby="hosted-agent-status" className="flex min-h-80 flex-col items-center justify-center rounded-2xl border border-border bg-white px-6 py-12 text-center sm:px-10">
      <span className="mb-5 flex size-16 items-center justify-center rounded-2xl bg-muted text-primary"><Bot className="size-8" strokeWidth={1.6} aria-hidden="true" /></span>
      <h2 id="hosted-agent-status" className="text-2xl font-semibold tracking-tight">{text.status}</h2>
      <p className="mt-4 max-w-2xl text-base leading-8 text-muted-foreground">{text.description}</p>
    </section>
  </div>;
}
