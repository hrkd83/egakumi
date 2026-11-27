-- 購読管理用の本人委任OAuth取引と暗号化トークンを専用テーブルで管理します。
SET XACT_ABORT ON;

IF OBJECT_ID(N'dbo.portalSubscriptionAuthContext', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalSubscriptionAuthContext (
        sessionId varchar(36) NOT NULL CONSTRAINT PK_portalSubscriptionAuthContext PRIMARY KEY,
        bindingHash char(64) NOT NULL,
        generation int NOT NULL CONSTRAINT DF_portalSubscriptionAuthContext_generation DEFAULT 0,
        revision int NOT NULL CONSTRAINT DF_portalSubscriptionAuthContext_revision DEFAULT 0,
        latestAttempt uniqueidentifier NULL,
        tokenCiphertext varbinary(max) NULL,
        nonceHash char(64) NULL,
        accessExpiresAt datetime2(3) NULL,
        connectionExpiresAt datetime2(3) NULL,
        refreshLease uniqueidentifier NULL,
        refreshLeaseUntil datetime2(3) NULL,
        refreshBlocked bit NOT NULL CONSTRAINT DF_portalSubscriptionAuthContext_refreshBlocked DEFAULT 0,
        CONSTRAINT FK_portalSubscriptionAuthContext_session FOREIGN KEY(sessionId) REFERENCES dbo.portalSession(id) ON DELETE CASCADE,
        CONSTRAINT CK_portalSubscriptionAuthContext_token CHECK(
            (tokenCiphertext IS NULL AND nonceHash IS NULL AND accessExpiresAt IS NULL AND connectionExpiresAt IS NULL) OR
            (DATALENGTH(tokenCiphertext) BETWEEN 62 AND 65597 AND nonceHash IS NOT NULL
             AND accessExpiresAt IS NOT NULL AND connectionExpiresAt IS NOT NULL)),
        CONSTRAINT CK_portalSubscriptionAuthContext_lease CHECK(
            (refreshLease IS NULL AND refreshLeaseUntil IS NULL) OR
            (refreshLease IS NOT NULL AND refreshLeaseUntil IS NOT NULL)),
        CONSTRAINT CK_portalSubscriptionAuthContext_version CHECK(generation >= 0 AND revision >= 0)
    );
    CREATE INDEX IX_portalSubscriptionAuthContext_connectionExpiresAt ON dbo.portalSubscriptionAuthContext(connectionExpiresAt);
END;

IF OBJECT_ID(N'dbo.portalSubscriptionAuthorizationTransaction', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalSubscriptionAuthorizationTransaction (
        attemptId uniqueidentifier NOT NULL CONSTRAINT PK_portalSubscriptionAuthorizationTransaction PRIMARY KEY,
        sessionId varchar(36) NOT NULL,
        generation int NOT NULL,
        stateHash char(64) NOT NULL CONSTRAINT UQ_portalSubscriptionAuthorizationTransaction_state UNIQUE,
        correlationHash char(64) NOT NULL,
        requestCiphertext varbinary(max) NOT NULL,
        codeCiphertext varbinary(max) NULL,
        expiresAt datetime2(3) NOT NULL,
        codeExpiresAt datetime2(3) NULL,
        CONSTRAINT FK_portalSubscriptionAuthorizationTransaction_context FOREIGN KEY(sessionId)
            REFERENCES dbo.portalSubscriptionAuthContext(sessionId) ON DELETE CASCADE,
        CONSTRAINT CK_portalSubscriptionAuthorizationTransaction_request CHECK(DATALENGTH(requestCiphertext) BETWEEN 62 AND 65597),
        CONSTRAINT CK_portalSubscriptionAuthorizationTransaction_code CHECK(
            (codeCiphertext IS NULL AND codeExpiresAt IS NULL) OR
            (DATALENGTH(codeCiphertext) BETWEEN 62 AND 65597 AND codeExpiresAt IS NOT NULL))
    );
    CREATE INDEX IX_portalSubscriptionAuthorizationTransaction_session ON dbo.portalSubscriptionAuthorizationTransaction(sessionId);
    CREATE INDEX IX_portalSubscriptionAuthorizationTransaction_expiresAt ON dbo.portalSubscriptionAuthorizationTransaction(expiresAt);
END;

IF NOT EXISTS(SELECT 1 FROM dbo.portalSchemaVersions WHERE [version]=12)
    INSERT dbo.portalSchemaVersions([version]) VALUES(12);
