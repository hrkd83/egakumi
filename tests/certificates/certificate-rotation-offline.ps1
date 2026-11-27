# Linuxで証明書Jobと同じPFX読込みを使い、鍵・SAN・用途・chainと差替え条件を検証します。
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
if (-not $IsLinux) {throw 'Run the certificate PFX fixture on Linux'}
. (Join-Path $PSScriptRoot 'certificate-fixtures.ps1')
$ContainerJob=$true
$script:Config=[pscustomobject]@{
    hostname='gateway.example.invalid';hostnames=@('*.example.invalid')
    requiredHostnames=@('gateway.example.invalid','portal.example.invalid')
    keyVaultName='kv-fixture';certificateName='tls-public';directoryUrl='https://acme.invalid/directory'
    minimumValidDays=7
}

function Check([bool]$Condition,[string]$Message) {if (-not $Condition) {throw $Message}}
function Expect-Failure([scriptblock]$Operation,[string]$Message) {
    try {& $Operation | Out-Null}
    catch {
        if ($_.Exception.Message -cne $Message) {throw}
        return
    }
    throw "Expected certificate validation to stop: $Message"
}

# OSの信頼ストアには触れず、chain.Buildの起点をこのプロセス内の合成CAに限定します。
function Set-CertificateJobChainTrust($Chain,[string]$DirectoryUrl) {
    if ($DirectoryUrl -cne $script:Config.directoryUrl) {throw 'Unexpected trust scope'}
    $Chain.ChainPolicy.TrustMode=[Security.Cryptography.X509Certificates.X509ChainTrustMode]::CustomRootTrust
    [void]$Chain.ChainPolicy.CustomTrustStore.Add($script:FixtureAuthority)
}

$fixture=Join-Path '/tmp' ('certificate-rotation-'+[Guid]::NewGuid().ToString('N'))
New-PrivateDirectory $fixture
$script:Root=$fixture
$env:POSHACME_HOME=Join-Path $fixture 'acme'
New-PrivateDirectory $env:POSHACME_HOME
$script:CachePath=Join-Path $env:POSHACME_HOME 'fullchain.pfx'
$script:Password='synthetic-pfx-password'
$script:Certs=[Collections.Generic.List[Security.Cryptography.X509Certificates.X509Certificate2]]::new()
$caKey=[Security.Cryptography.RSA]::Create(2048)
$caRequest=[Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=Offline Rotation CA',$caKey,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)
$caRequest.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($true,$false,0,$true))
$caRequest.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new([Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyCertSign,$true))
$script:FixtureAuthority=$caRequest.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-30),[DateTimeOffset]::UtcNow.AddDays(400))
function New-Leaf([int]$FromDays,[int]$ToDays,[string]$DnsName='*.example.invalid',[bool]$TlsUsage=$true) {
    $key=[Security.Cryptography.RSA]::Create(2048)
    try {
        $request=[Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=Offline Rotation Leaf',$key,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)
        $san=[Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new();$san.AddDnsName($DnsName)
        $request.CertificateExtensions.Add($san.Build())
        $usage=[Security.Cryptography.OidCollection]::new()
        [void]$usage.Add([Security.Cryptography.Oid]::new($(if ($TlsUsage) {'1.3.6.1.5.5.7.3.1'} else {'1.3.6.1.5.5.7.3.2'})))
        $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($usage,$false))
        $serial=[byte[]]::new(16);[Security.Cryptography.RandomNumberGenerator]::Fill($serial)
        $public=$request.Create($script:FixtureAuthority,[DateTimeOffset]::UtcNow.AddDays($FromDays),[DateTimeOffset]::UtcNow.AddDays($ToDays),$serial)
        try {$leaf=[Security.Cryptography.X509Certificates.RSACertificateExtensions]::CopyWithPrivateKey($public,$key)} finally {$public.Dispose()}
        $script:Certs.Add($leaf)
        return $leaf
    } finally {$key.Dispose()}
}
function Export-FixturePfx($Leaf,[string]$Password) {
    $collection=[Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
    [void]$collection.Add($Leaf)
    # CAの秘密鍵は配布せず、公開CAだけをfullchainへ入れます。
    $publicCa=[Security.Cryptography.X509Certificates.X509Certificate2]::new($script:FixtureAuthority.RawData)
    try {[void]$collection.Add($publicCa);return ,($collection.Export([Security.Cryptography.X509Certificates.X509ContentType]::Pfx,$Password))} finally {$publicCa.Dispose()}
}
function Set-Current($Leaf) {
    $script:Current=[pscustomobject]@{
        cer=[Convert]::ToBase64String($Leaf.RawData)
        sid='https://kv-fixture.vault.azure.net/secrets/tls-public/current'
        attributes=[pscustomobject]@{enabled=$true}
        policy=[pscustomobject]@{key_props=[pscustomobject]@{exportable=$true};secret_props=[pscustomobject]@{contentType='application/x-pkcs12'}}
    }
    $script:CurrentSecret=[pscustomobject]@{value=[Convert]::ToBase64String((Export-FixturePfx $Leaf ''));attributes=[pscustomobject]@{enabled=$true}}
}
function Set-Cache($Leaf) {Write-PrivateFile $script:CachePath (Export-FixturePfx $Leaf $script:Password)}

$script:PostCalls=0
$script:ImportMode='normal'
function Get-CurrentCertificate {return $script:Current}
function Invoke-Json([string]$Uri,[string]$Method='GET',$Body=$null) {
    if ($Method -eq 'GET' -and $Uri -eq ($script:Current.sid+'?api-version=7.4')) {return $script:CurrentSecret}
    $origin='https://'+$script:Config.keyVaultName+'.vault.azure.net'
    if ($Method -cne 'POST' -or $Uri -cne ($origin+'/certificates/tls-public/import?api-version=7.4')) {
        throw 'Unexpected mocked Vault request'
    }
    $script:PostCalls++
    Check ($Body.pwd -ceq $script:Password -and $Body.policy.key_props.reuse_key -eq $false) 'Invalid Vault import policy'
    Check ($Body.value -ceq [Convert]::ToBase64String($script:ReplacementPfx)) 'Vault import sent different bytes'
    if ($script:ImportMode -ne 'unconfirmed') {Set-Current $script:Replacement}
    if ($script:ImportMode -ne 'normal') {throw 'SYNTHETIC_IMPORT_RESPONSE_LOST'}
    return $script:Current
}

try {
    $current=New-Leaf -2 88
    $script:Replacement=New-Leaf -1 89
    $older=New-Leaf -3 87
    $wrongSan=New-Leaf -1 89 'other.invalid'
    $wrongUsage=New-Leaf -1 89 '*.example.invalid' $false
    $nearExpiry=New-Leaf -1 3

    $trustedAuthority=$script:FixtureAuthority
    $otherKey=[Security.Cryptography.RSA]::Create(2048)
    try {
        $otherRequest=[Security.Cryptography.X509Certificates.CertificateRequest]::new(
            'CN=Untrusted Offline CA',$otherKey,[Security.Cryptography.HashAlgorithmName]::SHA256,
            [Security.Cryptography.RSASignaturePadding]::Pkcs1)
        $otherRequest.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($true,$false,0,$true))
        $otherAuthority=$otherRequest.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-30),[DateTimeOffset]::UtcNow.AddDays(400))
        try {$script:FixtureAuthority=$otherAuthority;$untrusted=New-Leaf -1 89}
        finally {$script:FixtureAuthority=$trustedAuthority;$otherAuthority.Dispose()}
    } finally {$otherKey.Dispose()}

    Set-Current $current
    Check (Test-ReusableCertificate $script:Current 30) 'Registered certificate and its PFX did not match'
    $script:CurrentSecret.value=[Convert]::ToBase64String((Export-FixturePfx $script:Replacement ''))
    Expect-Failure {Test-ReusableCertificate $script:Current 30} 'Existing certificate and secret do not match'
    Set-Current $current
    $script:CurrentSecret.attributes.enabled=$false
    Expect-Failure {Test-ReusableCertificate $script:Current 30} 'The existing certificate secret was disabled by an operator'
    Set-Current $nearExpiry
    Check (-not (Test-ReusableCertificate $script:Current 30)) 'Expiring certificate was reused'

    $reference=@{thumbprint=$current.Thumbprint;notBeforeUtc=$current.NotBefore.ToUniversalTime();notAfterUtc=$current.NotAfter.ToUniversalTime()}
    $candidate=[pscustomobject]@{PfxFullChain=$script:CachePath}
    Set-Cache $script:Replacement
    Check (Test-CachedAcmeCertificate $candidate $script:Password 7 $reference) 'Valid replacement PFX was rejected'
    foreach ($leaf in @($current,$older,$wrongSan,$nearExpiry)) {
        Set-Cache $leaf
        Check (-not (Test-CachedAcmeCertificate $candidate $script:Password 7 $reference)) 'Unsuitable PFX was accepted for rotation'
    }
    Set-Cache $wrongUsage
    Expect-Failure {Test-CachedAcmeCertificate $candidate $script:Password 7 $reference} 'TLS server authentication usage is required'
    Set-Cache $untrusted
    Expect-Failure {Test-CachedAcmeCertificate $candidate $script:Password 7 $reference} 'Certificate trust chain verification failed'

    $script:ReplacementPfx=Export-FixturePfx $script:Replacement $script:Password
    Set-Current $current
    $script:ImportMode='unconfirmed'
    Expect-Failure {Import-Pfx $script:ReplacementPfx $script:Password} 'Certificate import was not confirmed'
    Check ($script:PostCalls -eq 1) 'Unconfirmed import was repeated'
    $script:ImportMode='lost-after-write'
    Import-Pfx $script:ReplacementPfx $script:Password
    Check ($script:PostCalls -eq 2) 'Successful import was not verified after a lost response'
    Import-Pfx $script:ReplacementPfx $script:Password
    Check ($script:PostCalls -eq 2) 'Existing identical certificate was imported again'
} finally {
    foreach ($leaf in $script:Certs) {$leaf.Dispose()}
    $script:FixtureAuthority.Dispose();$caKey.Dispose()
    if (Test-Path -LiteralPath $fixture) {Remove-Item -LiteralPath $fixture -Recurse -Force}
}
'Certificate Linux PFX PASS; no Azure, DNS or CA requests.'
