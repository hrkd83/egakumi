import { notFound, redirect } from "next/navigation";
import { Suspense } from "react";
import { hasLocale } from "@/lib/i18n/config";
import Loading from "./loading";

/**
 * ポータルの入口としてModel Gatewayのカタログを表示します。
 * @param props - URLから渡される言語。
 * @returns 公開API定義を使うカタログへの案内。
 */
export default function HomePage(props: PageProps<"/[lang]">) {
  return <Suspense fallback={<Loading />}><HomeContent {...props} /></Suspense>;
}

/** 認証を行うModel Gatewayの正規URLへまとめ、古いサンプル一覧を表示しません。 */
async function HomeContent({ params }: PageProps<"/[lang]">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  return redirect(`/${lang}/models`);
}
