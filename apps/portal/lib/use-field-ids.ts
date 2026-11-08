"use client";

import { useId } from "react";

/** 非表示で保持された別の画面とも衝突しない、label・入力・説明用のIDを作ります。 */
export function useFieldIds(): (name: string) => string {
  const instanceId = useId();
  return (name) => `${instanceId}-${name}`;
}
