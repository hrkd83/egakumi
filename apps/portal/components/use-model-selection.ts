"use client";

import { usePortalStore } from "@/components/portal-store-provider";
import { modelSelectionScope, resolveInitialModel, type ModelSelection } from "@/lib/model-selection";

/** 提供元または指定したシリーズごとに明示選択を保持し、初回だけカタログの先頭を選びます。 */
export function useModelSelection<T extends { id: string; providerId: string }>(category: string, models: readonly T[], selectionGroupId?: string) {
  const scope = modelSelectionScope(category, selectionGroupId ?? models[0]?.providerId ?? "");
  const savedSelection = usePortalStore((state) => state.modelSelections[scope]);
  const rememberSelection = usePortalStore((state) => state.rememberModelSelection);
  return {
    initialModel: resolveInitialModel(models, savedSelection),
    savedSelection,
    rememberModel: (selection: ModelSelection) => rememberSelection(scope, selection),
  };
}
