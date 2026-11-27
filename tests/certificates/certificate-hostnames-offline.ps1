<#
.SYNOPSIS
証明書の複数SAN・wildcard・再利用の判断を、外部へ接続せず検証します。
.DESCRIPTION
実際の証明書をメモリー内で作り、SANのDER読取と全ホストへの適用可否を調べます。
Key Vault・CA・DNSのAPIは呼びません。
.EXAMPLE
pwsh -NoProfile -File tests/certificates/certificate-hostnames-offline.ps1
#>
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'certificate-fixtures.ps1')
$checks=0
function Check([bool]$Condition,[string]$Message) {if (-not $Condition) {throw $Message};$script:checks++}
function Set-FixtureConfig {
    $script:Config=[pscustomobject]@{
        hostname='gateway.example.invalid';hostnames=@('*.example.invalid')
        requiredHostnames=@('gateway.example.invalid','portal.example.invalid')
        dnsPlugin='Azure';keyVaultName='kv-fixture';certificateName='tls-public';mode='dnsPlugin'
        accountEmail='test@example.invalid';directoryUrl='https://acme-v02.api.letsencrypt.org/directory'
        renewBeforeDays=30;minimumValidDays=7
    }
}
# 自己署名証明書はSAN検査専用です。OSの信頼ストアへ登録しません。
function New-FixtureCertificate([string[]]$Names) {
    $key=[Security.Cryptography.RSA]::Create(2048)
    try {
        $request=[Security.Cryptography.X509Certificates.CertificateRequest]::new(
            'CN=fixture.invalid',$key,[Security.Cryptography.HashAlgorithmName]::SHA256,
            [Security.Cryptography.RSASignaturePadding]::Pkcs1)
        $san=[Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
        foreach ($name in $Names) {$san.AddDnsName($name)}
        $request.CertificateExtensions.Add($san.Build())
        $signer=[Security.Cryptography.X509Certificates.X509SignatureGenerator]::CreateForRSA(
            $key,[Security.Cryptography.RSASignaturePadding]::Pkcs1)
        $serial=[byte[]]::new(16)
        [Security.Cryptography.RandomNumberGenerator]::Fill($serial)
        return $request.Create($request.SubjectName,$signer,[DateTimeOffset]::UtcNow.AddDays(-1),
            [DateTimeOffset]::UtcNow.AddDays(90),$serial)
    } finally {$key.Dispose()}
}
Set-FixtureConfig
Check ((@(Get-CertificateHostnames 'hostnames') -join ',') -eq '*.example.invalid') 'Wildcard SAN configuration differs'
Check ((@(Get-CertificateHostnames 'requiredHostnames')).Count -eq 2) 'Both public hostnames are required'
foreach ($case in @(
    @('*.example.invalid','gateway.example.invalid',$true),
    @('*.example.invalid','portal.example.invalid',$true),
    @('*.example.invalid','example.invalid',$false),
    @('*.example.invalid','a.b.example.invalid',$false),
    @('*.example.invalid','notexample.invalid',$false),
    @('gateway.example.invalid','gateway.example.invalid',$true),
    @('gateway.example.invalid','portal.example.invalid',$false)
)) {Check ((Test-CertificateHostnameCoverage $case[0] $case[1]) -eq $case[2]) 'Incorrect exact/wildcard hostname coverage'}

foreach ($invalid in @(
    @(),@('*.*.example.invalid'),@('a.*.example.invalid'),@('https://example.invalid'),
    @('*.Example.invalid'),@('*.example.invalid','*.example.invalid'),@('127.0.0.1')
)) {
    Set-FixtureConfig
    $script:Config.hostnames=$invalid
    $failed=$false;try {Get-CertificateHostnames 'hostnames' | Out-Null} catch {$failed=$true}
    Check $failed 'Invalid SAN array was accepted'
}
Set-FixtureConfig;$script:Config.hostnames='*.example.invalid'
$failed=$false;try {Get-CertificateHostnames 'hostnames' | Out-Null} catch {$failed=$true}
Check $failed 'SAN scalar was accepted as an array'
Set-FixtureConfig;$script:Config.requiredHostnames=@('*.example.invalid')
$failed=$false;try {Get-CertificateHostnames 'requiredHostnames' | Out-Null} catch {$failed=$true}
Check $failed 'Wildcard was accepted as an actual public hostname'
Set-FixtureConfig;$script:Config.requiredHostnames=@('portal.example.invalid')
$failed=$false;try {Get-CertificateHostnames 'requiredHostnames' | Out-Null} catch {$failed=$true}
Check $failed 'Actual gateway hostname was omitted'
Set-FixtureConfig
$failed=$false
try {Assert-PreviousCertificateHostname ([pscustomobject]@{hostname='gateway.example.invalid';hostnames=@('gateway.example.invalid')})} catch {$failed=$true}
Check (-not $failed) 'Same-gateway SAN change was rejected before reissuance'
$failed=$false
try {Assert-PreviousCertificateHostname ([pscustomobject]@{hostname='other-gateway.example.invalid'})} catch {$failed=$true}
Check $failed 'A different gateway reused the existing certificate state'

$wildcard=New-FixtureCertificate @('*.example.invalid')
$single=New-FixtureCertificate @('gateway.example.invalid')
$pair=New-FixtureCertificate @('gateway.example.invalid','portal.example.invalid')
$extra=New-FixtureCertificate @('*.example.invalid','example.invalid')
try {
    Set-FixtureConfig
    Check (Test-CertificateHostnames $wildcard) 'Wildcard did not cover both public hosts'
    Check (-not (Test-CertificateHostnames $single)) 'Single-name certificate was reused for wildcard'
    Check (-not (Test-CertificateHostnames $extra)) 'Changed SAN set was silently reused'
    $script:Config.requiredHostnames+=@('example.invalid')
    Check (-not (Test-CertificateHostnames $wildcard)) 'Wildcard covered the apex domain'
    Set-FixtureConfig;$script:Config.requiredHostnames+=@('a.b.example.invalid')
    Check (-not (Test-CertificateHostnames $wildcard)) 'Wildcard covered more than one label'
    Set-FixtureConfig;$script:Config.hostnames=@('portal.example.invalid','gateway.example.invalid')
    Check (Test-CertificateHostnames $pair) 'SAN order affected set comparison'
    Check (-not (Test-CertificateHostnames $single)) 'SAN certificate omitted portal'

    # 期限に余裕がある旧証明書でも、SAN変更後はKVからの再利用を選びません。
    # この分岐は秘密や信頼チェーンを読む前なので、APIへの代役すら不要です。
    Set-FixtureConfig
    $current=[pscustomobject]@{
        cer=[Convert]::ToBase64String($single.RawData);sid='https://kv-fixture.vault.azure.net/secrets/tls-public/version'
        attributes=[pscustomobject]@{enabled=$true}
        policy=[pscustomobject]@{key_props=[pscustomobject]@{exportable=$true};secret_props=[pscustomobject]@{contentType='application/x-pkcs12'}}
    }
    Check (-not (Test-ReusableCertificate $current 30)) 'Existing KV certificate ignored SAN change'
} finally {
    foreach ($certificate in @($wildcard,$single,$pair,$extra)) {$certificate.Dispose()}
}
"Certificate hostnames: $checks checks PASS; no Azure, DNS or CA requests."
