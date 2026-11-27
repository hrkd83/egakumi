import "server-only";

import { authorizationCodeRequest, createAuthorizationURL, refreshAccessTokenRequest } from "better-auth/oauth2";
import { createRemoteJWKSet, jwtVerify, type JWTVerifyGetKey } from "jose";
import { z } from "zod";
import { createClientAssertion } from "../client-assertion.ts";
import type { ApiAuthSession } from "./session.ts";
import { ApiAuthenticationError, readLimitedAuthBody, sha256, type ApiAuthenticationSettings } from "./settings.ts";

const tokenValue = z.string().min(16).max(16_384).regex(/^[A-Za-z0-9._~+\/-]+={0,2}$/);
const tokenResult = z.object({
  access_token: tokenValue, refresh_token: tokenValue.optional(), id_token: tokenValue.optional(),
  token_type: z.string().refine((value) => value.toLowerCase() === "bearer"),
  expires_in: z.number().int().min(1).max(86_400),
  scope: z.string().max(4096).optional(),
});

const apiClaimValue = z.string().min(1).max(512).regex(/^[^\x00-\x1f\x7f]+$/);
const apiClaims = z.strictObject({ access_token: z.record(
  z.string().regex(/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/),
  z.union([z.null(), z.strictObject({
    essential: z.boolean().optional(), value: apiClaimValue.optional(), values: z.array(apiClaimValue).max(16).optional(),
  })]),
).refine((claims) => Object.keys(claims).length > 0 && Object.keys(claims).length <= 16) });

/** Graphからの追加認証要求を、Entraの固定authorizeへ渡せるアクセス用claimsに限定します。 */
export function decodeApiClaims(value: string): string {
  try {
    if (!/^[A-Za-z0-9_-]{1,6144}$/.test(value)) throw new Error();
    const bytes = Buffer.from(value, "base64url");
    if (bytes.toString("base64url") !== value) throw new Error();
    const claims = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
    return JSON.stringify(apiClaims.parse(claims));
  } catch {
    throw new ApiAuthenticationError("ApiAuthorizationInvalid", 400);
  }
}

/** 初回OAuthの一回限り資格情報と、利用者が元の画面へ戻る先です。 */
export const authorizationRequestSchema = z.object({
  verifier: z.string().regex(/^[A-Za-z0-9_-]{43}$/),
  nonce: z.string().regex(/^[A-Za-z0-9_-]{43}$/),
  returnTo: z.string().max(256),
});

/** 取引状態から復元する認証開始情報です。 */
export type PortalAuthorizationRequest = z.infer<typeof authorizationRequestSchema>;

/** 保存したAPI資格情報です。サーバー境界内でだけ使います。 */
export const apiTokenSchema = z.object({ access: tokenValue, refresh: tokenValue });

/** Token endpointが応答した結果をDB基準時刻と合わせて保存します。 */
type ExchangedTokens = { access: string; refresh?: string; expiresSeconds: number };

/** 固定tenantだけを対象にOAuth要求を作成し、ID tokenでPortal本人を確認します。 */
export class PortalApiOAuth {
  private readonly options;
  private readonly scopes;
  private readonly tokenUrl;
  private readonly tokenEndpointAuth;
  private readonly remoteKey: JWTVerifyGetKey;
  private readonly settings: ApiAuthenticationSettings;
  private readonly fetcher: typeof globalThis.fetch;

  constructor(settings: ApiAuthenticationSettings, fetcher: typeof globalThis.fetch = globalThis.fetch, verificationKey?: JWTVerifyGetKey,
    signer: () => Promise<string> = createClientAssertion(settings.auth.microsoftTenantId, settings.auth.microsoftClientId, settings.auth.certificate)) {
    this.settings = settings;
    this.fetcher = fetcher;
    const { microsoftClientId } = settings.auth;
    this.options = { clientId: microsoftClientId };
    this.scopes = ["openid", "profile", "offline_access", settings.scope];
    this.tokenUrl = `${settings.entra}/oauth2/v2.0/token`;
    this.tokenEndpointAuth = {
      method: "private_key_jwt" as const,
      getClientAssertion: async (context: { tokenEndpoint: string; clientId: string }) => {
        if (context.tokenEndpoint !== this.tokenUrl || context.clientId !== microsoftClientId) {
          throw new ApiAuthenticationError("ApiAuthenticationUnavailable", 503);
        }
        return await new Promise<string>((resolve, reject) => {
          const timer = setTimeout(() => reject(new ApiAuthenticationError("ApiAuthenticationUnavailable", 503)), 10_000);
          void signer().then(resolve, reject).finally(() => clearTimeout(timer));
        });
      },
    };
    this.remoteKey = verificationKey ?? createRemoteJWKSet(new URL(`${settings.entra}/discovery/v2.0/keys`), {
      timeoutDuration: 5_000,
    });
  }

  /** Entra用の認証URLを生成し、固定callback・scopeとS256を使います。 */
  async authorizationURL(state: string, request: PortalAuthorizationRequest, claims?: string): Promise<URL> {
    return createAuthorizationURL({
      id: "portal-api", options: this.options, authorizationEndpoint: `${this.settings.entra}/oauth2/v2.0/authorize`,
      state, codeVerifier: request.verifier, nonce: request.nonce, redirectURI: this.settings.callback,
      scopes: this.scopes, responseMode: "form_post", additionalParams: claims ? { claims } : undefined,
    });
  }

  private async send(request: { body: URLSearchParams; headers: Record<string, string> }) {
    const signal = AbortSignal.timeout(25_000);
    const response = await this.fetcher(this.tokenUrl, { ...request, method: "POST", cache: "no-store", redirect: "error", signal });
    const parsed: unknown = JSON.parse(await readLimitedAuthBody(response.body, 65_536, signal));
    if (!response.ok) {
      if (response.status === 400 && parsed && typeof parsed === "object" &&
          "error" in parsed && parsed.error === "invalid_grant") {
        throw new ApiAuthenticationError("ApiAuthorizationDenied", 401);
      }
      throw new ApiAuthenticationError("ApiAuthenticationUnavailable", 503);
    }
    const tokens = tokenResult.parse(parsed);
    const requested = this.settings.scope;
    const name = requested.slice(requested.lastIndexOf("/") + 1);
    if (tokens.scope !== undefined && !tokens.scope.split(/ +/).some((scope) => scope === requested || scope === name)) {
      throw new ApiAuthenticationError("ApiAuthorizationDenied", 401);
    }
    return tokens;
  }

  private async verifyIdentity(token: string, session: ApiAuthSession, nonceHash: string, initial: boolean) {
    try {
      const { payload } = await jwtVerify(token, this.remoteKey, {
        algorithms: ["RS256"],
        issuer: `${this.settings.entra}/v2.0`,
        audience: this.settings.auth.microsoftClientId,
        requiredClaims: ["exp", "iat", "iss", "aud", "sub", "tid", "oid", ...(initial ? ["nonce"] : [])],
        maxTokenAge: "1h",
      });
      const claims = z.object({ tid: z.uuid(), oid: z.uuid(), nonce: z.string().max(1024).optional() }).parse(payload);
      if (claims.tid.toLowerCase() !== session.tenantId || claims.oid.toLowerCase() !== session.objectId ||
          (claims.nonce !== undefined && sha256(claims.nonce) !== nonceHash)) throw new Error();
    } catch {
      throw new ApiAuthenticationError("ApiAuthorizationDenied", 401);
    }
  }

  /** 一度消費したコードを交換し、同じ本人からの初回トークンだけを受け付けます。 */
  async exchange(code: string, request: PortalAuthorizationRequest, session: ApiAuthSession): Promise<ExchangedTokens & { refresh: string }> {
    const tokenRequest = await authorizationCodeRequest({
      code, codeVerifier: request.verifier, redirectURI: this.settings.callback, options: this.options,
      tokenEndpoint: this.tokenUrl, tokenEndpointAuth: this.tokenEndpointAuth,
      additionalParams: { scope: this.scopes.join(" ") },
    });
    const tokens = await this.send(tokenRequest);
    if (!tokens.id_token || !tokens.refresh_token) throw new ApiAuthenticationError("ApiAuthorizationDenied", 401);
    await this.verifyIdentity(tokens.id_token, session, sha256(request.nonce), true);
    return { access: tokens.access_token, refresh: tokens.refresh_token, expiresSeconds: tokens.expires_in };
  }

  /** refreshがID tokenも返すときは、署名・本人・任意nonceを再確認します。 */
  async refresh(refreshToken: string, session: ApiAuthSession, nonceHash: string): Promise<ExchangedTokens> {
    const tokenRequest = await refreshAccessTokenRequest({
      refreshToken, options: this.options, tokenEndpoint: this.tokenUrl,
      tokenEndpointAuth: this.tokenEndpointAuth, extraParams: { scope: this.scopes.join(" ") },
    });
    const tokens = await this.send(tokenRequest);
    if (tokens.id_token) await this.verifyIdentity(tokens.id_token, session, nonceHash, false);
    return { access: tokens.access_token, refresh: tokens.refresh_token, expiresSeconds: tokens.expires_in };
  }
}
