import { createHash } from "node:crypto";

/** アプリ自身の認証処理で署名・issuer・audienceまで確認した本人。UPN申告は使用しません。 */
export interface VerifiedPrincipal { tenantId: string; ownerId: string }

/** 秘密を含むため、暗号化したバックエンド共有DBだけに保存します。 */
export interface PrivateState {
  revision: number;
  status: "pending" | "complete" | "signed_out";
  current: string;
  sessionId: string;
  generation: number;
  metadata?: Metadata;
  authentication?: Authentication;
  authenticationExpiresAt?: number;
  pending?: Rotation;
  rotationCommitAttempted?: boolean;
}

/** 永続保存は版数の比較更新、排他は複数プロセス・ホストで有効な方式にします。 */
export interface StateTransaction {
  read(): Promise<PrivateState | undefined>;
  save(expectedRevision: number, next: PrivateState): Promise<void>;
}

/** 実装例: DBの利用者別advisory lockと暗号化列。ロックの期限切れにはfencingも必要です。 */
export interface SharedStateStore {
  withExclusive<T>(binding: string, action: (transaction: StateTransaction) => Promise<T>): Promise<T>;
}

interface Metadata {
  protocolVersion: 2; sessionId: string; generation: number; idleExpiresAt: number;
  absoluteExpiresAt: number; nextRotationAt: number; expiresAt: number;
}
interface Authentication {
  protocolVersion: 2; status: "need_login"; transactionId: string; pollSecret: string;
  provisionalConnectionSession: string; loginUrl: string; expiresIn: number; statusUrl?: string;
}
interface Rotation { rotationId: string; candidateSession: string; fromGeneration: number; generation: number; expiresAt: number }

/** ブラウザーへ返してよい値だけを含み、秘密ヘッダーは業務応答から除去済みです。 */
export type GatewayResult =
  | { kind: "login"; loginUrl: string; expiresIn: number }
  | { kind: "response"; response: Response; sessionUpdatePending: boolean };

/** 上流本文や例外を含まない固定エラー。再認証を必要としない更新競合も区別します。 */
export class ConnectionSessionError extends Error {
  public readonly code: string;
  public readonly status?: number;

  constructor(code: string, status?: number) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

const HEX = /^[a-f0-9]{64}$/;
const TOKEN = /^cs2\.([a-f0-9]{64})\.[a-f0-9]{64}$/;
const UUID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
const ROTATION_HEADERS = {
  candidateSession: "X-Core-Connection-Update", rotationId: "X-Core-Connection-Update-Id",
  fromGeneration: "X-Core-Connection-Update-From-Generation", generation: "X-Core-Connection-Update-Generation",
  expiresAt: "X-Core-Connection-Update-Expires-At",
} as const;

/** 制御要求は25秒を上限とし、業務処理中はその共通期限も越えません。 */
function controlDeadline(deadline?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(25_000);
  return deadline ? AbortSignal.any([deadline, timeout]) : timeout;
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ConnectionSessionError("InvalidResponse");
  return value as Record<string, unknown>;
}
function sessionId(value: unknown): string {
  const match = typeof value === "string" && TOKEN.exec(value);
  if (!match) throw new ConnectionSessionError("InvalidSession");
  return match[1];
}
function expiry(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value) && value > 0 && value <= 253402300799; }

async function readJson(response: Response): Promise<Record<string, unknown>> {
  const reader = response.body?.getReader();
  if (!reader) throw new ConnectionSessionError("InvalidResponse");
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.length;
      if (size > 65536) { await reader.cancel(); throw new ConnectionSessionError("ResponseTooLarge"); }
      chunks.push(chunk.value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    return record(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)));
  } catch (error) {
    if (error instanceof ConnectionSessionError) throw error;
    throw new ConnectionSessionError("InvalidResponse");
  } finally { reader.releaseLock(); }
}

/**
 * 業務本文を64KiBまで先に読み、値の更新確定を待つ間に元fetchの期限で失うことを防ぎます。
 * @remarks JSON用の有限応答を対象とします。ストリーミングや大容量ファイルには使いません。
 */
async function preserveBusinessResponse(response: Response): Promise<Response> {
  if (!response.body) return new Response(null, { status: response.status, statusText: response.statusText, headers: response.headers });
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.length;
      if (size > 65536) throw new ConnectionSessionError("ResponseTooLarge");
      chunks.push(chunk.value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    return new Response(bytes, { status: response.status, statusText: response.statusText, headers: response.headers });
  } catch (error) {
    if (error instanceof ConnectionSessionError) throw error;
    throw new ConnectionSessionError("GatewayUnavailable");
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

function metadata(value: unknown): Metadata {
  const item = record(value);
  if (item.protocolVersion !== 2 || typeof item.sessionId !== "string" || !HEX.test(item.sessionId)
    || !Number.isSafeInteger(item.generation) || Number(item.generation) < 1
    || ![item.idleExpiresAt, item.absoluteExpiresAt, item.nextRotationAt, item.expiresAt].every(expiry)
    || item.expiresAt !== Math.min(Number(item.idleExpiresAt), Number(item.absoluteExpiresAt))
    || ["connectionSession", "candidateSession", "provisionalConnectionSession", "pollSecret"].some((key) => key in item)) {
    throw new ConnectionSessionError("InvalidMetadata");
  }
  return { protocolVersion: 2, sessionId: item.sessionId, generation: Number(item.generation), idleExpiresAt: Number(item.idleExpiresAt),
    absoluteExpiresAt: Number(item.absoluteExpiresAt), nextRotationAt: Number(item.nextRotationAt), expiresAt: Number(item.expiresAt) };
}

/** 確認済み本人と固定の接続先、サーバー専用の秘密・共有保存先です。 */
export type ConnectionSessionOptions = {
  origin: string; apiKey: string; subscriptionId: string; profile: string;
  principal: VerifiedPrincipal; store: SharedStateStore; fetch?: typeof fetch;
};

/** Next.js等のNodeバックエンド専用。ブラウザーバンドルへimportしないでください。 */
export class ConnectionSessionClient {
  private readonly binding: string;
  private readonly origin: string;
  private readonly options: ConnectionSessionOptions;

  constructor(options: ConnectionSessionOptions) {
    this.options = options;
    const url = new URL(options.origin);
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash
      || (url.port && url.port !== "443") || !options.apiKey || /\s/.test(options.apiKey)
      || !/^[A-Za-z0-9._-]{1,256}$/.test(options.subscriptionId) || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(options.profile)
      || !UUID.test(options.principal.tenantId) || !UUID.test(options.principal.ownerId)) throw new ConnectionSessionError("InvalidConfiguration");
    this.origin = url.origin;
    this.binding = createHash("sha256").update(JSON.stringify([this.origin, options.subscriptionId, options.profile,
      options.principal.tenantId.toLowerCase(), options.principal.ownerId.toLowerCase()])).digest("hex");
  }

  private async send(path: string, current?: string, body?: object, method = body ? "POST" : "GET", signal = controlDeadline()): Promise<Response> {
    const headers = new Headers({ "api-key": this.options.apiKey, "Accept": "application/json" });
    if (current) { sessionId(current); headers.set("X-Core-Connection-Session", current); }
    if (body) headers.set("Content-Type", "application/json");
    try {
      signal.throwIfAborted();
      const response = await (this.options.fetch ?? fetch)(this.origin + path, { method, headers,
        body: body ? JSON.stringify(body) : undefined, redirect: "error", cache: "no-store", signal });
      return response;
    } catch { throw new ConnectionSessionError("GatewayUnavailable"); }
  }

  private authentication(value: unknown): Authentication {
    const item = record(value);
    const login = typeof item.loginUrl === "string" && !/\s/.test(item.loginUrl) ? new URL(item.loginUrl) : undefined;
    const fragment = login && new URLSearchParams(login.hash.slice(1));
    if (item.protocolVersion !== 2 || item.status !== "need_login" || typeof item.transactionId !== "string" || !HEX.test(item.transactionId)
      || typeof item.pollSecret !== "string" || !HEX.test(item.pollSecret) || !Number.isInteger(item.expiresIn)
      || Number(item.expiresIn) < 60 || Number(item.expiresIn) > 600 || !login || login.origin !== this.origin
      || login.pathname !== "/auth/connections/browser/launch" || login.search || login.username || login.password
      || !fragment || [...fragment.keys()].length !== 2 || fragment.get("transactionId") !== item.transactionId
      || !HEX.test(fragment.get("launch") ?? "") || sessionId(item.provisionalConnectionSession) === item.transactionId
      || String(item.provisionalConnectionSession).endsWith("." + item.pollSecret)
      || (item.statusUrl !== undefined && item.statusUrl !== `${this.origin}/auth/connections/status?transactionId=${item.transactionId}`)) {
      throw new ConnectionSessionError("InvalidAuthentication");
    }
    return { protocolVersion: 2, status: "need_login", transactionId: item.transactionId, pollSecret: item.pollSecret,
      provisionalConnectionSession: String(item.provisionalConnectionSession), loginUrl: login.href, expiresIn: Number(item.expiresIn) };
  }

  private rotation(value: unknown, state: PrivateState): Rotation {
    const item = record(value);
    if (typeof item.rotationId !== "string" || !HEX.test(item.rotationId) || sessionId(item.candidateSession) !== state.sessionId
      || item.candidateSession === state.current || !Number.isSafeInteger(item.fromGeneration) || !Number.isSafeInteger(item.generation)
      || item.fromGeneration !== state.generation || item.generation !== state.generation + 1 || !expiry(item.expiresAt)) {
      throw new ConnectionSessionError("InvalidRotation");
    }
    return { rotationId: item.rotationId, candidateSession: String(item.candidateSession), fromGeneration: state.generation,
      generation: state.generation + 1, expiresAt: item.expiresAt };
  }

  private async save(tx: StateTransaction, state: PrivateState, next: PrivateState): Promise<PrivateState> {
    const updated = { ...next, revision: state.revision + 1 };
    await tx.save(state.revision, updated);
    return updated;
  }

  /** 過去の取引や証明を残さず、バックエンド側の保存状態を消去します。 */
  private async clear(tx: StateTransaction, state: PrivateState): Promise<PrivateState> {
    return this.save(tx, state, { revision: state.revision, status: "signed_out", current: "", sessionId: "", generation: 0 });
  }

  /** 業務応答の期限だけを更新します。旧応答で世代・期限を巻き戻しません。 */
  private async businessMetadata(tx: StateTransaction, state: PrivateState, headers: Headers): Promise<PrivateState> {
    const names = { generation: "X-Core-Connection-Generation", idleExpiresAt: "X-Core-Connection-Idle-Expires-At",
      absoluteExpiresAt: "X-Core-Connection-Absolute-Expires-At", nextRotationAt: "X-Core-Connection-Rotate-After" };
    if (!Object.values(names).some((name) => headers.has(name))) return state;
    if (!Object.values(names).every((name) => headers.has(name))) throw new ConnectionSessionError("InvalidMetadata");
    const values = Object.fromEntries(Object.entries(names).map(([key, name]) => [key, Number(headers.get(name))]));
    const next = metadata({ ...values, protocolVersion: 2, sessionId: state.sessionId,
      expiresAt: Math.min(values.idleExpiresAt, values.absoluteExpiresAt) });
    if (state.metadata && next.absoluteExpiresAt !== state.metadata.absoluteExpiresAt) throw new ConnectionSessionError("InvalidMetadata");
    if (next.generation !== state.generation) return state;
    if (state.metadata) next.idleExpiresAt = Math.max(next.idleExpiresAt, state.metadata.idleExpiresAt);
    next.expiresAt = Math.min(next.idleExpiresAt, next.absoluteExpiresAt);
    return this.save(tx, state, { ...state, metadata: next });
  }

  private async commit(tx: StateTransaction, state: PrivateState, deadline?: AbortSignal): Promise<PrivateState> {
    const candidate = state.pending;
    if (!candidate) return state;
    const retry = state.rotationCommitAttempted ?? false;
    if (!retry) state = await this.save(tx, state, { ...state, rotationCommitAttempted: true });
    const body = { rotationId: candidate.rotationId, candidateSession: candidate.candidateSession, fromGeneration: candidate.fromGeneration };
    let response = await this.send("/auth/connections/session/commit", retry ? candidate.candidateSession : state.current, body, "POST", controlDeadline(deadline));
    let value = await readJson(response);
    if (response.status === 403 && record(value.error).code === "ConnectionNotAuthorized") {
      // 確定の成否が不明なら、もう一方の保存済み証明でも確認します。
      // 両方の証明が明示的に拒否されたときだけ、業務APIから認証を取り直せます。
      response = await this.send("/auth/connections/session/commit", retry ? state.current : candidate.candidateSession, body, "POST", controlDeadline(deadline));
      value = await readJson(response);
      if (response.status === 403 && record(value.error).code === "ConnectionNotAuthorized") {
        throw new ConnectionSessionError("ConnectionSessionExpired", 403);
      }
    }
    if (!response.ok) {
      const code = record(value.error).code;
      if (response.status === 409 && code === "SessionRotationExpired") {
        const next = { ...state };
        delete next.pending;
        delete next.rotationCommitAttempted;
        return this.save(tx, state, next);
      }
      if (response.status === 409 && code === "SessionRotationSuperseded") {
        const latest = await tx.read();
        if (latest && latest.generation > candidate.fromGeneration && !latest.pending) return latest;
        throw new ConnectionSessionError("SessionRotationSuperseded", 409);
      }
      throw new ConnectionSessionError("SessionCommitFailed", response.status);
    }
    const updated = metadata(value);
    if (updated.sessionId !== state.sessionId || updated.generation !== candidate.generation
      || (state.metadata && updated.absoluteExpiresAt !== state.metadata.absoluteExpiresAt)) throw new ConnectionSessionError("InvalidMetadata");
    const next = { ...state, current: candidate.candidateSession, generation: updated.generation, metadata: updated };
    delete next.pending;
    delete next.rotationCommitAttempted;
    return this.save(tx, state, next);
  }

  /**
   * 失効した更新候補で業務利用を永久に止めず、明示的な両証明の拒否後だけ新しい認証案内へ戻します。
   * @remarks 通信断や503は確定済みか判断できないため、候補を残して次回の確定確認を待ちます。
   */
  private async commitForBusiness(tx: StateTransaction, state: PrivateState, deadline: AbortSignal): Promise<PrivateState> {
    try { return await this.commit(tx, state, deadline); }
    catch (error) {
      if (error instanceof ConnectionSessionError && error.code === "ConnectionSessionExpired") {
        // 初回の確定試行フラグも先に永続化するため、消去の比較更新には最新の版を使います。
        const latest = await tx.read();
        if (!latest || latest.sessionId !== state.sessionId || latest.current !== state.current
          || latest.pending?.rotationId !== state.pending?.rotationId) throw new ConnectionSessionError("SessionStateInvalid");
        return this.clear(tx, latest);
      }
      throw error;
    }
  }

  /**
   * 更新確定が失敗しても成功済み業務の本文は返し、次回は確定だけを先に再試行します。
   * @remarks 64KiBまでの有限なJSON業務応答を保持します。ストリームや大容量応答には使用しません。
   */
  async get(path = "/tools/graph/v1/me"): Promise<GatewayResult> {
    if (!/^\/[A-Za-z0-9_./~-]+$/.test(path) || path.includes("//") || path.split("/").some((part) => part === "." || part === "..")
      || path.startsWith("/auth/")) throw new ConnectionSessionError("InvalidApiPath");
    return this.options.store.withExclusive(this.binding, async (tx) => {
      // HTTPの90秒予算は排他取得後に一度だけ開始し、業務前の更新確定と
      // 409後の再試行でも取り直しません。SQL自身の期限とfencingはstoreが担います。
      const deadline = AbortSignal.timeout(90_000);
      let state = await tx.read();
      if (state?.status === "pending") {
        const remaining = Math.floor((state.authenticationExpiresAt ?? 0) - Date.now() / 1000);
        if (remaining > 0 && state.authentication) return { kind: "login", loginUrl: state.authentication.loginUrl, expiresIn: remaining };
        // 認証取引は短時間で失効します。期限切れのURLを再利用せず、新しい業務要求から案内を取り直します。
        state = await this.clear(tx, state);
      }
      if (state?.status === "complete") state = await this.commitForBusiness(tx, state, deadline);
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const response = await preserveBusinessResponse(await this.send(path, state?.status === "complete" ? state.current : undefined, undefined, "GET", deadline));
        if (response.status === 409) {
          const value = await readJson(response.clone());
          const code = record(value.error).code;
          if (code === "ConnectionRequired") {
            const auth = this.authentication(value.authentication);
            const next: PrivateState = { revision: (state?.revision ?? 0) + 1, status: "pending", current: auth.provisionalConnectionSession,
              sessionId: sessionId(auth.provisionalConnectionSession), generation: 0, authentication: auth,
              authenticationExpiresAt: Date.now() / 1000 + auth.expiresIn };
            await tx.save(state?.revision ?? 0, next);
            return { kind: "login", loginUrl: auth.loginUrl, expiresIn: auth.expiresIn };
          }
          if (code === "SessionUpdateRequired" && state?.status === "complete" && attempt === 0) {
            state = await this.save(tx, state, { ...state, pending: this.rotation(value.rotation, state) });
            state = await this.commitForBusiness(tx, state, deadline);
            continue; // 業務処理前の拒否だけを一度再試行します。
          }
          // 更新用JSONには秘密が含まれます。再試行上限に達しても業務応答として画面へ返しません。
          if (code === "SessionUpdateRequired") throw new ConnectionSessionError("SessionUpdateRequired", 409);
        }
        let pending = false;
        if (state?.status === "complete") state = await this.businessMetadata(tx, state, response.headers);
        if (state?.status === "complete" && Object.values(ROTATION_HEADERS).some((name) => response.headers.has(name))) {
          const item = Object.fromEntries(Object.entries(ROTATION_HEADERS).map(([key, name]) => {
            const value = response.headers.get(name);
            return [key, ["fromGeneration", "generation", "expiresAt"].includes(key) && value !== null ? Number(value) : value];
          }));
          state = await this.save(tx, state, { ...state, pending: this.rotation(item, state) });
          try { state = await this.commit(tx, state, deadline); } catch { pending = true; }
        }
        const headers = new Headers(response.headers);
        for (const key of [...headers.keys()]) if (key.toLowerCase().startsWith("x-core-connection-")) headers.delete(key);
        return { kind: "response", response: new Response(response.body, { status: response.status, statusText: response.statusText, headers }), sessionUpdatePending: pending };
      }
      throw new ConnectionSessionError("SessionUpdateRequired", 409);
    });
  }

  /** 取引情報は共有保存先から取得し、HTTP引数で他利用者の取引を指定させません。 */
  async poll(): Promise<"pending" | "complete"> {
    return this.options.store.withExclusive(this.binding, async (tx) => {
      const state = await tx.read();
      if (!state || state.status === "signed_out") throw new ConnectionSessionError("NoAuthenticationTransaction");
      if (state.status === "complete") return "complete";
      if ((state.authenticationExpiresAt ?? 0) <= Date.now() / 1000) {
        await this.clear(tx, state);
        throw new ConnectionSessionError("AuthenticationTransactionExpired", 409);
      }
      const auth = state.authentication!;
      const headers = new Headers({ "api-key": this.options.apiKey, "Accept": "application/json", "X-Core-Connection-Poll": auth.pollSecret });
      let response: Response;
      try { response = await (this.options.fetch ?? fetch)(`${this.origin}/auth/connections/status?transactionId=${auth.transactionId}`,
        { headers, redirect: "error", cache: "no-store", signal: AbortSignal.timeout(25000) }); }
      catch { throw new ConnectionSessionError("GatewayUnavailable"); }
      const value = await readJson(response);
      if (!response.ok) {
        const error = value.error && typeof value.error === "object" && !Array.isArray(value.error)
          ? value.error as Record<string, unknown> : undefined;
        if ([409, 410].includes(response.status) && ["ConnectionNotAuthorized", "InvalidTransaction"].includes(String(error?.code))) {
          await this.clear(tx, state);
          throw new ConnectionSessionError("AuthenticationTransactionExpired", 409);
        }
        throw new ConnectionSessionError("AuthenticationStatusFailed", response.status);
      }
      if (value.status === "pending") return "pending";
      const completed = metadata(value);
      if (value.status !== "complete" || completed.sessionId !== state.sessionId
        || String(value.tenantId).toLowerCase() !== this.options.principal.tenantId.toLowerCase()
        || String(value.ownerId).toLowerCase() !== this.options.principal.ownerId.toLowerCase()) {
        // 使い終わった他人の認証取引を再表示しません。CMの接続自体は変更せず、このアプリの証明だけを捨てます。
        await this.clear(tx, state);
        throw new ConnectionSessionError("IdentityMismatch");
      }
      const next: PrivateState = { ...state, status: "complete", generation: completed.generation, metadata: completed };
      delete next.authentication;
      delete next.authenticationExpiresAt;
      await this.save(tx, state, next);
      return "complete";
    });
  }

  /** 有効な本人セッションから対象を決めるため、任意のUPN・本人IDは送信しません。 */
  private async end(operation: "logout" | "logout-all" | "disconnect"): Promise<string> {
    return this.options.store.withExclusive(this.binding, async (tx) => {
      let state = await tx.read();
      if (!state || state.status !== "complete") {
        if (operation !== "logout") throw new ConnectionSessionError("NoActiveConnectionSession");
        if (state) await this.clear(tx, state);
        return "signed_out";
      }
      // 確定応答が消失した後は旧値の猶予が終わっている可能性があります。
      // 保存済み候補で確定結果を回収してから、現在の証明を失効操作へ使います。
      state = await this.commit(tx, state);
      const response = await this.send(`/auth/connections/${operation}`, state.current, undefined, "POST");
      const value = await readJson(response);
      const expected = operation === "logout" ? "signed_out" : operation === "logout-all" ? "signed_out_all" : "disconnected";
      const acceptedPending = operation === "disconnect" && response.status === 202 && value.status === "disconnect_pending";
      if (!(response.status === 200 && value.status === expected) && !acceptedPending) {
        throw new ConnectionSessionError("SessionRevocationFailed", response.status);
      }
      await this.clear(tx, state);
      return String(value.status);
    });
  }

  /** このアプリで利用中のセッションだけを失効させ、CM接続は残します。 */
  async logout(): Promise<void> { await this.end("logout"); }

  /** 本人の全アプリ・購読に属するセッションを失効させます。 */
  async logoutAll(): Promise<void> { await this.end("logout-all"); }

  /** SQL側を先に利用禁止にし、CMの削除待ちも安全な切断として扱います。 */
  async disconnect(): Promise<"disconnected" | "disconnect_pending" | "signed_out"> {
    return await this.end("disconnect") as "disconnected" | "disconnect_pending" | "signed_out";
  }
}
