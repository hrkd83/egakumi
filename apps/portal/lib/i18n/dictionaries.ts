import "server-only";

import type { Locale } from "./config";

/** 日本語の辞書を基準とした、全言語で共通の翻訳項目です。 */
export type Dictionary = typeof import("./dictionaries/ja.json");

/** 各言語の辞書を必要になったときにサーバー上で読み込みます。 */
const dictionaries = {
  ja: () => import("./dictionaries/ja.json").then((module) => module.default),
  en: () => import("./dictionaries/en.json").then((module) => module.default),
} satisfies Record<Locale, () => Promise<Dictionary>>;

/**
 * 指定した言語の表示文言を取得します。
 * @param locale 検証済みの対応言語。
 * @returns 共通の翻訳項目を備えた辞書。
 * @remarks 辞書全体をクライアントの JavaScript に含めないため、サーバー専用です。
 */
export async function getDictionary(locale: Locale): Promise<Dictionary> {
  return dictionaries[locale]();
}
