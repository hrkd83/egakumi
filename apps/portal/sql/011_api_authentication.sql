-- Portalログインから独立したGateway用OAuth資格情報です。平文の資格情報を保存しません。
SET XACT_ABORT ON;

IF OBJECT_ID(N'dbo.portalApiAuthContext', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiAuthContext (
        sessionId varchar(36) NOT NULL CONSTRAINT PK_portalApiAuthContext PRIMARY KEY,
        bindingHash char(64) NOT NULL,
        generation int NOT NULL CONSTRAINT DF_portalApiAuthContext_generation DEFAULT 0,
        revision int NOT NULL CONSTRAINT DF_portalApiAuthContext_revision DEFAULT 0,
        latestAttempt uniqueidentifier NULL,
        tokenCiphertext varbinary(max) NULL,
        nonceHash char(64) NULL,
        accessExpiresAt datetime2(3) NULL,
        connectionExpiresAt datetime2(3) NULL,
        refreshLease uniqueidentifier NULL,
        refreshLeaseUntil datetime2(3) NULL,
        refreshBlocked bit NOT NULL CONSTRAINT DF_portalApiAuthContext_refreshBlocked DEFAULT 0,
        CONSTRAINT FK_portalApiAuthContext_session FOREIGN KEY(sessionId) REFERENCES dbo.portalSession(id) ON DELETE CASCADE,
        CONSTRAINT CK_portalApiAuthContext_token CHECK(
            (tokenCiphertext IS NULL AND nonceHash IS NULL AND accessExpiresAt IS NULL AND connectionExpiresAt IS NULL) OR
            (DATALENGTH(tokenCiphertext) BETWEEN 62 AND 65597 AND nonceHash IS NOT NULL
             AND accessExpiresAt IS NOT NULL AND connectionExpiresAt IS NOT NULL)),
        CONSTRAINT CK_portalApiAuthContext_lease CHECK(
            (refreshLease IS NULL AND refreshLeaseUntil IS NULL) OR
            (refreshLease IS NOT NULL AND refreshLeaseUntil IS NOT NULL)),
        CONSTRAINT CK_portalApiAuthContext_version CHECK(generation >= 0 AND revision >= 0)
    );
    CREATE INDEX IX_portalApiAuthContext_connectionExpiresAt ON dbo.portalApiAuthContext(connectionExpiresAt);
END;

IF OBJECT_ID(N'dbo.portalApiAuthorizationTransaction', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiAuthorizationTransaction (
        attemptId uniqueidentifier NOT NULL CONSTRAINT PK_portalApiAuthorizationTransaction PRIMARY KEY,
        sessionId varchar(36) NOT NULL,
        generation int NOT NULL,
        stateHash char(64) NOT NULL CONSTRAINT UQ_portalApiAuthorizationTransaction_state UNIQUE,
        correlationHash char(64) NOT NULL,
        requestCiphertext varbinary(max) NOT NULL,
        codeCiphertext varbinary(max) NULL,
        expiresAt datetime2(3) NOT NULL,
        codeExpiresAt datetime2(3) NULL,
        CONSTRAINT FK_portalApiAuthorizationTransaction_context FOREIGN KEY(sessionId)
            REFERENCES dbo.portalApiAuthContext(sessionId) ON DELETE CASCADE,
        CONSTRAINT CK_portalApiAuthorizationTransaction_request CHECK(DATALENGTH(requestCiphertext) BETWEEN 62 AND 65597),
        CONSTRAINT CK_portalApiAuthorizationTransaction_code CHECK(
            (codeCiphertext IS NULL AND codeExpiresAt IS NULL) OR
            (DATALENGTH(codeCiphertext) BETWEEN 62 AND 65597 AND codeExpiresAt IS NOT NULL))
    );
    CREATE INDEX IX_portalApiAuthorizationTransaction_session ON dbo.portalApiAuthorizationTransaction(sessionId);
    CREATE INDEX IX_portalApiAuthorizationTransaction_expiresAt ON dbo.portalApiAuthorizationTransaction(expiresAt);
END;

IF OBJECT_ID(N'dbo.portalApiActiveOperation', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiActiveOperation (
        operationId uniqueidentifier NOT NULL CONSTRAINT PK_portalApiActiveOperation PRIMARY KEY,
        sessionId varchar(36) NOT NULL,
        generation int NOT NULL,
        operationKind varchar(10) NOT NULL,
        handleHash char(64) NOT NULL,
        operationCiphertext varbinary(max) NOT NULL,
        expiresAt datetime2(3) NOT NULL,
        CONSTRAINT FK_portalApiActiveOperation_context FOREIGN KEY(sessionId)
            REFERENCES dbo.portalApiAuthContext(sessionId) ON DELETE CASCADE,
        CONSTRAINT CK_portalApiActiveOperation_kind CHECK(operationKind IN ('document','realtime')),
        CONSTRAINT CK_portalApiActiveOperation_cipher CHECK(DATALENGTH(operationCiphertext) BETWEEN 62 AND 65596)
    );
    CREATE INDEX IX_portalApiActiveOperation_expiration ON dbo.portalApiActiveOperation(expiresAt);
    CREATE INDEX IX_portalApiActiveOperation_session ON dbo.portalApiActiveOperation(sessionId,generation);
END;

IF OBJECT_ID(N'dbo.portalApiCallCleanup', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalApiCallCleanup (
        operationHash char(64) NOT NULL CONSTRAINT PK_portalApiCallCleanup PRIMARY KEY,
        snapshotCiphertext varbinary(max) NULL,
        snapshotExpiresAt datetime2(3) NOT NULL,
        operationExpiresAt datetime2(3) NOT NULL,
        confirmed bit NULL,
        CONSTRAINT CK_portalApiCallCleanup_cipher CHECK(snapshotCiphertext IS NULL OR DATALENGTH(snapshotCiphertext) BETWEEN 62 AND 65596),
        CONSTRAINT CK_portalApiCallCleanup_expiration CHECK(snapshotExpiresAt<=operationExpiresAt)
    );
    CREATE INDEX IX_portalApiCallCleanup_expiration ON dbo.portalApiCallCleanup(operationExpiresAt,snapshotExpiresAt);
END;

IF NOT EXISTS(SELECT 1 FROM dbo.portalSchemaVersions WHERE [version]=11)
    INSERT dbo.portalSchemaVersions([version]) VALUES(11);
