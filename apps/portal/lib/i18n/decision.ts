import type { Locale } from "./config.ts";

/** 意思決定の使い方と結果表示に用いる、環境値を含まない文言です。 */
const dictionaries = {
  ja: {
    introduction: "状況と質問を渡すと、文章を生成する代わりに、判定や選択肢ごとの確率を返します。問い合わせの振り分けや、条件への当てはまりをアプリで扱う用途に向いています。",
    state: "判断対象の状況（state）", stateHelp: "判断に使う事実をJSONオブジェクトで入力します。文章や数値、配列を含められます。",
    questions: "判定する質問（questions）", questionsHelp: "質問名と、type・instructionsを指定します。choiceは選択肢の辞書、scoreは小さい値から順に並べた段階の配列をcriteriaに指定します。",
    noul: "noul · 肯定の確率", noulHelp: "条件に当てはまる確率を0〜1で返します。true／falseを返すAPIではありません。",
    choice: "choice · 候補の選択", choiceHelp: "候補ごとの確率と、最も確率の高い候補を返します。criteriaのキーが応答の選択肢になります。",
    score: "score · 段階評価", scoreHelp: "0から始まる段階ごとの確率と、その加重平均を返します。結果は小数になることがあります。",
    limits: "リクエスト全体の上限は{bytes}バイトです。最大出力トークン・StreamはこのAPIでは指定しません。PortalのJSONエディターは、安全に表示・検証できる深さ32階層まで扱います。",
    sample: "初期例は問い合わせの返金意図・担当部署・緊急度をまとめて判定します。状況と質問は自由に編集でき、コード例にも反映されます。",
    caution: "確率は判断材料です。重要な承認や業務の確定処理には、アプリ側の基準や人による確認を組み合わせてください。",
    preview: "このモデルはPreviewです。利用できる機能やサービスの提供条件は、提供元の仕様をご確認ください。",
    execute: "判定を実行", running: "判定を実行中", reset: "入力例に戻す", invalid: "JSONの形式・質問型・選択肢、またはリクエスト全体のサイズを確認してください。",
    answer: "判定結果", probability: "確率", weightedScore: "加重平均", confidence: "確信度", noResult: "「判定を実行」を押すと、実際の結果をここに表示します。",
    waiting: "意思決定モデルの応答を待っています。", cancelled: "リクエストの待機を中止しました。送信済み処理や課金を取り消すものではありません。",
    inputTokens: "入力トークン", outputTokens: "出力トークン", totalTokens: "合計トークン", noUsage: "この応答にはトークン使用量が含まれていません。",
    docs: "Microsoft Decision の仕様", schema: "この画面で使う型付き質問の部分スキーマです。リクエストのmodelは配備名、応答のmodelは基礎モデル名です。",
  },
  en: {
    introduction: "Provide a situation and typed questions to receive decisions and candidate probabilities instead of generated prose. Use them to route requests or evaluate how well conditions match.",
    state: "Situation to evaluate (state)", stateHelp: "Enter facts as a JSON object. It may contain text, numbers, arrays, and nested objects.",
    questions: "Questions to evaluate (questions)", questionsHelp: "Give each question a name, type, and instructions. For choice, criteria is an object of candidates. For score, criteria is an array of levels in ascending order.",
    noul: "noul · Probability of yes", noulHelp: "Returns a probability from 0 to 1 that the condition holds, rather than a true or false value.",
    choice: "choice · Choose a candidate", choiceHelp: "Returns a probability for each candidate and selects a candidate with the highest probability. The keys in criteria identify the candidates.",
    score: "score · Rate on a scale", scoreHelp: "Returns probabilities for levels starting at 0 and their weighted average. The score may be fractional.",
    limits: "The complete request must fit within {bytes} bytes. This API has no maximum output-token or Stream setting. The Portal JSON editor handles up to 32 nested levels for safe display and validation.",
    sample: "The example evaluates refund intent, the responsible team, and urgency together. Edit the situation and questions to update the integration examples too.",
    caution: "Probabilities are inputs to a decision. Combine them with application rules and human review for important approvals or business actions.",
    preview: "This model is in Preview. Consult the provider's documentation for supported features and service terms.",
    execute: "Run decision", running: "Evaluating", reset: "Restore example", invalid: "Check JSON syntax, question types, criteria, and the complete request size.",
    answer: "Decision results", probability: "Probability", weightedScore: "Weighted average", confidence: "Confidence", noResult: "Select Run decision to display the actual results here.",
    waiting: "Waiting for the decision model.", cancelled: "Stopped waiting for the request. Processing and charges already incurred are not cancelled.",
    inputTokens: "Input tokens", outputTokens: "Output tokens", totalTokens: "Total tokens", noUsage: "Token usage was not included in this response.",
    docs: "Microsoft Decision documentation", schema: "Partial schemas for the typed questions used here. The request model is a deployment name; the response model is the underlying model name.",
  },
} satisfies Record<Locale, Record<string, string>>;

/** URLで選ばれた言語の意思決定ガイドを取得します。 */
export function getDecisionDictionary(locale: Locale) { return dictionaries[locale]; }
