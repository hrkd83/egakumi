-- 認証取引と本人セッションを分離する。既存のCMプール・本人接続は保持する。
SET XACT_ABORT ON;
GO
IF NOT EXISTS (SELECT 1 FROM broker.SchemaVersions WHERE Version=2)
BEGIN
 BEGIN TRANSACTION;
 -- サービス提供前の切替。旧証明を新しい長期セッションへ昇格させない。
 DROP TABLE broker.Sessions;
 DELETE FROM broker.Transactions;
 DROP INDEX IX_BrokerTransactionExpiry ON broker.Transactions;
 ALTER TABLE broker.Transactions DROP COLUMN SessionExpiresAt;
 ALTER TABLE broker.Transactions ADD
  PendingSessionId char(64) NULL,
  PendingSessionSecretHash char(64) NULL,
  SubjectEpochAtVerification bigint NULL,
  ConnectionEpochAtVerification bigint NULL;
 CREATE INDEX IX_BrokerTransactionExpiry ON broker.Transactions(ExpiresAt);
 ALTER TABLE broker.Connections ADD SecurityEpoch bigint NOT NULL
  CONSTRAINT DF_BrokerConnectionSecurityEpoch DEFAULT 0;
 CREATE TABLE broker.SubjectSecurity (
  TenantId uniqueidentifier NOT NULL,
  ObjectId uniqueidentifier NOT NULL,
  RevocationEpoch bigint NOT NULL DEFAULT 0,
  UpdatedAt bigint NOT NULL,
  CONSTRAINT PK_BrokerSubjectSecurity PRIMARY KEY(TenantId,ObjectId)
 );
 CREATE TABLE broker.Sessions (
  SessionId char(64) NOT NULL PRIMARY KEY,
  SecretHash char(64) NOT NULL,
  PreviousSecretHash char(64) NULL,
  PreviousValidUntil bigint NULL,
  SubscriptionId nvarchar(256) NOT NULL,
  ProfileName varchar(80) NOT NULL,
  TenantId uniqueidentifier NOT NULL,
  ObjectId uniqueidentifier NOT NULL,
  ProviderName varchar(80) NOT NULL,
  ConnectionId varchar(80) NOT NULL,
  AuthenticatedAt bigint NOT NULL,
  LastBusinessUsedAt bigint NOT NULL,
  IdleExpiresAt bigint NOT NULL,
  AbsoluteExpiresAt bigint NOT NULL,
  ExpiresAt bigint NOT NULL,
  IdleTimeoutSeconds int NOT NULL,
  RotationIntervalSeconds int NOT NULL,
  RotationGraceSeconds int NOT NULL,
  RotationHandoffSeconds int NOT NULL,
  RotationCandidateTtlSeconds int NOT NULL,
  Generation bigint NOT NULL DEFAULT 1,
  NextRotationAt bigint NOT NULL,
  RotationDeadlineAt bigint NULL,
  LastCommittedRotationId char(64) NULL,
  SubjectEpochAtAuthentication bigint NOT NULL,
  ConnectionEpochAtAuthentication bigint NOT NULL,
  Revoked bit NOT NULL DEFAULT 0,
  RevokedAt bigint NULL,
  RevocationReason varchar(48) NULL,
  CreatedAt bigint NOT NULL,
  Version rowversion NOT NULL,
  CONSTRAINT CK_BrokerSessionDeadlines CHECK
   (AuthenticatedAt<=LastBusinessUsedAt AND IdleExpiresAt<=AbsoluteExpiresAt
    AND ExpiresAt=IdleExpiresAt AND Generation>=1)
 );
 CREATE INDEX IX_BrokerSessionConnection ON broker.Sessions(TenantId,ObjectId,ProfileName,Revoked,ExpiresAt);
 CREATE INDEX IX_BrokerSessionExpiry ON broker.Sessions(ExpiresAt);
 CREATE TABLE broker.SessionRotationCandidates (
  RotationId char(64) NOT NULL PRIMARY KEY,
  SessionId char(64) NOT NULL,
  SecretHash char(64) NOT NULL,
  FromGeneration bigint NOT NULL,
  CreatedAt bigint NOT NULL,
  ExpiresAt bigint NOT NULL,
  CONSTRAINT FK_BrokerRotationSession FOREIGN KEY(SessionId)
   REFERENCES broker.Sessions(SessionId) ON DELETE CASCADE
 );
 CREATE INDEX IX_BrokerSessionRotation ON broker.SessionRotationCandidates(SessionId,FromGeneration,ExpiresAt);
 INSERT broker.SchemaVersions(Version) VALUES(2);
 COMMIT TRANSACTION;
END;
GO
