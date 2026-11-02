import { createStore } from "zustand/vanilla";
import type { ApiKeyAdministrationOverview } from "./client.ts";

/** 管理ページの絞り込みと取得結果を、表示インスタンスだけで保持します。 */
export function createAdministrationStore() {
  return createStore<{
    overview: ApiKeyAdministrationOverview | null;
    loading: boolean; refreshing: boolean; loadingMore: boolean; error: string | null;
    query: string; status: "all" | "active" | "deleted" | "ineligible" | "unknown";
  }>(() => ({ overview: null, loading: true, refreshing: false, loadingMore: false, error: null, query: "", status: "all" }));
}
