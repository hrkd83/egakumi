/** Better Authの標準4テーブルと、ポータル固有の本人識別列を定義します。 */
export const portalAuthModels = {
  user: {
    modelName: "portalUser",
    additionalFields: {
      // Better AuthのOAuthプロフィール取り込みもinput設定に従うため、署名検証済みの値を受け入れます。
      // HTTPからの変更は認証ルートの許可リストで禁止し、validateUserInfoでも出所を検証します。
      entraTenantId: { type: "string", required: true },
      entraObjectId: { type: "string", required: true },
    },
  },
  session: {
    modelName: "portalSession",
    expiresIn: 60 * 60 * 8,
    updateAge: 60 * 60,
    freshAge: 60 * 10,
    cookieCache: { enabled: false },
  },
  account: {
    modelName: "portalAccount",
    accountLinking: { enabled: false, disableImplicitLinking: true },
    updateAccountOnSignIn: false,
    storeAccountCookie: false,
    storeStateStrategy: "database",
  },
  verification: { modelName: "portalVerification" },
} as const;
