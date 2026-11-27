import "server-only";

import { randomUUID } from "node:crypto";
import { z } from "zod";
import { apiTokenSchema, authorizationRequestSchema, PortalApiOAuth } from "./oauth.ts";
import { ApiAuthenticationError, ApiAuthCryptography, apiAuthenticationEnabled, newAuthorizationSecret, readApiAuthenticationSettings, sha256, type ApiAuthenticationSettings } from "./settings.ts";
import type { ApiAuthSession } from "./session.ts";
import { bindApiAuthSession, getApiAuthStore, type ApiAuthBinding, type ApiAuthContext, type ApiAuthorizationTransaction, type ApiAuthStore } from "./store.ts";

/** 実行時だけ使うBearer値と、長い操作の本人再確認用の参照です。 */
export type PortalBearer = {
  value: string;
  expiresAt: number;
  accessExpiresAt: number;
  generation: number;
  binding: ApiAuthBinding;
};

/** UIに公開できる接続状態です。 */
export type ApiAuthStatus = "disconnected" | "connected" | "busy" | "reauthentication_required";

const codeSchema = z.string().min(1).max(8192).regex(/^[\x21-\x7E]+$/);
const accessLifetime = (context: ApiAuthContext) => Math.min(context.accessExpiresAt?.getTime() ?? 0,
  context.connectionExpiresAt?.getTime() ?? 0, context.sessionExpiresAt.getTime());
const availableContext = (context: ApiAuthContext | undefined): context is ApiAuthContext & {
  tokenCiphertext: Uint8Array; nonceHash: string; connectionExpiresAt: Date; accessExpiresAt: Date;
} => Boolean(context?.tokenCiphertext && context.nonceHash && context.accessExpiresAt && context.connectionExpiresAt &&
  context.connectionExpiresAt.getTime() > context.now.getTime());

/** 版ごとに本人・用途を束縛し、別の取引や行への暗号文の移植を防ぎます。 */
const tokenAad = (hash: string, generation: number, revision: number) => [hash, "token", generation, revision];
const attemptAad = (attempt: Pick<ApiAuthorizationTransaction, "bindingHash" | "attemptId" | "generation">, purpose: "request" | "code") =>
  [attempt.bindingHash, purpose, attempt.generation, attempt.attemptId.toLowerCase()];

/** サーバー専用の認証取引・保存・実行時Bearerを管理します。 */
export class PortalApiAuthentication {
  private readonly crypto;
  private readonly oauth;
  private readonly store: ApiAuthStore;
  readonly settings;

  constructor(settings: ApiAuthenticationSettings, store: ApiAuthStore) {
    this.settings = settings;
    this.store = store;
    this.crypto = new ApiAuthCryptography(settings);
    this.oauth = new PortalApiOAuth(settings);
  }

  /** SQLマイグレーションとKVの現在版を読取りで確認します。 */
  async assertReady(): Promise<void> {
    await this.store.assertReady();
    await this.crypto.assertReady();
  }

  /** 期限切れの暗号文と取引を件数を区切って回収します。 */
  async collectExpired(): Promise<void> {
    await this.store.collectExpired();
  }

  /** ログインとは独立の一回限り取引を作り、認証URLを返します。 */
  async start(session: ApiAuthSession, returnTo: string, claims?: string) {
    const binding = bindApiAuthSession(session, this.settings);
    await this.store.collectExpired();
    // 追加認証が要求された資格情報は、次の認証が成功するまで再利用しません。
    if (claims) await this.store.disconnect(binding);
    const attemptId = randomUUID();
    const state = newAuthorizationSecret();
    const correlation = newAuthorizationSecret();
    const request = { verifier: newAuthorizationSecret(), nonce: newAuthorizationSecret(), returnTo };
    const generation = await this.store.begin(binding, attemptId);
    const attempt = { bindingHash: binding.hash, attemptId, generation };
    const ciphertext = await this.crypto.seal(request, attemptAad(attempt, "request"));
    await this.store.putTransaction(binding, attemptId, generation, sha256(state), sha256(correlation), ciphertext);
    return { correlation, authorizationURL: await this.oauth.authorizationURL(state, request, claims) };
  }

  /** EntraからのPOSTを相関Cookieとstateで確認し、codeを暗号化して完了GETへ渡します。 */
  async callback(state: string, correlation: string, code: string | null): Promise<{ complete: true } | { complete: false; returnTo: string }> {
    const stateHash = sha256(state);
    const correlationHash = sha256(correlation);
    const attempt = await this.store.pendingTransaction(stateHash, correlationHash);
    if (!attempt) throw new ApiAuthenticationError("ApiAuthorizationInvalid", 400);
    if (code === null) {
      await this.store.cancel(attempt.attemptId, stateHash, correlationHash);
      const request = authorizationRequestSchema.parse(await this.crypto.open(attempt.requestCiphertext, attemptAad(attempt, "request")));
      return { complete: false, returnTo: request.returnTo };
    }
    const ciphertext = await this.crypto.seal(codeSchema.parse(code), attemptAad(attempt, "code"));
    if (!await this.store.recordCode(attempt.attemptId, stateHash, correlationHash, ciphertext)) {
      throw new ApiAuthenticationError("ApiAuthorizationInvalid", 400);
    }
    return { complete: true };
  }

  /** 元のSQLログインが再確認できたGETでだけコードを一度交換します。 */
  async complete(session: ApiAuthSession, correlation: string): Promise<{ returnTo: string; connected: boolean }> {
    const binding = bindApiAuthSession(session, this.settings);
    const attempt = await this.store.takeTransaction(binding, sha256(correlation));
    if (!attempt?.codeCiphertext) throw new ApiAuthenticationError("ApiAuthorizationInvalid", 400);
    const request = authorizationRequestSchema.parse(await this.crypto.open(attempt.requestCiphertext, attemptAad(attempt, "request")));
    const code = codeSchema.parse(await this.crypto.open(attempt.codeCiphertext, attemptAad(attempt, "code")));
    const context = await this.store.context(binding);
    if (!context || context.generation !== attempt.generation || context.latestAttempt?.toLowerCase() !== attempt.attemptId.toLowerCase()) {
      return { returnTo: request.returnTo, connected: false };
    }
    try {
      const tokens = await this.oauth.exchange(code, request, binding);
      const connectionExpiry = new Date(Math.min(context.now.getTime() + 8 * 3_600_000, context.sessionExpiresAt.getTime()));
      const expiry = new Date(context.now.getTime() + tokens.expiresSeconds * 1000);
      const value = await this.crypto.seal({ access: tokens.access, refresh: tokens.refresh },
        tokenAad(binding.hash, context.generation, context.revision + 1));
      const connected = await this.store.saveAuthorization(binding, context, attempt.attemptId, value, sha256(request.nonce), expiry, connectionExpiry);
      return { returnTo: request.returnTo, connected };
    } catch { return { returnTo: request.returnTo, connected: false }; }
  }

  /** 秘密を読み出さず、利用者に表示する状態だけを判定します。 */
  async status(session: ApiAuthSession): Promise<ApiAuthStatus> {
    const context = await this.store.context(bindApiAuthSession(session, this.settings));
    if (!context || !context.tokenCiphertext) return context?.refreshBlocked ? "reauthentication_required" : "disconnected";
    if (!availableContext(context)) return "reauthentication_required";
    if (context.refreshLeaseUntil && context.refreshLeaseUntil > context.now) return "busy";
    if (context.refreshBlocked && accessLifetime(context) <= context.now.getTime()) return "reauthentication_required";
    return "connected";
  }

  /** 現在のPortalセッションに対するAPI接続だけを解除します。 */
  async disconnect(session: ApiAuthSession): Promise<void> {
    await this.store.disconnect(bindApiAuthSession(session, this.settings));
  }

  /** この用途に限定したアクセストークンを解決し、必要時はSQLリース内で更新します。 */
  async bearer(session: ApiAuthSession): Promise<PortalBearer> {
    const binding = bindApiAuthSession(session, this.settings);
    const context = await this.store.context(binding);
    if (!availableContext(context)) throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
    const expiresAt = accessLifetime(context);
    const remaining = expiresAt - context.now.getTime();
    if (remaining <= 0 && context.refreshBlocked) {
      if (context.refreshLeaseUntil && context.refreshLeaseUntil > context.now) throw new ApiAuthenticationError("ApiAuthenticationBusy", 409);
      throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
    }
    const saved = apiTokenSchema.parse(await this.crypto.open(context.tokenCiphertext, tokenAad(binding.hash, context.generation, context.revision)));
    const existing = { value: saved.access, expiresAt, accessExpiresAt: context.accessExpiresAt.getTime(), binding, generation: context.generation };
    if (remaining > 60_000 || (remaining > 0 && context.refreshBlocked)) {
      if (await this.stillAuthorized(existing)) return existing;
      throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
    }
    const lease = randomUUID();
    const started = await this.store.acquireRefresh(binding, context, lease);
    if (!started) {
      if (remaining > 0 && await this.stillAuthorized(existing)) return existing;
      throw new ApiAuthenticationError("ApiAuthenticationBusy", 409);
    }
    try {
      const tokens = await this.oauth.refresh(saved.refresh, binding, context.nonceHash);
      const expiry = new Date(started.getTime() + tokens.expiresSeconds * 1000);
      const ciphertext = await this.crypto.seal({ access: tokens.access, refresh: tokens.refresh ?? saved.refresh },
        tokenAad(binding.hash, context.generation, context.revision + 1));
      if (!await this.store.saveRefresh(binding, context, lease, ciphertext, expiry)) {
        throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
      }
      return { value: tokens.access, accessExpiresAt: expiry.getTime(), expiresAt: Math.min(expiry.getTime(), context.connectionExpiresAt.getTime(), context.sessionExpiresAt.getTime()),
        binding, generation: context.generation };
    } catch (error) {
      const denied = error instanceof ApiAuthenticationError && error.code === "ApiAuthorizationDenied";
      await this.store.finishFailedRefresh(binding, context, lease, denied);
      if (denied) throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
      if (error instanceof ApiAuthenticationError && error.code === "ApiAuthenticationRequired") throw error;
      const current = await this.store.context(binding);
      if (current && current.generation === context.generation && current.revision === context.revision &&
        accessLifetime(current) > current.now.getTime()) return existing;
      throw new ApiAuthenticationError("ApiAuthenticationUnavailable", 503);
    }
  }

  /** 本人とAPI接続世代に束縛した操作をSQLへ封印し、ブラウザーへ不透明な参照を返します。 */
  async sealOperation(bearer: PortalBearer, kind: "document" | "realtime", id: string, state: unknown, expiresAt: number): Promise<string> {
    const identifier = z.uuid().parse(id).toLowerCase();
    const encrypted = await this.crypto.seal(state, [bearer.binding.hash, kind, bearer.generation, identifier]);
    const opaque = Buffer.concat([Buffer.from([2]), Buffer.from(identifier.replaceAll("-", ""), "hex"),
      Buffer.from(newAuthorizationSecret(), "base64url")]).toString("base64url");
    if (!await this.store.putOperation(bearer.binding, bearer.generation, kind, identifier, sha256(opaque), encrypted, new Date(expiresAt))) {
      throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
    }
    return opaque;
  }

  /** 同一Portalセッションの同一世代だけが、Bearerで生成した操作参照を復元できます。 */
  async openOperation(bearer: PortalBearer, kind: "document" | "realtime", handle: string): Promise<unknown> {
    if (!/^[A-Za-z0-9_-]{66}$/.test(handle)) throw new Error("Invalid operation handle");
    const bytes = Buffer.from(handle, "base64url");
    if (bytes[0] !== 2 || bytes.length !== 49) throw new Error("Invalid operation handle");
    const uuid = bytes.subarray(1, 17).toString("hex").replace(/^(........)(....)(....)(....)(............)$/, "$1-$2-$3-$4-$5");
    const ciphertext = await this.store.getOperation(bearer.binding, bearer.generation, kind, uuid, sha256(handle));
    if (!ciphertext) throw new Error("Operation is unavailable");
    return this.crypto.open(ciphertext, [bearer.binding.hash, kind, bearer.generation, uuid]);
  }

  /** 通常資格情報と分けた最大60秒の停止専用snapshotを作成し、固定通話の停止を一度だけ送ります。 */
  async stopRealtimeCall(bearer: PortalBearer, call: { callId: string; ticket: string; expiresAt: number },
    sendFixedStop: (access: string, ticket: string, signal: AbortSignal) => Promise<boolean>): Promise<boolean> {
    const operationHash = sha256(JSON.stringify([bearer.binding.hash, bearer.generation, call.callId]));
    const aad = [bearer.binding.hash, "realtime-stop", bearer.generation, operationHash];
    const ciphertext = await this.crypto.seal({ access: bearer.value, ticket: call.ticket }, aad);
    const claim = await this.store.claimCallStop(operationHash, ciphertext, new Date(bearer.accessExpiresAt), new Date(call.expiresAt));
    if (!claim.ciphertext) return claim.confirmed === true;
    let confirmed = false;
    const expiresAt = claim.expiresAt?.getTime() ?? 0;
    try {
      if (expiresAt <= Date.now()) return false;
      const snapshot = z.strictObject({
        access: z.string().min(16).max(16_384).regex(/^[A-Za-z0-9._~+\/-]+={0,2}$/),
        ticket: z.string().min(1).max(4096).regex(/^[A-Za-z0-9_.-]+$/),
      }).parse(await this.crypto.open(claim.ciphertext, aad));
      const remaining = expiresAt - Date.now();
      if (remaining <= 0) return false;
      confirmed = await sendFixedStop(snapshot.access, snapshot.ticket, AbortSignal.timeout(Math.min(12_000, remaining)));
      return confirmed;
    } finally {
      await this.store.finishCallStop(operationHash, confirmed);
    }
  }

  /** 接続中にSQL sessionとAPIの失効世代を再確認します。 */
  async stillAuthorized(bearer: PortalBearer): Promise<boolean> {
    const context = await this.store.context(bearer.binding);
    return Boolean(availableContext(context) && context.generation === bearer.generation && bearer.expiresAt > context.now.getTime());
  }
}

let service: PortalApiAuthentication | undefined;

/** 起動時や静的ビルド時にAzureへ通信しません。 */
export function getPortalApiAuthentication(): PortalApiAuthentication {
  return service ??= new PortalApiAuthentication(readApiAuthenticationSettings(), getApiAuthStore());
}


let nextCleanup = 0;

/** 通常のヘルス確認を止めず、同じプロセスで期限切れの回収を1分に1回まで開始します。 */
export function scheduleApiAuthCleanup(): void {
  if (!apiAuthenticationEnabled() || Date.now() < nextCleanup) return;
  nextCleanup = Date.now() + 60_000;
  void Promise.resolve().then(() => getPortalApiAuthentication().collectExpired()).catch(() => undefined);
}
