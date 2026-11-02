-- 会社名は統計用の選択肢です。既存キーや認証データの所属先は変更しません。
SET XACT_ABORT ON;
IF OBJECT_ID(N'dbo.portalCompany',N'U') IS NULL
BEGIN
  CREATE TABLE dbo.portalCompany(
    tenantId char(36) NOT NULL,
    id char(36) NOT NULL,
    name nvarchar(160) NOT NULL,
    nameKey nvarchar(160) COLLATE Latin1_General_100_BIN2 NOT NULL,
    isActive bit NOT NULL CONSTRAINT DF_portalCompany_active DEFAULT 1,
    createdAt datetime2(3) NOT NULL CONSTRAINT DF_portalCompany_created DEFAULT SYSUTCDATETIME(),
    updatedAt datetime2(3) NOT NULL CONSTRAINT DF_portalCompany_updated DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_portalCompany PRIMARY KEY(tenantId,id),
    CONSTRAINT UQ_portalCompany_name UNIQUE(tenantId,nameKey),
    CONSTRAINT CK_portalCompany_name CHECK(LEN(LTRIM(RTRIM(name)))>0 AND LEN(LTRIM(RTRIM(nameKey)))>0)
  );
END;
IF OBJECT_ID(N'dbo.portalCompanyAudit',N'U') IS NULL
BEGIN
  CREATE TABLE dbo.portalCompanyAudit(
    id char(36) NOT NULL CONSTRAINT PK_portalCompanyAudit PRIMARY KEY,
    tenantId char(36) NOT NULL,
    companyId char(36) NOT NULL,
    actorObjectId char(36) NOT NULL,
    action varchar(12) NOT NULL,
    previousName nvarchar(160) NULL,
    previousIsActive bit NULL,
    name nvarchar(160) NOT NULL,
    isActive bit NOT NULL,
    createdAt datetime2(3) NOT NULL CONSTRAINT DF_portalCompanyAudit_created DEFAULT SYSUTCDATETIME(),
    CONSTRAINT CK_portalCompanyAudit_action CHECK(action IN('create','update')),
    CONSTRAINT FK_portalCompanyAudit_company FOREIGN KEY(tenantId,companyId) REFERENCES dbo.portalCompany(tenantId,id)
  );
  CREATE INDEX IX_portalCompanyAudit_company ON dbo.portalCompanyAudit(tenantId,companyId,createdAt);
END;
-- 移行時には既存キーの値を推測しません。NULLのまま従来の操作を続けられます。
IF COL_LENGTH(N'dbo.portalApiKey',N'clientType') IS NULL ALTER TABLE dbo.portalApiKey ADD clientType varchar(20) NULL;
IF COL_LENGTH(N'dbo.portalApiKey',N'systemName') IS NULL ALTER TABLE dbo.portalApiKey ADD systemName nvarchar(120) NULL;
IF COL_LENGTH(N'dbo.portalApiKey',N'teamName') IS NULL ALTER TABLE dbo.portalApiKey ADD teamName nvarchar(120) NULL;
IF COL_LENGTH(N'dbo.portalApiKey',N'companyId') IS NULL ALTER TABLE dbo.portalApiKey ADD companyId char(36) NULL;
IF COL_LENGTH(N'dbo.portalApiKey',N'contactEmail') IS NULL ALTER TABLE dbo.portalApiKey ADD contactEmail nvarchar(254) NULL;
GO
IF NOT EXISTS(SELECT 1 FROM sys.check_constraints WHERE name=N'CK_portalApiKey_clientType' AND parent_object_id=OBJECT_ID(N'dbo.portalApiKey'))
  ALTER TABLE dbo.portalApiKey ADD CONSTRAINT CK_portalApiKey_clientType CHECK(clientType IS NULL OR clientType IN('custom-system','saas','oss','low-code','other'));
IF NOT EXISTS(SELECT 1 FROM sys.foreign_keys WHERE name=N'FK_portalApiKey_company' AND parent_object_id=OBJECT_ID(N'dbo.portalApiKey'))
  ALTER TABLE dbo.portalApiKey ADD CONSTRAINT FK_portalApiKey_company FOREIGN KEY(tenantId,companyId) REFERENCES dbo.portalCompany(tenantId,id);
GO
-- 通常製品は配備で設定するカタログから補充します。検証用の既存承認はそのまま維持します。
CREATE OR ALTER PROCEDURE dbo.portalEnsureApiKeyGroup
  @tenantId char(36), @groupId char(36), @displayName nvarchar(255)
AS
BEGIN
  SET NOCOUNT ON;
  IF @displayName IS NULL OR LEN(LTRIM(RTRIM(@displayName)))=0 THROW 51000,'Invalid eligible group',1;
  DECLARE @result int;
  EXEC @result=sp_getapplock @Resource=N'portal-api-key-registry',@LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=15000;
  IF @result<0 THROW 51000,'Group registry is busy',1;
  IF NOT EXISTS(SELECT 1 FROM dbo.portalApiKeyGroup WHERE tenantId=@tenantId AND groupId=@groupId)
    INSERT dbo.portalApiKeyGroup(tenantId,groupId,displayName,approvalSource) VALUES(@tenantId,@groupId,@displayName,'directory');
  INSERT dbo.portalApiKeyProduct(tenantId,groupId,productId,displayName,maxSubscriptions)
    SELECT @tenantId,@groupId,c.productId,c.displayName,c.maxSubscriptions
    FROM dbo.portalApiKeyCatalogProduct c WHERE c.tenantId=@tenantId AND c.enabled=1
      AND NOT EXISTS(SELECT 1 FROM dbo.portalApiKeyProduct p WHERE p.tenantId=@tenantId AND p.groupId=@groupId AND p.productId=c.productId);
END;
GO
IF NOT EXISTS(SELECT 1 FROM dbo.portalSchemaVersions WHERE [version]=8) INSERT dbo.portalSchemaVersions([version]) VALUES(8);
