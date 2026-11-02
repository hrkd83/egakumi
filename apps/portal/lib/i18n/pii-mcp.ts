import { getPiiDictionary } from "./pii.ts";
import type { Locale } from "./config.ts";

/** RESTと同じマスキング処理を、MCPクライアントから使うための案内です。 */
export function getPiiMcpDictionary(locale: Locale) {
  const base = getPiiDictionary(locale);
  return locale === "ja" ? { ...base,
    intro: "MCPクライアントからPIIマスキングをツールとして利用します。REST版と同じ処理・入力上限で、送受信はMCPのJSON-RPCです。",
    guide: "接続先へinitialize → notifications/initialized → tools/list → tools/callの順で通信します。ツール名はpii_redact_text、argumentsはtextと言語languageです。SDKは初期化・プロトコル版・MCPセッションの引き継ぎを管理します。この画面の試行では1回ごとに接続し直し、APIキーとMCPセッションIDをサーバー内だけで扱います。",
    execute: "MCPツールを実行", running: "MCPツールを実行中", response: "JSON-RPC応答（公開PII結果）",
    schema: "tools/callのJSON-RPC要求と応答です。実際の引数定義はtools/listでも取得できます。HTTP成功・isError:falseだけでは成功と判定せず、contentまたはstructuredContentが公開PII成功スキーマに一致することも検証します。上流エラーが通常のcontentに包まれる場合があるためです。",
    document: "MCPのStreamable HTTP仕様", model: "PIIモデルバージョン",
    notice: "サーバー側で初期化とツール一覧確認を行ってから、選択した購読キーでPIIツールを実行します。本人接続セッションとは別の一時的なMCPセッションを使い、終了時に破棄します。",
  } : { ...base,
    intro: "Use PII redaction as a tool from an MCP client. It has the same processing and input limits as the REST API, with MCP JSON-RPC messages.",
    guide: "Connect using initialize → notifications/initialized → tools/list → tools/call. The tool is pii_redact_text and its arguments are text and language. An SDK manages initialization, protocol negotiation, and the MCP session. Each trial on this page opens a fresh connection; the API key and MCP session ID remain on the server.",
    execute: "Run MCP tool", running: "Running MCP tool", response: "JSON-RPC response (public PII result)",
    schema: "JSON-RPC tools/call request and response. Retrieve the live arguments from tools/list. HTTP success or isError:false alone does not establish success: validate content or structuredContent against the public PII success schema, because upstream errors can be wrapped as ordinary content.",
    document: "MCP Streamable HTTP specification", model: "PII model version",
    notice: "The server initializes the connection and checks the tool list, then executes the PII tool with your selected subscription. Its temporary MCP session is separate from a personal connection session and is discarded when the trial ends.",
  };
}
