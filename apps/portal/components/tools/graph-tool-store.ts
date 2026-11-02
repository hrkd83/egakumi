import { createStore } from "zustand/vanilla";
import type { GraphToolComplete, GraphToolFailure, GraphToolResult } from "../../lib/graph-tool-contracts.ts";

/** 秘密情報を持たない、Graph確認パネルの表示状態です。 */
export type GraphToolViewState = {
  apiKeyId: string;
  phase: "idle" | "running" | "waiting" | "complete" | "expired" | "error";
  revision: number;
  loginUrl: string | null;
  deadline: number | null;
  result: GraphToolComplete | null;
  failure: GraphToolFailure | null;
  pollFailed: boolean;
  tab: "response" | "guide" | "schema";
  language: "curl" | "nextjs" | "fastapi" | "php" | "dify";
  selectKey: (id: string) => void;
  begin: () => number;
  receive: (revision: number, result: GraphToolResult, now: number) => void;
  fail: (revision: number, failure: GraphToolFailure) => void;
  expire: (revision: number) => void;
  markPollFailed: (revision: number, failed: boolean) => void;
  setTab: (tab: GraphToolViewState["tab"]) => void;
  setLanguage: (language: GraphToolViewState["language"]) => void;
};

/** キー変更・再実行・期限切れの後に届く旧認証の完了で、別の要求を再開させません。 */
export function canResumeGraphTool(state: Pick<GraphToolViewState, "phase" | "revision" | "apiKeyId">, revision: number, apiKeyId: string): boolean {
  return state.phase === "waiting" && state.revision === revision && state.apiKeyId === apiKeyId;
}

/**
 * パネルごとの表示ストアを作り、購読変更後の遅い応答を版数で無視します。
 * @remarks 永続化せず、キー・pollSecret・本人接続セッションは保持しません。
 */
export function createGraphToolStore() {
  return createStore<GraphToolViewState>((set, get) => ({
    apiKeyId: "", phase: "idle", revision: 0, loginUrl: null, deadline: null, result: null, failure: null, pollFailed: false,
    tab: "response", language: "curl",
    selectKey: (apiKeyId) => set((state) => ({ apiKeyId, revision: state.revision + 1, phase: "idle", loginUrl: null, deadline: null, result: null, failure: null, pollFailed: false })),
    begin: () => {
      const revision = get().revision + 1;
      set({ revision, phase: "running", loginUrl: null, deadline: null, result: null, failure: null, pollFailed: false, tab: "response" });
      return revision;
    },
    receive: (revision, result, now) => {
      if (get().revision !== revision) return;
      if (result.status === "need_login") set({ phase: "waiting", loginUrl: result.loginUrl, deadline: now + result.expiresIn * 1_000, result: null, failure: null });
      else set({ phase: "complete", result, failure: null, loginUrl: null, deadline: null });
    },
    fail: (revision, failure) => { if (get().revision === revision) set({ phase: "error", failure, loginUrl: null, deadline: null }); },
    expire: (revision) => { if (get().revision === revision) set({ phase: "expired", loginUrl: null, deadline: null, pollFailed: false }); },
    markPollFailed: (revision, pollFailed) => { if (get().revision === revision) set({ pollFailed }); },
    setTab: (tab) => set({ tab }), setLanguage: (language) => set({ language }),
  }));
}
