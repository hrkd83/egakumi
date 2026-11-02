-- 承認設定enabledとは分離し、Entraの観測結果を再配備で上書きしません。
SET XACT_ABORT ON;
IF COL_LENGTH(N'dbo.portalApiKeyGroup', N'directoryStatus') IS NULL
    ALTER TABLE dbo.portalApiKeyGroup ADD directoryStatus varchar(12) NOT NULL
        CONSTRAINT DF_portalApiKeyGroup_directoryStatus DEFAULT 'unknown';
IF COL_LENGTH(N'dbo.portalApiKeyGroup', N'directoryCheckedAt') IS NULL
    ALTER TABLE dbo.portalApiKeyGroup ADD directoryCheckedAt datetime2(3) NULL;
IF COL_LENGTH(N'dbo.portalApiKeyGroup', N'directoryErrorCode') IS NULL
    ALTER TABLE dbo.portalApiKeyGroup ADD directoryErrorCode varchar(64) NULL;
IF COL_LENGTH(N'dbo.portalApiKeyGroup', N'directorySecurityEnabled') IS NULL
    ALTER TABLE dbo.portalApiKeyGroup ADD directorySecurityEnabled bit NULL;
IF COL_LENGTH(N'dbo.portalApiKeyGroup', N'approvalSource') IS NULL
    ALTER TABLE dbo.portalApiKeyGroup ADD approvalSource varchar(12) NOT NULL
        CONSTRAINT DF_portalApiKeyGroup_approvalSource DEFAULT 'deployment';
GO
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name=N'CK_portalApiKeyGroup_directoryStatus'
    AND parent_object_id=OBJECT_ID(N'dbo.portalApiKeyGroup'))
    ALTER TABLE dbo.portalApiKeyGroup ADD CONSTRAINT CK_portalApiKeyGroup_directoryStatus
        CHECK (directoryStatus IN ('active','deleted','ineligible','unknown'));
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name=N'CK_portalApiKeyGroup_approvalSource'
    AND parent_object_id=OBJECT_ID(N'dbo.portalApiKeyGroup'))
    ALTER TABLE dbo.portalApiKeyGroup ADD CONSTRAINT CK_portalApiKeyGroup_approvalSource CHECK(approvalSource IN ('deployment','directory'));

IF OBJECT_ID(N'dbo.portalApiKeyTransferAudit',N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiKeyTransferAudit (
        id char(36) NOT NULL CONSTRAINT PK_portalApiKeyTransferAudit PRIMARY KEY,
        tenantId char(36) NOT NULL,
        apiKeyId varchar(43) NOT NULL,
        sourceGroupId char(36) NOT NULL,
        targetGroupId char(36) NOT NULL,
        actorObjectId char(36) NOT NULL,
        recordedAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyTransferAudit_recordedAt DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_portalApiKeyTransferAudit_key FOREIGN KEY (tenantId,apiKeyId) REFERENCES dbo.portalApiKey(tenantId,id),
        CONSTRAINT FK_portalApiKeyTransferAudit_source FOREIGN KEY (tenantId,sourceGroupId) REFERENCES dbo.portalApiKeyGroup(tenantId,groupId),
        CONSTRAINT FK_portalApiKeyTransferAudit_target FOREIGN KEY (tenantId,targetGroupId) REFERENCES dbo.portalApiKeyGroup(tenantId,groupId),
        CONSTRAINT CK_portalApiKeyTransferAudit_different CHECK (sourceGroupId<>targetGroupId)
    );
    CREATE INDEX IX_portalApiKeyTransferAudit_key ON dbo.portalApiKeyTransferAudit(tenantId,apiKeyId,recordedAt);
END;
GO
-- 実行IDにはこの手続きのEXECUTEだけを付与し、承認列enabledは変更させません。
CREATE OR ALTER PROCEDURE dbo.portalObserveApiKeyGroup
    @tenantId char(36), @groupId char(36), @displayName nvarchar(255),
    @securityEnabled bit, @status varchar(12), @checkedAt datetime2(3), @errorCode varchar(64)
AS
BEGIN
    SET NOCOUNT ON;
    IF @status NOT IN ('active','deleted','ineligible','unknown') OR @checkedAt IS NULL
        THROW 51000,'Invalid group observation',1;
    IF @status='active' AND (@displayName IS NULL OR @displayName COLLATE Latin1_General_100_BIN2 NOT LIKE N'ORG[_]%'
        OR @securityEnabled IS NULL OR @securityEnabled<>1)
        THROW 51000,'Invalid eligible group',1;
    UPDATE dbo.portalApiKeyGroup SET displayName=COALESCE(@displayName,displayName),
        directoryStatus=@status,directoryCheckedAt=@checkedAt,directoryErrorCode=@errorCode,
        directorySecurityEnabled=@securityEnabled,updatedAt=SYSUTCDATETIME()
    WHERE tenantId=@tenantId AND groupId=@groupId
        AND (directoryCheckedAt IS NULL OR directoryCheckedAt<=@checkedAt);
END;
GO
-- 新規グループは、管理者が配備した製品カタログの範囲でだけ自動登録します。
-- 既存グループの承認取り消し・独自上限は、自動発見で復活させません。
CREATE OR ALTER PROCEDURE dbo.portalEnsureApiKeyGroup
    @tenantId char(36), @groupId char(36), @displayName nvarchar(255)
AS
BEGIN
    SET NOCOUNT ON;
    IF @displayName IS NULL OR @displayName COLLATE Latin1_General_100_BIN2 NOT LIKE N'ORG[_]%'
        THROW 51000,'Invalid eligible group',1;
    DECLARE @result int;
    EXEC @result=sp_getapplock @Resource=N'portal-api-key-registry', @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=15000;
    IF @result<0 THROW 51000,'Group registry is busy',1;
    IF NOT EXISTS(SELECT 1 FROM dbo.portalApiKeyGroup WHERE tenantId=@tenantId AND groupId=@groupId)
        INSERT dbo.portalApiKeyGroup(tenantId,groupId,displayName,approvalSource) VALUES(@tenantId,@groupId,@displayName,'directory');
    -- 個別承認グループはカタログで拡張しません。動的グループも既存の無効化・上限を保持します。
    IF NOT EXISTS(SELECT 1 FROM dbo.portalApiKeyGroup WHERE tenantId=@tenantId AND groupId=@groupId AND approvalSource='directory') RETURN;
    INSERT dbo.portalApiKeyProduct(tenantId,groupId,productId,displayName,maxSubscriptions)
        SELECT @tenantId,@groupId,c.productId,c.displayName,c.maxSubscriptions
        FROM dbo.portalApiKeyCatalogProduct c WHERE c.tenantId=@tenantId AND c.enabled=1
            AND NOT EXISTS(SELECT 1 FROM dbo.portalApiKeyProduct p
                WHERE p.tenantId=@tenantId AND p.groupId=@groupId AND p.productId=c.productId);
END;
GO
IF NOT EXISTS (SELECT 1 FROM dbo.portalSchemaVersions WHERE [version]=4)
    INSERT INTO dbo.portalSchemaVersions([version]) VALUES(4);
