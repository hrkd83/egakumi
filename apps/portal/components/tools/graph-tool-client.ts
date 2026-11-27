import { graphToolCompleteSchema, graphToolErrorSchema, type GraphToolComplete, type GraphToolFailure } from "../../lib/graph-tool-contracts.ts";
import type { TryCredential } from "../../lib/api-auth/try-credential.ts";

/** 画面に公開できる、固定コードのGraph試用エラーです。 */
export class GraphToolResponseError extends Error {
  readonly failure: GraphToolFailure;
  constructor(failure: GraphToolFailure) { super(failure.error.code); this.name = "GraphToolResponseError"; this.failure = failure; }
}

/** Graphの試用は、資格情報の実値を送らず、同じPortalのBFFを呼び出します。 */
export async function executeGraphTool(credential: TryCredential, signal: AbortSignal): Promise<GraphToolComplete> {
  let response: Response;
  try {
    response = await fetch("/api/tools/graph/profile", {
      method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(credential), signal: AbortSignal.any([signal, AbortSignal.timeout(45_000)]),
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new GraphToolResponseError({ error: { code: "GatewayUnavailable" } });
  }
  const raw: unknown = await response.json().catch(() => null);
  if (response.status === 200) {
    const parsed = graphToolCompleteSchema.safeParse(raw);
    if (parsed.success) return parsed.data;
  }
  const parsed = graphToolErrorSchema.safeParse(raw);
  throw new GraphToolResponseError(parsed.success ? parsed.data : { error: { code: response.status === 401 ? "AuthenticationRequired" : "GatewayResponseInvalid" } });
}
