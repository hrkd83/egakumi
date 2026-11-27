-- The broker stores identity, routing and short-lived proof hashes only.
-- Microsoft access/refresh tokens remain in Credential Manager.
SET XACT_ABORT ON;
GO
IF SCHEMA_ID(N'broker') IS NULL EXEC(N'CREATE SCHEMA broker AUTHORIZATION dbo');
GO
IF OBJECT_ID(N'broker.SchemaVersions', N'U') IS NULL
CREATE TABLE broker.SchemaVersions (Version int NOT NULL PRIMARY KEY, AppliedAt datetime2 NOT NULL DEFAULT SYSUTCDATETIME());
GO
IF OBJECT_ID(N'broker.Pools', N'U') IS NULL
CREATE TABLE broker.Pools (
 ProfileName varchar(80) NOT NULL,
 ProviderName varchar(80) NOT NULL,
 Capacity int NOT NULL CHECK (Capacity BETWEEN 1 AND 10000),
 Allocated int NOT NULL DEFAULT 0 CHECK (Allocated >= 0),
 Enabled bit NOT NULL DEFAULT 1,
 AllocationBlocked bit NOT NULL DEFAULT 0,
 Version rowversion NOT NULL,
 CONSTRAINT PK_BrokerPools PRIMARY KEY (ProfileName, ProviderName),
 CONSTRAINT UQ_BrokerPoolProvider UNIQUE (ProviderName),
 CONSTRAINT CK_BrokerPoolsCapacity CHECK (Allocated <= Capacity)
);
GO
-- A restored ledger can lag behind CM. Keep its allocation quarantine across
-- subsequent migrations until an operator reconciles the physical inventory.
IF COL_LENGTH(N'broker.Pools', N'AllocationBlocked') IS NULL
ALTER TABLE broker.Pools ADD AllocationBlocked bit NOT NULL DEFAULT 0;
GO
IF OBJECT_ID(N'broker.Connections', N'U') IS NULL
CREATE TABLE broker.Connections (
 ProfileName varchar(80) NOT NULL,
 TenantId uniqueidentifier NOT NULL,
 ObjectId uniqueidentifier NOT NULL,
 UserPrincipalName nvarchar(320) NOT NULL,
 ProviderName varchar(80) NOT NULL,
 ConnectionId varchar(80) NOT NULL,
 State varchar(16) NOT NULL CHECK (State IN ('reserved','pending','ready','unverified','deleting')),
 LeaseOwner varchar(128) NULL,
 LeaseUntil bigint NOT NULL DEFAULT 0,
 CreatedAt bigint NOT NULL,
 LastUsedAt bigint NOT NULL,
 LastCheckedAt bigint NULL,
 Version rowversion NOT NULL,
 CONSTRAINT PK_BrokerConnections PRIMARY KEY (TenantId, ObjectId, ProfileName),
 CONSTRAINT UQ_BrokerPhysicalConnection UNIQUE (ProviderName, ConnectionId),
 CONSTRAINT FK_BrokerConnectionPool FOREIGN KEY (ProfileName, ProviderName) REFERENCES broker.Pools(ProfileName, ProviderName)
);
GO
IF OBJECT_ID(N'broker.Transactions', N'U') IS NULL
CREATE TABLE broker.Transactions (
 TransactionId char(64) NOT NULL PRIMARY KEY,
 ProfileName varchar(80) NOT NULL,
 SubscriptionId nvarchar(256) NOT NULL,
 PollHash char(64) NOT NULL,
 LaunchHash char(64) NULL,
 CookieHash char(64) NULL,
 OidcStateHash char(64) NULL,
 OidcFlow nvarchar(max) NULL,
 CmStateHash char(64) NULL,
 TicketHash char(64) NULL,
 Phase varchar(24) NOT NULL CHECK (Phase IN ('pending','identityPending','identityProcessing','identityVerified','cmPending','verifying','complete','failed')),
 TenantId uniqueidentifier NULL,
 ObjectId uniqueidentifier NULL,
 UserPrincipalName nvarchar(320) NULL,
 ProviderName varchar(80) NULL,
 ConnectionId varchar(80) NULL,
 ExpiresAt bigint NOT NULL,
 SessionExpiresAt bigint NOT NULL,
 CreatedAt bigint NOT NULL,
 FailureCode varchar(48) NULL,
 RequiredTenantId uniqueidentifier NULL,
 RequiredObjectId uniqueidentifier NULL,
 ForceCmLogin bit NOT NULL DEFAULT 0,
 Version rowversion NOT NULL,
 CONSTRAINT CK_BrokerOidcFlowJson CHECK (OidcFlow IS NULL OR ISJSON(OidcFlow) = 1)
);
GO
-- An API-triggered challenge may carry a verified session's identity forward.
-- These fields contain identity IDs and a fixed flag, never the old proof.
IF COL_LENGTH(N'broker.Transactions', N'RequiredTenantId') IS NULL
ALTER TABLE broker.Transactions ADD RequiredTenantId uniqueidentifier NULL;
IF COL_LENGTH(N'broker.Transactions', N'RequiredObjectId') IS NULL
ALTER TABLE broker.Transactions ADD RequiredObjectId uniqueidentifier NULL;
IF COL_LENGTH(N'broker.Transactions', N'ForceCmLogin') IS NULL
ALTER TABLE broker.Transactions ADD ForceCmLogin bit NOT NULL DEFAULT 0;
GO
IF OBJECT_ID(N'broker.Sessions', N'U') IS NULL
CREATE TABLE broker.Sessions (
 SessionId char(64) NOT NULL PRIMARY KEY,
 SecretHash char(64) NOT NULL,
 SubscriptionId nvarchar(256) NOT NULL,
 ProfileName varchar(80) NOT NULL,
 TenantId uniqueidentifier NOT NULL,
 ObjectId uniqueidentifier NOT NULL,
 ProviderName varchar(80) NOT NULL,
 ConnectionId varchar(80) NOT NULL,
 ExpiresAt bigint NOT NULL,
 Revoked bit NOT NULL DEFAULT 0,
 CreatedAt bigint NOT NULL,
 Version rowversion NOT NULL
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'broker.Sessions') AND name=N'IX_BrokerSessionConnection')
CREATE INDEX IX_BrokerSessionConnection ON broker.Sessions(TenantId, ObjectId, ProfileName, Revoked, ExpiresAt);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'broker.Sessions') AND name=N'IX_BrokerSessionExpiry')
CREATE INDEX IX_BrokerSessionExpiry ON broker.Sessions(ExpiresAt);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'broker.Transactions') AND name=N'IX_BrokerTransactionExpiry')
CREATE INDEX IX_BrokerTransactionExpiry ON broker.Transactions(ExpiresAt, SessionExpiresAt);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'broker.Transactions') AND name=N'IX_BrokerTransactionSubscriptionCreated')
CREATE INDEX IX_BrokerTransactionSubscriptionCreated ON broker.Transactions(SubscriptionId, CreatedAt);
GO
-- The identity key and pool reservation commit together. Concurrent first logins
-- cannot create two rows for one person or exceed the configured pool capacity.
CREATE OR ALTER PROCEDURE broker.AllocateConnection
 @ProfileName varchar(80), @TenantId uniqueidentifier, @ObjectId uniqueidentifier,
 @UserPrincipalName nvarchar(320), @ConnectionId varchar(80),
 @LeaseOwner varchar(128), @LeaseSeconds int
AS
BEGIN
 SET NOCOUNT ON;
 SET XACT_ABORT ON;
 SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
 DECLARE @Now bigint=DATEDIFF_BIG(second,CONVERT(datetime2,'1970-01-01'),SYSUTCDATETIME());
 DECLARE @ProviderName varchar(80), @IsNew bit=0;
 BEGIN TRY
  BEGIN TRANSACTION;
  SELECT @ProviderName=ProviderName FROM broker.Connections WITH (UPDLOCK,HOLDLOCK)
   WHERE TenantId=@TenantId AND ObjectId=@ObjectId AND ProfileName=@ProfileName;
  IF @ProviderName IS NULL
  BEGIN
   SELECT TOP (1) @ProviderName=ProviderName FROM broker.Pools WITH (UPDLOCK,HOLDLOCK)
    WHERE ProfileName=@ProfileName AND Enabled=1 AND AllocationBlocked=0 AND Allocated<Capacity
    ORDER BY CAST(Allocated AS decimal(20,8))/Capacity, ProviderName;
   IF @ProviderName IS NULL THROW 51001, 'No credential pool capacity', 1;
   UPDATE broker.Pools SET Allocated=Allocated+1 WHERE ProfileName=@ProfileName AND ProviderName=@ProviderName;
   INSERT broker.Connections(ProfileName,TenantId,ObjectId,UserPrincipalName,ProviderName,ConnectionId,State,LeaseOwner,LeaseUntil,CreatedAt,LastUsedAt)
    VALUES(@ProfileName,@TenantId,@ObjectId,@UserPrincipalName,@ProviderName,@ConnectionId,'reserved',@LeaseOwner,@Now+@LeaseSeconds,@Now,@Now);
   SET @IsNew=1;
  END
  ELSE
  BEGIN
   UPDATE broker.Connections SET UserPrincipalName=@UserPrincipalName, LastUsedAt=@Now,
    LeaseOwner=CASE WHEN State IN ('reserved','pending','unverified') AND (LeaseUntil<=@Now OR LeaseOwner=@LeaseOwner) THEN @LeaseOwner ELSE LeaseOwner END,
    LeaseUntil=CASE WHEN State IN ('reserved','pending','unverified') AND (LeaseUntil<=@Now OR LeaseOwner=@LeaseOwner) THEN @Now+@LeaseSeconds ELSE LeaseUntil END
    WHERE TenantId=@TenantId AND ObjectId=@ObjectId AND ProfileName=@ProfileName;
  END;
  COMMIT TRANSACTION;
  SELECT *,@IsNew AS IsNew,CAST(CASE WHEN LeaseOwner=@LeaseOwner AND LeaseUntil>@Now THEN 1 ELSE 0 END AS bit) AS LeaseOwned
   FROM broker.Connections WHERE TenantId=@TenantId AND ObjectId=@ObjectId AND ProfileName=@ProfileName;
 END TRY
 BEGIN CATCH
  IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
  THROW;
 END CATCH
END;
GO
IF NOT EXISTS (SELECT 1 FROM broker.SchemaVersions WHERE Version=1)
INSERT broker.SchemaVersions(Version) VALUES(1);
GO
