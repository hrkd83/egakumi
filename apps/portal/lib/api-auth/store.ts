import "server-only";

import { Kysely, sql } from "kysely";
import { getAuthDatabase } from "../auth-database.ts";
import type { ApiAuthSession } from "./session.ts";
import { ApiAuthenticationError, sha256, type ApiAuthenticationSettings } from "./settings.ts";
import type { ApiAuthPurpose } from "./purpose.ts";

type Database = Kysely<Record<string, unknown>>;

/** 本人セッションと、現在の配備が許可するAPIの組を固定します。 */
export type ApiAuthBinding = ApiAuthSession & { hash: string };

/** 取引と資格情報を競合検査するためにSQLから読み出す欄です。 */
export type ApiAuthContext = {
  bindingHash: string;
  generation: number;
  revision: number;
  latestAttempt: string | null;
  tokenCiphertext: Uint8Array | null;
  nonceHash: string | null;
  accessExpiresAt: Date | null;
  connectionExpiresAt: Date | null;
  refreshBlocked: boolean;
  refreshLease: string | null;
  refreshLeaseUntil: Date | null;
  now: Date;
  sessionExpiresAt: Date;
};

/** OAuthの個別取引です。秘密部分は暗号文のまま読み取ります。 */
export type ApiAuthorizationTransaction = {
  attemptId: string;
  generation: number;
  bindingHash: string;
  requestCiphertext: Uint8Array;
  codeCiphertext: Uint8Array | null;
};

/** AADとSQLの確認に使う、本人・設定へ結び付いた索引を作ります。 */
export function bindApiAuthSession(session: ApiAuthSession, settings: ApiAuthenticationSettings): ApiAuthBinding {
  const hash = sha256(JSON.stringify([1, session.sessionId, session.tenantId, session.objectId,
    settings.auth.microsoftClientId, settings.scope, settings.gatewayOrigin]));
  return { ...session, hash };
}

const dbId = (value: string) => sql`CAST(${value} AS varchar(36))`;
const dbHash = (value: string) => sql`CAST(${value} AS char(64))`;
const dbGuid = (value: string) => sql`CAST(${value} AS uniqueidentifier)`;

/** SQL上のPortalログインを確認し、期限または本人の変更を許可しません。 */
function activeSession(binding: ApiAuthBinding) {
  return sql`SELECT 1 FROM dbo.portalSession s INNER JOIN dbo.portalUser u ON u.id=s.userId
    WHERE s.id=${dbId(binding.sessionId)} AND s.expiresAt>SYSUTCDATETIME()
      AND u.entraTenantId=${dbId(binding.tenantId)} AND u.entraObjectId=${dbId(binding.objectId)}`;
}

/** SQL時刻・世代・版・所有リースを使う、複数Portalインスタンス共通の認証保存です。 */
export class ApiAuthStore {
  private readonly database: Database;
  private readonly contextTable;
  private readonly transactionTable;
  private readonly forGateway: boolean;

  constructor(database: Database, purpose: ApiAuthPurpose = "gateway") {
    this.database = database;
    this.forGateway = purpose === "gateway";
    this.contextTable = sql.raw(this.forGateway ? "dbo.portalApiAuthContext" : "dbo.portalSubscriptionAuthContext");
    this.transactionTable = sql.raw(this.forGateway ? "dbo.portalApiAuthorizationTransaction" : "dbo.portalSubscriptionAuthorizationTransaction");
  }

  /** SQLの追加マイグレーションと実行権限を照合します。 */
  async assertReady(): Promise<void> {
    const { rows } = await sql`SELECT [version] FROM dbo.portalSchemaVersions WHERE [version]=${this.forGateway ? 11 : 12}`.execute(this.database);
    if (!rows.length) throw new ApiAuthenticationError("ApiAuthenticationUnavailable", 503);
    await sql`SELECT TOP(0) sessionId,generation,revision FROM ${this.contextTable}`.execute(this.database);
    await sql`SELECT TOP(0) attemptId,requestCiphertext FROM ${this.transactionTable}`.execute(this.database);
    if (!this.forGateway) return;
    await sql`SELECT TOP(0) operationId,operationCiphertext FROM dbo.portalApiActiveOperation`.execute(this.database);
    await sql`SELECT TOP(0) operationHash,snapshotCiphertext FROM dbo.portalApiCallCleanup`.execute(this.database);
  }

  /** 本人の現在の接続とSQL時刻を読み取ります。 */
  async context(binding: ApiAuthBinding): Promise<ApiAuthContext | undefined> {
    const { rows } = await sql<ApiAuthContext>`SELECT c.*,SYSUTCDATETIME() AS now,s.expiresAt AS sessionExpiresAt
      FROM ${this.contextTable} c INNER JOIN dbo.portalSession s ON s.id=c.sessionId
      WHERE c.sessionId=${dbId(binding.sessionId)} AND c.bindingHash=${dbHash(binding.hash)}
        AND EXISTS(${activeSession(binding)})`.execute(this.database);
    return rows[0];
  }

  /** 現在の資格情報を保ったまま、最新の認証試行を直列に記録します。 */
  async begin(binding: ApiAuthBinding, attempt: string): Promise<number> {
    return this.database.transaction().execute(async (transaction) => {
      const { rows } = await sql`SELECT 1 FROM dbo.portalSession WITH (UPDLOCK,HOLDLOCK)
        WHERE id=${dbId(binding.sessionId)} AND EXISTS(${activeSession(binding)})`.execute(transaction);
      if (!rows.length) throw new ApiAuthenticationError("ApiAuthenticationRequired", 401);
      const existing = await sql<{ bindingHash: string; generation: number }>`SELECT bindingHash,generation
        FROM ${this.contextTable} WITH (UPDLOCK,HOLDLOCK) WHERE sessionId=${dbId(binding.sessionId)}`.execute(transaction);
      const current = existing.rows[0];
      if (!current) {
        await sql`INSERT ${this.contextTable}(sessionId,bindingHash,latestAttempt)
          VALUES(${dbId(binding.sessionId)},${dbHash(binding.hash)},${dbGuid(attempt)})`.execute(transaction);
        return 0;
      }
      await sql`DELETE FROM ${this.transactionTable} WHERE sessionId=${dbId(binding.sessionId)}`.execute(transaction);
      if (current.bindingHash !== binding.hash) {
        await sql`UPDATE ${this.contextTable} SET bindingHash=${dbHash(binding.hash)},generation=generation+1,
          revision=0,latestAttempt=${dbGuid(attempt)},tokenCiphertext=NULL,nonceHash=NULL,accessExpiresAt=NULL,
          connectionExpiresAt=NULL,refreshLease=NULL,refreshLeaseUntil=NULL,refreshBlocked=0
          WHERE sessionId=${dbId(binding.sessionId)}`.execute(transaction);
        return current.generation + 1;
      }
      await sql`UPDATE ${this.contextTable} SET latestAttempt=${dbGuid(attempt)}
        WHERE sessionId=${dbId(binding.sessionId)}`.execute(transaction);
      return current.generation;
    });
  }

  /** 現在の取引だけを10分間受け付けます。 */
  async putTransaction(binding: ApiAuthBinding, attempt: string, generation: number, state: string,
    correlation: string, ciphertext: Uint8Array): Promise<void> {
    const { rows } = await sql`INSERT ${this.transactionTable}(attemptId,sessionId,generation,stateHash,
      correlationHash,requestCiphertext,expiresAt) OUTPUT inserted.attemptId
      SELECT c.latestAttempt,c.sessionId,c.generation,${dbHash(state)},${dbHash(correlation)},${ciphertext},
        DATEADD(minute,10,SYSUTCDATETIME())
      FROM ${this.contextTable} c WITH (UPDLOCK)
      WHERE c.sessionId=${dbId(binding.sessionId)} AND c.bindingHash=${dbHash(binding.hash)}
        AND c.generation=${generation} AND c.latestAttempt=${dbGuid(attempt)} AND EXISTS(${activeSession(binding)})`.execute(this.database);
    if (!rows.length) throw new ApiAuthenticationError("ApiAuthenticationBusy", 409);
  }

  /** callbackの秘密の組から、まだ消費していない一件の取引だけを照合します。 */
  async pendingTransaction(state: string, correlation: string): Promise<ApiAuthorizationTransaction | undefined> {
    const { rows } = await sql<ApiAuthorizationTransaction>`SELECT t.attemptId,t.generation,c.bindingHash,t.requestCiphertext,t.codeCiphertext
      FROM ${this.transactionTable} t
      INNER JOIN ${this.contextTable} c ON c.sessionId=t.sessionId AND c.generation=t.generation AND c.latestAttempt=t.attemptId
      INNER JOIN dbo.portalSession s ON s.id=t.sessionId
      WHERE t.stateHash=${dbHash(state)} AND t.correlationHash=${dbHash(correlation)}
        AND t.codeCiphertext IS NULL AND t.expiresAt>SYSUTCDATETIME() AND s.expiresAt>SYSUTCDATETIME()`.execute(this.database);
    return rows[0];
  }

  /** コードを一度だけ登録し、サーバー内部の完了受付を最大60秒だけ許可します。 */
  async recordCode(attempt: string, state: string, correlation: string, ciphertext: Uint8Array): Promise<boolean> {
    const { rows } = await sql`UPDATE t SET codeCiphertext=${ciphertext},codeExpiresAt=DATEADD(second,60,SYSUTCDATETIME())
      OUTPUT inserted.attemptId FROM ${this.transactionTable} t
      INNER JOIN ${this.contextTable} c ON c.sessionId=t.sessionId AND c.generation=t.generation AND c.latestAttempt=t.attemptId
      INNER JOIN dbo.portalSession s ON s.id=t.sessionId
      WHERE t.attemptId=${dbGuid(attempt)} AND t.stateHash=${dbHash(state)} AND t.correlationHash=${dbHash(correlation)}
        AND t.codeCiphertext IS NULL AND t.expiresAt>SYSUTCDATETIME() AND s.expiresAt>SYSUTCDATETIME()`.execute(this.database);
    return rows.length === 1;
  }

  /** 認証取消時は取引だけを回収し、現在利用できるAPI接続は保持します。 */
  async cancel(attempt: string, state: string, correlation: string): Promise<void> {
    await sql`DELETE FROM ${this.transactionTable} WHERE attemptId=${dbGuid(attempt)}
      AND stateHash=${dbHash(state)} AND correlationHash=${dbHash(correlation)} AND codeCiphertext IS NULL`.execute(this.database);
  }

  /** 元のPortalセッションが戻った後にコードを一度消費します。交換結果の不明時も再送しません。 */
  async takeTransaction(binding: ApiAuthBinding, correlation: string): Promise<ApiAuthorizationTransaction | undefined> {
    const { rows } = await sql<Omit<ApiAuthorizationTransaction, "bindingHash">>`DELETE t
      OUTPUT deleted.attemptId,deleted.generation,deleted.requestCiphertext,deleted.codeCiphertext
      FROM ${this.transactionTable} t INNER JOIN ${this.contextTable} c
        ON c.sessionId=t.sessionId AND c.generation=t.generation AND c.latestAttempt=t.attemptId
      WHERE t.sessionId=${dbId(binding.sessionId)} AND c.bindingHash=${dbHash(binding.hash)}
        AND t.correlationHash=${dbHash(correlation)} AND t.codeExpiresAt>SYSUTCDATETIME()
        AND t.expiresAt>SYSUTCDATETIME() AND EXISTS(${activeSession(binding)})`.execute(this.database);
    return rows[0] && { ...rows[0], bindingHash: binding.hash };
  }

  /** 最新のOAuth交換だけを、読み取った世代・版に対して保存します。 */
  async saveAuthorization(binding: ApiAuthBinding, context: ApiAuthContext, attempt: string, ciphertext: Uint8Array,
    nonceHash: string, accessExpiresAt: Date, connectionExpiresAt: Date): Promise<boolean> {
    const { rows } = await sql`UPDATE ${this.contextTable} SET revision=revision+1,latestAttempt=NULL,
      tokenCiphertext=${ciphertext},nonceHash=${dbHash(nonceHash)},accessExpiresAt=${accessExpiresAt},
      connectionExpiresAt=${connectionExpiresAt},refreshLease=NULL,refreshLeaseUntil=NULL,refreshBlocked=0
      OUTPUT inserted.revision WHERE sessionId=${dbId(binding.sessionId)} AND bindingHash=${dbHash(binding.hash)}
        AND generation=${context.generation} AND revision=${context.revision} AND latestAttempt=${dbGuid(attempt)}
        AND ${accessExpiresAt}>SYSUTCDATETIME() AND ${connectionExpiresAt}>SYSUTCDATETIME()
        AND EXISTS(${activeSession(binding)})`.execute(this.database);
    return rows.length === 1;
  }

  /** refresh送信前にリースと再送防止を記録し、外部通信中はSQL接続を保持しません。 */
  async acquireRefresh(binding: ApiAuthBinding, context: ApiAuthContext, lease: string): Promise<Date | undefined> {
    const { rows } = await sql<{ now: Date }>`UPDATE ${this.contextTable} SET refreshLease=${dbGuid(lease)},
      refreshLeaseUntil=DATEADD(second,90,SYSUTCDATETIME()), refreshBlocked=1 OUTPUT SYSUTCDATETIME() AS now
      WHERE sessionId=${dbId(binding.sessionId)} AND bindingHash=${dbHash(binding.hash)}
        AND generation=${context.generation} AND revision=${context.revision}
        AND refreshBlocked=0 AND connectionExpiresAt>SYSUTCDATETIME() AND EXISTS(${activeSession(binding)})`.execute(this.database);
    return rows[0]?.now;
  }

  /** 成功したrefreshだけが新しい版へ進めます。失効や再認証後の遅延応答は破棄します。 */
  async saveRefresh(binding: ApiAuthBinding, context: ApiAuthContext, lease: string, ciphertext: Uint8Array,
    accessExpiresAt: Date): Promise<boolean> {
    const { rows } = await sql`UPDATE ${this.contextTable} SET revision=revision+1,tokenCiphertext=${ciphertext},
      accessExpiresAt=${accessExpiresAt},refreshLease=NULL,refreshLeaseUntil=NULL,refreshBlocked=0 OUTPUT inserted.revision
      WHERE sessionId=${dbId(binding.sessionId)} AND bindingHash=${dbHash(binding.hash)}
        AND generation=${context.generation} AND revision=${context.revision} AND refreshLease=${dbGuid(lease)}
        AND refreshLeaseUntil>SYSUTCDATETIME() AND connectionExpiresAt>SYSUTCDATETIME()
        AND ${accessExpiresAt}>SYSUTCDATETIME() AND EXISTS(${activeSession(binding)})`.execute(this.database);
    return rows.length === 1;
  }

  /** 明示的拒否では保存資格情報を消去し、結果不明の場合は元のaccess tokenだけを期限まで認めます。 */
  async finishFailedRefresh(binding: ApiAuthBinding, context: ApiAuthContext, lease: string, denied: boolean): Promise<void> {
    await sql`UPDATE ${this.contextTable} SET refreshLease=NULL,refreshLeaseUntil=NULL,
      tokenCiphertext=CASE WHEN ${denied ? 1 : 0}=1 THEN NULL ELSE tokenCiphertext END,
      nonceHash=CASE WHEN ${denied ? 1 : 0}=1 THEN NULL ELSE nonceHash END,
      accessExpiresAt=CASE WHEN ${denied ? 1 : 0}=1 THEN NULL ELSE accessExpiresAt END,
      connectionExpiresAt=CASE WHEN ${denied ? 1 : 0}=1 THEN NULL ELSE connectionExpiresAt END
      WHERE sessionId=${dbId(binding.sessionId)} AND bindingHash=${dbHash(binding.hash)}
        AND generation=${context.generation} AND revision=${context.revision} AND refreshLease=${dbGuid(lease)}`.execute(this.database);
  }

  /** 利用者の解除を世代の更新として保存し、未完了の取引も無効化します。 */
  async disconnect(binding: ApiAuthBinding): Promise<void> {
    await this.database.transaction().execute(async (transaction) => {
      await sql`UPDATE ${this.contextTable} SET generation=generation+1,revision=0,latestAttempt=NULL,
        tokenCiphertext=NULL,nonceHash=NULL,accessExpiresAt=NULL,connectionExpiresAt=NULL,
        refreshLease=NULL,refreshLeaseUntil=NULL,refreshBlocked=0
        WHERE sessionId=${dbId(binding.sessionId)} AND EXISTS(${activeSession(binding)})`.execute(transaction);
      await sql`DELETE FROM ${this.transactionTable}
        WHERE sessionId=${dbId(binding.sessionId)} AND EXISTS(${activeSession(binding)})`.execute(transaction);
    });
  }

  /** 操作の参照は、開始時の本人セッションとAPI認証世代へ束縛します。 */
  async putOperation(binding: ApiAuthBinding, generation: number, kind: "document" | "realtime", id: string,
    handleHash: string, ciphertext: Uint8Array, expiresAt: Date): Promise<boolean> {
    const { rows } = await sql`INSERT dbo.portalApiActiveOperation(operationId,sessionId,generation,operationKind,
        handleHash,operationCiphertext,expiresAt) OUTPUT inserted.operationId
      SELECT ${dbGuid(id)},c.sessionId,c.generation,${kind},${dbHash(handleHash)},${ciphertext},${expiresAt}
      FROM dbo.portalApiAuthContext c
      WHERE c.sessionId=${dbId(binding.sessionId)} AND c.bindingHash=${dbHash(binding.hash)}
        AND c.generation=${generation} AND c.connectionExpiresAt>SYSUTCDATETIME()
        AND ${expiresAt}>SYSUTCDATETIME() AND EXISTS(${activeSession(binding)})`.execute(this.database);
    return rows.length === 1;
  }

  /** SQLログインが失効した場合や、別方式・別セッションの参照は復元しません。 */
  async getOperation(binding: ApiAuthBinding, generation: number, kind: "document" | "realtime", id: string,
    handleHash: string): Promise<Uint8Array | undefined> {
    const { rows } = await sql<{ operationCiphertext: Uint8Array }>`
      SELECT o.operationCiphertext FROM dbo.portalApiActiveOperation o
      INNER JOIN dbo.portalApiAuthContext c ON c.sessionId=o.sessionId AND c.generation=o.generation
      WHERE o.operationId=${dbGuid(id)} AND o.operationKind=${kind} AND o.handleHash=${dbHash(handleHash)}
        AND c.sessionId=${dbId(binding.sessionId)} AND c.bindingHash=${dbHash(binding.hash)}
        AND c.generation=${generation} AND c.connectionExpiresAt>SYSUTCDATETIME() AND o.expiresAt>SYSUTCDATETIME()
        AND EXISTS(${activeSession(binding)})`.execute(this.database);
    return rows[0]?.operationCiphertext;
  }

  /** 停止専用snapshotと再送防止の記録を作成し、固定通話の送信所有者を一つに絞ります。 */
  async claimCallStop(operationHash: string, ciphertext: Uint8Array, tokenExpiresAt: Date,
    callExpiresAt: Date): Promise<{ ciphertext?: Uint8Array; expiresAt?: Date; confirmed?: boolean }> {
    return this.database.transaction().execute(async (transaction) => {
      const result = await sql<{ confirmed: boolean | null }>`
        SELECT confirmed FROM dbo.portalApiCallCleanup WITH (UPDLOCK,HOLDLOCK)
        WHERE operationHash=${dbHash(operationHash)}`.execute(transaction);
      if (result.rows[0]) return { confirmed: result.rows[0].confirmed === true };
      const { rows } = await sql<{ snapshotCiphertext: Uint8Array; snapshotExpiresAt: Date }>`
        INSERT dbo.portalApiCallCleanup(operationHash,snapshotCiphertext,snapshotExpiresAt,operationExpiresAt)
        OUTPUT inserted.snapshotCiphertext,inserted.snapshotExpiresAt
        SELECT ${dbHash(operationHash)},${ciphertext},
          LEAST(DATEADD(second,60,SYSUTCDATETIME()),${tokenExpiresAt},${callExpiresAt}),${callExpiresAt}
        WHERE ${tokenExpiresAt}>SYSUTCDATETIME() AND ${callExpiresAt}>SYSUTCDATETIME()`.execute(transaction);
      return { ciphertext: rows[0]?.snapshotCiphertext, expiresAt: rows[0]?.snapshotExpiresAt };
    });
  }

  /** 停止の成否を記録したら資格情報を直ちに消去し、送信記録だけを通話の期限まで保持します。 */
  async finishCallStop(operationHash: string, confirmed: boolean): Promise<void> {
    await sql`UPDATE dbo.portalApiCallCleanup SET confirmed=${confirmed ? 1 : 0},snapshotCiphertext=NULL
      WHERE operationHash=${dbHash(operationHash)} AND confirmed IS NULL`.execute(this.database);
  }

  /** 期限切れの暗号文を少量ずつ回収し、既存の世代番号は保持します。 */
  async collectExpired(): Promise<void> {
    await sql`DELETE TOP (64) FROM ${this.transactionTable}
      WHERE expiresAt<=SYSUTCDATETIME() OR codeExpiresAt<=SYSUTCDATETIME()`.execute(this.database);
    await sql`UPDATE TOP (64) ${this.contextTable} SET tokenCiphertext=NULL,nonceHash=NULL,
      accessExpiresAt=NULL,connectionExpiresAt=NULL,refreshLease=NULL,refreshLeaseUntil=NULL,refreshBlocked=0
      WHERE connectionExpiresAt<=SYSUTCDATETIME()`.execute(this.database);
    if (!this.forGateway) return;
    await sql`DELETE TOP (64) FROM dbo.portalApiActiveOperation WHERE expiresAt<=SYSUTCDATETIME()`.execute(this.database);
    await sql`UPDATE TOP (64) dbo.portalApiCallCleanup SET snapshotCiphertext=NULL
      WHERE snapshotExpiresAt<=SYSUTCDATETIME() AND snapshotCiphertext IS NOT NULL`.execute(this.database);
    await sql`DELETE TOP (64) FROM dbo.portalApiCallCleanup WHERE operationExpiresAt<=SYSUTCDATETIME()`.execute(this.database);
  }
}

const stores = new Map<ApiAuthPurpose, ApiAuthStore>();
let connection: Database | undefined;

/** 既存のPortal用SQL接続と同じ構成でアプリ用の保存先を初期化します。 */
export function getApiAuthStore(purpose: ApiAuthPurpose = "gateway"): ApiAuthStore {
  connection ??= new Kysely(getAuthDatabase());
  const store = stores.get(purpose) ?? new ApiAuthStore(connection, purpose);
  stores.set(purpose, store);
  return store;
}
