"use client";

import { createContext, useContext, useState, type ReactNode } from "react";
import { useStore } from "zustand";
import { createPortalStore, type PortalState } from "@/lib/portal-store";
const PortalStoreContext = createContext<ReturnType<typeof createPortalStore> | null>(null);

/**
 * 配下の画面へ、インスタンスごとに独立した表示状態を提供します。
 * @param props - ストアを利用する画面。
 * @returns Zustandストアを共有するProvider。
 */
export function PortalStoreProvider({ children }: { children: ReactNode }) {
  const [store] = useState(createPortalStore);
  return <PortalStoreContext.Provider value={store}>{children}</PortalStoreContext.Provider>;
}

/**
 * 必要な表示状態だけを購読して取得します。
 * @param selector - ストアから利用する値を選ぶ関数。
 * @returns 選択した状態または操作関数。
 */
export function usePortalStore<T>(selector: (state: PortalState) => T): T {
  const store = useContext(PortalStoreContext);
  if (!store) throw new Error("PortalStoreProvider is required");
  return useStore(store, selector);
}
