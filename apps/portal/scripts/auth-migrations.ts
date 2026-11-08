import { readdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { sql, type Kysely } from "kysely";
import { ZodError } from "zod";

/** 正式ジョブのDDL前に、秘密やDB名を含まない初期状態を取得します。 */
export async function observeAuthSchemaBeforeMigration(database: Kysely<Record<string, unknown>>) {
  const row = (await sql<{ userTableCount: number | null; migrationTablePresent: number }>`
    SELECT CASE WHEN HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','VIEW DEFINITION')=1
      THEN (SELECT COUNT(*) FROM sys.tables WHERE is_ms_shipped=0) ELSE NULL END AS userTableCount,
      CASE WHEN OBJECT_ID(N'dbo.portalSchemaVersions',N'U') IS NULL THEN 0 ELSE 1 END AS migrationTablePresent
  `.execute(database)).rows[0];
  if (!row || !Number.isSafeInteger(row.userTableCount) || row.userTableCount === null || row.userTableCount < 0
    || ![0, 1].includes(row.migrationTablePresent)) throw new Error("Initial schema metadata could not be verified");
  return { stage: "before-migration", userTableCount: row.userTableCount, migrationTablePresent: row.migrationTablePresent === 1 };
}

/** 順序付きのSQLファイルを一つずつ適用し、途中失敗をロールバックします。 */
export async function migrateAuth(database: Kysely<Record<string, unknown>>) {
  const folder = fileURLToPath(new URL("../sql/", import.meta.url));
  const files = (await readdir(folder)).filter((name) => /^\d{3}_[a-z0-9_]+\.sql$/.test(name)).sort();
  if (!files.length) throw new Error("Portal SQL migrations are missing");
  for (const file of files) {
    const source = await readFile(`${folder}/${file}`, "utf8");
    const batches = source.split(/^\s*GO\s*$/im).filter((batch) => batch.trim());
    await database.transaction().execute(async (transaction) => {
      await sql`SET XACT_ABORT ON`.execute(transaction);
      // 同時配備でDDLが競合しないよう、現在のDB内で初期化を直列化します。
      await sql`DECLARE @result int;
        EXEC @result = sp_getapplock @Resource=N'portal-auth-schema', @LockMode='Exclusive',
          @LockOwner='Transaction', @LockTimeout=20000;
        IF @result < 0 THROW 51000, 'Portal migration lock unavailable', 1;`.execute(transaction);
      for (const batch of batches) await sql.raw(batch).execute(transaction);
    });
  }
  return files.length;
}

/** SQL診断には番号だけを表示し、接続先・SQL本文・資格情報は表示しません。 */
export function sqlFailure(error: unknown): string {
  if (error instanceof ZodError && error.issues.some((issue) => issue.path.includes("groupNamePattern"))) {
    return "Invalid portalApiKeys.groupNamePattern: specify a non-empty RE2-compatible regular expression";
  }
  if (error && typeof error === "object" && "number" in error && typeof error.number === "number") {
    return `SQL error ${error.number}`;
  }
  return "SQL connection or initialization failed";
}
