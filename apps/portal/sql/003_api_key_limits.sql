-- APIMの共通XMLを購読ごとの編集で変更せず、SQLに設定値だけを保持します。
-- 利用量の集計・課金カウンター・購読の秘密キーはここには保存しません。
SET XACT_ABORT ON;

IF OBJECT_ID(N'dbo.portalApiKeyLimit', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiKeyLimit (
        tenantId char(36) NOT NULL,
        apiKeyId varchar(43) NOT NULL,
        version int NOT NULL,
        requestCalls int NULL,
        requestRenewalSeconds int NULL,
        tokensPerMinute int NULL,
        tokenQuota int NULL,
        tokenQuotaPeriod varchar(7) NULL,
        updatedAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyLimit_updatedAt DEFAULT SYSUTCDATETIME(),
        updatedByObjectId char(36) NOT NULL,
        CONSTRAINT PK_portalApiKeyLimit PRIMARY KEY (tenantId, apiKeyId),
        CONSTRAINT FK_portalApiKeyLimit_key FOREIGN KEY (tenantId, apiKeyId)
            REFERENCES dbo.portalApiKey (tenantId, id),
        CONSTRAINT CK_portalApiKeyLimit_version CHECK (version >= 1),
        CONSTRAINT CK_portalApiKeyLimit_request CHECK (
            (requestCalls IS NULL AND requestRenewalSeconds IS NULL) OR
            (requestCalls IS NOT NULL AND requestRenewalSeconds IS NOT NULL AND
                requestCalls BETWEEN 1 AND 1000000 AND requestRenewalSeconds BETWEEN 1 AND 300)),
        CONSTRAINT CK_portalApiKeyLimit_tpm CHECK (tokensPerMinute IS NULL OR tokensPerMinute >= 1),
        CONSTRAINT CK_portalApiKeyLimit_quota CHECK (
            (tokenQuota IS NULL AND tokenQuotaPeriod IS NULL) OR
            (tokenQuota IS NOT NULL AND tokenQuotaPeriod IS NOT NULL AND tokenQuota >= 1 AND
                tokenQuotaPeriod IN ('Hourly', 'Daily', 'Weekly', 'Monthly', 'Yearly')))
    );
END;

-- 設定値の前後・操作した本人・版を同じトランザクションで保存します。
IF OBJECT_ID(N'dbo.portalApiKeyLimitAudit', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiKeyLimitAudit (
        id char(36) NOT NULL CONSTRAINT PK_portalApiKeyLimitAudit PRIMARY KEY,
        tenantId char(36) NOT NULL,
        apiKeyId varchar(43) NOT NULL,
        actorObjectId char(36) NOT NULL,
        version int NOT NULL,
        previousProfile nvarchar(1024) NOT NULL,
        profile nvarchar(1024) NOT NULL,
        recordedAt datetime2(3) NOT NULL CONSTRAINT DF_portalApiKeyLimitAudit_recordedAt DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_portalApiKeyLimitAudit_key FOREIGN KEY (tenantId, apiKeyId)
            REFERENCES dbo.portalApiKey (tenantId, id),
        CONSTRAINT UQ_portalApiKeyLimitAudit_version UNIQUE (tenantId, apiKeyId, version),
        CONSTRAINT CK_portalApiKeyLimitAudit_version CHECK (version >= 1),
        CONSTRAINT CK_portalApiKeyLimitAudit_json CHECK (ISJSON(previousProfile)=1 AND ISJSON(profile)=1)
    );
    CREATE INDEX IX_portalApiKeyLimitAudit_key ON dbo.portalApiKeyLimitAudit (tenantId, apiKeyId, recordedAt);
END;

IF NOT EXISTS (SELECT 1 FROM dbo.portalSchemaVersions WHERE [version] = 3)
    INSERT INTO dbo.portalSchemaVersions ([version]) VALUES (3);
