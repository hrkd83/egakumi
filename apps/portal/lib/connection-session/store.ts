import "server-only";

import { randomUUID } from "node:crypto";
import { Kysely, sql } from "kysely";
import { createAuthDatabase } from "../auth-database.ts";
import { getAuthEnvironment, getSqlEnvironment } from "../auth-environment.ts";
import { ConnectionSessionError, type PrivateState, type SharedStateStore, type StateTransaction } from "./client.ts";
import { decryptConnectionState, encryptConnectionState } from "./crypto.ts";

type Database = Kysely<Record<string, unknown>>;
type StateRow = { revision: string | number; stateCiphertext: Uint8Array | null };

/** SQLや暗号処理の診断に、接続先・証明・クエリを混入させません。 */
async function safe<T>(operation: () => Promise<T>): Promise<T> {
  try { return await operation(); }
  catch (error) {
    if (error instanceof ConnectionSessionError) throw error;
    throw new ConnectionSessionError("SessionStoreUnavailable", 503);
  }
}

/** サーバーだけが作成した本人・購読・接続先のハッシュを索引型と揃えます。 */
const dbBinding = (binding: string) => sql`CAST(${binding} AS char(64))`;
/** リースを取得したワーカーを、SQL接続やプロセスの寿命から独立して識別します。 */
const dbLease = (token: string) => sql`CAST(${token} AS uniqueidentifier)`;

/**
 * 暗号化状態をSQLへ即時保存し、短い期限付きの行リースで複数ホスト間の処理を直列化します。
 * @remarks コールバック全体をSQLトランザクションへ入れません。更新候補は外部HTTPの確定要求より先に永続化されます。
 */
export class SqlConnectionSessionStore implements SharedStateStore {
  private readonly leaseSeconds: number;
  private readonly database: Database;
  private readonly secret: string;

  /** @param secret Better Authの既存サーバー専用鍵。用途別の鍵をHKDFで導出します。 */
  constructor(database: Database, secret: string, options: { leaseSeconds?: number } = {}) {
    this.database = database;
    this.secret = secret;
    this.leaseSeconds = options.leaseSeconds ?? 180;
    if (!Number.isInteger(this.leaseSeconds) || this.leaseSeconds < 1 || this.leaseSeconds > 300 || secret.length < 32) {
      throw new ConnectionSessionError("SessionStoreConfiguration", 503);
    }
  }

  /** 配備後の準備確認でスキーマと読取権限だけを検査し、本人セッションには触れません。 */
  async assertReady(): Promise<void> {
    await safe(async () => {
      const versions = await sql<{ version: number }>`SELECT [version] FROM dbo.portalSchemaVersions WHERE [version]=10`.execute(this.database);
      if (!versions.rows.length) throw new ConnectionSessionError("SessionStoreUnavailable", 503);
      await sql`SELECT TOP(0) bindingHash,revision,stateCiphertext,leaseToken,leaseUntil,updatedAt FROM dbo.portalConnectionState`.execute(this.database);
    });
  }

  /** SQL時刻を基準にリースだけを短いトランザクションで取得します。 */
  private async acquire(binding: string, token: string): Promise<void> {
    await this.database.transaction().setIsolationLevel("serializable").execute(async (transaction) => {
      const result = await sql<{ available: number }>`SELECT
        CASE WHEN leaseToken IS NULL OR leaseUntil <= SYSUTCDATETIME() THEN 1 ELSE 0 END AS available
        FROM dbo.portalConnectionState WITH (UPDLOCK,HOLDLOCK)
        WHERE bindingHash=${dbBinding(binding)}`.execute(transaction);
      if (!result.rows.length) {
        await sql`INSERT dbo.portalConnectionState(bindingHash,revision,stateCiphertext,leaseToken,leaseUntil)
          VALUES(${dbBinding(binding)},0,NULL,${dbLease(token)},DATEADD(second,${this.leaseSeconds},SYSUTCDATETIME()))`.execute(transaction);
      } else {
        if (result.rows[0].available !== 1) throw new ConnectionSessionError("SessionStoreBusy", 409);
        await sql`UPDATE dbo.portalConnectionState SET leaseToken=${dbLease(token)},
          leaseUntil=DATEADD(second,${this.leaseSeconds},SYSUTCDATETIME()),updatedAt=SYSUTCDATETIME()
          WHERE bindingHash=${dbBinding(binding)}`.execute(transaction);
      }
    });
  }

  /** 期限切れワーカーの読み書きは、新しい取得者がまだいない場合も拒否します。 */
  private async readRow(binding: string, token: string): Promise<StateRow> {
    const result = await sql<StateRow>`SELECT revision,stateCiphertext FROM dbo.portalConnectionState
      WHERE bindingHash=${dbBinding(binding)} AND leaseToken=${dbLease(token)} AND leaseUntil > SYSUTCDATETIME()`.execute(this.database);
    if (!result.rows.length) throw new ConnectionSessionError("SessionStoreLeaseLost", 409);
    return result.rows[0];
  }

  /** 版数と暗号文内の版数を一致させ、別行から移された状態を採用しません。 */
  private decode(binding: string, row: StateRow): PrivateState | undefined {
    const revision = Number(row.revision);
    if (!Number.isSafeInteger(revision) || revision < 0) throw new ConnectionSessionError("SessionStateInvalid", 503);
    if (row.stateCiphertext === null && revision === 0) return undefined;
    if (!row.stateCiphertext || revision < 1) throw new ConnectionSessionError("SessionStateInvalid", 503);
    const state = decryptConnectionState(binding, row.stateCiphertext, this.secret);
    if (state.revision !== revision) throw new ConnectionSessionError("SessionStateInvalid", 503);
    return state;
  }

  /** 同時利用は待ち続けず、呼び出し側が短い間隔で再確認できる固定エラーを返します。 */
  async withExclusive<T>(binding: string, action: (transaction: StateTransaction) => Promise<T>): Promise<T> {
    if (!/^[a-f0-9]{64}$/.test(binding)) throw new ConnectionSessionError("SessionStateInvalid", 503);
    return safe(async () => {
      const token = randomUUID();
      await this.acquire(binding, token);
      let callbackFailed = false;
      try {
        const transaction: StateTransaction = {
          read: () => safe(async () => this.decode(binding, await this.readRow(binding, token))),
          save: (expectedRevision, next) => safe(async () => {
            if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 0 || next.revision !== expectedRevision + 1) {
              throw new ConnectionSessionError("SessionStoreConflict", 409);
            }
            const encrypted = encryptConnectionState(binding, next, this.secret);
            const saved = await sql<{ revision: number }>`UPDATE dbo.portalConnectionState
              SET revision=${next.revision},stateCiphertext=${encrypted},updatedAt=SYSUTCDATETIME()
              OUTPUT inserted.revision WHERE bindingHash=${dbBinding(binding)} AND revision=${expectedRevision}
              AND leaseToken=${dbLease(token)} AND leaseUntil > SYSUTCDATETIME()`.execute(this.database);
            if (!saved.rows.length) {
              await this.readRow(binding, token);
              throw new ConnectionSessionError("SessionStoreConflict", 409);
            }
          }),
        };
        return await action(transaction);
      } catch (error) {
        callbackFailed = true;
        throw error;
      } finally {
        try {
          // 他ワーカーが取得済みなら何も変更せず、接続プールへ所有ロックも残しません。
          await sql`UPDATE dbo.portalConnectionState SET leaseToken=NULL,leaseUntil=NULL
            WHERE bindingHash=${dbBinding(binding)} AND leaseToken=${dbLease(token)}`.execute(this.database);
        } catch {
          if (!callbackFailed) throw new ConnectionSessionError("SessionStoreUnavailable", 503);
        }
      }
    });
  }
}

let store: SqlConnectionSessionStore | undefined;

/** 初回のサーバー要求で既存Portal DBと認証鍵を使い、メモリー内保存へは代替しません。 */
export function createConnectionSessionStore(): SqlConnectionSessionStore {
  if (!store) store = new SqlConnectionSessionStore(new Kysely(createAuthDatabase(getSqlEnvironment())), getAuthEnvironment().secret);
  return store;
}

/** ヘルス確認専用。読み取りだけで接続保存先の配備と実行権限を確認します。 */
export async function assertConnectionSessionStoreReady(): Promise<void> {
  await createConnectionSessionStore().assertReady();
}
