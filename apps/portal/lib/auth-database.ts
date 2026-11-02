import "server-only";

import { AzureCliCredential, ManagedIdentityCredential } from "@azure/identity";
import { MssqlDialect } from "kysely";
import * as Tarn from "tarn";
import * as Tedious from "tedious";
import { getSqlEnvironment, type SqlEnvironment } from "./auth-environment.ts";

/** Better Authが使うSQL Server接続設定です。初回リクエストまで生成しません。 */
let database: { dialect: MssqlDialect; type: "mssql" } | undefined;

/**
 * SQL Server接続プールを生成します。Azure用トークンは接続の作成時に取得します。
 * @returns Better Auth標準のKysely SQL Serverアダプターへ渡す設定。
 */
export function getAuthDatabase(): { dialect: MssqlDialect; type: "mssql" } {
  if (database) return database;
  database = createAuthDatabase(getSqlEnvironment());
  return database;
}

/** 初期化コマンドでも使えるよう、指定された接続先だけの独立したプールを作ります。 */
export function createAuthDatabase(settings: SqlEnvironment): { dialect: MssqlDialect; type: "mssql" } {
  const credential = settings.mode === "azure-cli"
    ? new AzureCliCredential({ tenantId: settings.tenantId, processTimeoutInMs: 15_000 })
    : settings.mode === "managed-identity"
      ? new ManagedIdentityCredential({ clientId: settings.clientId })
      : null;
  const dialect = new MssqlDialect({
    tarn: { ...Tarn, options: { min: 0, max: 5, acquireTimeoutMillis: 20_000, createTimeoutMillis: 20_000 } },
    tedious: {
      ...Tedious,
      TYPES: { ...Tedious.TYPES, DateTime: Tedious.TYPES.DateTime2 },
      connectionFactory: async () => {
        const access = credential ? await credential.getToken("https://database.windows.net/.default") : null;
        if (credential && !access) throw new Error("Azure SQL access token was not issued");
        return new Tedious.Connection({
          server: settings.host,
          authentication: access
            ? { type: "azure-active-directory-access-token", options: { token: access.token } }
            : { type: "default", options: { userName: settings.user, password: settings.password } },
          options: {
            database: settings.database,
            port: settings.port,
            serverName: settings.mode === "local-sql" ? "localhost" : settings.server,
            encrypt: true,
            trustServerCertificate: settings.mode === "local-sql" && settings.trustLocalCertificate,
            connectTimeout: 15_000,
            requestTimeout: 20_000,
            appName: "egakumi-portal",
          },
        });
      },
    },
  });
  return { dialect, type: "mssql" };
}
