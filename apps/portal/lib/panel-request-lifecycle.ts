/** Activityで非表示になる場合も要求を解放し、保持された画面を待機状態に戻します。 */
export function deactivatePanelRequest(reference: { current: AbortController | null }, reset: (hadPendingRequest: boolean) => void): void {
  const controller = reference.current;
  reference.current = null;
  controller?.abort();
  reset(controller !== null);
}
