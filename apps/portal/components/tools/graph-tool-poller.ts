/** 認証取引の表示期限と、画面が開いている間だけ実行する完了確認の境界です。 */
type PollerOptions = {
  deadline: number;
  poll: (signal: AbortSignal) => Promise<"pending" | "complete" | "expired">;
  visible: () => boolean;
  onComplete: () => void | Promise<void>;
  onExpired: () => void;
  onFailure: (error: unknown) => void;
  now?: () => number;
  schedule?: (callback: () => void) => unknown;
  cancel?: (timer: unknown) => void;
};

/**
 * 手動確認と自動確認を一つにまとめ、実行中の照会を中断・重複させません。
 * @remarks 照会は認証取引の期限内だけです。通常業務APIやセッション延長は実行しません。
 */
export function createGraphToolPoller(options: PollerOptions): { requestNow: () => void; stop: () => void } {
  const controller = new AbortController();
  const now = options.now ?? Date.now;
  const schedule = options.schedule ?? ((callback) => setTimeout(callback, 2_000));
  const cancel = options.cancel ?? ((timer) => clearTimeout(timer as ReturnType<typeof setTimeout>));
  let timer: unknown;
  let inFlight = false;
  let finished = false;

  /** 表示中で未実行のときだけ照会し、結果ごとに次の1回を決めます。 */
  async function run() {
    if (controller.signal.aborted || finished || inFlight || !options.visible()) return;
    if (now() >= options.deadline) { finished = true; options.onExpired(); return; }
    inFlight = true;
    try {
      const status = await options.poll(controller.signal);
      if (controller.signal.aborted) return;
      if (now() >= options.deadline || status === "expired") { finished = true; options.onExpired(); return; }
      if (status === "complete") { finished = true; await options.onComplete(); return; }
      if (options.visible()) timer = schedule(requestNow);
    } catch (error) {
      if (!controller.signal.aborted) options.onFailure(error);
    } finally { inFlight = false; }
  }

  /** 実行済みの要求を中断せず、同じ確認要求へ合流します。 */
  function requestNow() {
    if (timer !== undefined) { cancel(timer); timer = undefined; }
    void run();
  }

  /** パネルを閉じた時だけ照会を中断し、遅い応答を採用しません。 */
  function stop() {
    controller.abort();
    if (timer !== undefined) { cancel(timer); timer = undefined; }
  }

  return { requestNow, stop };
}
