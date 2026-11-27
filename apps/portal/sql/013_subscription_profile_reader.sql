-- APIMが利用制限を取得する際に、台帳の列を直接読む権限を与えません。
SET XACT_ABORT ON;
GO
CREATE OR ALTER PROCEDURE dbo.portalReadSubscriptionProfile
    @tenantId char(36),
    @apimSubscriptionId varchar(256)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT CONVERT(bit, CASE WHEN k.state='active' AND g.enabled=1 AND p.enabled=1 THEN 1 ELSE 0 END) AS ready,
        l.version, l.requestCalls, l.requestRenewalSeconds, l.tokensPerMinute, l.tokenQuota, l.tokenQuotaPeriod,
        CONVERT(varchar(23), l.updatedAt, 126)+'Z' AS updatedAt
    FROM dbo.portalApiKey k
    JOIN dbo.portalApiKeyGroup g ON g.tenantId=k.tenantId AND g.groupId=k.groupId
    JOIN dbo.portalApiKeyProduct p ON p.tenantId=k.tenantId AND p.groupId=k.groupId AND p.productId=k.productId
    LEFT JOIN dbo.portalApiKeyLimit l ON l.tenantId=k.tenantId AND l.apiKeyId=k.id
    WHERE k.tenantId=@tenantId AND k.apimSubscriptionId=@apimSubscriptionId;
END;
GO
IF NOT EXISTS(SELECT 1 FROM dbo.portalSchemaVersions WHERE [version]=13)
    INSERT dbo.portalSchemaVersions([version]) VALUES(13);
