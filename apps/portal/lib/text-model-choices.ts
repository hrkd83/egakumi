import type { TextGenerationModel } from "./model-types.ts";
import type { TextGenerationInput } from "./model-request.ts";

/** 同じ実モデルで使えるAPI方式をまとめます。表示名が一致するだけの別モデルは統合しません。 */
export type TextModelChoice = {
  id: string;
  label: string;
  providerId: TextGenerationModel["providerId"];
  variants: [TextGenerationModel, ...TextGenerationModel[]];
};

/** 提供元と実モデルIDでまとめ、API方式の数をモデル数として数えないようにします。 */
export function groupTextModels(models: readonly TextGenerationModel[]): TextModelChoice[] {
  const choices = new Map<string, TextModelChoice>();
  for (const model of models) {
    const key = JSON.stringify([model.providerId, model.id]);
    const current = choices.get(key);
    if (current) {
      if (!current.variants.some((variant) => variant.operationId === model.operationId)) current.variants.push(model);
    } else choices.set(key, { id: model.id, label: model.label, providerId: model.providerId, variants: [model] });
  }
  return [...choices.values()];
}

/** 選択済みの方式を優先し、未選択・非対応ならOpenAIはResponses、それ以外は提供中の方式を選びます。 */
export function chooseTextModelVariant(choice: TextModelChoice, preferred?: TextGenerationModel["operationId"]): TextGenerationModel {
  return choice.variants.find((model) => model.operationId === preferred)
    ?? (choice.providerId === "openai" ? choice.variants.find((model) => model.operationId === "responses-create") : undefined)
    ?? choice.variants.find((model) => model.operationId === "chat-completions")
    ?? choice.variants[0];
}

/** 保存済みのモデル・方式を現在のカタログへ解決します。廃止された組合せは対応中の方式へ戻します。 */
export function resolveTextModelVariant(models: readonly TextGenerationModel[], input: Pick<TextGenerationInput, "modelId" | "operationId">): TextGenerationModel | undefined {
  const choices = groupTextModels(models);
  const choice = choices.find((item) => item.id === input.modelId) ?? choices[0];
  return choice ? chooseTextModelVariant(choice, choice.id === input.modelId ? input.operationId : undefined) : undefined;
}

/** 比較用の文章とStreamは保持し、モデル・API方式を選び直した際は出力上限をその最大値へ設定します。 */
export function updateTextModelSelection(input: TextGenerationInput, next: TextGenerationModel): TextGenerationInput {
  return { ...input, modelId: next.id, operationId: next.operationId,
    maxOutputTokens: next.maxOutputTokens ?? undefined,
  };
}
