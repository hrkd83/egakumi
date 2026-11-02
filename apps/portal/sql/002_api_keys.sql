-- APIキーの所有者はEntraグループです。個人の認証テーブルとは独立して残します。
-- 秘密のキー値はAPIMだけに置き、この台帳・監査表には保存しません。
SET XACT_ABORT ON;

IF OBJECT_ID(N'dbo.portalApiKeyGroup', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiKeyGroup (
        tenantId char(36) NOT NULL,
        groupId char(36) NOT NULL,
        displayName nvarchar(255) NOT NULL,
        enabled bit NOT NULL CONSTRAINT DF_portalApiKeyGroup_enabled DEFAULT 1,
        createdAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyGroup_createdAt DEFAULT SYSUTCDATETIME(),
        updatedAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyGroup_updatedAt DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_portalApiKeyGroup PRIMARY KEY (tenantId, groupId)
    );
END;

IF OBJECT_ID(N'dbo.portalApiKeyProduct', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiKeyProduct (
        tenantId char(36) NOT NULL,
        groupId char(36) NOT NULL,
        productId varchar(80) NOT NULL,
        displayName nvarchar(255) NOT NULL,
        maxSubscriptions int NOT NULL CONSTRAINT DF_portalApiKeyProduct_maxSubscriptions DEFAULT 5,
        enabled bit NOT NULL CONSTRAINT DF_portalApiKeyProduct_enabled DEFAULT 1,
        createdAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyProduct_createdAt DEFAULT SYSUTCDATETIME(),
        updatedAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyProduct_updatedAt DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_portalApiKeyProduct PRIMARY KEY (tenantId, groupId, productId),
        CONSTRAINT FK_portalApiKeyProduct_group FOREIGN KEY (tenantId, groupId)
            REFERENCES dbo.portalApiKeyGroup (tenantId, groupId),
        CONSTRAINT CK_portalApiKeyProduct_limit CHECK (maxSubscriptions BETWEEN 1 AND 1000)
    );
END;

IF OBJECT_ID(N'dbo.portalApiKey', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiKey (
        id varchar(43) NOT NULL,
        tenantId char(36) NOT NULL,
        groupId char(36) NOT NULL,
        productId varchar(80) NOT NULL,
        displayName nvarchar(80) NOT NULL,
        state varchar(12) NOT NULL,
        budgetMode varchar(12) NOT NULL CONSTRAINT DF_portalApiKey_budgetMode DEFAULT 'unlimited',
        createdByObjectId char(36) NOT NULL,
        idempotencyKey char(36) NOT NULL,
        createdAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKey_createdAt DEFAULT SYSUTCDATETIME(),
        updatedAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKey_updatedAt DEFAULT SYSUTCDATETIME(),
        lastReviewedAt datetime2(3) NULL,
        lastReviewedByObjectId char(36) NULL,
        CONSTRAINT PK_portalApiKey PRIMARY KEY (id),
        CONSTRAINT UQ_portalApiKey_tenantId UNIQUE (tenantId, id),
        CONSTRAINT UQ_portalApiKey_idempotency UNIQUE (tenantId, createdByObjectId, idempotencyKey),
        CONSTRAINT FK_portalApiKey_product FOREIGN KEY (tenantId, groupId, productId)
            REFERENCES dbo.portalApiKeyProduct (tenantId, groupId, productId),
        CONSTRAINT CK_portalApiKey_id CHECK (id LIKE 'portal-%'),
        CONSTRAINT CK_portalApiKey_state CHECK (state IN ('creating', 'active', 'suspended', 'deleting', 'deleted'))
    );
    CREATE INDEX IX_portalApiKey_owner ON dbo.portalApiKey (tenantId, groupId, productId, state);
END;

-- 金額の上限判定は未実装のため、有限の予算値を保存して適用済みと見せません。
-- 開発中に旧002を適用済みのローカルDBも、データを保持したまま更新します。
IF COL_LENGTH(N'dbo.portalApiKey', N'budgetMode') IS NULL
    ALTER TABLE dbo.portalApiKey ADD budgetMode varchar(12) NOT NULL
        CONSTRAINT DF_portalApiKey_budgetMode DEFAULT 'unlimited';
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name=N'CK_portalApiKey_budgetMode' AND parent_object_id=OBJECT_ID(N'dbo.portalApiKey'))
    EXEC(N'ALTER TABLE dbo.portalApiKey ADD CONSTRAINT CK_portalApiKey_budgetMode CHECK (budgetMode=''unlimited'')');

IF OBJECT_ID(N'dbo.portalApiKeyAudit', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiKeyAudit (
        id char(36) NOT NULL CONSTRAINT PK_portalApiKeyAudit PRIMARY KEY,
        tenantId char(36) NOT NULL,
        apiKeyId varchar(43) NOT NULL,
        actorObjectId char(36) NOT NULL,
        action varchar(16) NOT NULL,
        state varchar(12) NOT NULL,
        recordedAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyAudit_recordedAt DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_portalApiKeyAudit_key FOREIGN KEY (tenantId, apiKeyId)
            REFERENCES dbo.portalApiKey (tenantId, id),
        CONSTRAINT CK_portalApiKeyAudit_action CHECK (action IN ('create', 'reveal', 'suspend', 'resume', 'review', 'delete')),
        CONSTRAINT CK_portalApiKeyAudit_state CHECK (state IN ('creating', 'active', 'suspended', 'deleting', 'deleted'))
    );
    CREATE INDEX IX_portalApiKeyAudit_key ON dbo.portalApiKeyAudit (tenantId, apiKeyId, recordedAt);
END;

IF NOT EXISTS (SELECT 1 FROM dbo.portalSchemaVersions WHERE [version] = 2)
    INSERT INTO dbo.portalSchemaVersions ([version]) VALUES (2);
