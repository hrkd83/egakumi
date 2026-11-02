"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Bot, BookOpen, Building2, ChevronRight, CircleHelp, Cpu, Globe2, KeyRound, Menu, ShieldCheck, Wrench, X } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { GuideDialog } from "@/components/guide-dialog";
import { AccountMenu } from "@/components/account-menu";
import type { PortalAccount } from "@/lib/auth-session";
import { usePortalStore } from "@/components/portal-store-provider";
import type { Gateway } from "@/lib/catalog";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";
import { getHostedAgentDictionary } from "@/lib/i18n/hosted-agent";
import brand from "@/lib/brand.json";

/** 3種類のゲートウェイと、ナビゲーションに用いるアイコンです。 */
const navigation = [
  { id: "models", label: "modelGateway", icon: Cpu },
  { id: "tools", label: "toolGateway", icon: Wrench },
  { id: "agents", label: "hostedAgent", icon: Bot },
] as const;

/** サイドバーとコンテンツに共有する、検証済みの表示情報です。 */
type ShellProps = {
  locale: Locale; dictionary: Dictionary; children: ReactNode; account: PortalAccount; canAdministerApiKeys?: boolean;
} & ({ gateway: Gateway; section?: never } | { section: "api-keys" | "api-key-administration" | "company-administration" | "subscription-sync"; gateway?: never });

/**
 * 元のブランド画像を使い、サービスの切り替えとガイドを表示します。
 * @param props - 現在の言語、サービス区分、表示文言。
 * @returns デスクトップとモバイルで共有するサイドバーの内容。
 */
function Sidebar({ locale, dictionary, gateway, section, canAdministerApiKeys }: Omit<ShellProps, "children">) {
  const resetFilters = usePortalStore((state) => state.resetFilters);

  return (
    <div className="flex h-full min-w-0 flex-col overflow-y-auto px-5 pb-5 pt-7">
      <Link href={`/${locale}`} className="brand-link mb-8 flex items-center gap-2.5 px-1" aria-label={`${brand.name} AI Gateway`} onClick={resetFilters}>
        <Image src="/brand/egakumi-symbol.webp" width={38} height={38} alt="" className="size-9 shrink-0" priority />
        <span className="min-w-0">
          <Image src="/brand/egakumi-wordmark.webp" width={150} height={50} alt={brand.name} className="-my-1.5 h-auto w-36" priority />
          <span className="mt-0.5 block whitespace-nowrap pl-1 text-xs font-semibold tracking-wider text-muted-foreground">{locale === "ja" ? brand.tagline : dictionary.common.tagline}</span>
        </span>
      </Link>

      <div className="mb-8 flex items-center gap-2.5 border-y border-border px-2 py-4">
        <BookOpen className="size-5 shrink-0 text-primary" />
        <div className="min-w-0"><p className="text-sm font-semibold">API Documentation</p><p className="mt-0.5 text-xs text-muted-foreground">{locale === "ja" ? "使い方・仕様・実行例" : "Guides, reference & examples"}</p></div>
      </div>

      <p className="mb-3 px-3 text-xs font-semibold tracking-[.18em] text-muted-foreground">SERVICES</p>
      <nav aria-label={dictionary.common.serviceNavigation} className="space-y-1.5">
        {navigation.map(({ id, label, icon: Icon }) => (
          <Link key={id} href={`/${locale}/${id}`} onClick={resetFilters} aria-current={gateway === id ? "page" : undefined} className={`nav-item flex h-12 items-center gap-3 rounded-lg px-3 text-sm font-medium ${gateway === id ? "bg-primary/9 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}>
            <Icon className="size-[18px] shrink-0" strokeWidth={1.7} />
            <span>{dictionary.navigation[label]}</span>
            {gateway === id && <ChevronRight className="ml-auto size-3.5" />}
          </Link>
        ))}
      </nav>

      <p className="mb-3 mt-8 px-3 text-xs font-semibold tracking-[.18em] text-muted-foreground">{dictionary.navigation.management}</p>
      <nav aria-label={dictionary.navigation.management} className="space-y-1.5">
        <Link href={`/${locale}/api-keys`} onClick={resetFilters} aria-current={section === "api-keys" ? "page" : undefined} className={`nav-item flex h-12 items-center gap-3 rounded-lg px-3 text-sm font-medium ${section === "api-keys" ? "bg-primary/9 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}>
          <KeyRound className="size-[18px] shrink-0" strokeWidth={1.7} /><span>{dictionary.navigation.apiKeys}</span>
          {section === "api-keys" && <ChevronRight className="ml-auto size-3.5" />}
        </Link>
      </nav>

      {canAdministerApiKeys && <>
        <p className="mb-3 mt-8 px-3 text-xs font-semibold tracking-wide text-muted-foreground">{dictionary.navigation.systemAdministration}</p>
        <nav aria-label={dictionary.navigation.systemAdministration} className="space-y-1.5">
        <Link href={`/${locale}/subscription-sync`} onClick={resetFilters} aria-current={section === "subscription-sync" ? "page" : undefined} className={`nav-item flex h-12 items-center gap-3 rounded-lg px-3 text-sm font-medium ${section === "subscription-sync" ? "bg-primary/9 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}>
          <ShieldCheck className="size-[18px] shrink-0" strokeWidth={1.7} /><span>{dictionary.navigation.subscriptionSync}</span>
          {section === "subscription-sync" && <ChevronRight className="ml-auto size-3.5" />}
        </Link>
        <Link href={`/${locale}/api-key-administration`} onClick={resetFilters} aria-current={section === "api-key-administration" ? "page" : undefined} className={`nav-item flex h-12 items-center gap-3 rounded-lg px-3 text-sm font-medium ${section === "api-key-administration" ? "bg-primary/9 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}>
          <ShieldCheck className="size-[18px] shrink-0" strokeWidth={1.7} /><span>{dictionary.navigation.apiKeyAdministration}</span>
          {section === "api-key-administration" && <ChevronRight className="ml-auto size-3.5" />}
        </Link>
        <Link href={`/${locale}/company-administration`} onClick={resetFilters} aria-current={section === "company-administration" ? "page" : undefined} className={`nav-item flex h-12 items-center gap-3 rounded-lg px-3 text-sm font-medium ${section === "company-administration" ? "bg-primary/9 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}>
          <Building2 className="size-[18px] shrink-0" strokeWidth={1.7} /><span>{dictionary.navigation.companyAdministration}</span>
          {section === "company-administration" && <ChevronRight className="ml-auto size-3.5" />}
        </Link>
        </nav>
      </>}

      <div className="mt-auto pt-10">
        <div className="rounded-xl border border-border bg-muted/50 px-4 pb-2 pt-4">
          <span className="mb-2 flex size-8 items-center justify-center rounded-lg bg-white text-primary"><CircleHelp className="size-[17px]" /></span>
          <p className="text-xs font-semibold leading-6">{dictionary.catalog.guide.title}</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{dictionary.catalog.guide.description}</p>
          <div className="mt-1"><GuideDialog dictionary={dictionary} compact /></div>
        </div>
        <div className="mt-5 flex items-center gap-2 px-2 text-xs text-muted-foreground"><span className={`size-1.5 rounded-full ${gateway === "models" || gateway === "tools" || section ? "bg-primary" : "bg-amber-500"}`} />{section === "api-key-administration" || section === "company-administration" || section === "subscription-sync" ? dictionary.apiKeyAdministration.adminOnly : section === "api-keys" ? dictionary.apiKeys.groupOwned : gateway === "agents" ? getHostedAgentDictionary(locale).status : dictionary.modelGateway.connected}<span className="ml-auto">v0.1</span></div>
      </div>
    </div>
  );
}

/**
 * 左サイドバーと右コンテンツからなる、Portalの基本画面を構成します。
 * @param props - 言語・表示文言・現在のゲートウェイと、メインコンテンツ。
 * @returns レスポンシブ対応の画面枠。
 */
export function PortalShell({ children, ...props }: ShellProps) {
  const { locale, dictionary, gateway, section } = props;
  const mobileMenuButtonRef = useRef<HTMLButtonElement | null>(null);
  const pathname = usePathname();
  const router = useRouter();
  const mobileNavOpen = usePortalStore((state) => state.mobileNavOpen);
  const setMobileNavOpen = usePortalStore((state) => state.setMobileNavOpen);

  /** 選択中のサービスとクエリを保ったまま、URLの言語部分を切り替えます。 */
  function changeLanguage(language: string) {
    const nextPath = pathname.replace(/^\/(ja|en)(?=\/|$)/, `/${language}`);
    const query = new URLSearchParams(window.location.search).toString();
    router.push(query ? `${nextPath}?${query}` : nextPath);
  }

  return (
    <div className="portal-layout">
      <aside className="portal-sidebar hidden lg:block"><Sidebar {...props} /></aside>
      <div className="min-w-0">
        <header className="portal-topbar flex items-center justify-between gap-4 border-b border-border bg-white px-5 lg:px-9">
          <div className="flex min-w-0 items-center gap-3">
            <Button ref={mobileMenuButtonRef} variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileNavOpen(true)} aria-label={dictionary.common.openMenu}><Menu className="size-5" /></Button>
            <span className="hidden text-sm text-muted-foreground sm:block">egakumi</span><ChevronRight className="hidden size-3.5 text-muted-foreground/60 sm:block" />
            <span className="truncate text-xs font-medium">{section === "subscription-sync" ? dictionary.navigation.subscriptionSync : section === "company-administration" ? dictionary.navigation.companyAdministration : section === "api-key-administration" ? dictionary.navigation.apiKeyAdministration : gateway ? dictionary.catalog.titles[gateway] : dictionary.navigation.apiKeys}</span>
          </div>
          <div className="flex shrink-0 items-center gap-3 sm:gap-5">
            <div className="flex items-center gap-1.5 text-muted-foreground"><Globe2 className="hidden size-4 sm:block" /><Select value={locale} onValueChange={changeLanguage}><SelectTrigger className="w-24 border-none bg-transparent text-xs shadow-none" aria-label={dictionary.common.language}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ja">日本語</SelectItem><SelectItem value="en">English</SelectItem></SelectContent></Select></div>
            <span className="h-6 w-px bg-border" />
            <AccountMenu account={props.account} locale={locale} dictionary={dictionary} />
          </div>
        </header>
        <main id="main-content" className="portal-content">{children}</main>
      </div>
      <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
        <SheetContent side="left" className="gap-0 p-0" style={{ width: "min(288px, calc(100vw - 24px))" }} showCloseButton={false} onCloseAutoFocus={(event) => { event.preventDefault(); if (mobileMenuButtonRef.current?.isConnected) mobileMenuButtonRef.current.focus(); }}>
          <SheetTitle className="sr-only">{dictionary.common.serviceNavigation}</SheetTitle><SheetDescription className="sr-only">AI Gateway</SheetDescription>
          <SheetClose asChild><Button variant="ghost" size="icon-sm" className="absolute right-2 top-2" aria-label={dictionary.common.close}><X className="size-4" /></Button></SheetClose>
          <Sidebar {...props} />
        </SheetContent>
      </Sheet>
    </div>
  );
}
