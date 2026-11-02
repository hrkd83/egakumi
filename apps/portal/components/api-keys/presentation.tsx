import { Badge } from "@/components/ui/badge";
import type { ApiKeyRecord } from "@/lib/api-keys/contracts";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import type { Locale } from "@/lib/i18n/config";

/** 安全に表示できるコード以外は、一般的な障害メッセージへまとめます。 */
export function errorMessage(dictionary: Dictionary, code: string): string {
  const errors = dictionary.apiKeys.errors;
  const aliases: Record<string, keyof typeof errors> = {
    AccessDenied: "Forbidden", ProductNotAllowed: "Forbidden", InvalidInput: "InvalidRequest",
    SubscriptionLimitReached: "LimitReached", KeyNotFound: "NotFound",
    InvalidKeyState: "UpstreamConflict", SubscriptionNotReady: "UpstreamConflict",
    IdempotencyConflict: "UpstreamConflict", UpstreamBusy: "UpstreamUnavailable",
  };
  const key = aliases[code] ?? code as keyof typeof errors;
  return errors[key] ?? errors.ServiceUnavailable;
}

/** 日付の欠損や不正値を考慮して、表示中の言語に合わせます。 */
export function formatKeyDate(value: string | null, locale: Locale, fallback: string): string {
  if (!value) return fallback;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? fallback : new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(date);
}

/** 公開台帳の状態を色と文字の両方で表します。 */
export function KeyState({ state, dictionary }: { state: ApiKeyRecord["state"]; dictionary: Dictionary }) {
  return <Badge variant="outline" className={`gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${state === "active" ? "border-primary/15 bg-primary/5 text-primary" : state === "suspended" ? "border-amber-200 bg-amber-50 text-amber-800" : "text-muted-foreground"}`}>
    <span aria-hidden="true" className={`size-1.5 rounded-full ${state === "active" ? "bg-primary" : state === "suspended" ? "bg-amber-500" : "bg-muted-foreground"}`} />{dictionary.apiKeys.states[state]}
  </Badge>;
}
