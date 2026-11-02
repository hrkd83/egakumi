import "server-only";

import { AzureCliCredential, ManagedIdentityCredential } from "@azure/identity";
import { z } from "zod";
import { ApiKeyError, apimApiIdSchema, apimSubscriptionIdSchema, type ApiKeySecrets } from "./contracts.ts";
import type { ApiKeyEnvironment } from "./environment.ts";
import { requestApiKeyJson, type ApiKeyHttpOptions, type ApiKeyHttpResponse } from "./http-client.ts";

/** 秘密値を除いた、APIMで現在確認できる購読です。 */
export type ManagedApiKeySubscription = {
  id: string;
  displayName: string;
  scope: string;
  state: string;
  etag?: string;
};

/** サービスが利用するAPIM操作です。URLや任意のリソース名は受け付けません。 */
export interface ApiKeyApim {
  scopeFor(productId: string): string;
  get(id: string): Promise<ManagedApiKeySubscription | null>;
  create(id: string, productId: string, displayName: string): Promise<ManagedApiKeySubscription>;
  productIncludesApi(productId: string, apiId: string): Promise<boolean>;
  setState(id: string, state: "active" | "suspended", etag: string): Promise<void>;
  delete(id: string, etag: string): Promise<boolean>;
  secrets(id: string): Promise<ApiKeySecrets>;
}

/** テストで通信とトークン取得を差し替えるための依存です。 */
export type ApiKeyApimOptions = ApiKeyHttpOptions & { getAccessToken?: () => Promise<string> };

const subscriptionSchema = z.object({
  id: z.string().min(1),
  etag: z.string().optional(),
  properties: z.object({ displayName: z.string().nullish(), scope: z.string().min(1), state: z.string().min(1) }),
});
const secretSchema = z.object({
  primaryKey: z.string().regex(/^[\x21-\x7e]{16,512}$/),
  secondaryKey: z.string().regex(/^[\x21-\x7e]{16,512}$/),
});
const productMembershipPageSchema = z.object({
  value: z.array(z.object({ id: z.string().min(1) })).max(10_000),
  nextLink: z.string().min(1).nullish(),
});

/**
 * ポータルが所有する購読だけを、固定APIMの管理APIで操作します。
 * @remarks 一覧・状態取得では上流が返したキーを捨て、明示的なsecrets操作でだけ取り出します。
 */
export class AzureApiKeyApim implements ApiKeyApim {
  private settings: ApiKeyEnvironment;
  private options: ApiKeyApimOptions;
  private getAccessToken: () => Promise<string>;

  constructor(settings: ApiKeyEnvironment, options: ApiKeyApimOptions = {}) {
    // factory以外から構築した場合も、Azure以外や別種のリソースへ向けられないよう検証します。
    if (!/^\/subscriptions\/[0-9a-f-]{36}\/resourceGroups\/[a-zA-Z0-9_.()-]{1,90}\/providers\/Microsoft\.ApiManagement\/service\/[a-zA-Z][a-zA-Z0-9-]{0,49}$/i.test(settings.apimResourceId)) {
      throw new ApiKeyError("ConfigurationUnavailable", 503);
    }
    this.settings = settings;
    this.options = options;
    const credential = options.getAccessToken ? null : settings.mode === "azure-cli"
      ? new AzureCliCredential({ tenantId: settings.tenantId, processTimeoutInMs: 15_000 })
      : new ManagedIdentityCredential({ clientId: settings.clientId });
    this.getAccessToken = options.getAccessToken ?? (async () => {
      const access = await credential!.getToken("https://management.azure.com/.default", { abortSignal: AbortSignal.timeout(15_000) });
      if (!access) throw new ApiKeyError("UpstreamUnavailable", 503);
      return access.token;
    });
  }

  /** 登録済み製品との完全一致に使う、APIM内の製品スコープです。 */
  scopeFor(productId: string): string {
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/.test(productId)) throw new ApiKeyError("InvalidInput", 400);
    return `${this.settings.apimResourceId}/products/${productId}`;
  }

  /** クライアント入力から、管理先のホストやAPIMを変えられないURLを作ります。 */
  private resourceUrl(id: string, suffix = ""): string {
    if (!apimSubscriptionIdSchema.safeParse(id).success || id.toLowerCase() === "master") throw new ApiKeyError("NotFound", 404);
    return `https://management.azure.com${this.settings.apimResourceId}/subscriptions/${id}${suffix}?api-version=2024-05-01`;
  }

  /** 固定APIM内で検証したURLへ通信し、ARMの本文やアクセストークンを外へ返しません。 */
  private async requestResource(url: string, method: string, body?: unknown, headers: Record<string, string> = {}): Promise<ApiKeyHttpResponse> {
    try {
      return await requestApiKeyJson(url, {
        method,
        headers: { Authorization: `Bearer ${await this.getAccessToken()}`, "Content-Type": "application/json", ...headers },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      }, this.options);
    } catch {
      throw new ApiKeyError("UpstreamUnavailable", 503);
    }
  }

  /** 検証した購読IDだけを、固定APIMの管理APIへ送ります。 */
  private request(id: string, method: string, body?: unknown, headers: Record<string, string> = {}, suffix = ""): Promise<ApiKeyHttpResponse> {
    return this.requestResource(this.resourceUrl(id, suffix), method, body, headers);
  }

  /** 発行失敗時だけ製品の未配備を確かめ、確認できない障害を未配備と断定しません。 */
  private async productIsMissing(productId: string): Promise<boolean> {
    try {
      const result = await this.requestResource(`https://management.azure.com${this.scopeFor(productId)}?api-version=2024-05-01`, "GET");
      return result.status === 404;
    } catch {
      return false;
    }
  }

  /** 更新衝突や権限不足を、秘密情報のない一貫したエラーへ変換します。 */
  private requireStatus(result: ApiKeyHttpResponse, accepted: readonly number[]): void {
    if (accepted.includes(result.status)) return;
    if ([409, 412].includes(result.status)) throw new ApiKeyError("UpstreamConflict", 409);
    if (result.status === 429) throw new ApiKeyError("UpstreamBusy", 503);
    throw new ApiKeyError("UpstreamUnavailable", 503);
  }

  /** 応答の購読IDを照合し、上流から返されてもキーと個人所有者は取り出しません。 */
  private subscription(id: string, result: ApiKeyHttpResponse): ManagedApiKeySubscription {
    const parsed = subscriptionSchema.safeParse(result.value);
    const expectedId = `${this.settings.apimResourceId}/subscriptions/${id}`;
    if (!parsed.success || parsed.data.id.toLowerCase() !== expectedId.toLowerCase()) throw new ApiKeyError("UpstreamUnavailable", 503);
    const etag = result.headers.get("etag") ?? parsed.data.etag;
    return {
      id, displayName: parsed.data.properties.displayName ?? id, scope: parsed.data.properties.scope,
      state: parsed.data.properties.state,
      ...(etag && etag !== "*" && !/[\r\n]/.test(etag) ? { etag } : {}),
    };
  }

  /** 自分の台帳にあるIDの状態を確認します。存在しない場合だけnullにします。 */
  async get(id: string): Promise<ManagedApiKeySubscription | null> {
    const result = await this.request(id, "GET");
    if (result.status === 404) return null;
    this.requireStatus(result, [200]);
    return this.subscription(id, result);
  }

  /** 全購読のメタデータだけを取得します。ページ取得失敗を「存在しない」と扱いません。 */
  async listMetadata(): Promise<ManagedApiKeySubscription[]> {
    const path = `${this.settings.apimResourceId}/subscriptions`;
    let url: string | null = `https://management.azure.com${path}?api-version=2024-05-01&$top=100`;
    const visited = new Set<string>(), ids = new Set<string>();
    const records: ManagedApiKeySubscription[] = [];
    while (url) {
      const next = new URL(url);
      if (next.origin !== "https://management.azure.com" || next.username || next.password || next.hash
          || next.pathname.toLowerCase() !== path.toLowerCase() || visited.has(next.href) || visited.size >= 1000
          || next.searchParams.getAll("api-version").length !== 1 || next.searchParams.get("api-version") !== "2024-05-01") {
        throw new ApiKeyError("UpstreamUnavailable", 503);
      }
      visited.add(next.href);
      const response = await this.requestResource(next.href, "GET");
      this.requireStatus(response, [200]);
      const page = z.object({ value: z.array(subscriptionSchema).max(1000), nextLink: z.string().nullish() }).safeParse(response.value);
      if (!page.success || records.length + page.data.value.length > 100_000) throw new ApiKeyError("UpstreamUnavailable", 503);
      for (const item of page.data.value) {
        const prefix = `${path}/`;
        const id = item.id.slice(prefix.length);
        if (!item.id.toLowerCase().startsWith(prefix.toLowerCase()) || !apimSubscriptionIdSchema.safeParse(id).success
            || ids.has(id.toLowerCase())) throw new ApiKeyError("UpstreamUnavailable", 503);
        ids.add(id.toLowerCase());
        records.push(this.subscription(id, { ...response, headers: new Headers(), value: item }));
      }
      url = page.data.nextLink ?? null;
    }
    return records;
  }

  /** SQLの確定値を反映します。既存キーは送信せず保持し、再作成時も停止状態を直接指定できます。 */
  async reconcileMetadata(id: string, desired: { displayName: string; productId: string; state: "active" | "suspended" }, etag: string | null): Promise<void> {
    if (!desired.displayName || desired.displayName.length > 100 || !["active", "suspended"].includes(desired.state)) throw new ApiKeyError("InvalidInput", 400);
    if (etag !== null) this.validateEtag(etag);
    const result = await this.request(id, etag === null ? "PUT" : "PATCH", {
      properties: { displayName: desired.displayName, scope: this.scopeFor(desired.productId), state: desired.state,
        ...(etag === null ? { allowTracing: false } : {}) },
    }, etag === null ? { "If-None-Match": "*" } : { "If-Match": etag });
    this.requireStatus(result, etag === null ? [200, 201] : [200, 204]);
  }

  /**
   * 指定APIに製品が実際に関連付いていることを、現在のARM情報から確認します。
   * @remarks ページ送り先も同一APIM・API・APIバージョンに限定し、秘密取得は行いません。
   */
  async productIncludesApi(productId: string, apiId: string): Promise<boolean> {
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(productId) || !apimApiIdSchema.safeParse(apiId).success) {
      throw new ApiKeyError("NotFound", 404);
    }
    const scope = this.scopeFor(productId).toLowerCase();
    const path = `${this.settings.apimResourceId}/apis/${apiId}/products`;
    const canonicalPrefix = `${this.settings.apimResourceId}/products/`;
    const apiPrefix = `${path}/`;
    const visited = new Set<string>();
    let url: string | null = `https://management.azure.com${path}?api-version=2024-05-01`;
    let count = 0;
    while (url) {
      let next: URL;
      try { next = new URL(url); } catch { throw new ApiKeyError("UpstreamUnavailable", 503); }
      if (next.protocol !== "https:" || next.hostname !== "management.azure.com" || next.username || next.password
          || (next.port && next.port !== "443") || next.hash || next.pathname.toLowerCase() !== path.toLowerCase()
          || next.searchParams.getAll("api-version").length !== 1 || next.searchParams.get("api-version") !== "2024-05-01"
          || visited.has(next.href) || visited.size >= 50) {
        throw new ApiKeyError("UpstreamUnavailable", 503);
      }
      visited.add(next.href);
      const response = await this.requestResource(next.href, "GET");
      if (response.status === 404) return false;
      this.requireStatus(response, [200]);
      const page = productMembershipPageSchema.safeParse(response.value);
      if (!page.success || (count += page.data.value.length) > 10_000) throw new ApiKeyError("UpstreamUnavailable", 503);
      let matched = false;
      for (const product of page.data.value) {
        // ARMは製品の通常ID、または要求API配下の製品IDを返します。別APIや別APIMのIDは拒否します。
        const prefix = [canonicalPrefix, apiPrefix].find((value) => product.id.toLowerCase().startsWith(value.toLowerCase()));
        const identifier = prefix ? product.id.slice(prefix.length) : "";
        if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(identifier)) throw new ApiKeyError("UpstreamUnavailable", 503);
        if (this.scopeFor(identifier).toLowerCase() === scope) matched = true;
      }
      if (matched) return true;
      url = page.data.nextLink ?? null;
    }
    return false;
  }

  /** 個人ownerIdを設定せず、指定製品に限定した購読を新規作成します。 */
  async create(id: string, productId: string, displayName: string): Promise<ManagedApiKeySubscription> {
    const result = await this.request(id, "PUT", {
      properties: { displayName, scope: this.scopeFor(productId), state: "active", allowTracing: false },
    }, { "If-None-Match": "*" });
    if ([400, 404].includes(result.status) && await this.productIsMissing(productId)) {
      throw new ApiKeyError("ProductNotDeployed", 503);
    }
    this.requireStatus(result, [200, 201]);
    return this.subscription(id, result);
  }

  /** 棚卸中に別の管理者が変更した購読を、古い状態で上書きしません。 */
  async setState(id: string, state: "active" | "suspended", etag: string): Promise<void> {
    this.validateEtag(etag);
    const result = await this.request(id, "PATCH", { properties: { state } }, { "If-Match": etag });
    this.requireStatus(result, [200, 204]);
  }

  /** 削除完了を確認できた場合だけtrueを返し、非同期削除は台帳で継続状態にします。 */
  async delete(id: string, etag: string): Promise<boolean> {
    this.validateEtag(etag);
    const result = await this.request(id, "DELETE", undefined, { "If-Match": etag });
    this.requireStatus(result, [200, 202, 204, 404]);
    return result.status !== 202;
  }

  /** 明示的な表示操作に限り、キーを取得します。呼び出し側で所属と製品を再検証します。 */
  async secrets(id: string): Promise<ApiKeySecrets> {
    const result = await this.request(id, "POST", undefined, {}, "/listSecrets");
    this.requireStatus(result, [200]);
    const parsed = secretSchema.safeParse(result.value);
    if (!parsed.success) throw new ApiKeyError("UpstreamUnavailable", 503);
    return parsed.data;
  }

  /** 無条件更新やHTTPヘッダー注入に使える値を拒否します。 */
  private validateEtag(etag: string): void {
    if (!etag || etag === "*" || /[\r\n]/.test(etag)) throw new ApiKeyError("UpstreamUnavailable", 503);
  }
}
