/** URL と辞書で共通して利用する対応言語の一覧です。 */
export const locales = ["ja", "en"] as const;

/** ポータルが提供する言語を表します。 */
export type Locale = (typeof locales)[number];

/** 言語を判定できないアクセスには日本語を表示します。 */
export const defaultLocale: Locale = "ja";

/**
 * URL などから取得した値が対応言語かを確認します。
 * @param locale 検証する言語コード。
 * @returns 対応言語の場合は true を返し、Locale 型に絞り込みます。
 */
export function hasLocale(locale: string): locale is Locale {
  return locales.some((supportedLocale) => supportedLocale === locale);
}
