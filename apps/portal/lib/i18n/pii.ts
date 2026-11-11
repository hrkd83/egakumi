import type { Locale } from "./config.ts";

const dictionaries = {
  ja: {
    intro: "提供元が対応する言語の文章から個人情報を検出し、該当箇所をマスキングします。入力は1文書ずつ処理します。",
    guide: "textに対象の文章、languageにja・enなどの対応言語コードを指定します。結果のredactedTextを後続の処理へ渡し、detectedCountで検出数を確認できます。検出した名前・住所などの実値や詳細な位置情報は返しません。本人接続セッションやMicrosoftへのサインインは不要です。",
    caution: "検出には見落としや誤検出があり得ます。出力が完全な匿名化済みであるとは扱わず、利用目的に応じて確認してください。試す際は架空の情報をおすすめします。",
    limits: "1回1文書、最大5,120文字要素（Unicode 8.0 TextElements）。提供元のLanguage API要求全体は1,000,000 UTF-8バイトまでです。JSONの空白・エスケープ表現と文字列値を区別し、文章は正規化・切り詰めません。文字要素は絵文字や結合文字を考慮してサービス側で判定します。",
    text: "マスキングする文章", language: "文章の言語", japanese: "日本語（ja）", english: "英語（en）",
    editable: "文章と言語は自由に変更できます。変更はガイドのリクエスト例へも反映されます。",
    execute: "個人情報をマスキング", running: "マスキング中", redacted: "マスキング後の文章", count: "検出数", model: "モデルバージョン",
    invalid: "本文、言語、サイズを確認してください。提供元の上限は5,120文字要素、Language API要求全体で1,000,000バイトです。",
    failed: "マスキング結果を取得できませんでした。時間をおいてお試しください。",
    empty: "実行すると、マスキング後の文章と検出数を表示します。",
    notice: "選択した購読キーを使い、入力した文章をGateway経由でPII解析へ送信します。Portalは入力や結果をデータベースに保存しません。",
    response: "公開応答JSON", document: "Azure AI LanguageのPIIドキュメント", bytes: "コンパクトJSONのサイズ", schema: "このGatewayが公開するREST APIの完全な入力・出力契約です。",
  },
  en: {
    intro: "Detect and mask personal information in text in provider-supported languages. One document is processed per request.",
    guide: "Set text to the document and language to a supported code such as ja or en. Pass redactedText to the next operation and use detectedCount to check the number of detections. Original entity values and detailed offsets are not returned. No personal connection session or Microsoft sign-in is required.",
    caution: "Detection can miss personal information or mark other content. Do not assume the output is fully anonymized; review it for your use case. Use fictional information when testing.",
    limits: "One document per request, up to 5,120 Unicode 8.0 text elements. The complete provider Language API request is limited to 1,000,000 UTF-8 bytes. JSON whitespace and escaping do not normalize or truncate the text value. Text elements account for emoji and combining characters and are checked by the service.",
    text: "Text to redact", language: "Text language", japanese: "Japanese (ja)", english: "English (en)",
    editable: "Edit the text and language freely. Your changes also update the request examples in the guide.",
    execute: "Redact personal information", running: "Redacting", redacted: "Redacted text", count: "Detections", model: "Model version",
    invalid: "Check the text, language, and request size. Provider limits are 5,120 text elements and 1,000,000 bytes for the complete Language API request.",
    failed: "The redaction result could not be retrieved. Please try again later.",
    empty: "Run a request to see the redacted text and detection count.",
    notice: "The text is sent through the Gateway to PII analysis using the selected subscription key. Portal does not save the text or result to its database.",
    response: "Public response JSON", document: "Azure AI Language PII documentation", bytes: "Compact JSON size", schema: "The complete input and output contract of the REST API published by this Gateway.",
  },
} satisfies Record<Locale, Record<string, string>>;

/** 表示言語に対応したPIIガイドと結果表示文言を返します。 */
export function getPiiDictionary(locale: Locale) { return dictionaries[locale]; }
