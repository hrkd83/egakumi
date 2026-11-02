import type { Locale } from "./config.ts";

/** リランクの動作とGateway側の公開範囲を、日英で同じ内容に揃えます。 */
const dictionaries = {
  ja: {
    introduction: "検索で得た候補文書を、質問との関連度で並べ替えます。新しい文章の生成や候補の追加は行いません。",
    guide: "質問をquery、候補文書の配列をdocumentsへ渡します。応答のindexは入力配列の0始まりの位置なので、元の文書と対応付けて表示します。relevance_scoreは関連度を比較する値であり、正しさの確率ではありません。",
    query: "質問・検索クエリ", document: "候補文書", addDocument: "文書を追加", removeDocument: "削除", topN: "返す件数（top_n）",
    limits: "Gatewayの公開上限：候補{documents}件、返却{top}件、本文{bytes}バイト。返す件数は候補数以下にします。",
    editable: "質問と候補文書は自由に編集できます。変更は下のJSONと各言語の組み込み例に反映されます。",
    execute: "関連度で並べ替える", running: "並べ替え中", rank: "順位", original: "入力文書", score: "関連度スコア", searchUnits: "検索単位（search_units）",
    invalidInput: "質問、候補文書、返却件数と本文サイズの上限を確認してください。",
    emptyResponse: "実行すると候補文書の順位・関連度と応答JSONを表示します。",
    requestNotice: "入力した質問と候補文書を、選択した購読キーでGatewayへ送信します。文書は関連度で並べ替えられ、JSONで返ります。",
    resultHelp: "関連度が高い順に表示しています。入力文書の番号は1始まりで表示し、APIのindexは0始まりです。",
    schemaNotice: "この画面で扱う文字列文書の部分スキーマです。提供元の追加フィールドは生JSONで確認できます。",
    providerDocs: "Cohere Rerank API", noUsage: "この応答には使用量が含まれていません。",
  },
  en: {
    introduction: "Reorder candidate search results by their relevance to a query. Reranking does not generate text or add new candidates.",
    guide: "Send the question as query and the candidate texts as documents. Each result index is the zero-based position in the input array; use it to find the original document. relevance_score compares relevance and is not a probability that the content is correct.",
    query: "Question / search query", document: "Candidate document", addDocument: "Add document", removeDocument: "Remove", topN: "Results to return (top_n)",
    limits: "Gateway limits: {documents} candidates, {top} results, {bytes} request bytes. Return no more results than the number of candidates.",
    editable: "Edit the query and candidate documents freely. Changes update the JSON and integration examples below.",
    execute: "Rank by relevance", running: "Ranking", rank: "Rank", original: "Input document", score: "Relevance score", searchUnits: "Search units",
    invalidInput: "Check the query, candidate documents, result count, and request size limit.",
    emptyResponse: "Run a request to see document rankings, relevance scores, and the response JSON.",
    requestNotice: "Send the query and candidate documents through the Gateway using the selected subscription key. The documents are ranked by relevance and returned as JSON.",
    resultHelp: "Results are ordered by relevance. Input document numbers displayed here start at 1; the API index starts at 0.",
    schemaNotice: "A partial schema for the string documents used here. Additional provider fields remain available in the raw JSON.",
    providerDocs: "Cohere Rerank API", noUsage: "This response does not include usage information.",
  },
} satisfies Record<Locale, Record<string, string>>;

/** 現在の表示言語に対応するリランク専用の説明を返します。 */
export function getRerankDictionary(locale: Locale) { return dictionaries[locale]; }
