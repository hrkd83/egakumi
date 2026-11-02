import { Suspense } from "react";
import { notFound, redirect } from "next/navigation";
import { LoginPanel } from "@/components/login-panel";
import { safeReturnTo } from "@/lib/auth-navigation";
import { readPortalSession } from "@/lib/auth-session";
import { hasLocale } from "@/lib/i18n/config";
import { getDictionary } from "@/lib/i18n/dictionaries";

/** セッションの確認を、リクエストごとに解決する境界の内側で行います。 */
export default function LoginPage(props: PageProps<"/[lang]/login">) {
  return <Suspense fallback={<div className="min-h-screen bg-[#f5f8f6]" />}><LoginContent {...props} /></Suspense>;
}

/** 言語と戻り先を検証し、ログイン済みの場合はカタログへ戻します。 */
async function LoginContent({ params, searchParams }: PageProps<"/[lang]/login">) {
  const { lang } = await params;
  if (!hasLocale(lang)) notFound();
  const query = await searchParams;
  const returnTo = safeReturnTo(typeof query.next === "string" ? query.next : undefined, lang);
  let sessionUnavailable = false;
  let account = null;
  try { account = await readPortalSession(); } catch { sessionUnavailable = true; }
  if (account) redirect(returnTo);
  const dictionary = await getDictionary(lang);
  return <LoginPanel locale={lang} dictionary={dictionary} returnTo={returnTo} hasError={Boolean(query.error) || sessionUnavailable} />;
}
