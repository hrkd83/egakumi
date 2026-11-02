import { createStore } from "zustand/vanilla";
import type { ApiKeyOverview } from "@/lib/api-keys/contracts";

/** 秘密情報を含まない、グループ選択・検索・画面開閉の状態です。 */
type ApiKeyDisplayState = {
  overview: ApiKeyOverview | null;
  loading: boolean;
  error: string | null;
  notice: string | null;
  groupId: string;
  query: string;
  stateFilter: "all" | "active" | "suspended";
  selectedId: string | null;
  createOpen: boolean;
  selectGroup: (groupId: string) => void;
  setQuery: (query: string) => void;
  setStateFilter: (filter: "all" | "active" | "suspended") => void;
  selectKey: (id: string | null) => void;
  setCreateOpen: (open: boolean) => void;
};

/** 画面のインスタンスごとに作成し、選択状態を利用者間や永続ストレージで共有しません。 */
export function createApiKeyDisplayStore() {
  return createStore<ApiKeyDisplayState>((set) => ({
    overview: null, loading: true, error: null, notice: null,
    groupId: "", query: "", stateFilter: "all", selectedId: null, createOpen: false,
    selectGroup: (groupId) => set({ groupId, query: "", stateFilter: "all", selectedId: null, createOpen: false }),
    setQuery: (query) => set({ query }),
    setStateFilter: (stateFilter) => set({ stateFilter }),
    selectKey: (selectedId) => set({ selectedId }),
    setCreateOpen: (createOpen) => set({ createOpen }),
  }));
}
