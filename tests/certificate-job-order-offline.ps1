# Test the production ACA order checkpoint functions using independent, in-memory fixtures.
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'certificate-fixtures.ps1')
$script:Config=[pscustomobject]@{ certificateName='synthetic'; dnsPlugin='Azure' }
$script:events=[Collections.Generic.List[string]]::new()
$script:previous=[pscustomobject]@{ location='previous-order' }
$script:actual=[pscustomobject]@{ location='new-order' }

function Save-CertificateJobOrderIntent($State) { $script:events.Add($State.status) }
function Save-AcmeState { $script:events.Add('state-backed-up') }
function New-PAOrder {
    param($Domain,$Name,$Plugin,$PluginArgs,$KeyLength,$PfxPassSecure,$DnsSleep,$ValidationTimeout,[switch]$Force)
    if ($Name -ne 'synthetic' -or $Plugin -ne 'Azure' -or -not $Force) { throw 'Unexpected order' }
    $script:events.Add('ca-request')
}
function Get-PAOrder($Name) { return $script:actual }

$order=New-CertificateJobOrder $script:previous @('service.example.com') @{} 'synthetic'
if ($order.location -ne 'new-order' -or $script:events -join ',' -ne 'requesting,ca-request,state-backed-up,saved') {
    throw 'ACME ordering was not protected before validation'
}

$script:events.Clear()
$intent=@{ status='requesting'; previous='previous-order' }
try {
    $null=Resolve-CertificateJobOrder $intent $script:previous $true
    throw 'Uncertain order must stop'
} catch {
    if ($_.Exception.Message -ne 'ACME issuance needs order reconciliation') {throw}
}
if ($script:events.Count) {throw 'Uncertain order caused a write'}
$reconciled=Resolve-CertificateJobOrder $intent $script:actual $true
if ($reconciled.location -ne 'new-order' -or $script:events -join ',' -ne 'saved') {
    throw 'A saved replacement order was not reconciled'
}

. (Join-Path $PSScriptRoot '../apps/certificate-jobs/certificate-job.ps1') -LibraryOnly
$config=Get-PortalJobConfiguration '{"validityMonths":12}' '{"certificateId":"synthetic-reference"}' 'inspect' | ConvertFrom-Json -AsHashtable
if ($config.certificateId -ne 'synthetic-reference' -or $config.validityMonths -ne 12) {throw 'Incorrect Portal input'}
try {
    $null=Get-PortalJobConfiguration '{}' '{"vaultName":"unexpected"}' 'inspect'
    throw 'Unsafe override was accepted'
} catch {
    if ($_.Exception.Message -ne 'Invalid Portal certificate operation input') {throw}
}

$defaultChain=[Security.Cryptography.X509Certificates.X509Chain]::new()
$testChain=[Security.Cryptography.X509Certificates.X509Chain]::new()
try {
    $anchors=(Join-Path $PSScriptRoot '../apps/certificate-jobs/staging-ca.crt')
    Set-CertificateJobChainTrust $defaultChain 'https://acme-v02.api.letsencrypt.org/directory' $anchors
    if ($defaultChain.ChainPolicy.TrustMode -ne 'System' -or $defaultChain.ChainPolicy.CustomTrustStore.Count) {
        throw 'Production was assigned staging roots'
    }
    Set-CertificateJobChainTrust $testChain 'https://acme-staging-v02.api.letsencrypt.org/directory' $anchors
    if ($testChain.ChainPolicy.TrustMode -ne 'CustomRootTrust' -or $testChain.ChainPolicy.CustomTrustStore.Count -ne 4) {
        throw 'Staging trust roots were not restricted'
    }
} finally { $defaultChain.Dispose(); $testChain.Dispose() }
