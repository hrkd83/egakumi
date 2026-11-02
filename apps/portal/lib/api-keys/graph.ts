import "server-only";

import { z } from "zod";
import type { AuthEnvironment } from "../auth-environment.ts";
import { ApiKeyError, type ApiKeyActor } from "./contracts.ts";
import { requestApiKeyJson, type ApiKeyHttpOptions } from "./http-client.ts";
import { apiKeyGroupPolicySchema, isEligibleApiKeyGroup, type ApiKeyGroupPolicy, type DirectoryGroup, type DirectoryGroupObservation, type DirectoryGroupPage } from "./group-policy.ts";

/** 毎回の操作時に、検証済み本人の現在のグループ所属を確認する依存です。 */
export interface ApiKeyMembership {
  check(actor: ApiKeyActor, groupIds: readonly string[]): Promise<Set<string>>;
}

/** 本人の所属、移管先候補、管理台帳の観測で共有するGraph読取操作です。 */
export interface ApiKeyDirectory extends ApiKeyMembership {
  listEligibleGroups(actor: ApiKeyActor): Promise<DirectoryGroup[]>;
  findEligibleGroups(actor: ApiKeyActor, query: string): Promise<DirectoryGroup[]>;
  listEligibleGroupPage(actor: ApiKeyActor, input?: { search?: string; cursor?: string }): Promise<DirectoryGroupPage>;
  checkGroups(actor: ApiKeyActor, groupIds: readonly string[]): Promise<Map<string, DirectoryGroupObservation>>;
  isPlatformAdmin(actor: ApiKeyActor): Promise<boolean>;
}

/** グループ規則だけをSQLから読み、所属結果を永続キャッシュしないための依存です。 */
export type GraphApiKeyOptions = ApiKeyHttpOptions & { now?: () => number; readPolicy?: () => Promise<ApiKeyGroupPolicy> };

const tokenSchema = z.object({ access_token: z.string().min(1), expires_in: z.number().int().min(60).max(86_400), token_type: z.string().regex(/^bearer$/i) });
const membershipSchema = z.object({ value: z.array(z.uuid()).max(20) });
const directoryGroupSchema = z.object({ id: z.uuid(), displayName: z.string().min(1).max(255), securityEnabled: z.boolean() });
const directoryPageSchema = z.object({ value: z.array(directoryGroupSchema).max(100), "@odata.nextLink": z.string().max(16_384).optional() });

/**
 * Graphのアプリケーション権限で所属を確認します。トークンだけをメモリーに保持します。
 * @remarks checkMemberGroupsは入れ子の所属も判定します。所属結果はキャッシュしません。
 */
export class GraphApiKeyMembership implements ApiKeyDirectory {
  private settings: Pick<AuthEnvironment, "microsoftClientId" | "microsoftClientSecret" | "microsoftTenantId">;
  private options: ApiKeyHttpOptions;
  private now: () => number;
  private cachedToken: { value: string; expiresAt: number } | undefined;
  private pendingToken: Promise<string> | undefined;
  private readPolicy: (() => Promise<ApiKeyGroupPolicy>) | undefined;

  constructor(settings: Pick<AuthEnvironment, "microsoftClientId" | "microsoftClientSecret" | "microsoftTenantId">, options: GraphApiKeyOptions = {}) {
    if (!z.uuid().safeParse(settings.microsoftClientId).success || !z.uuid().safeParse(settings.microsoftTenantId).success || !settings.microsoftClientSecret) {
      throw new ApiKeyError("ConfigurationUnavailable", 503);
    }
    this.settings = settings;
    this.options = options;
    this.now = options.now ?? Date.now;
    this.readPolicy = options.readPolicy;
  }

  /** 短い有効期限余裕を残し、同時要求によるトークン取得の重複だけをまとめます。 */
  private async token(): Promise<string> {
    if (this.cachedToken && this.cachedToken.expiresAt > this.now() + 60_000) return this.cachedToken.value;
    if (this.pendingToken) return this.pendingToken;
    this.pendingToken = this.requestToken();
    try { return await this.pendingToken; }
    finally { this.pendingToken = undefined; }
  }

  /** 固定テナントへclient credentials要求を送り、秘密値を永続化しません。 */
  private async requestToken(): Promise<string> {
    const result = await requestApiKeyJson(`https://login.microsoftonline.com/${this.settings.microsoftTenantId}/oauth2/v2.0/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: this.settings.microsoftClientId,
        client_secret: this.settings.microsoftClientSecret,
        grant_type: "client_credentials",
        scope: "https://graph.microsoft.com/.default",
      }).toString(),
    }, this.options);
    const parsed = tokenSchema.safeParse(result.value);
    if (result.status !== 200 || !parsed.success) throw new ApiKeyError("AuthorizationUnavailable", 503);
    this.cachedToken = { value: parsed.data.access_token, expiresAt: this.now() + parsed.data.expires_in * 1_000 };
    return this.cachedToken.value;
  }

  /** 対象は登録済みグループIDに限定し、Graphの20件制限に従って分割します。 */
  async check(actor: ApiKeyActor, groupIds: readonly string[]): Promise<Set<string>> {
    if (actor.tenantId.toLowerCase() !== this.settings.microsoftTenantId.toLowerCase() || !z.uuid().safeParse(actor.objectId).success) {
      throw new ApiKeyError("AccessDenied", 403);
    }
    const requested = [...new Set(groupIds.map((id) => id.toLowerCase()))];
    if (requested.length > 1_000 || requested.some((id) => !z.uuid().safeParse(id).success)) throw new ApiKeyError("ConfigurationUnavailable", 503);
    const allowed = new Set<string>();
    try {
      for (let offset = 0; offset < requested.length; offset += 20) {
        const batch = requested.slice(offset, offset + 20);
        const result = await requestApiKeyJson(`https://graph.microsoft.com/v1.0/users/${actor.objectId}/checkMemberGroups`, {
          method: "POST",
          headers: { Authorization: `Bearer ${await this.token()}`, "Content-Type": "application/json" },
          body: JSON.stringify({ groupIds: batch }),
        }, this.options);
        if (result.status === 401) this.cachedToken = undefined;
        const parsed = membershipSchema.safeParse(result.value);
        if (result.status !== 200 || !parsed.success || parsed.data.value.some((id) => !batch.includes(id.toLowerCase()))) {
          throw new ApiKeyError("AuthorizationUnavailable", 503);
        }
        for (const id of parsed.data.value) allowed.add(id.toLowerCase());
      }
    } catch (error) {
      if (error instanceof ApiKeyError && error.code === "AccessDenied") throw error;
      throw new ApiKeyError("AuthorizationUnavailable", 503);
    }
    return allowed;
  }

  /** 署名検証済みの本人と、アプリが固定するテナントだけを受け付けます。 */
  private validateActor(actor: ApiKeyActor): void {
    if (!z.uuid().safeParse(actor.tenantId).success || actor.tenantId.toLowerCase() !== this.settings.microsoftTenantId.toLowerCase()
        || !z.uuid().safeParse(actor.objectId).success) throw new ApiKeyError("AccessDenied", 403);
  }

  /** 配備設定がまだない状態を、全グループを許可する既定値として扱いません。 */
  private async policy(): Promise<ApiKeyGroupPolicy> {
    const parsed = apiKeyGroupPolicySchema.safeParse(await this.readPolicy?.());
    if (!parsed.success) throw new ApiKeyError("ConfigurationUnavailable", 503);
    return parsed.data;
  }

  /** Graph呼出しのトークンを共有し、401では次回のトークン再取得を促します。 */
  private async readDirectory(url: string, advanced = false) {
    const result = await requestApiKeyJson(url, { method: "GET", headers: {
      Authorization: `Bearer ${await this.token()}`, ...(advanced ? { ConsistencyLevel: "eventual" } : {}),
    } }, this.options);
    if (result.status === 401) this.cachedToken = undefined;
    return result;
  }

  /** ページング先は同じGraphパス・選択列・検索条件に限定します。任意URLを追いません。 */
  private nextPage(value: string, initial: URL): string {
    let url: URL;
    try { url = new URL(value); } catch { throw new ApiKeyError("AuthorizationUnavailable", 503); }
    if (url.origin !== "https://graph.microsoft.com" || url.pathname !== initial.pathname || url.username || url.password || url.hash) {
      throw new ApiKeyError("AuthorizationUnavailable", 503);
    }
    const allowed = new Set([...initial.searchParams.keys(), "$skiptoken"]);
    for (const name of url.searchParams.keys()) {
      if (!allowed.has(name) || url.searchParams.getAll(name).length !== 1) throw new ApiKeyError("AuthorizationUnavailable", 503);
    }
    for (const [name, expected] of initial.searchParams) {
      if (url.searchParams.get(name) !== expected) throw new ApiKeyError("AuthorizationUnavailable", 503);
    }
    if (!url.searchParams.get("$skiptoken")) throw new ApiKeyError("AuthorizationUnavailable", 503);
    return url.href;
  }

  /** 本人が現在所属している適格グループを、入れ子を含めて全ページ確認します。 */
  async listEligibleGroups(actor: ApiKeyActor): Promise<DirectoryGroup[]> {
    this.validateActor(actor);
    const policy = await this.policy();
    const initial = new URL(`https://graph.microsoft.com/v1.0/users/${actor.objectId}/transitiveMemberOf/microsoft.graph.group`);
    initial.searchParams.set("$select", "id,displayName,securityEnabled");
    initial.searchParams.set("$top", "100");
    initial.searchParams.set("$count", "true");
    const groups = new Map<string, DirectoryGroup>();
    const visited = new Set<string>();
    let url: string | undefined = initial.href;
    for (let page = 0; url && page < 100; page++) {
      if (visited.has(url)) throw new ApiKeyError("AuthorizationUnavailable", 503);
      visited.add(url);
      const response = await this.readDirectory(url, true);
      const parsed = directoryPageSchema.safeParse(response.value);
      if (response.status !== 200 || !parsed.success) throw new ApiKeyError("AuthorizationUnavailable", 503);
      for (const group of parsed.data.value) {
        if (isEligibleApiKeyGroup(group, policy)) groups.set(group.id.toLowerCase(), { ...group, id: group.id.toLowerCase() });
      }
      if (groups.size > 1_000) throw new ApiKeyError("DirectoryResultTooLarge", 503);
      const next = parsed.data["@odata.nextLink"];
      url = next ? this.nextPage(next, initial) : undefined;
    }
    if (url) throw new ApiKeyError("DirectoryResultTooLarge", 503);
    return [...groups.values()].sort((a, b) => a.displayName.localeCompare(b.displayName) || a.id.localeCompare(b.id));
  }

  /**
   * 移管候補はGraphの各100件ページを読み、配備設定の正規表現で絞って返します。
   * @remarks Graphへ正規表現は渡せないため、検索入力だけを前方一致に使います。
   * 一致がないページにも続きがあり得るため、その場合もカーソルを返します。
   */
  async listEligibleGroupPage(actor: ApiKeyActor, input: { search?: string; cursor?: string } = {}): Promise<DirectoryGroupPage> {
    this.validateActor(actor);
    const policy = await this.policy();
    const search = input.search?.trim() ?? "";
    if (search.length > 80 || /[\x00-\x1f\x7f]/.test(search) || (input.cursor?.length ?? 0) > 24_000) {
      throw new ApiKeyError("InvalidRequest", 400);
    }
    const initial = new URL("https://graph.microsoft.com/v1.0/groups");
    initial.searchParams.set("$select", "id,displayName,securityEnabled");
    initial.searchParams.set("$top", "100");
    initial.searchParams.set("$filter", search
      ? `securityEnabled eq true and startswith(displayName,'${search.replaceAll("'", "''")}')`
      : "securityEnabled eq true");
    let url = initial.href;
    if (input.cursor) {
      try {
        if (!/^[A-Za-z0-9_-]+$/.test(input.cursor)) throw new Error();
        url = this.nextPage(Buffer.from(input.cursor, "base64url").toString("utf-8"), initial);
      } catch { throw new ApiKeyError("InvalidRequest", 400); }
    }
    const response = await this.readDirectory(url);
    const parsed = directoryPageSchema.safeParse(response.value);
    if (response.status !== 200 || !parsed.success) throw new ApiKeyError("AuthorizationUnavailable", 503);
    const next = parsed.data["@odata.nextLink"];
    const nextUrl = next ? this.nextPage(next, initial) : undefined;
    return { groups: parsed.data.value.filter((group) => isEligibleApiKeyGroup(group, policy))
      .map((group) => ({ ...group, id: group.id.toLowerCase() })),
    nextCursor: nextUrl ? Buffer.from(nextUrl).toString("base64url") : null };
  }

  /** 名前の前方一致で候補を絞ります。候補への所属は移管元の認可と別の条件です。 */
  async findEligibleGroups(actor: ApiKeyActor, query: string): Promise<DirectoryGroup[]> {
    if (typeof query !== "string" || query.trim().length < 2) throw new ApiKeyError("InvalidRequest", 400);
    return (await this.listEligibleGroupPage(actor, { search: query })).groups.slice(0, 50);
  }

  /**
   * 明示的なグループGETだけで削除を判定します。403・429・通信障害はunknownとして残します。
   * @remarks 所属を要求しないため、移管先の適格性検査にも利用できます。最大4件だけ並行処理します。
   */
  async checkGroups(actor: ApiKeyActor, groupIds: readonly string[]): Promise<Map<string, DirectoryGroupObservation>> {
    this.validateActor(actor);
    return this.observeGroups(groupIds);
  }

  /** 定期照合は固定テナントの登録済みIDだけを読み取り、人の本人識別子を作りません。 */
  async observeGroups(groupIds: readonly string[]): Promise<Map<string, DirectoryGroupObservation>> {
    const policy = await this.policy();
    const ids = [...new Set(groupIds.map((id) => id.toLowerCase()))];
    if (ids.length > 1_000 || ids.some((id) => !z.uuid().safeParse(id).success)) throw new ApiKeyError("InvalidRequest", 400);
    const results = new Map<string, DirectoryGroupObservation>();
    let offset = 0;
    const observe = async (id: string) => {
      const result: DirectoryGroupObservation = { id, displayName: null, securityEnabled: null,
        status: "unknown", checkedAt: new Date(this.now()).toISOString(), errorCode: "DirectoryUnavailable" };
      try {
        const url = new URL(`https://graph.microsoft.com/v1.0/groups/${id}`);
        url.searchParams.set("$select", "id,displayName,securityEnabled");
        const response = await this.readDirectory(url.href);
        result.checkedAt = new Date(this.now()).toISOString();
        if (response.status === 404) { result.status = "deleted"; result.errorCode = null; }
        else if (response.status === 200) {
          const parsed = directoryGroupSchema.safeParse(response.value);
          if (parsed.success && parsed.data.id.toLowerCase() === id) {
            result.displayName = parsed.data.displayName;
            result.securityEnabled = parsed.data.securityEnabled;
            result.status = isEligibleApiKeyGroup(parsed.data, policy) ? "active" : "ineligible";
            result.errorCode = null;
          } else { result.errorCode = "DirectoryResponseInvalid"; }
        } else if (response.status === 403) result.errorCode = "DirectoryAccessDenied";
        else if (response.status === 429) result.errorCode = "DirectoryRateLimited";
      } catch { /* 応答本文やトークンをログへ出さず、不明の観測として保存します。 */ }
      results.set(id, result);
    };
    await Promise.all(Array.from({ length: Math.min(4, ids.length) }, async () => {
      while (offset < ids.length) { const id = ids[offset++]; await observe(id); }
    }));
    return results;
  }

  /** 管理画面の権限は、表示名やメールではなく配備済み管理グループの現在の所属で決めます。 */
  async isPlatformAdmin(actor: ApiKeyActor): Promise<boolean> {
    this.validateActor(actor);
    const policy = await this.policy();
    return (await this.check(actor, [policy.platformGroupId])).has(policy.platformGroupId);
  }
}
