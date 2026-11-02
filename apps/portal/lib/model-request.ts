import { z } from "zod";
import { apiKeyIdSchema } from "./api-keys/contracts.ts";
import type { TextGenerationModel } from "./model-types.ts";

/** APIごとの出力下限を返します。Responsesは上流契約により16未満を受理しません。 */
export function getMinimumTextOutputTokens(model: Pick<TextGenerationModel, "operationId" | "minOutputTokens">): number {
  return model.minOutputTokens ?? (model.operationId === "responses-create" ? 16 : 1);
}

/** 入力欄の文字数上限です。実際のAPI本文には別途UTF-8バイト数の上限を適用します。 */
export const MAX_TEXT_MESSAGE_CHARACTERS = 32_000;
/** モデルのトークン上限とは別に、Portalが保持・中継する応答量を制限します。 */
export const MODEL_MAX_RESPONSE_BYTES = 32 * 1_048_576;
/** Responses APIの最終イベントには完成文全体が含まれるため、単一イベントにも十分な枠を設けます。 */
export const MODEL_MAX_EVENT_BYTES = 16 * 1_048_576;
/** 細かいトークン断片も扱いながら、無限イベント列を停止するための上限です。 */
export const MODEL_MAX_STREAM_EVENTS = 1_000_000;
/** 長い推論・出力を許容するPortal側の期限です。経路や提供元の期限を延長するものではありません。 */
export const MODEL_REQUEST_TIMEOUT_MS = 600_000;
/** JSON応答はrawResponseと表示文を併記するため、上流本文には全体枠の半分を割り当てます。 */
export const MODEL_MAX_UPSTREAM_JSON_BYTES = MODEL_MAX_RESPONSE_BYTES / 2 - 16_384;
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
  | "RateLimitExceeded" | "GatewayTimeout" | "GenerationFailed"
  | "AuthenticationUnavailable" | "RequestCancelled"
  | "ApiKeyUnavailable" | "ApiKeyAccessUnavailable";

/** 生成失敗時の公開応答です。上流の任意のエラー本文は含めません。 */
export type TextGenerationFailure = {
  error: { code: ModelGatewayErrorCode };
  requestId?: string;
  retryAfterSeconds?: number;
};
