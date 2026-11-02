import { match } from "@formatjs/intl-localematcher";
import Negotiator from "negotiator";
import { NextResponse, type NextRequest } from "next/server";

import { defaultLocale, hasLocale, locales, type Locale } from "@/lib/i18n/config";

/**
 * ブラウザーの優先言語から、ポータルが提供する表示言語を選びます。
 * @param request Accept-Language を含む受信リクエスト。
 * @returns 一致する対応言語。指定がない場合や一致しない場合は日本語。
 */
function getLocale(request: NextRequest): Locale {
  const languages = new Negotiator({
    headers: { "accept-language": request.headers.get("accept-language") ?? "" },
  }).languages();

  // ワイルドカードや不正な言語コードを除き、ヘッダーによる例外を防ぎます。
  const requestedLocales = languages.flatMap((language) => {
    try {
      return Intl.getCanonicalLocales(language);
    } catch {
      return [];
    }
  });

  const locale = match(requestedLocales, locales, defaultLocale);
  return hasLocale(locale) ? locale : defaultLocale;
}

/**
 * 言語のないページ URL を、パスとクエリを保った言語付き URL に転送します。
 * @param request 判定対象のページリクエスト。
 * @returns 言語付き URL へのリダイレクト、または元のリクエストの継続。
 */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const pathLocale = pathname.split("/")[1] ?? "";

  if (hasLocale(pathLocale)) {
    return NextResponse.next();
  }

  const redirectUrl = request.nextUrl.clone();
  redirectUrl.pathname = `/${getLocale(request)}${pathname}`;
  return NextResponse.redirect(redirectUrl);
}

/** API、Next.js の内部パス、拡張子付きアセットは言語の転送対象にしません。 */
export const config = {
  matcher: ["/((?!api(?:/|$)|_next(?:/|$)|.*\\..*).*)"],
};
