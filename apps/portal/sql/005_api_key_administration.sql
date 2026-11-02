-- 配備が所有する単一テナント設定です。キーや外部資格情報は保存しません。
SET XACT_ABORT ON;

IF OBJECT_ID(N'dbo.portalApiKeyAdministration', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiKeyAdministration (
        tenantId char(36) NOT NULL CONSTRAINT PK_portalApiKeyAdministration PRIMARY KEY,
        platformGroupId char(36) NOT NULL,
        groupPrefix nvarchar(16) NOT NULL CONSTRAINT DF_portalApiKeyAdministration_prefix DEFAULT N'ORG_',
        createdAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyAdministration_created DEFAULT SYSUTCDATETIME(),
        updatedAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyAdministration_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT CK_portalApiKeyAdministration_prefix CHECK (groupPrefix=N'ORG_')
    );
END;

IF OBJECT_ID(N'dbo.portalApiKeyCatalogProduct', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiKeyCatalogProduct (
        tenantId char(36) NOT NULL,
        productId varchar(80) NOT NULL,
        displayName nvarchar(255) NOT NULL,
        maxSubscriptions int NOT NULL CONSTRAINT DF_portalApiKeyCatalogProduct_limit DEFAULT 5,
        enabled bit NOT NULL CONSTRAINT DF_portalApiKeyCatalogProduct_enabled DEFAULT 1,
        createdAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyCatalogProduct_created DEFAULT SYSUTCDATETIME(),
        updatedAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyCatalogProduct_updated DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_portalApiKeyCatalogProduct PRIMARY KEY (tenantId, productId),
        CONSTRAINT CK_portalApiKeyCatalogProduct_limit CHECK (maxSubscriptions BETWEEN 1 AND 1000)
    );
END;

IF NOT EXISTS (SELECT 1 FROM dbo.portalSchemaVersions WHERE [version] = 5)
    INSERT INTO dbo.portalSchemaVersions ([version]) VALUES (5);
