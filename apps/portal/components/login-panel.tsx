"use client";

import Image from "next/image";
import Link from "next/link";
import { ArrowRight, LoaderCircle, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { authClient } from "@/lib/auth-client";
import { safeReturnTo } from "@/lib/auth-navigation";
import type { Locale } from "@/lib/i18n/config";
import type { Dictionary } from "@/lib/i18n/dictionaries";

/** Microsoft認証へ渡す文言と、検証済みの戻り先です。 */
type LoginPanelProps = { locale: Locale; dictionary: Dictionary; returnTo: string; hasError: boolean };

/**
 * パスワードを預からず、Microsoftの認証画面へ案内します。
 * @param props - 言語、文言、カタログ内の戻り先、直前の認証結果。
 * @returns ポータル共通のログイン画面。
 */
export function LoginPanel({ locale, dictionary, returnTo, hasError }: LoginPanelProps) {
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(hasError);
  const text = dictionary.auth;
  const destination = safeReturnTo(returnTo, locale);
  const otherLocale = locale === "ja" ? "en" : "ja";
  const translatedDestination = destination.replace(/^\/(ja|en)(?=\/|$)/, `/${otherLocale}`);

  /** 認証は同一オリジンのAPIで開始し、クライアントシークレットをブラウザーに渡しません。 */
  async function signIn() {
    setPending(true);
    setFailed(false);
    try {
      const result = await authClient.signIn.social({
        provider: "microsoft",
        callbackURL: destination,
        errorCallbackURL: `/${locale}/login?error=authentication_failed&next=${encodeURIComponent(destination)}`,
      });
      if (result.error) {
        setFailed(true);
        setPending(false);
      }
    } catch {
      setFailed(true);
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col bg-[#f5f8f6]">
      <header className="flex items-center justify-between px-6 py-6 sm:px-10">
        <Image src="/brand/egakumi-wordmark.webp" alt="egakumi" width={150} height={50} className="h-auto w-32" priority />
        <Link className="text-sm text-muted-foreground hover:text-foreground" href={`/${otherLocale}/login?next=${encodeURIComponent(translatedDestination)}`}>{otherLocale === "ja" ? "日本語" : "English"}</Link>
      </header>
      <div className="flex flex-1 items-center justify-center px-6 pb-16">
        <section className="w-full max-w-[440px] rounded-3xl border border-border bg-white p-8 shadow-sm sm:p-10" aria-labelledby="login-title">
          <Image src="/brand/egakumi-symbol.webp" alt="" width={64} height={64} className="mb-8" priority />
          <p className="mb-3 text-xs font-semibold tracking-[.18em] text-primary">AI GATEWAY PORTAL</p>
          <h1 id="login-title" className="whitespace-pre-line text-2xl font-semibold leading-snug tracking-tight">{text.title}</h1>
          <p className="mb-8 mt-4 text-sm leading-7 text-muted-foreground">{text.description}</p>
          {failed && <p role="alert" className="mb-5 rounded-lg bg-destructive/8 p-3 text-sm leading-6 text-destructive">{text.error}</p>}
          <Button className="h-12 w-full gap-3 text-sm" onClick={signIn} disabled={pending}>
            {pending ? <LoaderCircle className="size-4 animate-spin" /> : <MicrosoftMark />}
            {pending ? text.signingIn : text.signIn}
            {!pending && <ArrowRight className="ml-auto size-4" />}
          </Button>
          <div className="mt-6 flex items-start gap-2 text-xs leading-6 text-muted-foreground"><ShieldCheck className="mt-1 size-4 shrink-0" /><p>{text.notice}</p></div>
        </section>
      </div>
      <footer className="pb-6 text-center text-xs text-muted-foreground">egakumi · AI Gateway</footer>
    </main>
  );
}

/** Microsoftサインインで利用する4色のブランドマークです。 */
function MicrosoftMark() {
  return <span className="grid size-4 grid-cols-2 gap-[2px]" aria-hidden="true"><span className="bg-[#f25022]" /><span className="bg-[#7fba00]" /><span className="bg-[#00a4ef]" /><span className="bg-[#ffb900]" /></span>;
}
