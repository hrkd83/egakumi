-- Portal認証用。既存の資格情報ブローカーのDBには適用しません。
-- 呼び出し側がファイル単位のトランザクションと排他制御を行います。
SET XACT_ABORT ON;

IF OBJECT_ID(N'dbo.portalSchemaVersions', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalSchemaVersions (
        [version] int NOT NULL CONSTRAINT PK_portalSchemaVersions PRIMARY KEY,
        [appliedAt] datetime2(3) NOT NULL CONSTRAINT DF_portalSchemaVersions_appliedAt DEFAULT SYSUTCDATETIME()
    );
END;

IF OBJECT_ID(N'dbo.portalUser', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalUser (
        [id] varchar(36) NOT NULL CONSTRAINT PK_portalUser PRIMARY KEY,
        [name] nvarchar(255) NOT NULL,
        [email] nvarchar(320) NOT NULL,
        [emailVerified] bit NOT NULL CONSTRAINT DF_portalUser_emailVerified DEFAULT 0,
        [image] nvarchar(max) NULL,
        [createdAt] datetime2(3) NOT NULL,
        [updatedAt] datetime2(3) NOT NULL,
        [entraTenantId] varchar(36) NOT NULL,
        [entraObjectId] varchar(36) NOT NULL,
        CONSTRAINT UQ_portalUser_email UNIQUE NONCLUSTERED ([email]),
        CONSTRAINT UQ_portalUser_entraIdentity UNIQUE NONCLUSTERED ([entraTenantId], [entraObjectId])
    );
END;

IF OBJECT_ID(N'dbo.portalSession', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalSession (
        [id] varchar(36) NOT NULL CONSTRAINT PK_portalSession PRIMARY KEY,
        [expiresAt] datetime2(3) NOT NULL,
        [token] nvarchar(255) NOT NULL,
        [createdAt] datetime2(3) NOT NULL,
        [updatedAt] datetime2(3) NOT NULL,
        [ipAddress] nvarchar(255) NULL,
        [userAgent] nvarchar(max) NULL,
        [userId] varchar(36) NOT NULL,
        CONSTRAINT UQ_portalSession_token UNIQUE NONCLUSTERED ([token]),
        CONSTRAINT FK_portalSession_user FOREIGN KEY ([userId]) REFERENCES dbo.portalUser ([id]) ON DELETE CASCADE
    );
    CREATE INDEX IX_portalSession_userId ON dbo.portalSession ([userId]);
    CREATE INDEX IX_portalSession_expiresAt ON dbo.portalSession ([expiresAt]);
END;

IF OBJECT_ID(N'dbo.portalAccount', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalAccount (
        [id] varchar(36) NOT NULL CONSTRAINT PK_portalAccount PRIMARY KEY,
        [accountId] nvarchar(255) NOT NULL,
        [providerId] nvarchar(128) NOT NULL,
        [userId] varchar(36) NOT NULL,
        [accessToken] nvarchar(max) NULL,
        [refreshToken] nvarchar(max) NULL,
        [idToken] nvarchar(max) NULL,
        [accessTokenExpiresAt] datetime2(3) NULL,
        [refreshTokenExpiresAt] datetime2(3) NULL,
        [scope] nvarchar(2000) NULL,
        [password] nvarchar(max) NULL,
        [createdAt] datetime2(3) NOT NULL,
        [updatedAt] datetime2(3) NOT NULL,
        CONSTRAINT UQ_portalAccount_providerAccount UNIQUE NONCLUSTERED ([providerId], [accountId]),
        CONSTRAINT FK_portalAccount_user FOREIGN KEY ([userId]) REFERENCES dbo.portalUser ([id]) ON DELETE CASCADE,
        -- Better Auth標準列は残し、誤設定時もOAuthトークンとパスワードを保存させません。
        CONSTRAINT CK_portalAccount_noCredentials CHECK (
            [accessToken] IS NULL AND [refreshToken] IS NULL AND [idToken] IS NULL AND
            [accessTokenExpiresAt] IS NULL AND [refreshTokenExpiresAt] IS NULL AND [password] IS NULL
        )
    );
    CREATE INDEX IX_portalAccount_userId ON dbo.portalAccount ([userId]);
END;

IF OBJECT_ID(N'dbo.portalVerification', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalVerification (
        [id] varchar(36) NOT NULL CONSTRAINT PK_portalVerification PRIMARY KEY,
        [identifier] nvarchar(512) NOT NULL,
        [value] nvarchar(max) NOT NULL,
        [expiresAt] datetime2(3) NOT NULL,
        [createdAt] datetime2(3) NOT NULL,
        [updatedAt] datetime2(3) NOT NULL
    );
    CREATE INDEX IX_portalVerification_identifier ON dbo.portalVerification ([identifier]);
    CREATE INDEX IX_portalVerification_expiresAt ON dbo.portalVerification ([expiresAt]);
END;

IF NOT EXISTS (SELECT 1 FROM dbo.portalSchemaVersions WHERE [version] = 1)
    INSERT INTO dbo.portalSchemaVersions ([version]) VALUES (1);
