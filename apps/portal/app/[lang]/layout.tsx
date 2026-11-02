import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { notFound } from "next/navigation";
import { PortalStoreProvider } from "@/components/portal-store-provider";
import { hasLocale, locales } from "@/lib/i18n/config";
import "../globals.css";

/** ポータル共通のブラウザータイトルと、提供するサービスの説明です。 */
export const metadata: Metadata = {
  title: "egakumi · AI Gateway",
  description: "Model Gateway, Tool Gateway and Hosted Agent API — egakumi AI Gateway Portal",
  icons: { icon: "/brand/favicon.svg", shortcut: "/favicon.ico" },
};

/**
 * 対応言語のページをビルド時に準備します。
 * @returns 日本語と英語のルートパラメーター。
 */
export function generateStaticParams() {
  return locales.map((lang) => ({ lang }));
}

/**
 * URLの言語をHTML文書へ反映し、利用者ごとに独立した表示状態を提供します。
 * @param props - 言語パラメーターと現在のページ。
 * @returns 言語付きの共通レイアウト。
 */
export default async function RootLayout({ children, params }: LayoutProps<"/[lang]">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return (
    <html
      lang={lang}
      className={`${GeistSans.variable} ${GeistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full"><PortalStoreProvider>{children}</PortalStoreProvider></body>
    </html>
  );
}
