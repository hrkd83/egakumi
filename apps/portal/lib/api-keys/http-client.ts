import "server-only";

import { ApiKeyError } from "./contracts.ts";

/** 外部APIの本文と待機時間を制限する、サーバー専用の通信設定です。 */
export type ApiKeyHttpOptions = {
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
  maximumBytes?: number;
};

/** 上流本文を利用者へ転送せず、各クライアント内で検証する応答です。 */
export type ApiKeyHttpResponse = { status: number; headers: Headers; value: unknown };

/** 本文の読み取り中もタイムアウトとサイズ上限を守ります。 */
async function readBody(response: Response, signal: AbortSignal, maximumBytes: number): Promise<string> {
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let bytes = 0;
  let abortListener: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    abortListener = () => reject(new Error("Upstream timeout"));
    if (signal.aborted) abortListener();
    else signal.addEventListener("abort", abortListener, { once: true });
  });
  try {
    while (true) {
      const part = await Promise.race([reader.read(), aborted]);
      if (part.done) break;
      bytes += part.value.byteLength;
      if (bytes > maximumBytes) throw new Error("Upstream response too large");
      chunks.push(part.value);
    }
    const buffer = new Uint8Array(bytes);
    let offset = 0;
    for (const part of chunks) { buffer.set(part, offset); offset += part.byteLength; }
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } finally {
    if (abortListener) signal.removeEventListener("abort", abortListener);
    void reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

/**
 * 検証済みの接続先にだけJSON通信を行います。リダイレクト先へ認証情報を渡しません。
 * @remarks URLの許可範囲は呼び出すGraph/APIMクライアントが固定します。自動再試行は行いません。
 */
export async function requestApiKeyJson(url: string, init: RequestInit, options: ApiKeyHttpOptions = {}): Promise<ApiKeyHttpResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? 15_000);
  try {
    const response = await (options.fetch ?? globalThis.fetch)(url, {
      ...init, signal: controller.signal, redirect: "error", cache: "no-store",
    });
    const text = await readBody(response, controller.signal, options.maximumBytes ?? 1_048_576);
    // Azureのエラー本文には内部情報が含まれ得るため、成功時にだけ内容を解釈します。
    const value = response.ok && text ? JSON.parse(text) as unknown : null;
    return { status: response.status, headers: response.headers, value };
  } catch {
    throw new ApiKeyError("UpstreamUnavailable", 503);
  } finally {
    clearTimeout(timer);
  }
}
