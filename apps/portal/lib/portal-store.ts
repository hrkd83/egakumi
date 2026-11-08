import { createStore } from "zustand/vanilla";
import type { ModelCategoryId } from "./model-types.ts";
import type { TextGenerationInput } from "./model-request.ts";
import { SAMPLE_TEXT_PROMPT, type IntegrationLanguage } from "./model-integration.ts";
import type { ModelSelection } from "./model-selection.ts";
import type { ToolProtocol } from "./catalog.ts";
import type { TextModelDraft } from "./text-model-choices.ts";
import { modelSettingsDraft, type ModelSettings, type ScopedModelSettings } from "./model-settings.ts";

/** 提供元を切り替えて比較する入力だけです。接続設定・キー・実行結果は含めません。 */
export type ModelDrafts = { embeddingTexts: string[] | null; imagePrompt: string | null; speechText: string | null; speechFile: File | null };
/** カタログの表示状態と、ページを開いている間だけ共有する入力・結果です。 */
export type PortalState = {
  query: string; category: string; activeItemId: string | null; mobileNavOpen: boolean;
  modelCategory: ModelCategoryId; activeProviderId: string | null;
  toolProtocol: ToolProtocol;
  modelDrafts: ModelDrafts;
  modelSelections: Record<string, ModelSelection>;
  textInput: Pick<TextGenerationInput, "message" | "stream">;
  textModelDrafts: Record<string, TextModelDraft>;
  modelSettings: ScopedModelSettings;
  integrationLanguage: IntegrationLanguage | "json"; integrationTab: "request" | "schema" | "response";
  setQuery(query: string): void; setCategory(category: string): void; setActiveItem(id: string | null): void; setMobileNavOpen(open: boolean): void;
  setModelCategory(category: ModelCategoryId): void; setActiveProvider(id: string | null): void; updateModelDrafts(drafts: Partial<ModelDrafts>): void;
  setToolProtocol(protocol: ToolProtocol): void;
  rememberModelSelection(scope: string, selection: ModelSelection): void;
  updateTextInput(input: Partial<Pick<TextGenerationInput, "message" | "stream">>): void;
  setTextModelDraft(scope: string, draft: TextModelDraft): void;
  setModelSettings<Category extends keyof ModelSettings>(category: Category, scope: string, settings: ModelSettings[Category]): void;
  setIntegrationLanguage(language: IntegrationLanguage | "json"): void; setIntegrationTab(tab: "request" | "schema" | "response"): void;
  resetFilters(): void;
};

/** ページごとに独立したメモリー状態を作り、ファイルや入力を永続化・自動送信しません。 */
export function createPortalStore() {
  return createStore<PortalState>((set) => ({
    query: "", category: "all", activeItemId: null, mobileNavOpen: false, modelCategory: "text", activeProviderId: null,
    toolProtocol: "rest",
    modelDrafts: { embeddingTexts: null, imagePrompt: null, speechText: null, speechFile: null },
    modelSelections: {},
    // 出力上限はカードを開く際にカタログのモデル最大値で初期化し、ストアへ固定値を重複定義しません。
    textInput: { stream: false, message: SAMPLE_TEXT_PROMPT }, textModelDrafts: {},
    modelSettings: { embeddings: {}, image: {}, speech: {}, realtime: {}, rerank: {} },
    integrationLanguage: "json", integrationTab: "request",
    setQuery: (query) => set({ query }), setCategory: (category) => set({ category }), setActiveItem: (activeItemId) => set({ activeItemId }), setMobileNavOpen: (mobileNavOpen) => set({ mobileNavOpen }),
    setModelCategory: (modelCategory) => set({ modelCategory, activeProviderId: null }), setActiveProvider: (activeProviderId) => set({ activeProviderId }),
    setToolProtocol: (toolProtocol) => set({ toolProtocol }),
    rememberModelSelection: (scope, selection) => set((state) => ({ modelSelections: { ...state.modelSelections, [scope]: { ...selection } } })),
    updateModelDrafts: (drafts) => set((state) => ({ modelDrafts: { ...state.modelDrafts, ...drafts,
      ...(drafts.embeddingTexts !== undefined ? { embeddingTexts: drafts.embeddingTexts === null ? null : [...drafts.embeddingTexts] } : {}),
    } })),
    setTextModelDraft: (scope, draft) => set((state) => ({ textModelDrafts: { ...state.textModelDrafts, [scope]: { ...draft } } })),
    setModelSettings: (category, scope, settings) => set((state) => ({ modelSettings: { ...state.modelSettings, [category]: { ...state.modelSettings[category], [scope]: modelSettingsDraft(category, settings) } } })),
    updateTextInput: (input) => set((state) => ({ textInput: { ...state.textInput, ...input } })),
    setIntegrationLanguage: (integrationLanguage) => set({ integrationLanguage }), setIntegrationTab: (integrationTab) => set({ integrationTab }),
    resetFilters: () => set({ query: "", category: "all", activeItemId: null, activeProviderId: null, mobileNavOpen: false }),
  }));
}
