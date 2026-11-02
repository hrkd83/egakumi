import type { Locale } from "./config.ts";

/** 埋め込み画面の言語別文言です。秘密や環境値を含まないためクライアントでも利用できます。 */
const dictionaries = {
  ja: {
    introduction: "入力したテキストを数値ベクトルへ変換し、モデルを比較しながらリクエスト・応答の構造を確認します。",
    integrationGuide: "検索に使う文書と質問は、同じモデル・同じ次元でベクトル化します。Cohereでは文書にsearch_document、質問にsearch_queryを指定します。出力はテキストごとの数値配列です。",
    dimensions: "出力次元", defaultDimensions: "モデル既定値", dimensionsHelp: "既定値では次元のパラメーターを省略します。数値ベクトル（float）で応答します。",
    inputType: "入力の用途", inputTypeHelp: "文書の索引化は search_document、検索する質問は search_query を使います。検索用ベクトルは同じモデルと次元で揃えます。",
    text: "テキスト", addText: "テキストを追加", removeText: "削除", textsHelp: "このモデルでは最大{count}件をまとめて送信できます。", sampleNotice: "入力内容を編集すると、JSONと各言語の組み込み例にも反映されます。モデル変更時も入力文は保持します。",
    execute: "埋め込みを実行", running: "埋め込みを実行中", vectorCount: "ベクトル数", vectorPreview: "ベクトルの先頭8要素", vectorNotice: "要約では各ベクトルの先頭8要素を表示します。生JSONには全要素が含まれます。",
    noUsage: "この応答にはトークン使用量が含まれていません。", inputTokens: "入力トークン", totalTokens: "合計トークン", sources: "提供元の仕様", openaiDocs: "OpenAI 埋め込みガイド", cohereDocs: "Cohere Embed API", schemaNotice: "この画面で使うテキスト埋め込みの部分スキーマです。画像入力や他の形式は含みません。",
  },
  en: {
    introduction: "Convert your text into numeric vectors and compare models while inspecting request and response structures.",
    integrationGuide: "Embed search documents and queries with the same model and dimensions. With Cohere, use search_document for documents and search_query for queries. The response contains a numeric array for each text.",
    dimensions: "Output dimensions", defaultDimensions: "Model default", dimensionsHelp: "The default omits the dimensions parameter. Responses use numeric float vectors.",
    inputType: "Input purpose", inputTypeHelp: "Use search_document to index documents and search_query for search questions. Keep the same model and dimensions for vectors used together in search.",
    text: "Text", addText: "Add text", removeText: "Remove", textsHelp: "This model accepts up to {count} texts in one request.", sampleNotice: "Edits update the JSON and integration examples. Your text is preserved when switching models.",
    execute: "Run embeddings", running: "Running embeddings", vectorCount: "Vectors", vectorPreview: "First 8 values of each vector", vectorNotice: "The summary shows the first 8 values. The raw JSON includes every vector value.",
    noUsage: "This response does not include token usage.", inputTokens: "Input tokens", totalTokens: "Total tokens", sources: "Provider documentation", openaiDocs: "OpenAI embeddings guide", cohereDocs: "Cohere Embed API", schemaNotice: "A partial schema for the text embeddings used by this panel. Image input and other formats are not included.",
  },
} satisfies Record<Locale, Record<string, string>>;

/** URLで選択された言語の埋め込み用文言を返します。 */
export function getEmbeddingDictionary(locale: Locale) { return dictionaries[locale]; }
