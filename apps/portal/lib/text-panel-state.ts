import { createStore } from "zustand/vanilla";
import type { TextGenerationInput, TextGenerationResult, TextGenerationFailure } from "./model-request.ts";

/** 表示する結果と、その実行に使った入力を同じパネルに閉じます。 */
type TextPanelGeneration = {
  generationInput: TextGenerationInput | null;
  generationStatus: "idle" | "loading" | "complete" | "error";
  generationResult: TextGenerationResult | null;
  generationFailure: TextGenerationFailure | null;
  generationCancelled: boolean;
};

/** 数値編集中のNaNも同じ入力として扱い、不要な状態更新を避けます。 */
function sameInput(first: TextGenerationInput | null, second: TextGenerationInput): boolean {
  return first !== null && first.modelId === second.modelId && first.operationId === second.operationId
    && Object.is(first.maxOutputTokens, second.maxOutputTokens) && first.message === second.message && first.stream === second.stream;
}

/** 他の提供元で共有入力が編集されたら、effectを待たず古い結果を表示から外します。 */
export function getTextPanelView(state: TextPanelGeneration, input: TextGenerationInput): Omit<TextPanelGeneration, "generationInput"> {
  return sameInput(state.generationInput, input) ? state : {
    generationStatus: "idle", generationResult: null, generationFailure: null, generationCancelled: false,
  };
}

/** 提供元のパネルに実行状態とキーIDを閉じ、別の提供元の結果を表示しません。 */
export function createTextPanelStore() {
  return createStore<TextPanelGeneration & {
    modelApiKeyId: string;
    setModelApiKeyId(id: string): void;
    reconcileInput(input: TextGenerationInput): void;
    beginGeneration(input: TextGenerationInput): void;
    updateGeneration(result: TextGenerationResult): void;
    completeGeneration(result: TextGenerationResult): void;
    failGeneration(failure: TextGenerationFailure): void;
    cancelGeneration(): void;
    resetGeneration(): void;
  }>((set) => ({
    modelApiKeyId: "", generationInput: null, generationStatus: "idle", generationResult: null, generationFailure: null, generationCancelled: false,
    setModelApiKeyId: (modelApiKeyId) => set({ modelApiKeyId, generationResult: null, generationFailure: null, generationStatus: "idle", generationCancelled: false }),
    reconcileInput: (input) => set((state) => sameInput(state.generationInput, input) ? state : {
      generationInput: { ...input }, generationStatus: "idle", generationResult: null, generationFailure: null, generationCancelled: false,
    }),
    beginGeneration: (input) => set({ generationInput: { ...input }, generationStatus: "loading", generationResult: null, generationFailure: null, generationCancelled: false }),
    updateGeneration: (generationResult) => set({ generationResult }),
    completeGeneration: (generationResult) => set({ generationStatus: "complete", generationResult, generationFailure: null, generationCancelled: false }),
    failGeneration: (generationFailure) => set({ generationStatus: "error", generationFailure, generationCancelled: false }),
    cancelGeneration: () => set({ generationStatus: "idle", generationResult: null, generationFailure: null, generationCancelled: true }),
    resetGeneration: () => set({ generationStatus: "idle", generationResult: null, generationFailure: null, generationCancelled: false }),
  }));
}
