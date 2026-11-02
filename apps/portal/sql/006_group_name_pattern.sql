-- 環境固有の正規表現へ移行します。既存グループ・購読・制限・監査は削除しません。
SET XACT_ABORT ON;

IF EXISTS(SELECT 1 FROM sys.check_constraints WHERE name=N'CK_portalApiKeyAdministration_prefix'
    AND parent_object_id=OBJECT_ID(N'dbo.portalApiKeyAdministration'))
    ALTER TABLE dbo.portalApiKeyAdministration DROP CONSTRAINT CK_portalApiKeyAdministration_prefix;
IF EXISTS(SELECT 1 FROM sys.default_constraints WHERE name=N'DF_portalApiKeyAdministration_prefix'
    AND parent_object_id=OBJECT_ID(N'dbo.portalApiKeyAdministration'))
    ALTER TABLE dbo.portalApiKeyAdministration DROP CONSTRAINT DF_portalApiKeyAdministration_prefix;

IF COL_LENGTH(N'dbo.portalApiKeyAdministration',N'groupNamePattern') IS NULL
BEGIN
    IF COL_LENGTH(N'dbo.portalApiKeyAdministration',N'groupPrefix') IS NOT NULL
    BEGIN
        EXEC sp_rename N'dbo.portalApiKeyAdministration.groupPrefix',N'groupNamePattern',N'COLUMN';
        EXEC(N'UPDATE dbo.portalApiKeyAdministration SET groupNamePattern=N''^ORG_'' WHERE groupNamePattern=N''ORG_''');
    END
    ELSE
        ALTER TABLE dbo.portalApiKeyAdministration ADD groupNamePattern nvarchar(512) NULL;
END;
GO
IF EXISTS(SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID(N'dbo.portalApiKeyAdministration')
    AND name=N'groupNamePattern' AND (max_length<>1024 OR is_nullable<>0))
    ALTER TABLE dbo.portalApiKeyAdministration ALTER COLUMN groupNamePattern nvarchar(512) NOT NULL;
IF NOT EXISTS(SELECT 1 FROM sys.check_constraints WHERE name=N'CK_portalApiKeyAdministration_namePattern'
    AND parent_object_id=OBJECT_ID(N'dbo.portalApiKeyAdministration'))
    ALTER TABLE dbo.portalApiKeyAdministration ADD CONSTRAINT CK_portalApiKeyAdministration_namePattern
        CHECK(LEN(LTRIM(RTRIM(groupNamePattern))) BETWEEN 1 AND 512);
GO
-- SQL Server 2022には汎用の正規表現照合がないため、名称規則はサーバーの共通照合処理で検証します。
-- この限定手続きは非空の実名とsecurityEnabledを守り、承認enabledを書き換えません。
CREATE OR ALTER PROCEDURE dbo.portalObserveApiKeyGroup
    @tenantId char(36), @groupId char(36), @displayName nvarchar(255),
    @securityEnabled bit, @status varchar(12), @checkedAt datetime2(3), @errorCode varchar(64)
AS
BEGIN
    SET NOCOUNT ON;
    IF @status NOT IN ('active','deleted','ineligible','unknown') OR @checkedAt IS NULL
        THROW 51000,'Invalid group observation',1;
    IF @status='active' AND (@displayName IS NULL OR LEN(LTRIM(RTRIM(@displayName)))=0
        OR @securityEnabled IS NULL OR @securityEnabled<>1)
        THROW 51000,'Invalid eligible group',1;
    UPDATE dbo.portalApiKeyGroup SET displayName=COALESCE(@displayName,displayName),
        directoryStatus=@status,directoryCheckedAt=@checkedAt,directoryErrorCode=@errorCode,
        directorySecurityEnabled=@securityEnabled,updatedAt=SYSUTCDATETIME()
    WHERE tenantId=@tenantId AND groupId=@groupId
        AND (directoryCheckedAt IS NULL OR directoryCheckedAt<=@checkedAt);
END;
GO
CREATE OR ALTER PROCEDURE dbo.portalEnsureApiKeyGroup
    @tenantId char(36), @groupId char(36), @displayName nvarchar(255)
AS
BEGIN
    SET NOCOUNT ON;
    IF @displayName IS NULL OR LEN(LTRIM(RTRIM(@displayName)))=0
        THROW 51000,'Invalid eligible group',1;
    DECLARE @result int;
    EXEC @result=sp_getapplock @Resource=N'portal-api-key-registry', @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=15000;
    IF @result<0 THROW 51000,'Group registry is busy',1;
    IF NOT EXISTS(SELECT 1 FROM dbo.portalApiKeyGroup WHERE tenantId=@tenantId AND groupId=@groupId)
        INSERT dbo.portalApiKeyGroup(tenantId,groupId,displayName,approvalSource) VALUES(@tenantId,@groupId,@displayName,'directory');
    IF NOT EXISTS(SELECT 1 FROM dbo.portalApiKeyGroup WHERE tenantId=@tenantId AND groupId=@groupId AND approvalSource='directory') RETURN;
    INSERT dbo.portalApiKeyProduct(tenantId,groupId,productId,displayName,maxSubscriptions)
        SELECT @tenantId,@groupId,c.productId,c.displayName,c.maxSubscriptions
        FROM dbo.portalApiKeyCatalogProduct c WHERE c.tenantId=@tenantId AND c.enabled=1
            AND NOT EXISTS(SELECT 1 FROM dbo.portalApiKeyProduct p
                WHERE p.tenantId=@tenantId AND p.groupId=@groupId AND p.productId=c.productId);
END;
GO
IF NOT EXISTS(SELECT 1 FROM dbo.portalSchemaVersions WHERE [version]=6)
    INSERT dbo.portalSchemaVersions([version]) VALUES(6);
