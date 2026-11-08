import type { BetterAuthOptions } from "better-auth";

/** 可変メッセージや例外の詳細を認証ログへ渡さず、固定した事象だけを記録します。 */
const authenticationEvents = new Map([
  ["Failed to parse state", "state-rejected"],
  ["State not found", "state-missing"],
  ["INVALID_CALLBACK_REQUEST", "callback-invalid"],
  ["Failed to create verification", "state-create-failed"],
]);

/** 認証コード・Cookie・token・stateが例外へ含まれても固定の事象だけを出力します。 */
export const portalAuthLogger = {
  level: "warn",
  disableColors: true,
  log(level, message, ..._details: unknown[]) {
    const event = `[portal-auth] ${authenticationEvents.get(message) ?? "library-event"}`;
    switch (level) {
      case "error": console.error(event); break;
      case "warn": console.warn(event); break;
      case "info": console.info(event); break;
      case "debug": console.debug(event); break;
    }
  },
} satisfies NonNullable<BetterAuthOptions["logger"]>;
