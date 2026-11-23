import "server-only";
import { createHash, randomUUID } from "node:crypto";
import { AzureCliCredential, ManagedIdentityCredential } from "@azure/identity";
import { CryptographyClient } from "@azure/keyvault-keys";

/** 認証EntraとAzure署名主体を分離し、鍵と公開証明書の版を固定します。 */
export type CertificateAuthentication = {
  keyId: string;
  thumbprint: string;
  mode: "azure-cli" | "managed-identity";
  azureTenantId?: string;
  identityClientId?: string;
};

/** ダイジェストだけを受け取り、非exportable RSA鍵で署名する依存です。 */
export type AssertionSigner = (digest: Uint8Array) => Promise<Uint8Array>;

/** 設定値を例外へ含めず、版未固定の鍵や対話認証へのfallbackを拒否します。 */
export function validateCertificateAuthentication(value: CertificateAuthentication): CertificateAuthentication {
  if (!/^https:\/\/[a-z0-9-]+\.vault\.azure\.net\/keys\/[a-z0-9-]+\/[a-f0-9]{32}$/i.test(value.keyId)
    || !/^[a-f0-9]{40}$/i.test(value.thumbprint)
    || !["azure-cli", "managed-identity"].includes(value.mode)
    || (value.mode === "managed-identity" && !/^[a-f0-9-]{36}$/i.test(value.identityClientId ?? ""))
    || (value.mode === "azure-cli" && (process.env.NODE_ENV === "production" || !/^[a-f0-9-]{36}$/i.test(value.azureTenantId ?? "")))) {
    throw new Error("Portal certificate authentication configuration is invalid");
  }
  return value;
}

/** Azure CLIはAzure側KVへの認可だけに使い、認証Entraの資格情報として転用しません。 */
export function createKeyVaultSigner(settings: CertificateAuthentication): AssertionSigner {
  validateCertificateAuthentication(settings);
  const credential = settings.mode === "managed-identity"
    ? new ManagedIdentityCredential({ clientId: settings.identityClientId! })
    : new AzureCliCredential({ tenantId: settings.azureTenantId! });
  const client = new CryptographyClient(settings.keyId, credential, { retryOptions: { maxRetries: 0 } });
  return async (digest) => {
    try {
      const result = await client.sign("RS256", digest);
      if (result.keyID !== settings.keyId || !result.result?.length) throw new Error();
      return result.result;
    } catch { throw new Error("Portal certificate signing unavailable"); }
  };
}

/** 要求ごとに短命・一意のclient assertionを作り、JWT本文ではなくSHA-256をKVへ渡します。 */
export function createClientAssertion(tenantId: string, clientId: string, certificate: CertificateAuthentication,
  signer: AssertionSigner = createKeyVaultSigner(certificate), now: () => number = Date.now): () => Promise<string> {
  validateCertificateAuthentication(certificate);
  if (![tenantId, clientId].every((id) => /^[a-f0-9-]{36}$/i.test(id))) throw new Error("Portal assertion identity is invalid");
  return async () => {
    const seconds = Math.floor(now() / 1000);
    const header = Buffer.from(JSON.stringify({ alg: "RS256", typ: "JWT", x5t: Buffer.from(certificate.thumbprint, "hex").toString("base64url") })).toString("base64url");
    const payload = Buffer.from(JSON.stringify({ iss: clientId, sub: clientId,
      aud: `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/token`, jti: randomUUID(),
      iat: seconds, nbf: seconds - 30, exp: seconds + 300 })).toString("base64url");
    const content = `${header}.${payload}`;
    try { return `${content}.${Buffer.from(await signer(createHash("sha256").update(content).digest())).toString("base64url")}`; }
    catch { throw new Error("Portal certificate signing unavailable"); }
  };
}

/** 定期Graph jobも同じ固定鍵参照と署名主体を読みます。 */
export function readCertificateAuthentication(environment: Readonly<Record<string, string | undefined>> = process.env): CertificateAuthentication | undefined {
  if (!environment.PORTAL_AUTH_KEY_ID && !environment.PORTAL_AUTH_CERTIFICATE_THUMBPRINT) return undefined;
  return validateCertificateAuthentication({ keyId: environment.PORTAL_AUTH_KEY_ID ?? "",
    thumbprint: environment.PORTAL_AUTH_CERTIFICATE_THUMBPRINT ?? "",
    mode: (environment.PORTAL_AUTH_SIGNING_MODE ?? "managed-identity") as CertificateAuthentication["mode"],
    azureTenantId: environment.AZURE_TENANT_ID, identityClientId: environment.PORTAL_AUTH_IDENTITY_CLIENT_ID });
}
