import { z } from "zod";
import { readDocumentAnalysisResponse, DocumentAnalysisResponseError, type DocumentAnalysisModel, type DocumentAnalysisResult } from "./document-analysis-contracts.ts";

/** ブラウザーが持つのは本人に束縛された暗号化参照だけで、Gatewayチケットは含みません。 */
export const documentAnalysisPendingSchema = z.object({ operationId: z.uuid(), model: z.enum(["prebuilt-read", "prebuilt-layout"]), status: z.literal("running"), handle: z.string().regex(/^[A-Za-z0-9_-]{40,16000}$/), expiresAt: z.iso.datetime({ offset: true }), retryAfterSeconds: z.number().int().positive().max(86_400), requestId: z.string().optional() }).strict();

/** Portalが保持する本人専用の受付参照と、次回の状態確認間隔です。 */
export type DocumentAnalysisPending = z.infer<typeof documentAnalysisPendingSchema>;

/** 利用者が待機を停止したとき、次の照会までの待機もすぐに停止します。 */
async function pause(milliseconds: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  await new Promise<void>((resolve, reject) => {
    const cancel = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", cancel); resolve(); }, milliseconds);
    signal.addEventListener("abort", cancel, { once: true });
  });
}

/** 受付後は同一オリジンのBFFへ状態を問い合わせ、元のファイルを再送せず完了を待ちます。 */
export async function waitForDocumentAnalysis(response: Response, model: DocumentAnalysisModel, apiKeyId: string, signal: AbortSignal, dependencies: { fetch?: typeof globalThis.fetch; wait?: typeof pause; onPending?: (operation: DocumentAnalysisPending) => void } = {}): Promise<DocumentAnalysisResult> {
  let current = response;
  let operation: DocumentAnalysisPending | undefined;
  while (current.status === 202) {
    operation = documentAnalysisPendingSchema.parse(await current.json());
    dependencies.onPending?.(operation);
    if (operation.model !== model) throw new Error("Unexpected document model");
    signal.throwIfAborted();
    if (Date.parse(operation.expiresAt) <= Date.now()) throw new DocumentAnalysisResponseError({ error: { code: "DocumentOperationExpired" } });
    await (dependencies.wait ?? pause)(Math.min(operation.retryAfterSeconds * 1000, Math.max(0, Date.parse(operation.expiresAt) - Date.now())), signal);
    try {
      current = await (dependencies.fetch ?? globalThis.fetch)("/api/tools/documents/status", { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ apiKeyId, handle: operation.handle }), signal });
      if ([429, 502, 503, 504].includes(current.status)) {
        const seconds = Number(current.headers.get("retry-after"));
        await current.body?.cancel();
        current = Response.json({ ...operation, retryAfterSeconds: Number.isInteger(seconds) && seconds > 0 && seconds <= 86_400 ? seconds : Math.min(60, operation.retryAfterSeconds * 2) }, { status: 202 });
      }
    } catch (error) {
      if (signal.aborted || Date.parse(operation.expiresAt) <= Date.now()) throw error;
      current = Response.json({ ...operation, retryAfterSeconds: Math.min(60, operation.retryAfterSeconds * 2) }, { status: 202 });
    }
  }
  return readDocumentAnalysisResponse(current, model, signal);
}
