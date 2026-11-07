import { z } from "zod";
import { apiKeyIdSchema } from "./api-keys/contracts.ts";
import type { DecisionAnswer, DecisionInput, DecisionJson, DecisionModel, DecisionResult } from "./decision-types.ts";

/**
 * JSONの辞書を検証し、予約名を含む各キーをownデータプロパティとして保存します。
 * @remarks プロトタイプ設定用の代入を使わず、受理した名前を黙って除去しません。
 */
function ownDataRecord<T>(valueSchema: z.ZodType<T>, keySchema: z.ZodType<string> = z.string()) {
  return z.unknown().transform((input, context): Record<string, T> => {
    if (input === null || typeof input !== "object" || Array.isArray(input) || Object.getPrototypeOf(input) !== Object.prototype) {
      context.addIssue({ code: "custom", message: "Expected a JSON object" });
      return z.NEVER;
    }
    const output: Record<string, T> = {};
    for (const [key, value] of Object.entries(input)) {
      const name = keySchema.safeParse(key);
      const parsed = valueSchema.safeParse(value);
      if (!name.success || !parsed.success) {
        context.addIssue({ code: "custom", message: "Invalid dictionary entry", path: [key] });
        continue;
      }
      Object.defineProperty(output, key, { value: parsed.data, enumerable: true, configurable: true, writable: true });
    }
    return output;
  });
}

/** 入力JSONの深さを制限し、再帰の深い値やJSONでない値を検証時に拒否します。 */
function isJson(value: unknown): value is DecisionJson {
  const pending: { value: unknown; depth: number }[] = [{ value, depth: 0 }];
  while (pending.length) {
    const entry = pending.pop()!;
    if (entry.depth > 32) return false;
    const current = entry.value;
    if (current === null || typeof current === "string" || typeof current === "boolean") continue;
    if (typeof current === "number") { if (!Number.isFinite(current)) return false; continue; }
    if (typeof current !== "object" || Object.getPrototypeOf(current) !== Object.prototype && !Array.isArray(current)) return false;
    for (const nested of Object.values(current)) pending.push({ value: nested, depth: entry.depth + 1 });
  }
  return true;
}

const nonempty = z.string().min(1).refine((value) => value.trim().length > 0);
const names = z.string().min(1);
const question = z.discriminatedUnion("type", [
  z.object({ type: z.literal("noul"), instructions: nonempty }).strict(),
  z.object({ type: z.literal("choice"), instructions: nonempty, criteria: ownDataRecord(nonempty, names).refine((value) => Object.keys(value).length >= 2) }).strict(),
  z.object({ type: z.literal("score"), instructions: nonempty, criteria: z.array(nonempty).min(2) }).strict(),
]);

/** 実行先や任意ヘッダーは入力に含めず、モデル名と型付き質問だけを受け取ります。 */
export const decisionInputSchema = z.object({
  modelId: z.string().min(1).max(160),
  state: ownDataRecord(z.unknown()).refine(isJson).transform((value) => value as Record<string, DecisionJson>),
  questions: ownDataRecord(question, names).refine((value) => Object.keys(value).length > 0),
}).strict();

/** 実行要求にだけ台帳IDを付け、コード例に購読情報が混ざることを防ぎます。 */
export const decisionExecutionSchema = decisionInputSchema.extend({ apiKeyId: apiKeyIdSchema });

const probability = z.number().finite().min(0).max(1);
const summarySchema = z.object({
  name: z.string().min(1), type: z.enum(["noul", "choice", "score"]), value: z.union([z.string(), z.number().finite()]),
  probabilities: ownDataRecord(probability).optional(), confidence: probability.optional(),
});

/** サーバーからブラウザーへ返す結果の形です。実データの意味は元の質問とも照合します。 */
export const decisionResultSchema = z.object({
  model: z.string().min(1), answers: z.array(summarySchema).min(1), durationMs: z.number().finite().nonnegative(),
  requestId: z.string().regex(/^[a-zA-Z0-9_-]{1,128}$/).optional(),
  usage: z.object({ inputTokens: z.number().int().nonnegative().safe().optional(), outputTokens: z.number().int().nonnegative().safe().optional(), totalTokens: z.number().int().nonnegative().safe().optional() }).optional(),
  rawResponse: ownDataRecord(z.unknown()),
}).strict();

/** オブジェクトかを確認し、配列・nullを回答の辞書として読みません。 */
function record(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value); }

/** 確率の選択肢が要求と一致し、合計が1であることを確認します。 */
function readProbabilities(value: unknown, expected: string[]): Record<string, number> {
  const parsed = ownDataRecord(probability).parse(value);
  const keys = Object.keys(parsed);
  if (keys.length !== expected.length || expected.some((key) => !Object.hasOwn(parsed, key)) || Math.abs(Object.values(parsed).reduce((sum, item) => sum + item, 0) - 1) > 0.0001) throw new Error("Invalid decision distribution");
  return parsed;
}

/**
 * 回答を元の質問と突き合わせ、型・確率・選択肢・段階評価の整合を確認します。
 * @remarks 配備名decision-1と基礎モデル名Microsoft-Decision-1は別です。未回答や別モデルの成功応答は採用しません。
 */
export function parseDecisionResult(value: unknown, model: DecisionModel, input: DecisionInput, durationMs: number, requestId?: string): DecisionResult {
  if (!record(value) || value.error != null || typeof value.model !== "string" || value.model.toLowerCase() !== model.responseModel.toLowerCase() || !record(value.answers)) throw new Error("Invalid decision response");
  const returned = value.answers;
  const names = Object.keys(input.questions);
  if (Object.keys(returned).length !== names.length || names.some((name) => !Object.hasOwn(returned, name))) throw new Error("Unexpected decision answers");
  const answers: DecisionAnswer[] = names.map((name) => {
    const expected = input.questions[name];
    const answer = returned[name];
    if (!record(answer) || answer.type !== expected.type) throw new Error("Unexpected decision type");
    const confidence = answer.confidence === undefined ? undefined : probability.parse(answer.confidence);
    const common = { name, type: expected.type, ...(confidence === undefined ? {} : { confidence }) };
    if (expected.type === "noul") return { ...common, value: probability.parse(answer.noul) };
    const keys = expected.type === "choice" ? Object.keys(expected.criteria) : expected.criteria.map((_entry, index) => String(index));
    const probabilities = readProbabilities(answer.probabilities, keys);
    if (expected.type === "choice") {
      if (typeof answer.choice !== "string" || !Object.hasOwn(probabilities, answer.choice) || probabilities[answer.choice] !== Math.max(...Object.values(probabilities))) throw new Error("Invalid selected decision");
      return { ...common, value: answer.choice, probabilities };
    }
    const score = z.number().finite().min(0).max(keys.length - 1).parse(answer.score);
    const weighted = keys.reduce((sum, key) => sum + Number(key) * probabilities[key], 0);
    if (Math.abs(score - weighted) > 0.0001) throw new Error("Invalid weighted score");
    return { ...common, value: score, probabilities };
  });
  const usage: NonNullable<DecisionResult["usage"]> = {};
  if (value.usage !== undefined) {
    if (!record(value.usage)) throw new Error("Invalid decision usage");
    for (const [source, target] of [["input_tokens", "inputTokens"], ["output_tokens", "outputTokens"], ["total_tokens", "totalTokens"]] as const) {
      if (value.usage[source] !== undefined) usage[target] = z.number().int().nonnegative().safe().parse(value.usage[source]);
    }
  }
  return decisionResultSchema.parse({ model: value.model, answers, durationMs, rawResponse: value, ...(requestId ? { requestId } : {}), ...(Object.keys(usage).length ? { usage } : {}) });
}
