import { z } from "zod";
import { MAX_RUNTIME_STRING_CHARACTERS } from "./model-runtime-limits.ts";
import { apiKeyIdSchema } from "./api-keys/contracts.ts";

/** 公開済みPII操作の固定契約です。正本API定義との一致は契約テストで確認します。 */
export const PII_API = {
  apiId: "pii-v1", operationId: "pii_redact_text", path: "/tools/pii/v1/redact",
  maxRequestBytes: 1_000_000, modelVersion: "2026-05-01", languages: ["ja", "en"], allowedWarningCodes: [],
} as const;

/** 一度に送信する本文と言語です。接続先・任意ヘッダー・秘密値は入力できません。 */
export const piiInputSchema = z.object({
  text: z.string().min(1).max(PII_API.maxRequestBytes).refine((value) => value.trim().length > 0),
  language: z.string().regex(/^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/),
}).strict();

/** 試行画面とコード例で共有する入力です。 */
export type PiiInput = z.infer<typeof piiInputSchema>;
/** 実行時のみ、本人が選んだSQL台帳の購読IDを受け取ります。 */
export const piiExecutionSchema = piiInputSchema.extend({ apiKeyId: apiKeyIdSchema });

/** Gatewayが公開するマスキング済みの本文と、安全なメタデータだけを許可します。 */
export const piiResponseSchema = z.object({
  redactedText: z.string().max(MAX_RUNTIME_STRING_CHARACTERS), language: z.string().regex(/^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/),
  detectedCount: z.number().int().nonnegative().safe(), modelVersion: z.literal(PII_API.modelVersion),
  warningCodes: z.array(z.string()).max(0), requestId: z.string().uuid(),
}).strict();

/** マスキング結果です。検出した実値や入力の原文は追加しません。 */
export type PiiResponse = z.infer<typeof piiResponseSchema>;
/** Portal内の表示用に、実行時間だけを追加した応答です。 */
export const piiResultSchema = z.object({ result: piiResponseSchema, durationMs: z.number().finite().nonnegative() }).strict();
/** 検証済み結果と実行時間です。 */
export type PiiResult = z.infer<typeof piiResultSchema>;

/** 日本語の架空データを初期入力とします。実在する個人情報の入力を要求しません。 */
export const PII_SAMPLE: PiiInput = { language: "ja", text: "サンプル担当者は山田太郎です。連絡先はtaro.yamada@example.com、電話番号は090-1234-5678です。" };

/** GatewayのJSON.NETと同じエスケープ表現で、値を変えずコンパクトJSONのバイト数を測ります。 */
export function getPiiRequestBytes(input: PiiInput): number {
  // JSON.NETはこの3文字を常にUnicode escapeへ直列化します。送信する文章は変更しません。
  const compact = JSON.stringify(input).replace(/[\u0085\u2028\u2029]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
  return new TextEncoder().encode(compact).byteLength;
}

/** コンパクトJSONのUTF-8予算を、文字列を正規化せず画面とサーバーで共通に検査します。 */
export function buildPiiRequest(input: PiiInput): PiiInput {
  const parsed = piiInputSchema.strip().parse(input);
  if (getPiiRequestBytes(parsed) > PII_API.maxRequestBytes) throw new RangeError("PII request too large");
  return parsed;
}
