import "server-only";

import { z } from "zod";

/** ログインを受け付けるURLと、サーバーだけで使うEntra・Cookie署名の設定です。 */
const authEnvironmentSchema = z.object({
  baseURL: z.url(),
  secret: z.string().min(32),
  microsoftClientId: z.uuid().transform((value) => value.toLowerCase()),
  microsoftClientSecret: z.string().min(1),
  microsoftTenantId: z.uuid().transform((value) => value.toLowerCase()),
});

/** Azureの実行ID認証と、手元専用のSQL Server認証を区別します。 */
const sqlEnvironmentSchema = z.object({
  server: z.string().min(1),
  host: z.string().min(1),
  port: z.coerce.number().int().min(1).max(65535),
  database: z.string().regex(/^[A-Za-z][A-Za-z0-9_-]{0,127}$/),
  mode: z.enum(["azure-cli", "managed-identity", "local-sql"]),
  tenantId: z.uuid().optional(),
  clientId: z.uuid().optional(),
  user: z.string().min(1).optional(),
  password: z.string().min(1).optional(),
  trustLocalCertificate: z.boolean(),
});

/** 検証済みのログイン設定です。秘密値はブラウザーへ渡しません。 */
export type AuthEnvironment = z.infer<typeof authEnvironmentSchema>;
/** 接続方式を検証済みのSQL設定です。 */
export type SqlEnvironment = z.infer<typeof sqlEnvironmentSchema>;

/** 設定名だけを通知し、例外やログに入力値・シークレットを混入させません。 */
function parseEnvironment<T>(schema: z.ZodType<T>, values: unknown, label: string): T {
  const parsed = schema.safeParse(values);
  if (!parsed.success) {
    const fields = [...new Set(parsed.error.issues.map((issue) => issue.path.join(".")))];
    throw new Error(`${label} configuration is incomplete: ${fields.join(", ")}`);
  }
  return parsed.data;
}

/**
 * 実際のリクエスト時にログイン設定を読みます。ビルド時には秘密情報を要求しません。
 * @returns 許可した公開URL、署名鍵、Entraアプリの設定。
 */
export function getAuthEnvironment(): AuthEnvironment {
  const settings = parseEnvironment(authEnvironmentSchema, {
    baseURL: process.env.BETTER_AUTH_URL,
    secret: process.env.BETTER_AUTH_SECRET,
    microsoftClientId: process.env.MICROSOFT_CLIENT_ID,
    microsoftClientSecret: process.env.MICROSOFT_CLIENT_SECRET,
    microsoftTenantId: process.env.MICROSOFT_TENANT_ID,
  }, "Portal authentication");
  const url = new URL(settings.baseURL);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.username || url.password || url.search || url.hash || url.pathname !== "/"
      || (url.protocol !== "https:" && !(url.protocol === "http:" && local && process.env.NODE_ENV !== "production"))) {
    throw new Error("BETTER_AUTH_URL must be an HTTPS origin, or a local HTTP origin during development");
  }
  return { ...settings, baseURL: url.origin };
}

/**
 * SQLの接続先を検証します。AzureではEntra認証と証明書検証を必須にします。
 * @remarks SQLパスワードと自己署名証明書の許容は、開発中のループバック接続だけです。
 */
export function getSqlEnvironment(): SqlEnvironment {
  const settings = parseEnvironment(sqlEnvironmentSchema, {
    server: process.env.PORTAL_SQL_SERVER,
    host: process.env.PORTAL_SQL_HOST ?? process.env.PORTAL_SQL_SERVER,
    port: process.env.PORTAL_SQL_PORT ?? "1433",
    database: process.env.PORTAL_SQL_DATABASE,
    mode: process.env.PORTAL_SQL_AUTH ?? (process.env.NODE_ENV === "production" ? "managed-identity" : "azure-cli"),
    tenantId: process.env.AZURE_TENANT_ID,
    clientId: process.env.AZURE_CLIENT_ID,
    user: process.env.PORTAL_SQL_USER,
    password: process.env.PORTAL_SQL_PASSWORD,
    trustLocalCertificate: process.env.PORTAL_SQL_TRUST_LOCAL_CERTIFICATE === "true",
  }, "Portal SQL");
  const loopback = ["localhost", "127.0.0.1", "::1"].includes(settings.host);
  if (settings.mode === "local-sql") {
    if (process.env.NODE_ENV === "production" || !loopback || !settings.user || !settings.password) {
      throw new Error("Local SQL authentication requires a loopback development server and local credentials");
    }
  } else {
    if (!/^[a-z0-9-]+\.database\.windows\.net$/i.test(settings.server)
        || settings.trustLocalCertificate || settings.user || settings.password
        || (settings.mode === "azure-cli" && !settings.tenantId)
        || (settings.mode === "managed-identity" && !settings.clientId)) {
      throw new Error("Azure SQL requires its service hostname, verified TLS, and an explicit Entra identity");
    }
  }
  return settings;
}
