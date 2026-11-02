import { createRemoteJWKSet, jwtVerify, type JWTVerifyGetKey } from "jose";
import { z } from "zod";
import type { MicrosoftEntraIDProfile } from "better-auth/social-providers";
import type { BetterAuthOptions } from "better-auth";

/** 本人識別には変更可能なメールやUPNを使わず、テナントとオブジェクトのIDを使います。 */
const identityClaims = z.object({
  tid: z.uuid(),
  oid: z.uuid(),
  sub: z.string().min(1),
  name: z.string().trim().min(1).optional(),
  email: z.email().optional(),
  email_verified: z.boolean().optional(),
});

/** Microsoftの署名検証と対象テナントの固定に必要な、公開設定です。 */
export type MicrosoftIdentityConfiguration = { tenantId: string; clientId: string };

/** SQLへ永続化しないOAuthの資格情報と、その有効期限の列です。 */
type OAuthTokenField = "accessToken" | "refreshToken" | "idToken" | "accessTokenExpiresAt" | "refreshTokenExpiresAt";

/** Better Authが本人情報を保存・更新する直前の検証関数です。 */
type IdentityGate = NonNullable<NonNullable<BetterAuthOptions["user"]>["validateUserInfo"]>;

/**
 * 本人識別列の出所をMicrosoftの検証済みプロフィールに限定します。
 * @remarks メールアドレスの一致や、利用者から送られたtid/oidだけでは登録を許可しません。
 */
export function createIdentityGate(tenantId: string): IdentityGate {
  return ({ user, source }) => {
    const profile = source.oauth?.profile;
    const claims = identityClaims.safeParse(profile);
    if (
      source.method !== "oauth" || source.oauth?.providerId !== "microsoft" ||
      !claims.success || claims.data.tid.toLowerCase() !== tenantId.toLowerCase() ||
      user.entraTenantId !== claims.data.tid.toLowerCase() ||
      user.entraObjectId !== claims.data.oid.toLowerCase()
    ) {
      return { error: "identity_not_allowed" };
    }
  };
}

/**
 * Microsoftが署名したIDトークンから、ポータルで扱う本人情報を取り出します。
 * @param configuration - 許可する単一テナントと、このアプリのクライアントID。
 * @param verificationKey - 通常はEntraの公開鍵。テストでは固定鍵を注入できます。
 * @returns Better AuthのMicrosoftプロバイダーへ渡すプロフィール取得処理。
 * @remarks OAuthのstateとPKCEはBetter Authが検証し、ここでは署名・発行者・宛先・有効期限を検証します。
 */
export function createMicrosoftProfileResolver(
  configuration: MicrosoftIdentityConfiguration,
  verificationKey: JWTVerifyGetKey = createRemoteJWKSet(
    new URL(`https://login.microsoftonline.com/${configuration.tenantId}/discovery/v2.0/keys`),
  ),
) {
  return async (tokens: { idToken?: string }) => {
    if (!tokens.idToken) throw new Error("ENTRA_ID_TOKEN_REQUIRED");
    const { payload } = await jwtVerify(tokens.idToken, verificationKey, {
      algorithms: ["RS256"],
      issuer: `https://login.microsoftonline.com/${configuration.tenantId}/v2.0`,
      audience: configuration.clientId,
      requiredClaims: ["exp", "iat", "iss", "aud", "sub", "tid", "oid"],
      maxTokenAge: "1h",
    });
    const claims = identityClaims.parse(payload);
    if (claims.tid.toLowerCase() !== configuration.tenantId.toLowerCase()) {
      throw new Error("ENTRA_TENANT_NOT_ALLOWED");
    }
    const tenantId = claims.tid.toLowerCase();
    const objectId = claims.oid.toLowerCase();
    // emailは表示用です。欠ける場合も、安定した本人IDに基づく配信不可アドレスで登録できます。
    const email = claims.email ?? `${objectId}.${tenantId}@identity.invalid`;
    return {
      user: {
        name: claims.name ?? "Microsoft user",
        email,
        emailVerified: Boolean(claims.email && claims.email_verified === true),
        entraTenantId: tenantId,
        entraObjectId: objectId,
      },
      data: { ...payload, tid: tenantId, oid: objectId } as unknown as MicrosoftEntraIDProfile,
    };
  };
}

/**
 * ポータルは本人確認にだけトークンを使い、OAuth資格情報をSQLへ保存しません。
 * @param account - Better Authが保存しようとするアカウントの情報。
 * @returns アカウントの対応関係を残し、トークン値を除いた情報。
 */
export function withoutOAuthTokens<T extends object>(account: T): Omit<T, OAuthTokenField> & Record<OAuthTokenField, null> {
  return {
    ...account,
    accessToken: null,
    refreshToken: null,
    idToken: null,
    accessTokenExpiresAt: null,
    refreshTokenExpiresAt: null,
  } as Omit<T, OAuthTokenField> & Record<OAuthTokenField, null>;
}
