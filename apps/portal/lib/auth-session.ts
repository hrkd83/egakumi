import "server-only";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { connection } from "next/server";
import { getAuth } from "@/lib/auth";
import { getAuthEnvironment } from "@/lib/auth-environment";
import { safeReturnTo } from "@/lib/auth-navigation";
import type { Locale } from "@/lib/i18n/config";

/** ブラウザーへ渡してよい、ヘッダー表示用の最小限の本人情報です。 */
export type PortalAccount = { name: string; email: string | null };

/** サーバー内の認可でだけ使う、検証済みEntra本人識別子です。 */
export type PortalPrincipal = PortalAccount & { tenantId: string; objectId: string };

/**
 * リクエストのCookieをSQLのセッションと照合し、表示用の本人情報だけを返します。
 * @returns 有効な本人情報。未ログイン・期限切れはnullです。
 * @remarks サーバー共有キャッシュは使いません。DB障害を未認証の成功として扱わず、上位で処理します。
 */
export async function readPortalPrincipal(): Promise<PortalPrincipal | null> {
  // Better Authの有効期限判定は現在時刻を使うため、事前描画や先読みでは実行しません。
  await connection();
  const requestHeaders = await headers();
  if (!requestHeaders.get("cookie")?.includes("egakumi.portal")) return null;
  // Server ComponentではCookieを書き換えられないため、有効性の読み取りだけを行います。
  const session = await getAuth().api.getSession({ headers: requestHeaders, query: { disableCookieCache: true, disableRefresh: true } });
  if (!session) return null;
  const expectedTenant = getAuthEnvironment().microsoftTenantId.toLowerCase();
  if (session.user.entraTenantId !== expectedTenant || !session.user.entraObjectId) return null;
  return {
    tenantId: session.user.entraTenantId,
    objectId: session.user.entraObjectId,
    name: session.user.name,
    email: session.user.email.endsWith("@identity.invalid") ? null : session.user.email,
  };
}

/** 表示に不要なEntra識別子を除き、ヘッダー表示用の本人情報を返します。 */
export async function readPortalSession(): Promise<PortalAccount | null> {
  const principal = await readPortalPrincipal();
  return principal ? { name: principal.name, email: principal.email } : null;
}

/**
 * カタログを返す前に、毎回サーバーで本人のセッションを確認します。
 * @param locale - 検証済みの言語。
 * @param returnTo - ログイン後に戻るカタログのパス。
 * @returns 有効な本人情報。ログインが必要な場合はログイン画面へ転送します。
 */
export async function requirePortalSession(locale: Locale, returnTo: string): Promise<PortalAccount> {
  const account = await readPortalSession();
  if (account) return account;
  redirect(`/${locale}/login?next=${encodeURIComponent(safeReturnTo(returnTo, locale))}`);
}
