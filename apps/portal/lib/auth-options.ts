import type { BetterAuthOptions } from "better-auth";
import { nextCookies } from "better-auth/next-js";
import type { AuthEnvironment } from "./auth-environment";
import { createIdentityGate, createMicrosoftProfileResolver, withoutOAuthTokens } from "./auth-identity.ts";
import { portalAuthModels } from "./auth-schema.ts";
import { portalAuthLogger } from "./auth-logger.ts";

/**
 * Microsoftへの認証要求とSQL保存の方針を、実行環境の値から組み立てます。
 * @param environment - サーバーで検証済みの認証設定。
 * @param database - 実行時のSQLアダプター。テストでは専用アダプターを指定できます。
 * @returns Better Authの設定。構築した時点ではSQL接続や外部通信を行いません。
 */
export function createPortalAuthOptions(environment: AuthEnvironment, database: BetterAuthOptions["database"]) {
  return {
    appName: "egakumi AI Gateway",
    logger: portalAuthLogger,
    baseURL: environment.baseURL,
    basePath: "/api/auth",
    secret: environment.secret,
    trustedOrigins: [environment.baseURL],
    database,
    onAPIError: { errorURL: `${environment.baseURL}/ja/login` },
    emailAndPassword: { enabled: false },
    socialProviders: {
      microsoft: {
        clientId: environment.microsoftClientId,
        clientSecret: environment.microsoftClientSecret,
        tenantId: environment.microsoftTenantId,
        disableDefaultScope: true,
        scope: ["openid", "profile", "email"],
        disableProfilePhoto: true,
        disableIdTokenSignIn: true,
        overrideUserInfoOnSignIn: true,
        getUserInfo: createMicrosoftProfileResolver({
          clientId: environment.microsoftClientId,
          tenantId: environment.microsoftTenantId,
        }),
      },
    },
    ...portalAuthModels,
    user: {
      ...portalAuthModels.user,
      validateUserInfo: createIdentityGate(environment.microsoftTenantId),
    },
    advanced: {
      cookiePrefix: "egakumi.portal",
      useSecureCookies: environment.baseURL.startsWith("https://"),
      defaultCookieAttributes: { httpOnly: true, sameSite: "lax" },
      database: { generateId: "uuid" },
    },
    databaseHooks: {
      account: {
        create: { before: async (account) => ({ data: withoutOAuthTokens(account) }) },
        update: { before: async (account) => ({ data: withoutOAuthTokens(account) }) },
      },
    },
    telemetry: { enabled: false },
    plugins: [nextCookies()],
  } satisfies BetterAuthOptions;
}
