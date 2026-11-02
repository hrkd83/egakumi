<#
.SYNOPSIS
計画的な証明書交換と再試行を、Linux上で外部接続せずに検証します。
.DESCRIPTION
workerの実関数で本物のX509・PFX・SAN・秘密鍵・chainを調べます。信頼の起点だけを
このプロセス内の合成CAへ差し替え、OSの信頼ストアは変更しません。Azure、DNS、CAの
通信はメモリー内の代役です。期限を偽装せず、現在も有効な証明書を交換します。
.EXAMPLE
pwsh -NoProfile -File tests/certificate-rotation-offline.ps1
#>
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
if (-not $IsLinux) {throw 'Run this PFX behavior suite on Linux; production EphemeralKeySet is not weakened for macOS.'}
. (Join-Path $PSScriptRoot 'certificate-fixtures.ps1') -Transform {
    param($Name,$Body)
    if ($Name -eq 'Assert-Certificate') {
        # Only the chain trust anchor is replaced; SAN, expiry and key checks run unchanged.
        $anchor='        $chain.ChainPolicy.RevocationMode='
        if (-not $Body.Contains($anchor)) {throw 'Expected chain validation seam was not found'}
        $Body=$Body.Replace($anchor,('        $chain.ChainPolicy.TrustMode=[Security.Cryptography.X509Certificates.X509ChainTrustMode]::CustomRootTrust'+"`n"+'        [void]$chain.ChainPolicy.CustomTrustStore.Add($script:FixtureAuthority)'+"`n"+$anchor))
    }
    return $Body
}
$script:checks=0
function Check([bool]$Condition,[string]$Message) {if (-not $Condition) {throw $Message};$script:checks++}
function Expect-Failure([scriptblock]$Operation,[string]$Message) {
    $failed=$false;try {& $Operation | Out-Null} catch {$failed=$true}
    Check $failed $Message
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
function Reset-Fixture {
    $script:Config=[pscustomobject]@{hostname='gateway.example.invalid';hostnames=@('*.example.invalid');requiredHostnames=@('gateway.example.invalid','portal.example.invalid');mode='dnsPlugin';dnsPlugin='Cloudflare';keyVaultName='kv-fixture';certificateName='tls-public';accountEmail='operator@example.invalid';directoryUrl='https://acme.invalid/directory';renewBeforeDays=30;minimumValidDays=7}
    $script:Action='Renew';$script:RotateFromThumbprint=$script:Old.Thumbprint.ToLowerInvariant()
    $script:DnsPluginArgumentsJson='';$script:PreparedPfxBase64='';$script:PreparedPfxPassword=''
    $script:RenewCalls=0;$script:NewCalls=0;$script:ImportCalls=0;$script:ReadCalls=0;$script:SaveCalls=0;$script:DependencyCalls=0
    $script:AccountExists=$true;$script:PasswordExists=$true;$script:FailImport=$false;$script:FailSecretRead=$false;$script:IssueSameLeaf=$false
    $script:Order=[pscustomobject]@{MainDomain='*.example.invalid';SANs=@()}
    $script:CacheOverride='';$script:RotationOutcome=''
    Set-Current $script:Old;Set-Cache $script:Old
}
# 以下だけが外部依存の代役です。証明書の検査とKV取り込み判断は実関数を使います。
function Get-CurrentCertificate {$script:ReadCalls++;return $script:Current}
function Invoke-Json([string]$Uri,[string]$Method='GET',$Body=$null,[bool]$AllowNotFound=$false) {
    if ($Uri -eq 'https://kv-fixture.vault.azure.net/secrets/tls-public/current?api-version=7.4') {
        if ($script:FailSecretRead) {throw 'Synthetic secret read denied'}
        return $script:CurrentSecret
    }
    if ($Method -eq 'POST' -and $Uri -eq 'https://kv-fixture.vault.azure.net/certificates/tls-public/import?api-version=7.4') {
        $script:ImportCalls++
        if ($script:FailImport) {throw 'Synthetic transient import failure'}
        $collection=[Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
        try {
            $collection.Import([Convert]::FromBase64String($Body.value),[string]$Body.pwd,[Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet)
            $leaf=@($collection | Where-Object {$_.HasPrivateKey})[0]
            Set-Current $leaf
            return $script:Current
        } finally {foreach ($item in $collection) {$item.Dispose()}}
    }
    throw 'Unexpected network operation'
}
function Ensure-PoshAcme {$script:DependencyCalls++}
function Restore-AcmeState {}
function Save-AcmeState {$script:SaveCalls++}
function Get-VaultSecret([string]$Name) {
    if ($Name -eq 'tls-public-dns-plugin-args') {return [pscustomobject]@{value='{"CFToken":{"type":"secureString","value":"synthetic-dns-token"}}'}}
    if ($Name -eq 'tls-public-acme-pfx-password' -and $script:PasswordExists) {return [pscustomobject]@{value=$script:Password}}
    return $null
}
function Set-VaultSecret {throw 'A planned rotation must not create missing secrets'}
function Set-PAServer {}
function Get-PAAccount {if ($script:AccountExists) {return [pscustomobject]@{id='synthetic-account'}};return $null}
function Set-PAAccount {}
function New-PAAccount {$script:NewCalls++;throw 'Unexpected new ACME account'}
function Get-PAOrder {return $script:Order}
function Set-PAOrder {}
function New-PACertificate {$script:NewCalls++;throw 'Unexpected new ACME order'}
function Get-PACertificate {
    $path=if ($script:CacheOverride) {$script:CacheOverride} else {$script:CachePath}
    # metadataは故意に誤った値。判断はPFXの実公開leafでなければなりません。
    return [pscustomobject]@{PfxFullChain=$path;Thumbprint=('f'*40)}
}
function Submit-Renewal([string]$Name,$PluginArgs,[switch]$Force) {
    Check ($Name -eq 'tls-public' -and $Force.IsPresent) 'Planned operation did not target the existing order'
    $script:RenewCalls++
    Set-Cache $(if ($script:IssueSameLeaf) {$script:Old} else {$script:New})
}
try {
    $script:Old=New-Leaf -2 88
    $script:New=New-Leaf -1 89
    $older=New-Leaf -3 87
    $wrongSan=New-Leaf -1 89 'other.invalid'
    $wrongUsage=New-Leaf -1 89 '*.example.invalid' $false
    $nearExpiry=New-Leaf -1 3
    # 同じSAN・期限・鍵でも別CAのleafは、実chain.Buildで拒否されなければなりません。
    $trustedAuthority=$script:FixtureAuthority
    $otherKey=[Security.Cryptography.RSA]::Create(2048)
    try {
        $otherRequest=[Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=Untrusted Offline CA',$otherKey,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)
        $otherRequest.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($true,$false,0,$true))
        $otherAuthority=$otherRequest.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-30),[DateTimeOffset]::UtcNow.AddDays(400))
        try {$script:FixtureAuthority=$otherAuthority;$untrusted=New-Leaf -1 89} finally {$script:FixtureAuthority=$trustedAuthority;$otherAuthority.Dispose()}
    } finally {$otherKey.Dispose()}
    Reset-Fixture
    $configBefore=$script:Config | ConvertTo-Json -Depth 10 -Compress
    Ensure-Certificate
    Check ($script:RenewCalls -eq 1 -and $script:NewCalls -eq 0 -and $script:ImportCalls -eq 1) 'Same current/cache did not cause exactly one real-order renewal'
    Check ($script:Current.cer -eq [Convert]::ToBase64String($script:New.RawData) -and $script:RotationOutcome -eq 'replacement-issued-and-imported') 'New public leaf was not adopted'
    Check (($script:Config | ConvertTo-Json -Depth 10 -Compress) -eq $configBefore) 'One-off request changed the persisted public config'
    Ensure-Certificate
    Check ($script:RenewCalls -eq 1 -and $script:ImportCalls -eq 1 -and $script:RotationOutcome -eq 'current-already-different') 'Retry after adoption issued another certificate'
    $logs=(Write-MaintenanceEvent $true 'complete') -join "`n"
    Check ($logs -match 'current-already-different' -and $logs -notmatch 'synthetic-dns-token|synthetic-pfx-password|BEGIN PRIVATE') 'Rotation logs are ambiguous or leak protected material'
    $unit=Get-RenewalUnitContent
    Check ($unit.service -notmatch 'RotateFromThumbprint' -and $unit.timer -notmatch 'RotateFromThumbprint') 'One-off rotation request reached periodic timer'

    Reset-Fixture;Set-Current $script:New
    $script:AccountExists=$false;$script:PasswordExists=$false
    Ensure-Certificate
    Check ($script:RenewCalls -eq 0 -and $script:ImportCalls -eq 0 -and $script:DependencyCalls -eq 0 -and $script:RotationOutcome -eq 'current-already-different') 'Already-different valid leaf unnecessarily contacted ACME dependencies'
    Reset-Fixture;Set-Cache $script:New
    Ensure-Certificate
    Check ($script:RenewCalls -eq 0 -and $script:ImportCalls -eq 1 -and $script:RotationOutcome -eq 'cached-replacement-imported') 'Already-issued newer cache was not recovered without CA issuance'
    Reset-Fixture;Set-Cache $older
    Ensure-Certificate
    Check ($script:RenewCalls -eq 1 -and $script:Current.cer -eq [Convert]::ToBase64String($script:New.RawData)) 'Older cached public leaf caused rollback'

    Reset-Fixture;$script:FailImport=$true
    Expect-Failure {Ensure-Certificate} 'Failed import was reported as success'
    Check ($script:RenewCalls -eq 1 -and $script:SaveCalls -eq 1 -and $script:Current.cer -eq [Convert]::ToBase64String($script:Old.RawData)) 'Failed import did not preserve old KV leaf and issued state'
    $script:FailImport=$false
    Ensure-Certificate
    Check ($script:RenewCalls -eq 1 -and $script:ImportCalls -eq 2 -and $script:RotationOutcome -eq 'cached-replacement-imported') 'Retry after import failure repeated CA issuance'

    foreach ($invalid in @('bad',('a'*39),('a'*41),('g'*40),('a'*20+':'+('a'*20)),(' '+('a'*40)),(('a'*40)+"`n"))) {
        Reset-Fixture;$script:RotateFromThumbprint=$invalid
        Expect-Failure {Ensure-Certificate} 'Invalid planned thumbprint was accepted'
        Check ($script:ReadCalls -eq 0 -and $script:RenewCalls -eq 0) 'Invalid request performed external inspection'
    }
    Reset-Fixture;$script:Action='Install'
    Expect-Failure {Ensure-Certificate} 'Install accepted a rotation request'
    Check ($script:ReadCalls -eq 0) 'Install rotation read current certificate'
    Reset-Fixture;$script:Config.mode='prepared'
    Expect-Failure {Ensure-Certificate} 'Prepared mode accepted DNS planned rotation'
    Check ($script:ReadCalls -eq 0) 'Prepared rotation read current certificate'
    Reset-Fixture;$script:Current=$null
    Expect-Failure {Ensure-Certificate} 'Missing current certificate was silently provisioned'
    Check ($script:NewCalls -eq 0 -and $script:RenewCalls -eq 0) 'Missing current certificate called CA'

    foreach ($case in @('certificate-disabled','secret-disabled','secret-denied','secret-mismatch','wrong-san','wrong-usage','untrusted-chain','too-soon')) {
        Reset-Fixture
        switch ($case) {
            'certificate-disabled' {$script:Current.attributes.enabled=$false}
            'secret-disabled' {$script:CurrentSecret.attributes.enabled=$false}
            'secret-denied' {$script:FailSecretRead=$true}
            'secret-mismatch' {$script:CurrentSecret.value=[Convert]::ToBase64String((Export-FixturePfx $script:New ''))}
            'wrong-san' {Set-Current $wrongSan;$script:RotateFromThumbprint=$wrongSan.Thumbprint}
            'wrong-usage' {Set-Current $wrongUsage;$script:RotateFromThumbprint=$wrongUsage.Thumbprint}
            'untrusted-chain' {Set-Current $untrusted;$script:RotateFromThumbprint=$untrusted.Thumbprint}
            'too-soon' {Set-Current $nearExpiry;$script:RotateFromThumbprint=$nearExpiry.Thumbprint}
        }
        Expect-Failure {Ensure-Certificate} ('Invalid current certificate was accepted: '+$case)
        Check ($script:RenewCalls -eq 0 -and $script:ImportCalls -eq 0) 'Failed full current inspection triggered a replacement'
    }
    foreach ($case in @('account','order','domains','password')) {
        Reset-Fixture
        switch ($case) {
            'account' {$script:AccountExists=$false}
            'order' {$script:Order=$null}
            'domains' {$script:Order=[pscustomobject]@{MainDomain='other.invalid';SANs=@()}}
            'password' {$script:PasswordExists=$false}
        }
        Expect-Failure {Ensure-Certificate} ('Missing existing ACME state accepted: '+$case)
        Check ($script:RenewCalls -eq 0 -and $script:NewCalls -eq 0 -and $script:ImportCalls -eq 0) 'Missing state caused account creation or new issuance'
    }
    Reset-Fixture;$script:IssueSameLeaf=$true
    Expect-Failure {Ensure-Certificate} 'Unchanged CA public leaf was reported as a replacement'
    Check ($script:RenewCalls -eq 1 -and $script:ImportCalls -eq 0) 'Unchanged CA leaf reached Vault import'
    Reset-Fixture;$script:CacheOverride=Join-Path $fixture 'outside.pfx'
    Expect-Failure {Ensure-Certificate} 'Cache metadata escaped its protected ACME directory'
    Check ($script:RenewCalls -eq 0 -and $script:ImportCalls -eq 0) 'Untrusted cache path called CA or Vault import'
    Reset-Fixture;Set-Cache $wrongUsage
    Expect-Failure {Ensure-Certificate} 'Invalid cached TLS usage passed full inspection'
    Check ($script:RenewCalls -eq 0) 'Invalid cached chain/usage caused blind issuance'
    Reset-Fixture;Set-Cache $untrusted
    Expect-Failure {Ensure-Certificate} 'Untrusted cached chain passed real validation'
    Check ($script:RenewCalls -eq 0 -and $script:ImportCalls -eq 0) 'Untrusted cached chain caused blind issuance or import'
    Reset-Fixture
    $link=Join-Path $env:POSHACME_HOME 'linked.pfx'
    [void][IO.File]::CreateSymbolicLink($link,$script:CachePath);$script:CacheOverride=$link
    Expect-Failure {Ensure-Certificate} 'Symlink cache was accepted for planned rotation'
    Check ($script:RenewCalls -eq 0 -and $script:ImportCalls -eq 0) 'Symlink cache caused issuance or import'
    Remove-Item -LiteralPath $link -Force

    # 通常up/timerはfreshな既存leafを再利用し、計画更新へ変わりません。
    foreach ($actionValue in @('Install','Renew')) {
        Reset-Fixture;$script:RotateFromThumbprint='';$script:Action=$actionValue
        Ensure-Certificate
        Check ($script:RenewCalls -eq 0 -and $script:NewCalls -eq 0 -and $script:ImportCalls -eq 0 -and $script:RotationOutcome -eq '') 'Normal maintenance behavior changed'
    }
    Reset-Fixture
    [Environment]::SetEnvironmentVariable('RotateFromThumbprint',$script:Old.Thumbprint)
    $script:RotateFromThumbprint='';Read-ManagedInput
    Check ($script:RotateFromThumbprint -eq $script:Old.Thumbprint -and [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('RotateFromThumbprint'))) 'Named managed input was not consumed and removed'
} finally {
    foreach ($leaf in $script:Certs) {$leaf.Dispose()}
    $script:FixtureAuthority.Dispose();$caKey.Dispose()
    if (Test-Path -LiteralPath $fixture) {Remove-Item -LiteralPath $fixture -Recurse -Force}
    [Environment]::SetEnvironmentVariable('POSHACME_HOME',$null)
    [Environment]::SetEnvironmentVariable('RotateFromThumbprint',$null)
}
"Certificate planned rotation: $script:checks checks PASS; real X509/PFX/chain decisions; no Azure, DNS or CA requests."
