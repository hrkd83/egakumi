import { createStore } from "zustand/vanilla";
import type { GraphToolComplete, GraphToolFailure } from "../../lib/graph-tool-contracts.ts";

/** 秘密を保持しない、Graph確認パネルの表示状態です。 */
export type GraphToolViewState = {
  phase: "idle" | "running" | "complete" | "error";
  result: GraphToolComplete | null;
  failure: GraphToolFailure | null;
  language: "curl" | "nextjs" | "fastapi" | "php" | "dify";
};

type GraphToolActions = {
  reset: () => void;
  begin: () => void;
  receive: (result: GraphToolComplete) => void;
  fail: (failure: GraphToolFailure) => void;
  setLanguage: (language: GraphToolViewState["language"]) => void;
};

/** Graph試用の結果をパネル内に保持し、接続を解除すると表示から消します。 */
export function createGraphToolStore() {
  return createStore<GraphToolViewState & GraphToolActions>((set) => ({
    phase: "idle", result: null, failure: null, language: "curl",
    reset: () => set({ phase: "idle", result: null, failure: null }),
    begin: () => set({ phase: "running", result: null, failure: null }),
    receive: (result) => set({ phase: "complete", result, failure: null }),
    fail: (failure) => set({ phase: "error", result: null, failure }),
    setLanguage: (language) => set({ language }),
  }));
}
