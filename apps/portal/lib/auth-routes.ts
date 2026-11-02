import { z } from "zod";
import { isPortalReturnPath } from "./auth-navigation.ts";

/** ポータルが公開する認証操作だけを列挙します。本人情報編集やアカウント連携APIは公開しません。 */
const routes = new Map([
  ["/api/auth/sign-in/social", "POST"],
  ["/api/auth/callback/microsoft", "GET"],
  ["/api/auth/get-session", "GET"],
  ["/api/auth/sign-out", "POST"],
]);

/**
 * Better Authの汎用APIから、現在のポータルで使用する操作だけを通します。
 * @param pathname - URLオブジェクトで読み取ったパス。
 * @param method - リクエストのHTTPメソッド。
 */
export function isAllowedAuthRoute(pathname: string, method: string): boolean {
  return routes.get(pathname) === method;
}

/** 認証開始では画面が使う3項目だけを受け取り、追加スコープや直接のトークン指定を禁止します。 */
const signInSchema = z.object({
  provider: z.literal("microsoft"),
  callbackURL: z.string().max(256).refine((path) => {
    const locale = path.split("/")[1];
    return (locale === "ja" || locale === "en") && isPortalReturnPath(path, locale);
  }),
  errorCallbackURL: z.string().max(1024),
}).strict().refine((value) => {
  const locale = value.callbackURL.split("/")[1];
  if (!value.errorCallbackURL.startsWith(`/${locale}/login?`)) return false;
  const errorURL = new URL(value.errorCallbackURL, "https://portal.invalid");
  const parameters = errorURL.searchParams;
  return errorURL.pathname === `/${locale}/login` && !errorURL.hash &&
    [...parameters.keys()].every((key) => key === "error" || key === "next") &&
    parameters.getAll("error").length === 1 && parameters.get("error") === "authentication_failed" &&
    parameters.getAll("next").length === 1 && parameters.get("next") === value.callbackURL;
});

/**
 * HTTPからの認証開始入力を、ポータルが提供するログイン操作に限定します。
 * @param value - まだ信頼しないJSON本文。
 * @returns 許可されたMicrosoft認証の入力。不正な項目や戻り先がある場合はnullです。
 */
export function parsePortalSignIn(value: unknown): z.infer<typeof signInSchema> | null {
  const result = signInSchema.safeParse(value);
  return result.success ? result.data : null;
}
