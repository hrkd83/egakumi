import { readdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { sql, type Kysely } from "kysely";
import { ZodError } from "zod";

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
