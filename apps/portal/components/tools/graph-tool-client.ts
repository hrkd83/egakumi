import {
  graphToolCompleteSchema, graphToolErrorSchema, graphToolKeysSchema, graphToolLoginSchema, graphToolStatusSchema,
  type GraphToolFailure, type GraphToolKey, type GraphToolResult,
} from "../../lib/graph-tool-contracts.ts";

/** HTTPや通信の生の詳細を画面へ持ち出さない、公開エラーです。 */
export class GraphToolResponseError extends Error {
  readonly failure: GraphToolFailure;
  constructor(failure: GraphToolFailure) { super(failure.error.code); this.name = "GraphToolResponseError"; this.failure = failure; }
}

/** 秘密値を追加せず、Portal自身の限定APIへだけ通信します。 */
async function request(path: "keys" | "profile" | "connection", signal: AbortSignal, body?: object): Promise<Response> {
  try {
    return await fetch(`/api/tools/graph/${path}`, {
      method: body ? "POST" : "GET", credentials: "same-origin", cache: "no-store",
      ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
      signal: AbortSignal.any([signal, AbortSignal.timeout(45_000)]),
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new GraphToolResponseError({ error: { code: path === "keys" ? "ApiKeyAccessUnavailable" : "GatewayUnavailable" } });
  }
}

/** 応答の失敗を既知のコードへ限定し、上流本文や例外を表示しません。 */
async function failure(response: Response): Promise<never> {
  const parsed = graphToolErrorSchema.safeParse(await response.json().catch(() => null));
  throw new GraphToolResponseError(parsed.success ? parsed.data : { error: { code: response.status === 401 ? "AuthenticationRequired" : "GatewayResponseInvalid" } });
}

/** 利用できるキーの台帳情報だけを取得します。APIキーの実値は読みません。 */
export async function loadGraphToolKeys(signal: AbortSignal): Promise<GraphToolKey[]> {
  const response = await request("keys", signal);
  if (!response.ok) return failure(response);
  const parsed = graphToolKeysSchema.safeParse(await response.json().catch(() => null));
  if (!parsed.success) throw new GraphToolResponseError({ error: { code: "GatewayResponseInvalid" } });
  return parsed.data.keys;
}

/** 表示する認証URLを、設定済みGatewayの固定認証画面へ限定します。 */
export function isGraphToolLoginUrl(value: string, gatewayOrigin: string | null): boolean {
  if (!gatewayOrigin) return false;
  try {
    const target = new URL(value), expected = new URL(gatewayOrigin);
    return target.protocol === "https:" && target.origin === expected.origin && target.pathname === "/auth/connections/browser/launch"
      && !target.username && !target.password && !target.search && Boolean(target.hash);
  } catch { return false; }
}

/** APIを先に実行し、本人接続が必要な場合だけ認証案内として扱います。 */
export async function executeGraphTool(apiKeyId: string, gatewayOrigin: string | null, signal: AbortSignal): Promise<GraphToolResult> {
  const response = await request("profile", signal, { apiKeyId });
  const raw: unknown = await response.json().catch(() => null);
  if (response.status === 200) {
    const parsed = graphToolCompleteSchema.safeParse(raw);
    if (parsed.success) return parsed.data;
  } else if (response.status === 409) {
    const parsed = graphToolLoginSchema.safeParse(raw);
    if (parsed.success && isGraphToolLoginUrl(parsed.data.loginUrl, gatewayOrigin)) return parsed.data;
  }
  const parsed = graphToolErrorSchema.safeParse(raw);
  throw new GraphToolResponseError(parsed.success ? parsed.data : { error: { code: response.status === 401 ? "AuthenticationRequired" : "GatewayResponseInvalid" } });
}

/** 認証済みPortal利用者に紐づく、サーバー保存済み取引の状態だけを確認します。 */
export async function checkGraphToolConnection(apiKeyId: string, signal: AbortSignal): Promise<"pending" | "complete" | "expired"> {
  const response = await request("connection", signal, { apiKeyId, action: "status" });
  if (!response.ok) return failure(response);
  const parsed = graphToolStatusSchema.safeParse(await response.json().catch(() => null));
  if (!parsed.success) throw new GraphToolResponseError({ error: { code: "GatewayResponseInvalid" } });
  return parsed.data.status;
}
