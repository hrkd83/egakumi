import type { IntegrationLanguage } from "./model-integration.ts";
import { PII_API, buildPiiRequest, type PiiInput } from "./pii-contract.ts";
import { PII_MCP, getPiiMcpEndpoint } from "./pii-mcp-contract.ts";
import { getPiiRequestSchema, getPiiResponseSchema } from "./pii-integration.ts";

/** MCPの組み込み例は、公式SDKのTypeScript/Pythonと生HTTP確認用cURLを提供します。 */
export const PII_MCP_LANGUAGES = ["typescript", "python", "curl"] as const;

/** tools/callへ渡すJSON-RPCです。HTTPの接続管理はSDKまたはクライアントが担当します。 */
export function buildPiiMcpCall(input: PiiInput) {
  return { jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: PII_MCP.toolName, arguments: buildPiiRequest(input) } };
}

/** MCP共通ヘッダーの例です。セッションとバージョンは初期化応答から引き継ぎます。 */
export function getPiiMcpHeaders() {
  return { "Content-Type": "application/json", Accept: "application/json, text/event-stream", "api-key": "${GATEWAY_API_KEY}", "MCP-Protocol-Version": "<negotiated version>", "Mcp-Session-Id": "<only if initialize returned this header>" };
}

/** REST引数をJSON-RPCのparams.argumentsに包んだ要求仕様です。 */
export function getPiiMcpRequestSchema() {
  return { type: "object", required: ["jsonrpc", "id", "method", "params"], properties: {
    jsonrpc: { type: "string", const: "2.0" }, id: { type: "integer", minimum: 1, description: "Correlates this request with its response. Use numeric IDs with the generated APIM MCP endpoint." }, method: { type: "string", const: "tools/call" },
    params: { type: "object", required: ["name", "arguments"], properties: { name: { type: "string", const: PII_MCP.toolName }, arguments: getPiiRequestSchema() }, additionalProperties: false },
  }, additionalProperties: false };
}

/** JSON-RPC結果のcontentはJSON文字列です。対応版ではstructuredContentも返せます。 */
export function getPiiMcpResponseSchema() {
  return { type: "object", required: ["jsonrpc", "id", "result"], properties: {
    jsonrpc: { type: "string", const: "2.0" }, id: { type: "integer", description: "Must match the numeric tools/call request ID." },
    result: { type: "object", properties: { content: { type: "array", items: { type: "object", properties: { type: { const: "text" }, text: { type: "string", description: "JSON-encoded public PII result, with redactedText and safe metadata." } } } }, structuredContent: getPiiResponseSchema(), isError: { type: "boolean", description: "true means tool execution failed; do not treat content as a successful PII result." } } },
  } };
}

/** 公式SDK v2の公開APIを使った接続例と、生HTTPの初期化手順を示します。 */
export function getPiiMcpIntegrationExample(language: IntegrationLanguage, input: PiiInput, origin: string): string {
  const endpoint = getPiiMcpEndpoint(origin), body = JSON.stringify(buildPiiRequest(input), null, 2);
  if (language === "typescript") return `// npm install @modelcontextprotocol/client zod\nimport { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";\nimport { z } from "zod";\n\n// native MCP may wrap a REST error with isError:false. Validate the public success schema.\nconst outputSchema = z.object({\n  redactedText: z.string().max(131072), language: z.literal(${JSON.stringify(input.language)}),\n  detectedCount: z.number().int().nonnegative().safe(), modelVersion: z.literal(${JSON.stringify(PII_API.modelVersion)}),\n  warningCodes: z.array(z.string()).max(0), requestId: z.uuid(),\n}).strict();\n\nconst apiKey = process.env.GATEWAY_API_KEY;\nif (!apiKey) throw new Error("GATEWAY_API_KEY is required");\nconst client = new Client({ name: "pii-client", version: "1.0.0" });\nconst transport = new StreamableHTTPClientTransport(new URL(${JSON.stringify(endpoint)}), {\n  requestInit: { headers: { "api-key": apiKey }, redirect: "error" },\n});\ntry {\n  await client.connect(transport); // initialize + initialized\n  const tools = await client.listTools();\n  if (!tools.tools.some(tool => tool.name === "${PII_MCP.toolName}")) throw new Error("PII tool unavailable");\n  const result = await client.callTool({ name: "${PII_MCP.toolName}", arguments: ${body} });\n  if (result.isError) throw new Error("PII tool failed");\n  const candidates: unknown[] = result.structuredContent === undefined ? [] : [result.structuredContent];\n  for (const entry of result.content) {\n    if (entry.type === "text") {\n      try { candidates.push(JSON.parse(entry.text)); } catch { throw new Error("Invalid PII success result"); }\n    }\n  }\n  const outputs = candidates.map(value => {\n    const parsed = outputSchema.safeParse(value);\n    if (!parsed.success) throw new Error("Invalid PII success result");\n    return parsed.data;\n  });\n  if (!outputs.length || outputs.some(value => JSON.stringify(value) !== JSON.stringify(outputs[0]))) {\n    throw new Error("PII result is missing or inconsistent");\n  }\n  console.log(outputs[0]);\n} finally {\n  try { await transport.terminateSession(); } finally { await client.close(); }\n}\n`;
  if (language === "python") return `# pip install "mcp>=2,<3" httpx2\nimport asyncio\nimport json\nimport os\nimport re\nimport httpx2\nfrom mcp import Client\nfrom mcp.client.streamable_http import streamable_http_client\n\n${pythonPiiOutputGuard(input)}\n\nasync def main():\n    payload = json.loads(r'''${body.replaceAll("'", "\\u0027")}''')\n    async with httpx2.AsyncClient(\n        headers={"api-key": os.environ["GATEWAY_API_KEY"]},\n        timeout=100, follow_redirects=False,\n    ) as http:\n        transport = streamable_http_client(${JSON.stringify(endpoint)}, http_client=http)\n        async with Client(transport, read_timeout_seconds=100, mode="legacy") as client:\n            tools = await client.list_tools()\n            if not any(tool.name == "${PII_MCP.toolName}" for tool in tools.tools):\n                raise RuntimeError("PII tool unavailable")\n            result = await client.call_tool("${PII_MCP.toolName}", payload)\n            if result.is_error:\n                raise RuntimeError("PII tool failed")\n            # is_error alone cannot distinguish all errors in native REST-backed MCP.\n            wire = json.loads(result.model_dump_json(by_alias=True))\n            candidates = []\n            if wire.get("structuredContent") is not None:\n                candidates.append(wire["structuredContent"])\n            for entry in wire.get("content", []):\n                if entry.get("type") == "text":\n                    candidates.append(json.loads(entry["text"]))\n            if any(not valid_pii_output(value) for value in candidates):\n                raise RuntimeError("Invalid PII success result")\n            if not candidates or any(value != candidates[0] for value in candidates):\n                raise RuntimeError("PII result is missing or inconsistent")\n            print(json.dumps(candidates[0], ensure_ascii=False))\n\nasyncio.run(main())\n`;
  if (language === "curl") return `# Requires bash, curl and Python 3. Keep temporary session headers private.\nset -eu\n: "\${GATEWAY_API_KEY:?GATEWAY_API_KEY is required}"\numask 077\ndir=$(mktemp -d)\ntrap 'rm -rf "$dir"' EXIT\nurl='${endpoint}'\nsession=''\nversion=''\n\n# JSON and SSE responses are both valid. Extract only the requested JSON-RPC reply.\ncat > "$dir/read.py" <<'PY'\nimport json, pathlib, re, sys\n${pythonPiiOutputGuard(input)}\nraw = pathlib.Path(sys.argv[1]).read_text()\ntry:\n    entries = [json.loads(raw)]\nexcept json.JSONDecodeError:\n    entries = []\n    for event in raw.replace("\\r\\n", "\\n").split("\\n\\n"):\n        data = "\\n".join(line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:"))\n        if data: entries.append(json.loads(data))\nreply = next(item for item in entries if item.get("id") == int(sys.argv[2]))\nif "error" in reply: raise SystemExit("MCP request failed")\nresult = reply["result"]\nif int(sys.argv[2]) == 3:\n    if result.get("isError"): raise SystemExit("PII tool failed")\n    values = ([result["structuredContent"]] if "structuredContent" in result else [])\n    values += [json.loads(item["text"]) for item in result.get("content", []) if item.get("type") == "text"]\n    if not values or any(not valid_pii_output(value) for value in values):\n        raise SystemExit("Invalid PII success result")\n    if any(value != values[0] for value in values): raise SystemExit("Inconsistent PII result")\nprint(json.dumps(result, ensure_ascii=False))\nPY\n\nsend() {\n  set -- --header "api-key: $GATEWAY_API_KEY" --header 'Content-Type: application/json' --header 'Accept: application/json, text/event-stream'\n  if [ -n "$session" ]; then set -- "$@" --header "Mcp-Session-Id: $session"; fi\n  if [ -n "$version" ]; then set -- "$@" --header "MCP-Protocol-Version: $version"; fi\n  curl --silent --show-error --fail-with-body --max-time 100 "$url" "$@" --data-binary @"$dir/request.json" --dump-header "$dir/headers" --output "$dir/body"\n}\n\ncat > "$dir/request.json" <<'JSON'\n{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"pii-curl","version":"1.0.0"}}}\nJSON\nsend\npython3 "$dir/read.py" "$dir/body" 1 > "$dir/initialized.json"\nversion=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["protocolVersion"])' "$dir/initialized.json")\nsession=$(python3 -c 'import pathlib,sys; print(next((line.split(":",1)[1].strip() for line in pathlib.Path(sys.argv[1]).read_text().splitlines() if line.lower().startswith("mcp-session-id:")), ""))' "$dir/headers")\nprintf '%s' '{"jsonrpc":"2.0","method":"notifications/initialized"}' > "$dir/request.json"\nsend\nprintf '%s' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' > "$dir/request.json"\nsend\npython3 "$dir/read.py" "$dir/body" 2\ncat > "$dir/request.json" <<'JSON'\n${JSON.stringify(buildPiiMcpCall(input), null, 2)}\nJSON\nsend\npython3 "$dir/read.py" "$dir/body" 3\nif [ -n "$session" ]; then\n  curl --silent --show-error --max-time 10 --request DELETE "$url" --header "api-key: $GATEWAY_API_KEY" --header "Mcp-Session-Id: $session" --header "MCP-Protocol-Version: $version" --output /dev/null\nfi\n`;
  throw new TypeError("Use an MCP SDK or the JSON-RPC transport example");
}

/** PythonとcURLに、公開PII成功スキーマと一致する固定エラーの検証を埋め込みます。 */
function pythonPiiOutputGuard(input: PiiInput): string {
  return `def valid_pii_output(value):
    required = {"redactedText", "language", "detectedCount", "modelVersion", "warningCodes", "requestId"}
    uuid_pattern = r"(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)"
    return (isinstance(value, dict) and set(value) == required
        and isinstance(value["redactedText"], str)
        and len(value["redactedText"]) <= 131072
        and value["language"] == ${JSON.stringify(input.language)}
        and type(value["detectedCount"]) is int and 0 <= value["detectedCount"] <= 9007199254740991
        and value["modelVersion"] == ${JSON.stringify(PII_API.modelVersion)}
        and value["warningCodes"] == []
        and isinstance(value["requestId"], str)
        and re.fullmatch(uuid_pattern, value["requestId"], flags=re.IGNORECASE) is not None)`;
}
