import assert from "node:assert/strict";
import { test } from "node:test";
import { deactivatePanelRequest } from "../lib/panel-request-lifecycle.ts";

test("保持された画面が非表示になったら参照を切って中止し、待機状態へ戻す", () => {
  const controller = new AbortController(), reference = { current: controller as AbortController | null };
  const view = { loading: true, cancelled: false }; let calls = 0;
  controller.signal.addEventListener("abort", () => { calls++; assert.equal(reference.current, null); });
  deactivatePanelRequest(reference, (pending) => { view.loading = false; view.cancelled = pending; });
  assert.equal(controller.signal.aborted, true); assert.equal(calls, 1); assert.deepEqual(view, { loading: false, cancelled: true });
  deactivatePanelRequest(reference, () => { view.loading = false; }); assert.equal(calls, 1);
});

test("同時に保持された他の提供元の要求は中止しない", () => {
  const first = { current: new AbortController() as AbortController | null }, second = new AbortController();
  deactivatePanelRequest(first, () => undefined);
  assert.equal(second.signal.aborted, false);
});
