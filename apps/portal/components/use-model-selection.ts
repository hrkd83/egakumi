"use client";

import { usePortalStore } from "@/components/portal-store-provider";
import { modelSelectionScope, resolveInitialModel, type ModelSelection } from "@/lib/model-selection";

/** 提供元ごとの明示選択をページ間で保持し、初回だけ最新カタログの先頭を選びます。 */
export function useModelSelection<T extends { id: string; providerId: string }>(category: string, models: readonly T[]) {
  const scope = modelSelectionScope(category, models[0]?.providerId ?? "");
  const savedSelection = usePortalStore((state) => state.modelSelections[scope]);
  const rememberSelection = usePortalStore((state) => state.rememberModelSelection);
  return {
    initialModel: resolveInitialModel(models, savedSelection),
    savedSelection,
    rememberModel: (selection: ModelSelection) => rememberSelection(scope, selection),
  };
}
