-- 台帳の内部ID・既存FKを保持したまま、実APIM購読名との対応を追加します。
SET XACT_ABORT ON;
IF COL_LENGTH(N'dbo.portalApiKey',N'apimSubscriptionId') IS NULL
    ALTER TABLE dbo.portalApiKey ADD apimSubscriptionId varchar(256) COLLATE Latin1_General_100_CI_AS NULL;
IF COL_LENGTH(N'dbo.portalApiKey',N'creationSource') IS NULL
    ALTER TABLE dbo.portalApiKey ADD creationSource varchar(20) NOT NULL
        CONSTRAINT DF_portalApiKey_creationSource DEFAULT 'portal';
GO
UPDATE dbo.portalApiKey SET apimSubscriptionId=id WHERE apimSubscriptionId IS NULL;
IF EXISTS(SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID(N'dbo.portalApiKey')
    AND name=N'apimSubscriptionId' AND is_nullable<>0)
    ALTER TABLE dbo.portalApiKey ALTER COLUMN apimSubscriptionId varchar(256) COLLATE Latin1_General_100_CI_AS NOT NULL;
IF NOT EXISTS(SELECT 1 FROM sys.key_constraints WHERE name=N'UQ_portalApiKey_apimSubscription'
    AND parent_object_id=OBJECT_ID(N'dbo.portalApiKey'))
    ALTER TABLE dbo.portalApiKey ADD CONSTRAINT UQ_portalApiKey_apimSubscription UNIQUE(tenantId,apimSubscriptionId);

-- 自動登録に架空のユーザーOIDを作りません。通常発行は従来どおり実本人OIDを必須にします。
IF EXISTS(SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID(N'dbo.portalApiKey')
    AND name=N'createdByObjectId' AND is_nullable=0)
    ALTER TABLE dbo.portalApiKey ALTER COLUMN createdByObjectId char(36) NULL;
IF NOT EXISTS(SELECT 1 FROM sys.check_constraints WHERE name=N'CK_portalApiKey_creationSource'
    AND parent_object_id=OBJECT_ID(N'dbo.portalApiKey'))
    ALTER TABLE dbo.portalApiKey ADD CONSTRAINT CK_portalApiKey_creationSource CHECK(
        (creationSource='portal' AND createdByObjectId IS NOT NULL)
        OR (creationSource='deployment' AND createdByObjectId IS NULL));

IF OBJECT_ID(N'dbo.portalApiKeyImportAudit',N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiKeyImportAudit (
        id char(36) NOT NULL CONSTRAINT PK_portalApiKeyImportAudit PRIMARY KEY,
        runId char(36) NOT NULL,
        manifestHash char(64) NOT NULL,
        tenantId char(36) NOT NULL,
        apiKeyId varchar(43) NOT NULL,
        apimResourceId nvarchar(2048) NOT NULL,
        apimSubscriptionId varchar(256) COLLATE Latin1_General_100_CI_AS NOT NULL,
        apimScope nvarchar(2048) NOT NULL,
        groupId char(36) NOT NULL,
        productId varchar(80) NOT NULL,
        importedAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyImportAudit_importedAt DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_portalApiKeyImportAudit_key UNIQUE(tenantId,apiKeyId),
        CONSTRAINT FK_portalApiKeyImportAudit_key FOREIGN KEY(tenantId,apiKeyId) REFERENCES dbo.portalApiKey(tenantId,id),
        CONSTRAINT FK_portalApiKeyImportAudit_group FOREIGN KEY(tenantId,groupId) REFERENCES dbo.portalApiKeyGroup(tenantId,groupId),
        CONSTRAINT CK_portalApiKeyImportAudit_hash CHECK(manifestHash COLLATE Latin1_General_100_BIN2 NOT LIKE '%[^0-9a-f]%')
    );
    CREATE INDEX IX_portalApiKeyImportAudit_run ON dbo.portalApiKeyImportAudit(tenantId,runId,importedAt);
END;
IF NOT EXISTS(SELECT 1 FROM dbo.portalSchemaVersions WHERE [version]=7)
    INSERT dbo.portalSchemaVersions([version]) VALUES(7);
