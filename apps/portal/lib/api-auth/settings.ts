import "server-only";

import { AzureCliCredential, ManagedIdentityCredential } from "@azure/identity";
import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { z } from "zod";
import { getAuthEnvironment, type AuthEnvironment } from "../auth-environment.ts";
import { getPublicModelGatewayOrigin } from "../model-public-config.ts";
import type { ApiAuthPurpose } from "./purpose.ts";

/** API用認証の失敗を、資格情報を含まない固定の応答へ変換します。 */
export class ApiAuthenticationError extends Error {
  readonly code: "ApiAuthenticationDisabled" | "ApiAuthenticationRequired" | "ApiAuthenticationBusy" |
    "ApiAuthenticationUnavailable" | "ApiAuthorizationInvalid" | "ApiAuthorizationDenied";
  readonly status: number;

  constructor(code: ApiAuthenticationError["code"], status: number) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

/** API用OAuthと暗号鍵の固定参照です。クライアント入力で上書きできません。 */
export type ApiAuthenticationSettings = {
  auth: AuthEnvironment & { certificate: NonNullable<AuthEnvironment["certificate"]> };
  gatewayOrigin: string;
  scope: string;
  callback: string;
  entra: string;
  vaultSecret: string;
  activeVersion: string;
  allowedVersions: ReadonlySet<string>;
  keyIdentity?: string;
};

const secretUrl = /^(https:\/\/[a-z0-9-]+\.vault\.azure\.net\/secrets\/[a-z0-9-]+)\/([a-f0-9]{32})$/;
const version = /^[a-f0-9]{32}$/;

/** 構築済みフラグの確認です。無効時は既存のキー経路だけを利用します。 */
export function apiAuthenticationEnabled(): boolean {
  return process.env.PORTAL_API_AUTH_ENABLED === "true";
}

/** Azureの購読管理サービスを利用するPortalで追加認証を要求します。 */
export function subscriptionManagementConfigured(): boolean {
  return Boolean(process.env.PORTAL_SUBSCRIPTION_MANAGER_URL);
}

/** 証明書認証、Gatewayの公開オリジン、版固定の鍵をまとめて検証します。 */
export function readApiAuthenticationSettings(purpose: ApiAuthPurpose = "gateway"): ApiAuthenticationSettings {
  const management = purpose === "subscription-management";
  if (!(management ? subscriptionManagementConfigured() : apiAuthenticationEnabled())) {
    throw new ApiAuthenticationError("ApiAuthenticationDisabled", 404);
  }
  try {
    const auth = getAuthEnvironment();
    const gatewayOrigin = getPublicModelGatewayOrigin();
    const key = secretUrl.exec(process.env.PORTAL_API_AUTH_KEY_URL ?? "");
    const scope = (management ? process.env.PORTAL_SUBSCRIPTION_MANAGEMENT_SCOPE : process.env.PORTAL_API_AUTH_SCOPE) ?? "";
    const match = /^api:\/\/([a-f0-9-]{36})\/([A-Za-z.]+)$/.exec(scope);
    const audience = match?.[1];
    const keyIdentity = process.env.PORTAL_API_AUTH_KEY_CLIENT_ID;
    const previous = (process.env.PORTAL_API_AUTH_PREVIOUS_KEY_VERSIONS ?? "").split(",").filter(Boolean);
    if (!auth.certificate || !auth.baseURL.startsWith("https:") || !gatewayOrigin || !key ||
      !z.uuid().safeParse(audience).success || audience === auth.microsoftClientId ||
      match?.[0] !== scope || match[2] !== (management ? "Subscription.Manage" : "Gateway.Invoke") ||
      (auth.certificate.mode === "managed-identity" && !z.uuid().safeParse(keyIdentity).success) ||
      previous.length > 4 || previous.some((item) => !version.test(item))) {
      throw new Error();
    }
    return {
      auth: { ...auth, certificate: auth.certificate }, gatewayOrigin, scope,
      callback: new URL("/api/api-auth/callback", auth.baseURL).href,
      entra: `https://login.microsoftonline.com/${auth.microsoftTenantId}`,
      vaultSecret: key[1], activeVersion: key[2], allowedVersions: new Set([key[2], ...previous]), keyIdentity,
    };
  } catch {
    throw new ApiAuthenticationError("ApiAuthenticationUnavailable", 503);
  }
}

/** 生の状態値をSQLへ置かず、固定長の照合値だけを置きます。 */
export function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

/** 独立したOAuth相関値を作ります。 */
export function newAuthorizationSecret(): string {
  return randomBytes(32).toString("base64url");
}

/** 上限を超えた本文を取り込みません。ネットワーク時は取得側のAbortSignalも適用されます。 */
export async function readLimitedAuthBody(body: ReadableStream<Uint8Array> | null, limit: number, signal?: AbortSignal): Promise<string> {
  if (!body) throw new Error();
  const reader = body.getReader();
  const chunks = [];
  let bytes = 0;
  const cancel = () => { void reader.cancel().catch(() => undefined); };
  signal?.addEventListener("abort", cancel, { once: true });
  try {
    for (;;) {
      signal?.throwIfAborted();
      const part = await reader.read();
      signal?.throwIfAborted();
      if (part.done) break;
      bytes += part.value.byteLength;
      if (bytes > limit) throw new Error();
      chunks.push(part.value);
    }
    return new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks, bytes));
  } finally {
    signal?.removeEventListener("abort", cancel);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

/** 再試行した結果を共有しません。成功した許可版だけをプロセス内で短時間キャッシュします。 */
export class ApiAuthCryptography {
  private readonly keys = new Map<string, { until: number; key: Promise<Buffer> }>();
  private readonly credential;
  private readonly settings: ApiAuthenticationSettings;
  private readonly fetcher: typeof globalThis.fetch;

  constructor(settings: ApiAuthenticationSettings, fetcher: typeof globalThis.fetch = globalThis.fetch) {
    this.settings = settings;
    this.fetcher = fetcher;
    const certificate = settings.auth.certificate;
    this.credential = certificate.mode === "managed-identity"
      ? new ManagedIdentityCredential({ clientId: settings.keyIdentity! })
      : new AzureCliCredential({ tenantId: certificate.azureTenantId, processTimeoutInMs: 8_000 });
  }

  private async fetchKey(keyVersion: string): Promise<Buffer> {
    const signal = AbortSignal.timeout(10_000);
    const access = await this.credential.getToken("https://vault.azure.net/.default", { abortSignal: signal });
    if (!access) throw new Error();
    const keyUrl = `${this.settings.vaultSecret}/${keyVersion}`;
    const response = await this.fetcher(`${keyUrl}?api-version=7.4`, {
      headers: { Authorization: `Bearer ${access.token}`, Accept: "application/json" },
      cache: "no-store", redirect: "error", signal,
    });
    if (!response.ok) { await response.body?.cancel(); throw new Error(); }
    const secret: unknown = JSON.parse(await readLimitedAuthBody(response.body, 16_384, signal));
    const record = z.object({
      id: z.literal(keyUrl), value: z.string().regex(/^[A-Za-z0-9_-]{43}$/),
      contentType: z.literal("application/x-egakumi-portal-api-auth-key"),
      tags: z.object({ "egakumi-purpose": z.literal("portal-api-auth"), "egakumi-format": z.literal("base64url-32-v1") }),
      attributes: z.object({ enabled: z.literal(true), nbf: z.number().int().optional(), exp: z.number().int().optional() }),
    }).parse(secret);
    const now = Date.now() / 1000;
    if ((record.attributes.nbf ?? 0) > now || (record.attributes.exp ?? Infinity) <= now) throw new Error();
    const { value } = record;
    const bytes = Buffer.from(value, "base64url");
    if (bytes.length !== 32 || bytes.toString("base64url") !== value) throw new Error();
    return bytes;
  }

  private async key(keyVersion: string): Promise<Buffer> {
    if (!this.settings.allowedVersions.has(keyVersion)) throw new ApiAuthenticationError("ApiAuthenticationUnavailable", 503);
    const cached = this.keys.get(keyVersion);
    if (cached && cached.until > Date.now()) return cached.key;
    const key = this.fetchKey(keyVersion).catch(() => {
      if (this.keys.get(keyVersion)?.key === key) this.keys.delete(keyVersion);
      throw new ApiAuthenticationError("ApiAuthenticationUnavailable", 503);
    });
    this.keys.set(keyVersion, { until: Date.now() + 60_000, key });
    return key;
  }

  /** 配備の鍵読取権限と現在版の形式を確認します。 */
  async assertReady(): Promise<void> {
    await this.key(this.settings.activeVersion);
  }

  /** AADで行、本人、用途および版を結び付けます。 */
  async seal(value: unknown, binding: readonly unknown[]): Promise<Buffer> {
    const plain = Buffer.from(JSON.stringify(value));
    if (plain.length > 65_536) throw new ApiAuthenticationError("ApiAuthenticationUnavailable", 503);
    const iv = randomBytes(12);
    const active = this.settings.activeVersion;
    const cipher = createCipheriv("aes-256-gcm", await this.key(active), iv);
    cipher.setAAD(Buffer.from(JSON.stringify(["portal-api-authorization", active, ...binding])));
    const payload = Buffer.concat([cipher.update(plain), cipher.final()]);
    return Buffer.concat([Buffer.from(active, "ascii"), iv, cipher.getAuthTag(), payload]);
  }

  /** 設定された名前の許可版だけを復号し、異なる行の暗号文を拒否します。 */
  async open(value: Uint8Array, binding: readonly unknown[]): Promise<unknown> {
    try {
      const data = Buffer.from(value);
      if (data.length < 62 || data.length > 65_596) throw new Error();
      const keyVersion = data.subarray(0, 32).toString("ascii");
      const cipher = createDecipheriv("aes-256-gcm", await this.key(keyVersion), data.subarray(32, 44));
      cipher.setAAD(Buffer.from(JSON.stringify(["portal-api-authorization", keyVersion, ...binding])));
      cipher.setAuthTag(data.subarray(44, 60));
      return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat([
        cipher.update(data.subarray(60)), cipher.final(),
      ])));
    } catch {
      throw new ApiAuthenticationError("ApiAuthenticationUnavailable", 503);
    }
  }
}
