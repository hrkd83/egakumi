import { z } from "zod";
import { MAX_BUFFERED_BODY_BYTES, MAX_RUNTIME_STRING_CHARACTERS } from "./model-runtime-limits.ts";
import { apiKeyIdSchema } from "./api-keys/contracts.ts";
import type { TextGenerationModel } from "./model-types.ts";

/** APIごとの出力下限を返します。Responsesは上流契約により16未満を受理しません。 */
export function getMinimumTextOutputTokens(model: Pick<TextGenerationModel, "operationId" | "minOutputTokens">): number {
  return model.minOutputTokens ?? (model.operationId === "responses-create" ? 16 : 1);
}

/** 対応する実行環境の単一文字列の技術上限です。モデルの入力枠は提供元が判定します。 */
export const MAX_TEXT_MESSAGE_CHARACTERS = MAX_RUNTIME_STRING_CHARACTERS;
/** 非ストリーム応答を扱うAPIM経路の技術上限です。SSE全体の累積量は制限しません。 */
export const MODEL_MAX_RESPONSE_BYTES = MAX_BUFFERED_BODY_BYTES;
/** 単一SSEイベントはJavaScript文字列の技術上限まで扱います。 */
export const MODEL_MAX_EVENT_CHARACTERS = MAX_RUNTIME_STRING_CHARACTERS;
/** 上流JSONにはAPIMのバッファー可能量を適用し、独自の小容量枠は設けません。 */
export const MODEL_MAX_UPSTREAM_JSON_BYTES = MAX_BUFFERED_BODY_BYTES;
/** テキスト生成画面とサーバーで共通に使う入力制約です。モデルごとの上限はサーバーでも確認します。 */
export const textGenerationInputSchema = z.object({
  modelId: z.string().min(1).max(160),
  operationId: z.enum(["responses-create", "chat-completions"]).optional(),
  message: z.string().min(1).max(MAX_TEXT_MESSAGE_CHARACTERS).refine((value) => value.trim().length > 0),
  maxOutputTokens: z.number().int().positive().safe().nullish(),
  stream: z.boolean(),
}).strict().superRefine((input, context) => {
  if (input.operationId === "responses-create" && input.maxOutputTokens != null && input.maxOutputTokens < 16) {
    context.addIssue({ code: "custom", path: ["maxOutputTokens"], message: "Responses requires at least 16 output tokens" });
  }
});

/** ブラウザーが要求できるモデル名・API形式・文章です。接続先や任意ヘッダーは受け取りません。 */
export type TextGenerationInput = z.infer<typeof textGenerationInputSchema>;

/** 実行時だけ台帳IDを必須にし、モデルへ送る本文やコード例へ認証情報を混ぜません。 */
export const textGenerationExecutionSchema = textGenerationInputSchema.safeExtend({ apiKeyId: apiKeyIdSchema });

/** APIMの応答本文と、表示用に取り出した生成文・使用量です。 */
export type TextGenerationResult = {
  text: string;
  model: string;
  usage?: { inputTokens: number; outputTokens: number; totalTokens: number };
  requestId?: string;
  durationMs: number;
  finishReason?: string;
  rawResponse: Record<string, unknown>;
};

/** 内部の通信内容を露出せず、画面側で説明文を選ぶための失敗種別です。 */
export type ModelGatewayErrorCode =
  | "InvalidRequest" | "AuthenticationRequired" | "RequestNotAllowed"
  | "ModelUnavailable" | "GatewayNotConfigured" | "GatewayUnauthorized"
  | "RateLimitExceeded" | "OutputLimitReached" | "GatewayTimeout" | "GenerationFailed"
  | "AuthenticationUnavailable" | "RequestCancelled"
  | "ApiKeyUnavailable" | "ApiKeyAccessUnavailable";

/** 生成失敗時の公開応答です。上流の任意のエラー本文は含めません。 */
export type TextGenerationFailure = {
  error: { code: ModelGatewayErrorCode };
  requestId?: string;
  retryAfterSeconds?: number;
};
