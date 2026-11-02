import type { Locale } from "./i18n/config";

/**
 * 同じ言語の画面ルートだけを許可し、認証後も専用のAPIガイドへ戻れるようにします。
 * @remarks パーセントエンコード、クエリ、フラグメント、相対移動は許可しません。
 * 詳細画面の提供元・サービスIDが実在するかは、その画面のカタログ照合で確認します。
 */
export function isPortalReturnPath(candidate: string | undefined, locale: Locale): candidate is string {
  if (!candidate || candidate.length > 256) return false;
  const allowed = new Set([`/${locale}`, `/${locale}/models`, `/${locale}/tools`, `/${locale}/agents`, `/${locale}/api-keys`, `/${locale}/api-key-administration`, `/${locale}/company-administration`, `/${locale}/subscription-sync`]);
  if (allowed.has(candidate)) return true;
  const prefix = `/${locale}/`;
  if (!candidate.startsWith(prefix)) return false;
  const path = candidate.slice(prefix.length);
  return /^(?:models\/(?:text|embeddings|image|speech|realtime|rerank|decision)|tools\/(?:identity|privacy|document)|agents\/(?:assistant|research|workflow))\/[a-z0-9]+(?:-[a-z0-9]+)*(?![\s\S])/.test(path);
}

/**
 * ログイン後の戻り先を、現在の言語のカタログ内に限定します。
 * @param candidate - URLのクエリから受け取った、信頼しない戻り先。
 * @param locale - 検証済みの表示言語。
 * @returns 外部サイトや認証APIへ転送しない、カタログの相対パス。
 */
export function safeReturnTo(candidate: string | undefined, locale: Locale): string {
  return isPortalReturnPath(candidate, locale) ? candidate : `/${locale}/models`;
}
