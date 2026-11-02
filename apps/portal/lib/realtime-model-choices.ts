import type { RealtimeModel } from "./model-types.ts";
import type { RealtimeInput } from "./realtime-request.ts";
import { defaultRealtimeInput } from "./realtime-integration.ts";
import { getRealtimeVoiceOptions } from "./realtime-voices.ts";

/** 同じモデルの接続方式をまとめた選択肢です。送信にはvariants内の実API定義を使います。 */
export type RealtimeModelChoice = {
  id: string;
  family: RealtimeModel["family"];
  modelId: string;
  variants: [RealtimeModel, ...RealtimeModel[]];
};

/** 表示名に依存せず、提供元・API種別・モデル識別子が一致するものだけをまとめます。 */
export function groupRealtimeModels(models: readonly RealtimeModel[]): RealtimeModelChoice[] {
  const choices = new Map<string, RealtimeModelChoice>();
  for (const model of models) {
    const id = JSON.stringify([model.providerId, model.family, model.modelId]);
    const existing = choices.get(id);
    if (existing) existing.variants.push(model);
    else choices.set(id, { id, family: model.family, modelId: model.modelId, variants: [model] });
  }
  return [...choices.values()];
}

/** モデル変更時も現在の方式を優先し、非対応ならそのモデルが提供する方式へ切り替えます。 */
export function chooseRealtimeVariant(choice: RealtimeModelChoice, preferred: RealtimeModel["transport"]): RealtimeModel {
  return choice.variants.find((model) => model.transport === preferred) ?? choice.variants[0];
}

/** モデル・方式を変えたときだけ最大値に戻し、同じ選択内で編集した値は保持します。 */
export function updateRealtimeModelSelection(current: { input: RealtimeInput; apiKeyId: string }, previous: RealtimeModel | undefined, next: RealtimeModel): { input: RealtimeInput; apiKeyId: string } {
  const nextInput = defaultRealtimeInput(next);
  if (current.input.voice && getRealtimeVoiceOptions(next).some((voice) => voice.id === current.input.voice)) nextInput.voice = current.input.voice;
  return {
    input: previous?.id === next.id && current.input.modelId === next.id ? current.input : nextInput,
    apiKeyId: previous?.apiId === next.apiId ? current.apiKeyId : "",
  };
}
