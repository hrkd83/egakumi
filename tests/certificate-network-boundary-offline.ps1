<#
.SYNOPSIS
証明書workerの実HTTP境界を、ネットワークへ接続せず検証します。
#>
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'certificate-fixtures.ps1')

# Keep Invoke-Json unchanged. Intercept the .NET request factory so that even
# a regression in its allowlist cannot contact Azure or any other endpoint.
Add-Type -TypeDefinition @'
using System;
using System.Net;
public sealed class OfflineCertificateRequestFactory : IWebRequestCreate {
    public static int Calls;
    public WebRequest Create(Uri uri) {
        Calls++;
        throw new InvalidOperationException("Offline certificate network sentinel");
    }
}
'@
foreach ($prefix in @('http://', 'https://')) {
    if (-not [Net.WebRequest]::RegisterPrefix($prefix, [OfflineCertificateRequestFactory]::new())) {
        throw 'Could not install offline request factory'
    }
}
$script:Config=[pscustomobject]@{keyVaultName='kv-fixture'}
$Action='Install'

foreach ($target in @(
    'https://management.azure.com/subscriptions/fixture/resourceGroups/fixture/providers/Microsoft.ApiManagement/service/apim-fixture?api-version=2024-05-01',
    'https://management.azure.com/',
    'https://other-vault.vault.azure.net/secrets/fixture',
    'https://kv-fixture.vault.azure.net.untrusted.invalid/secrets/fixture'
)) {
    $denied=$false
    try {Invoke-Json $target 'PATCH' @{} | Out-Null}
    catch {
        if ($_.Exception.Message -ne 'Unexpected certificate service endpoint') {throw 'Unexpected transport rejection'}
        $denied=$true
    }
    if (-not $denied -or [OfflineCertificateRequestFactory]::Calls -ne 0) {
        throw 'Forbidden certificate request reached the network factory'
    }
}

# Positive controls prove the interceptor is active and that the real guard
# still permits its two intended destinations. No request object is created.
foreach ($target in @(
    'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fmanagement.azure.com%2F',
    'https://kv-fixture.vault.azure.net/secrets/fixture?api-version=7.4'
)) {
    $before=[OfflineCertificateRequestFactory]::Calls
    $intercepted=$false
    try {Invoke-Json $target | Out-Null}
    catch {
        if ($_.Exception.Message -notlike '*Offline certificate network sentinel*') {throw 'Allowed endpoint did not reach offline factory'}
        $intercepted=$true
    }
    if (-not $intercepted -or [OfflineCertificateRequestFactory]::Calls -ne $before+1) {
        throw 'Offline factory did not intercept the allowed request'
    }
}
'Certificate transport boundary PASS; ARM/APIM and foreign Vault requests denied; no network requests.'
