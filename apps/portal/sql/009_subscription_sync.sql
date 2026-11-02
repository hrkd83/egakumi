-- 同期の開始・完了を別々に追記します。APIMキーの値は保存しません。
SET XACT_ABORT ON;
IF OBJECT_ID(N'dbo.portalSubscriptionSyncAudit', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalSubscriptionSyncAudit (
        id char(36) NOT NULL CONSTRAINT PK_portalSubscriptionSyncAudit PRIMARY KEY,
        tenantId char(36) NOT NULL,
        actorObjectId char(36) NOT NULL,
        planId char(36) NOT NULL,
        apiKeyId varchar(43) NULL,
        subscriptionId varchar(256) NOT NULL,
        action varchar(24) NOT NULL,
        phase varchar(12) NOT NULL,
        code varchar(80) NULL,
        snapshotHash char(64) NOT NULL,
        recordedAt datetime2(3) NOT NULL CONSTRAINT DF_portalSubscriptionSyncAudit_time DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_portalSubscriptionSyncAudit_step UNIQUE(tenantId,planId,subscriptionId,phase),
        CONSTRAINT FK_portalSubscriptionSyncAudit_key FOREIGN KEY(tenantId,apiKeyId) REFERENCES dbo.portalApiKey(tenantId,id),
        CONSTRAINT CK_portalSubscriptionSyncAudit_action CHECK(action IN ('update','recreate','delete','delete-unregistered')),
        CONSTRAINT CK_portalSubscriptionSyncAudit_phase CHECK(phase IN ('attempted','succeeded','pending','failed'))
    );
    CREATE INDEX IX_portalSubscriptionSyncAudit_time ON dbo.portalSubscriptionSyncAudit(tenantId,recordedAt DESC);
END;
IF NOT EXISTS(SELECT 1 FROM dbo.portalSchemaVersions WHERE [version]=9)
    INSERT dbo.portalSchemaVersions([version]) VALUES(9);
