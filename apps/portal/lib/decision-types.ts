/** 公開定義から読み取った意思決定モデルです。応答モデル名と配備名を区別します。 */
export type DecisionModel = {
  id: string;
  label: string;
  providerId: "microsoft";
  provider: "Microsoft";
  apiId: "microsoft-decision-v1";
  operationId: "systemone";
  path: string;
  maxRequestBytes: number;
  responseModel: string;
};

/** 判断に渡すJSON値です。関数や無限大など、JSONにならない値は受け付けません。 */
export type DecisionJson = null | boolean | number | string | DecisionJson[] | { [key: string]: DecisionJson };

/** 判定の目的を明示した質問です。選択と段階評価では、それぞれの選択肢を渡します。 */
export type DecisionQuestion =
  | { type: "noul"; instructions: string }
  | { type: "choice"; instructions: string; criteria: Record<string, string> }
  | { type: "score"; instructions: string; criteria: string[] };

/** 購読の秘密値を含まない、意思決定の入力です。 */
export type DecisionInput = { modelId: string; state: Record<string, DecisionJson>; questions: Record<string, DecisionQuestion> };

/** 元の質問名と対応する、表示用の判定と確率です。 */
export type DecisionAnswer = {
  name: string;
  type: DecisionQuestion["type"];
  value: string | number;
  probabilities?: Record<string, number>;
  confidence?: number;
};

/** 応答を検証した後の表示用データです。生JSONも改変せず併記します。 */
export type DecisionResult = {
  model: string;
  answers: DecisionAnswer[];
  durationMs: number;
  requestId?: string;
  usage?: { inputTokens?: number; outputTokens?: number; totalTokens?: number };
  rawResponse: Record<string, unknown>;
};
