import type { Locale } from "./config";

/** 未提供のAPIを利用可能と誤認させないための、Hosted Agent案内文です。 */
const messages = {
  ja: {
    status: "準備中",
    description: "Hosted Agent APIは現在準備中です。提供するAPIと使い方ガイドは、準備ができ次第このページに掲載します。",
  },
  en: {
    status: "Coming soon",
    description: "Hosted Agent API is being prepared. Available APIs and integration guides will appear here when they are ready.",
  },
} as const;

/** 指定言語の準備中ページに必要な文言を返します。 */
export function getHostedAgentDictionary(locale: Locale) {
  return messages[locale];
}
