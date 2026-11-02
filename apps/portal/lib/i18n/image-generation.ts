import type { Locale } from "./config.ts";

const ja = {
  integrationGuide: "プロンプトから画像を生成します。モデルごとのサイズ・品質・形式は「試す」で変更でき、下のコード例にも反映されます。応答のBase64画像データや一時URLは、アプリ側で形式を確認して扱います。",
  parameters: "画像生成の設定", width: "幅", height: "高さ", size: "画像サイズ", quality: "品質", format: "出力形式",
  range: "{min}〜{max} px", count: "生成枚数: 1枚", prompt: "プロンプト", fixedNotice: "プロンプトと設定の変更は、リクエストJSONと組み込み例にも反映されます。モデル変更時もプロンプトは保持します。",
  run: "画像を生成して確認", running: "画像を生成しています…", wait: "画像生成は最大180秒待機します。中止しても、送信済みの生成処理と課金が取り消されるとは限りません。自動再試行は行いません。",
  responseEmpty: "設定を選んで実行すると、実際のAPIM応答と生成画像を確認できます。", preview: "生成画像", imageAlt: "入力したプロンプトから生成された画像",
  previewUnavailable: "このプロバイダーは画像の一時URLを返しました。画像を自動取得しません。応答JSONでURLとレスポンス構造を確認できます。",
  truncated: "長いBase64などを画面上だけ省略しています。「応答をコピー」では省略前の完全なJSONをコピーします。",
  backend: "各コード例はアプリケーションのバックエンドで実行します。GATEWAY_API_KEYを環境変数に設定し、ブラウザーへ渡さないでください。",
  schema: "パネルで使用する主要フィールドの部分スキーマです。APIの全機能や、他の画像編集操作の仕様ではありません。",
  json: "リクエストJSON", headers: "リクエストヘッダー", requestSchema: "リクエストスキーマ", responseSchema: "応答スキーマ", rawResponse: "実際の応答JSON", language: "組み込み例の言語",
  png: "PNG", jpeg: "JPEG", pixel: "px", source: "Microsoft公式のAPI仕様", openaiDocs: "Azure OpenAI 画像生成", fluxDocs: "Azure Foundry FLUX",
};

/** 画像生成画面の翻訳項目です。言語間で項目の抜けを検出します。 */
export type ImageGenerationDictionary = { [K in keyof typeof ja]: string };

const en: ImageGenerationDictionary = {
  integrationGuide: "Generate an image from a prompt. Change the model's size, quality, and format in Try; the code below updates to match. Check the response format before handling Base64 image data or a temporary URL in your application.",
  parameters: "Image generation parameters", width: "Width", height: "Height", size: "Image size", quality: "Quality", format: "Output format",
  range: "{min}–{max} px", count: "Images: 1", prompt: "Prompt", fixedNotice: "Changes update the request JSON and integration examples. Your prompt is preserved when switching models.",
  run: "Generate and inspect", running: "Generating image…", wait: "The request waits for up to 180 seconds. Cancelling does not guarantee cancellation of generation or charges. Requests are never retried automatically.",
  responseEmpty: "Choose parameters and run to inspect the actual APIM response and generated image.", preview: "Generated image", imageAlt: "Image generated from your prompt",
  previewUnavailable: "This provider returned a temporary image URL. The portal does not load it automatically. Inspect the URL and response structure in the JSON response.",
  truncated: "Long Base64 values are shortened on screen only. Copy response copies the complete, original JSON.",
  backend: "Run each example in your application backend. Set GATEWAY_API_KEY as an environment variable and never expose it to the browser.",
  schema: "These partial schemas describe the panel's main fields, not every API capability or image editing operation.",
  json: "Request JSON", headers: "Request headers", requestSchema: "Request schema", responseSchema: "Response schema", rawResponse: "Actual response JSON", language: "Integration example language",
  png: "PNG", jpeg: "JPEG", pixel: "px", source: "Microsoft API documentation", openaiDocs: "Azure OpenAI image generation", fluxDocs: "Azure Foundry FLUX",
};

/** クライアントにも安全に渡せる、画像生成専用の辞書を選びます。 */
export function getImageGenerationDictionary(locale: Locale): ImageGenerationDictionary { return locale === "en" ? en : ja; }
