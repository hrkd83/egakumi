import { createStore } from "zustand/vanilla";
import type { ModelCategoryId } from "./model-types.ts";
import type { TextGenerationInput, TextGenerationResult, TextGenerationFailure } from "./model-request.ts";
import { SAMPLE_TEXT_PROMPT, type IntegrationLanguage } from "./model-integration.ts";
import type { ModelSelection } from "./model-selection.ts";
import type { ToolProtocol } from "./catalog.ts";

/** 提供元を切り替えて比較する入力だけです。接続設定・キー・実行結果は含めません。 */
export type ModelDrafts = { embeddingTexts: string[] | null; imagePrompt: string | null; speechText: string | null; speechFile: File | null };
/** カタログの表示状態と、ページを開いている間だけ共有する入力・結果です。 */
export type PortalState = {
  query: string; category: string; activeItemId: string | null; mobileNavOpen: boolean;
  modelCategory: ModelCategoryId; activeProviderId: string | null;
  toolProtocol: ToolProtocol;
  modelDrafts: ModelDrafts;
  modelSelections: Record<string, ModelSelection>;
  textInput: TextGenerationInput; modelApiKeyId: string;
  integrationLanguage: IntegrationLanguage | "json"; integrationTab: "request" | "schema" | "response";
  generationStatus: "idle" | "loading" | "complete" | "error"; generationResult: TextGenerationResult | null; generationFailure: TextGenerationFailure | null;
  setQuery(query: string): void; setCategory(category: string): void; setActiveItem(id: string | null): void; setMobileNavOpen(open: boolean): void;
  setModelCategory(category: ModelCategoryId): void; setActiveProvider(id: string | null): void; updateModelDrafts(drafts: Partial<ModelDrafts>): void;
  setToolProtocol(protocol: ToolProtocol): void;
  rememberModelSelection(scope: string, selection: ModelSelection): void;
  updateTextInput(input: Partial<TextGenerationInput>): void; setModelApiKeyId(id: string): void;
  setIntegrationLanguage(language: IntegrationLanguage | "json"): void; setIntegrationTab(tab: "request" | "schema" | "response"): void;
  beginGeneration(): void; updateGeneration(result: TextGenerationResult): void; completeGeneration(result: TextGenerationResult): void;
  failGeneration(failure: TextGenerationFailure): void; cancelGeneration(): void; resetFilters(): void;
};

/** ページごとに独立したメモリー状態を作り、ファイルや入力を永続化・自動送信しません。 */
export function createPortalStore() {
  return createStore<PortalState>((set) => ({
    query: "", category: "all", activeItemId: null, mobileNavOpen: false, modelCategory: "text", activeProviderId: null,
    toolProtocol: "rest",
    modelDrafts: { embeddingTexts: null, imagePrompt: null, speechText: null, speechFile: null },
    modelSelections: {},
    // 出力上限はカードを開く際にカタログのモデル最大値で初期化し、ストアへ固定値を重複定義しません。
    textInput: { modelId: "", operationId: "responses-create", stream: false, message: SAMPLE_TEXT_PROMPT },
    modelApiKeyId: "", integrationLanguage: "json", integrationTab: "request", generationStatus: "idle", generationResult: null, generationFailure: null,
    setQuery: (query) => set({ query }), setCategory: (category) => set({ category }), setActiveItem: (activeItemId) => set({ activeItemId }), setMobileNavOpen: (mobileNavOpen) => set({ mobileNavOpen }),
    setModelCategory: (modelCategory) => set({ modelCategory, activeProviderId: null }), setActiveProvider: (activeProviderId) => set({ activeProviderId }),
    setToolProtocol: (toolProtocol) => set({ toolProtocol }),
    rememberModelSelection: (scope, selection) => set((state) => ({ modelSelections: { ...state.modelSelections, [scope]: { ...selection } } })),
    updateModelDrafts: (drafts) => set((state) => ({ modelDrafts: { ...state.modelDrafts, ...drafts,
      ...(drafts.embeddingTexts !== undefined ? { embeddingTexts: drafts.embeddingTexts === null ? null : [...drafts.embeddingTexts] } : {}),
    } })),
    setModelApiKeyId: (modelApiKeyId) => set({ modelApiKeyId, generationResult: null, generationFailure: null, generationStatus: "idle" }),
    updateTextInput: (input) => set((state) => ({ textInput: { ...state.textInput, ...input },
      ...(Object.entries(input).some(([key, value]) => state.textInput[key as keyof TextGenerationInput] !== value)
        ? { generationResult: null, generationFailure: null, generationStatus: "idle" as const } : {}),
    })),
    setIntegrationLanguage: (integrationLanguage) => set({ integrationLanguage }), setIntegrationTab: (integrationTab) => set({ integrationTab }),
    beginGeneration: () => set({ generationStatus: "loading", generationResult: null, generationFailure: null }),
    updateGeneration: (generationResult) => set({ generationResult }), completeGeneration: (generationResult) => set({ generationStatus: "complete", generationResult, generationFailure: null }),
    failGeneration: (generationFailure) => set({ generationStatus: "error", generationFailure }), cancelGeneration: () => set({ generationStatus: "idle", generationFailure: null }),
    resetFilters: () => set({ query: "", category: "all", activeItemId: null, activeProviderId: null, mobileNavOpen: false }),
  }));
}
