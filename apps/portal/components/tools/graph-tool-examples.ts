import { GRAPH_TOOL_PATH, GRAPH_TOOL_PROFILE_SELECT } from "../../lib/graph-tool-contracts.ts";
import type { GraphToolViewState } from "./graph-tool-store.ts";

/** 設定済みの公開URLを使い、秘密値を含まない組み込み例を作ります。 */
export function getGraphToolExample(language: GraphToolViewState["language"], gatewayOrigin: string | null): string {
  const origin = gatewayOrigin ?? "https://gateway.example.com";
  if (language === "curl") return `# Backend only. API_KEY and CONNECTION_SESSION are stored securely.
curl --silent --show-error --include '${origin}${GRAPH_TOOL_PATH}' \\
  --header "api-key: \${API_KEY}" \\
  --header "X-Core-Connection-Session: \${CONNECTION_SESSION}"

# On first use, omit X-Core-Connection-Session.
# On 409 ConnectionRequired: save authentication on your backend,
# display only authentication.loginUrl, then poll from your backend:
curl --silent --show-error '${origin}/auth/connections/status?transactionId=<TRANSACTION_ID>' \\
  --header "api-key: \${API_KEY}" \\
  --header "X-Core-Connection-Poll: \${POLL_SECRET}"

# Compare tenantId / ownerId with your application's verified user.
# Status returns metadata, not a connectionSession.
# Use the provisionalConnectionSession saved from the initial response.
# Then retry GET ${GRAPH_TOOL_PATH}.`;
  if (language === "nextjs") return `import "server-only";
import { ConnectionSessionClient } from "./connection-session";

// app/api/graph/me/route.ts
export async function GET() {
  const principal = await requireVerifiedPrincipal();
  const key = await authorizedSubscriptionFor(principal);
  const client = new ConnectionSessionClient({
    origin: "${origin}", apiKey: key.primaryKey,
    subscriptionId: key.id, profile: "graph-delegated",
    principal: { tenantId: principal.tenantId, ownerId: principal.objectId },
    store: encryptedSharedConnectionStore,
  });
  const result = await client.get("${GRAPH_TOOL_PATH}");
  if (result.kind === "login") {
    return Response.json({ status: "need_login", loginUrl: result.loginUrl },
      { status: 409, headers: { "Cache-Control": "no-store" } });
  }
  return result.response; // Session update headers are removed by the client.
}

// Your authenticated status route calls client.poll() with the same binding.
// Send only pending/complete to the UI. Retry this read after completion.
// Source: apps/portal/lib/connection-session/client.ts
// SDK entry: clients/connection-session/typescript/connection-session.ts`;
  if (language === "fastapi") return `from fastapi import Depends
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from credential_client import CredentialClient, LoginRequired

@app.get("/api/graph/me")
async def graph_me(principal=Depends(require_verified_principal)):
    key = await authorized_subscription_for(principal)
    client = CredentialClient(
        "${origin}", key.primary_key,
        subscription_id=key.id,
        expected_subject={"tenantId": principal.tenant_id, "ownerId": principal.object_id},
        store=encrypted_shared_store_for(principal, key.id, "graph-delegated"),
    )
    result = await run_in_threadpool(client.get_api, "${GRAPH_TOOL_PATH}")
    if isinstance(result, LoginRequired):
        return JSONResponse({"status": "need_login", "loginUrl": result.login_url},
            status_code=409, headers={"Cache-Control": "no-store"})
    return result

# Your authenticated status endpoint uses the same store and client.status().
# Verify the completed identity before allowing the Graph read to resume.
# Reference: scripts/credential_client.py; use a shared DB store in production.`;
  if (language === "php") return `<?php
// Server-side integration outline; implement these application boundaries.
$principal = requireVerifiedPrincipal();
$key = authorizedSubscriptionFor($principal);
$binding = [
    'tenantId' => $principal->tenantId,
    'ownerId' => $principal->objectId,
    'subscriptionId' => $key->id,
    'profile' => 'graph-delegated',
];

// Must implement the v2 contract with encrypted shared SQL storage + CAS.
$client = connectionSessionClient('${origin}', $key->primaryKey, $binding);
$result = $client->get('${GRAPH_TOOL_PATH}');
if ($result->needsLogin()) {
    // Provisional session and pollSecret stay on the server.
    sendJson(409, ['status' => 'need_login', 'loginUrl' => $result->loginUrl]);
} else {
    sendJson(200, $result->profile); // Do not forward session update headers.
}

// Status: check the saved transaction, verify tenantId/ownerId, then resume.
// Rotation: save candidate -> POST /auth/connections/session/commit -> save current.
// A failed commit must not replay a successful business request.
// Full contract: docs/reference/connection-session-clients.md`;
  return `# Dify -> authenticated application BFF -> Gateway -> Microsoft Graph
# BFF verifies a trusted SSO identity and owns per-user encrypted shared storage.

Tool request:
  GET https://<YOUR-APPLICATION-BFF>/api/graph/me
  Authorization: <APPLICATION-SPECIFIC-VERIFIED-CREDENTIAL>

If authentication is required:
  { "status": "need_login", "loginUrl": "https://<GATEWAY>/auth/connections/browser/launch#..." }

1. Show loginUrl to the user.
2. Your frontend checks the BFF authentication-status endpoint.
3. After completion, retry the read through the BFF.

# Never put a personal session or pollSecret in Tool output, traces,
# shared environment variables, or user-controlled workflow inputs.
# Automatic resume depends on your Dify frontend / custom Tool implementation.`;
}

/** Gatewayに送信する固定GETの構造です。HTTPヘッダーの値はすべてプレースホルダーです。 */
export const graphToolRequestContract = {
  method: "GET", path: GRAPH_TOOL_PATH,
  upstream: `/me?$select=${GRAPH_TOOL_PROFILE_SELECT}`,
  headers: { "api-key": "<SUBSCRIPTION_KEY>", "X-Core-Connection-Session": "<PERSONAL_CONNECTION_SESSION; omit on first use>" },
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

/** 初回409の契約例です。秘密に当たる項目は、実値を持たない説明用です。 */
export const graphToolAuthenticationContract = {
  error: { code: "ConnectionRequired" }, authentication: {
    protocolVersion: 2, status: "need_login", loginUrl: "https://<GATEWAY>/auth/connections/browser/launch#...",
    provisionalConnectionSession: "<SERVER_ONLY>", transactionId: "<SERVER_ONLY>", pollSecret: "<SERVER_ONLY>",
    statusUrl: "https://<GATEWAY>/auth/connections/status?transactionId=<TRANSACTION_ID>", expiresIn: 600,
  },
};
