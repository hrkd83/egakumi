-- 購読契約とAPIM検証済みユーザー主体を分離する。
-- 既存の購読、cs2セッション、取引、CM接続は更新・削除しない。
SET XACT_ABORT ON;
GO
IF NOT EXISTS (SELECT 1 FROM broker.SchemaVersions WHERE Version=4)
BEGIN
 BEGIN TRY
  BEGIN TRANSACTION;
  DROP INDEX IX_BrokerTransactionSubscriptionCreated ON broker.Transactions;
  ALTER TABLE broker.Transactions ALTER COLUMN SubscriptionId nvarchar(256) NULL;
  CREATE INDEX IX_BrokerTransactionSubscriptionCreated
   ON broker.Transactions(SubscriptionId,CreatedAt);
  ALTER TABLE broker.Transactions ADD CallerId nvarchar(256) NULL;
  -- 新列を参照するDDLは、列追加後に別のbatchとしてコンパイルする。
  EXEC(N'ALTER TABLE broker.Transactions WITH CHECK ADD CONSTRAINT CK_BrokerTransactionCaller
   CHECK ((SubscriptionId IS NOT NULL AND CallerId IS NULL)
       OR (SubscriptionId IS NULL AND CallerId IS NOT NULL));');
  EXEC(N'CREATE INDEX IX_BrokerTransactionCallerCreated
   ON broker.Transactions(CallerId,CreatedAt) WHERE CallerId IS NOT NULL;');
  ALTER TABLE broker.Sessions ALTER COLUMN SubscriptionId nvarchar(256) NULL;
  ALTER TABLE broker.Sessions ADD CallerId nvarchar(256) NULL;
  EXEC(N'ALTER TABLE broker.Sessions WITH CHECK ADD CONSTRAINT CK_BrokerSessionCaller
   CHECK ((SubscriptionId IS NOT NULL AND CallerId IS NULL)
       OR (SubscriptionId IS NULL AND CallerId IS NOT NULL));');
  INSERT broker.SchemaVersions(Version) VALUES(4);
  COMMIT TRANSACTION;
 END TRY
 BEGIN CATCH
  IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
  THROW;
 END CATCH;
END;
GO
