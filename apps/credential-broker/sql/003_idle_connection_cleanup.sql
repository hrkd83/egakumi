-- CM idle retention uses creation time until the first allowed business use.
-- Existing LastUsedAt values are retained conservatively; authentication no longer extends them.
SET XACT_ABORT ON;
GO
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
 DECLARE @ProviderName varchar(80), @State varchar(16), @IsNew bit=0;
 BEGIN TRY
  BEGIN TRANSACTION;
  SELECT @ProviderName=ProviderName,@State=State FROM broker.Connections WITH (UPDLOCK,HOLDLOCK)
   WHERE TenantId=@TenantId AND ObjectId=@ObjectId AND ProfileName=@ProfileName;
  SET @Now=DATEDIFF_BIG(second,CONVERT(datetime2,'1970-01-01'),SYSUTCDATETIME());
  IF @State='deleting' THROW 51002, 'Credential connection is being deleted', 1;
  IF @ProviderName IS NULL
  BEGIN
   SELECT TOP (1) @ProviderName=ProviderName FROM broker.Pools WITH (UPDLOCK,HOLDLOCK)
    WHERE ProfileName=@ProfileName AND Enabled=1 AND AllocationBlocked=0 AND Allocated<Capacity
    ORDER BY CAST(Allocated AS decimal(20,8))/Capacity, ProviderName;
   IF @ProviderName IS NULL THROW 51001, 'No credential pool capacity', 1;
   UPDATE broker.Pools SET Allocated=Allocated+1 WHERE ProfileName=@ProfileName AND ProviderName=@ProviderName;
   SET @Now=DATEDIFF_BIG(second,CONVERT(datetime2,'1970-01-01'),SYSUTCDATETIME());
   INSERT broker.Connections(ProfileName,TenantId,ObjectId,UserPrincipalName,ProviderName,ConnectionId,State,LeaseOwner,LeaseUntil,CreatedAt,LastUsedAt)
    VALUES(@ProfileName,@TenantId,@ObjectId,@UserPrincipalName,@ProviderName,@ConnectionId,'reserved',@LeaseOwner,@Now+@LeaseSeconds,@Now,@Now);
   SET @IsNew=1;
  END
  ELSE
  BEGIN
   UPDATE broker.Connections SET UserPrincipalName=@UserPrincipalName,
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
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'broker.Connections') AND name=N'IX_BrokerConnectionCleanup')
CREATE INDEX IX_BrokerConnectionCleanup ON broker.Connections(ProfileName,ProviderName,LastCheckedAt,LastUsedAt)
 INCLUDE(TenantId,ObjectId,ConnectionId,State,LeaseUntil,Version);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'broker.Transactions') AND name=N'IX_BrokerTransactionConnection')
CREATE INDEX IX_BrokerTransactionConnection ON broker.Transactions(ProfileName,TenantId,ObjectId,Phase,ExpiresAt);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'broker.Transactions') AND name=N'IX_BrokerTransactionRequiredConnection')
CREATE INDEX IX_BrokerTransactionRequiredConnection ON broker.Transactions(ProfileName,RequiredTenantId,RequiredObjectId,Phase,ExpiresAt);
GO
IF NOT EXISTS (SELECT 1 FROM broker.SchemaVersions WHERE Version=3)
INSERT broker.SchemaVersions(Version) VALUES(3);
GO
