import { z } from "zod";

/** カタログの主要なサービス区分です。URL と画面で同じ値を使います。 */
export const gatewaySchema = z.enum(["models", "tools", "agents"]);
export type Gateway = z.infer<typeof gatewaySchema>;

/** Tool Gatewayで案内する接続方式です。A2Aの提供状況はREST/MCPと分けて表示します。 */
export type ToolProtocol = "rest" | "mcp" | "a2a";

/** サンプルデータの構造を検査し、将来の取得データにも同じ契約を使えるようにします。 */
export const catalogItemSchema = z.object({
  id: z.string().min(1),
  title: z.string().min(1),
  vendor: z.string().min(1),
  gateway: gatewaySchema,
  protocol: z.enum(["rest", "mcp", "a2a"]).default("rest"),
  capability: z.enum(["chat", "embedding", "image", "identity", "privacy", "document", "assistant", "research", "workflow"]),
  description: z.object({ ja: z.string().min(1), en: z.string().min(1) }),
  endpoint: z.string().startsWith("/"),
  method: z.enum(["GET", "POST"]),
  featured: z.boolean().default(false),
  accent: z.enum(["green", "blue", "violet", "amber"]),
});

/** サービスカードと詳細画面に渡す、検査済みのカタログ項目です。 */
export type CatalogItem = z.infer<typeof catalogItemSchema>;

/** モックアップ専用データです。Azure の配備状態・利用可否を表す値ではありません。 */
export const catalogItems: CatalogItem[] = z.array(catalogItemSchema).parse([
  { id: "gpt-54", title: "GPT-5.4", vendor: "OpenAI", gateway: "models", capability: "chat", description: { ja: "複雑な課題の推論からコード生成まで。幅広い業務を支える高性能モデル。", en: "A capable model for complex reasoning, coding, and everyday business workflows." }, endpoint: "/models/openai/v1/chat/completions", method: "POST", featured: true, accent: "green" },
  { id: "gpt-54-mini", title: "GPT-5.4 mini", vendor: "OpenAI", gateway: "models", capability: "chat", description: { ja: "品質と応答速度のバランスに優れた、日常的なタスクのためのモデル。", en: "A balanced model for responsive conversations and everyday tasks." }, endpoint: "/models/openai/v1/chat/completions", method: "POST", featured: false, accent: "green" },
  { id: "gpt-54-nano", title: "GPT-5.4 nano", vendor: "OpenAI", gateway: "models", capability: "chat", description: { ja: "分類や抽出を軽快に。小さなタスクを効率よく処理する軽量モデル。", en: "A lightweight model for efficient classification, extraction, and focused tasks." }, endpoint: "/models/openai/v1/chat/completions", method: "POST", featured: false, accent: "green" },
  { id: "embedding-small", title: "text-embedding-3-small", vendor: "OpenAI", gateway: "models", capability: "embedding", description: { ja: "テキストをベクトルに変換。検索やナレッジ活用の基盤づくりに。", en: "Turn text into vectors for semantic search and knowledge retrieval." }, endpoint: "/models/openai/v1/embeddings", method: "POST", featured: false, accent: "blue" },
  { id: "cohere-embed", title: "Cohere Embed v4", vendor: "Cohere", gateway: "models", capability: "embedding", description: { ja: "多言語の情報をつなぐ埋め込みモデル。社内文書の検索体験を向上。", en: "Multilingual embeddings that connect information across your knowledge base." }, endpoint: "/models/cohere/v1/embed", method: "POST", featured: false, accent: "amber" },
  { id: "gpt-image", title: "GPT Image", vendor: "OpenAI", gateway: "models", capability: "image", description: { ja: "アイデアをビジュアルに。テキストから画像を生成するクリエイティブモデル。", en: "Bring ideas to life with image generation from natural-language descriptions." }, endpoint: "/models/openai/v1/images/generations", method: "POST", featured: true, accent: "violet" },
  { id: "graph-profile", title: "Microsoft Graph /me", vendor: "Microsoft", gateway: "tools", capability: "identity", description: { ja: "サインインした本人の氏名、所属、連絡先などのユーザー情報を取得します。", en: "Retrieve the signed-in user's name, organization, contact details, and other profile attributes." }, endpoint: "/tools/graph/v1/me", method: "GET", featured: true, accent: "blue" },
  { id: "pii-redact", title: "PII Redaction", vendor: "Azure AI", gateway: "tools", capability: "privacy", description: { ja: "文章に含まれる個人情報を検出・マスキングし、データの活用をサポート。", en: "Detect and redact personal information to support safer data workflows." }, endpoint: "/tools/pii/v1/redact", method: "POST", featured: false, accent: "green" },
  { id: "pii-redact-mcp", title: "PII Redaction", vendor: "Azure AI", gateway: "tools", protocol: "mcp", capability: "privacy", description: { ja: "MCPクライアントから個人情報の検出・マスキングを呼び出します。", en: "Detect and redact personal information from an MCP client." }, endpoint: "/tools/pii-v1-mcp/mcp", method: "POST", featured: false, accent: "green" },
  { id: "document-analysis", title: "Document Analysis", vendor: "Azure AI Document Intelligence", gateway: "tools", capability: "document", description: { ja: "PDFや画像から文字、表、文書のレイアウトを抽出します。", en: "Extract text, tables, and document layout from PDF files and images." }, endpoint: "/tools/documents/v1/analyze", method: "POST", featured: false, accent: "amber" },
  { id: "knowledge-agent", title: "Knowledge Assistant", vendor: "egakumi", gateway: "agents", capability: "assistant", description: { ja: "社内ナレッジを探し、情報源とともに質問に答えるアシスタント。", en: "An assistant that answers questions with sources from your knowledge base." }, endpoint: "/agents/knowledge/v1/runs", method: "POST", featured: true, accent: "green" },
  { id: "research-agent", title: "Research Agent", vendor: "egakumi", gateway: "agents", capability: "research", description: { ja: "調査と情報整理をサポート。複数の情報をまとめてレポートを作成。", en: "Research a topic, organize findings, and prepare a concise report." }, endpoint: "/agents/research/v1/runs", method: "POST", featured: false, accent: "violet" },
  { id: "workflow-agent", title: "Workflow Agent", vendor: "egakumi", gateway: "agents", capability: "workflow", description: { ja: "ツールを組み合わせて定型業務を進める、ワークフロー型エージェント。", en: "Connect tools into repeatable workflows for routine business tasks." }, endpoint: "/agents/workflow/v1/runs", method: "POST", featured: false, accent: "blue" },
]);

/** デモ試用フォームの入力制約です。外部サービスへは送信しません。 */
export const demoInputSchema = z.object({
  message: z.string().trim().min(1).max(500),
});
