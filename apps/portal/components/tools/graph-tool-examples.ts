import { GRAPH_TOOL_PATH, GRAPH_TOOL_PROFILE_SELECT } from "../../lib/graph-tool-contracts.ts";
import type { GraphToolViewState } from "./graph-tool-store.ts";

/** Gateway.Invokeを取得済みのクライアントが使う、秘密値を含まないコード例です。 */
export function getGraphToolExample(language: GraphToolViewState["language"], gatewayOrigin: string | null): string {
  const endpoint = (gatewayOrigin ?? "https://gateway.example.com") + GRAPH_TOOL_PATH;
  const url = JSON.stringify(endpoint);
  if (language === "curl") return `# Set GATEWAY_ACCESS_TOKEN to the signed-in user’s Gateway.Invoke access token.
curl --silent --show-error --fail-with-body ${url} \\
  --header "Authorization: Bearer \${GATEWAY_ACCESS_TOKEN:?Gateway.Invoke access token required}"`;
  if (language === "nextjs") return `import "server-only";

// Resolve accessToken for the signed-in user before calling this function.
export async function readGraphProfile(accessToken: string) {
  const response = await fetch(${url}, {
    headers: { Authorization: "Bearer " + accessToken },
    cache: "no-store",
    redirect: "error",
  });
  if (!response.ok) throw new Error("Gateway HTTP " + response.status);
  return response.json();
}`;
  if (language === "fastapi") return `import httpx

# Call with a Gateway.Invoke access token for the signed-in user.
async def read_graph_profile(access_token: str):
    async with httpx.AsyncClient(follow_redirects=False) as client:
        response = await client.get(${url}, headers={"Authorization": "Bearer " + access_token})
    response.raise_for_status()
    return response.json()`;
  if (language === "php") return `<?php
function readGraphProfile(string $accessToken): array {
    $request = curl_init(${url});
    curl_setopt_array($request, [
        CURLOPT_HTTPHEADER => ["Authorization: Bearer " . $accessToken],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => false,
    ]);
    $body = curl_exec($request);
    $status = curl_getinfo($request, CURLINFO_RESPONSE_CODE);
    if ($body === false || $status !== 200) throw new RuntimeException("Gateway HTTP " . $status);
    return json_decode($body, true, 512, JSON_THROW_ON_ERROR);
}`;
  return `# Custom Tool or authenticated BFF
GET ${endpoint}
Authorization: Bearer <SIGNED_IN_USERS_GATEWAY_INVOKE_ACCESS_TOKEN>

# Resolve the token for each signed-in user on your backend.`;
}

/** Gatewayに送信する固定GETの構造です。 */
export const graphToolRequestContract = {
  method: "GET", path: GRAPH_TOOL_PATH,
  upstream: `/me?$select=${GRAPH_TOOL_PROFILE_SELECT}`,
  headers: { Authorization: "Bearer <GATEWAY_ACCESS_TOKEN>" },
  body: null,
};

/** Graphプロフィール成功応答の、公開項目だけを示すJSON Schemaです。 */
export const graphToolSuccessContract = {
  type: "object", required: ["id"], additionalProperties: false, properties: {
    "@odata.context": { type: "string" }, id: { type: "string", description: "Entra object ID" },
    displayName: { type: ["string", "null"], description: "Display name" },
    givenName: { type: ["string", "null"], description: "Given name" },
    surname: { type: ["string", "null"], description: "Surname" },
    userPrincipalName: { type: ["string", "null"], description: "User principal name (UPN)" },
    jobTitle: { type: ["string", "null"], description: "Job title" },
    companyName: { type: ["string", "null"], description: "Company name" },
    department: { type: ["string", "null"], description: "Department" },
    employeeId: { type: ["string", "null"], description: "Employee ID" },
    officeLocation: { type: ["string", "null"], description: "Office location" },
    mail: { type: ["string", "null"], description: "Email address; may differ from UPN" },
    accountEnabled: { type: ["boolean", "null"], description: "Whether the account is enabled; null or omission is not false" },
  },
};
