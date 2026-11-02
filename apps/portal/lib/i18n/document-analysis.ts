import type { Locale } from "./config.ts";

/** 文書解析のモデル選択、ファイル入力と結果表示に使う日本語文言です。 */
const ja = {
  title: "Document Analysis", intro: "PDFや画像の文字を読み取り、Layoutモデルでは段落・表・文書の構造も抽出します。ファイルを選んで実際の解析結果を確認できます。",
  model: "解析モデル", read: "Read — 文字の読み取り（OCR）", layout: "Layout — 文字・表・文書構造",
  readHelp: "印刷文字・手書き文字とページ上の配置を読み取ります。", layoutHelp: "OCRに加えて、段落・表など文書の構造を取得します。検出内容は文書により異なります。",
  file: "解析するファイル", chooseFile: "ファイルを選択", fileHelp: "PDF / JPEG / PNG / TIFF / BMP · 1 MiBまで。URL指定はできません。全ページを解析し、ページの省略は行いません。",
  limits: "ファイルは1 MiBまで、Gatewayが受信する解析結果は4 MiBまでです。これらはこのGatewayの上限であり、Azureサービスの最大容量ではありません。",
  async: "Azure側の非同期ジョブの開始と完了確認はGatewayが担当します。アプリはこのAPIへ1回POSTし、完了した解析結果を受け取ります。解析の待ち時間を含むため、タイムアウトを100秒程度に設定してください。",
  privacy: "選択したファイルは解析のためにAzureへ送信します。Portalではファイルを保存しません。結果は画面表示中のメモリーで保持します。",
  execute: "文書を解析", running: "文書を解析しています…", waiting: "解析の完了を待っています。ページ数や文書の内容により時間がかかります。", noResult: "ファイルとAPIキーを選び、解析を実行すると結果が表示されます。",
  cancelled: "画面からの待機を停止しました。開始済みのAzure解析が取り消されるとは限りません。", emptyText: "文字は検出されませんでした。", pages: "解析ページ", pageNumbers: "ページ番号", tables: "検出した表", text: "抽出した全文", table: "表", row: "行", column: "列", cell: "内容", noTables: "表は検出されませんでした。", cells: "表のセル", copied: "コピーしました", invalidFile: "対応形式とファイルサイズを確認してください。", raw: "解析結果のJSON", docs: "Microsoft公式のAPI仕様", apiKey: "実行に使うAPIキー", timeoutNote: "タイムアウトした場合、完了済みか確認できないため自動再試行は行いません。",
  errors: {
    AuthenticationRequired: "Portalへのサインインが必要です。", AuthenticationUnavailable: "本人確認に失敗しました。時間をおいて再実行してください。", RequestNotAllowed: "この操作は許可されていません。", InvalidRequest: "入力を確認してください。", DocumentTooLarge: "ファイルは1 MiB以内で指定してください。", UnsupportedDocument: "PDF / JPEG / PNG / TIFF / BMPのファイルを指定してください。形式とファイル内容が一致している必要があります。", DocumentRejected: "解析サービスが文書を受け付けませんでした。破損や暗号化がないか、対応した文書かを確認してください。", ApiKeyUnavailable: "このAPIキーを利用できません。一覧を更新してください。", ApiKeyAccessUnavailable: "APIキーの利用権限を確認できませんでした。", GatewayNotConfigured: "Gatewayの接続先が設定されていません。", GatewayUnauthorized: "Gatewayでこの呼び出しが許可されませんでした。", RateLimitExceeded: "呼び出し上限に達しました。時間をおいて再実行してください。", DocumentAnalysisTimeout: "解析の待機時間を超えました。文書を確認し、必要な場合に再実行してください。", DocumentAnalysisUnavailable: "解析サービスに接続できませんでした。時間をおいて再実行してください。", DocumentAnalysisFailed: "文書を解析できませんでした。対応形式やファイル内容を確認してください。", DocumentResultTooLarge: "解析結果がPortalの8 MiB上限を超えました。文書を分割してお試しください。", RequestCancelled: "解析の待機を停止しました。",
  },
};
export type DocumentAnalysisDictionary = typeof ja;
const en: DocumentAnalysisDictionary = {
  title: "Document Analysis", intro: "Read text from PDF files and images. The Layout model also extracts paragraphs, tables, and document structure. Choose a file to inspect a real analysis result.",
  model: "Analysis model", read: "Read — OCR text extraction", layout: "Layout — Text, tables, and structure", readHelp: "Extracts printed and handwritten text with its page placement.", layoutHelp: "Adds paragraphs and tables to OCR. Detected structures depend on the document.",
  file: "Document to analyze", chooseFile: "Choose a file", fileHelp: "PDF / JPEG / PNG / TIFF / BMP · Up to 1 MiB. URLs are not accepted. All pages are analyzed without truncation.", limits: "Files are limited to 1 MiB, and analysis results received by the Gateway to 4 MiB. These are Gateway limits, not Azure's maximum capacity.",
  async: "The Gateway starts and polls Azure's asynchronous analysis job. Your app sends one POST and receives the completed result. Allow approximately 100 seconds for the request timeout.", privacy: "The selected file is sent to Azure for analysis. The Portal does not save the file. Results remain in memory while this page is open.",
  execute: "Analyze document", running: "Analyzing document…", waiting: "Waiting for analysis. Processing time depends on the number of pages and document content.", noResult: "Choose a file and an API key, then run analysis to view the result.", cancelled: "Stopped waiting in this page. An Azure analysis that already started may continue.", emptyText: "No text was detected.", pages: "Analyzed pages", pageNumbers: "Page numbers", tables: "Detected tables", text: "Full extracted text", table: "Table", row: "Row", column: "Column", cell: "Content", noTables: "No tables were detected.", cells: "Table cells", copied: "Copied", invalidFile: "Check the file format and size.", raw: "Analysis result JSON", docs: "Microsoft API documentation", apiKey: "API key for this request", timeoutNote: "A timeout does not confirm whether analysis completed, so requests are not retried automatically.",
  errors: {
    AuthenticationRequired: "Sign in to the Portal.", AuthenticationUnavailable: "Your identity could not be checked. Try again later.", RequestNotAllowed: "This operation is not allowed.", InvalidRequest: "Check the input.", DocumentTooLarge: "The file must be no larger than 1 MiB.", UnsupportedDocument: "Choose a PDF, JPEG, PNG, TIFF, or BMP file. The file contents must match its format.", DocumentRejected: "The analysis service rejected this document. Check for damage, encryption, or an unsupported document.", ApiKeyUnavailable: "This API key is unavailable. Refresh the list.", ApiKeyAccessUnavailable: "API key access could not be checked.", GatewayNotConfigured: "The Gateway origin is not configured.", GatewayUnauthorized: "The Gateway denied this request.", RateLimitExceeded: "The request limit was reached. Try again later.", DocumentAnalysisTimeout: "Analysis timed out. Check your document and retry if needed.", DocumentAnalysisUnavailable: "The analysis service could not be reached. Try again later.", DocumentAnalysisFailed: "The document could not be analyzed. Check its format and contents.", DocumentResultTooLarge: "The analysis result exceeds Portal's 8 MiB limit. Split the document and try again.", RequestCancelled: "Stopped waiting for analysis.",
  },
};

/** 検証済みロケールに応じて同じ文書解析項目を返します。 */
export function getDocumentAnalysisDictionary(locale: Locale): DocumentAnalysisDictionary { return locale === "en" ? en : ja; }
