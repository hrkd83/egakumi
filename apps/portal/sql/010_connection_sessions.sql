-- Portalが利用する本人接続証明です。Brokerのハッシュ台帳とは分離し、平文の証明を保存しません。
SET XACT_ABORT ON;
IF OBJECT_ID(N'dbo.portalConnectionState', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.portalConnectionState (
        bindingHash char(64) NOT NULL CONSTRAINT PK_portalConnectionState PRIMARY KEY,
        revision bigint NOT NULL CONSTRAINT DF_portalConnectionState_revision DEFAULT 0,
        stateCiphertext varbinary(max) NULL,
        leaseToken uniqueidentifier NULL,
        leaseUntil datetime2(3) NULL,
        updatedAt datetime2(3) NOT NULL CONSTRAINT DF_portalConnectionState_updatedAt DEFAULT SYSUTCDATETIME(),
        CONSTRAINT CK_portalConnectionState_binding CHECK(bindingHash COLLATE Latin1_General_100_BIN2 NOT LIKE '%[^0-9a-f]%' AND LEN(bindingHash)=64),
        CONSTRAINT CK_portalConnectionState_revision CHECK(revision >= 0),
        CONSTRAINT CK_portalConnectionState_cipher CHECK(
            (revision=0 AND stateCiphertext IS NULL) OR
            (revision>0 AND stateCiphertext IS NOT NULL AND DATALENGTH(stateCiphertext)>29 AND DATALENGTH(stateCiphertext)<=32797)),
        CONSTRAINT CK_portalConnectionState_lease CHECK(
            (leaseToken IS NULL AND leaseUntil IS NULL) OR (leaseToken IS NOT NULL AND leaseUntil IS NOT NULL))
    );
END;
IF NOT EXISTS(SELECT 1 FROM dbo.portalSchemaVersions WHERE [version]=10)
    INSERT dbo.portalSchemaVersions([version]) VALUES(10);
