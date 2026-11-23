# 合成鍵をメモリー内だけで作り、KV応答を置換します。Azure通信・秘密鍵の保存は行いません。
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/../infra/guest/portal-auth-certificate.ps1" -LibraryOnly
$script:Vault = 'https://kv-synthetic.vault.azure.net'
$script:Name = 'portal-auth'
$script:Months = 12
$version = 'a' * 32
$certId = "$script:Vault/certificates/$script:Name/$version"
$keyId = "$script:Vault/keys/$script:Name/$version"
$rsa = [Security.Cryptography.RSA]::Create(3072)
$request = [Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=Portal authentication', $rsa, [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)
$request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new([Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature, $true))
$leaf = $request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddMinutes(-5), [DateTimeOffset]::UtcNow.AddMonths(12))
$p = $rsa.ExportParameters($false)
function Encode-Public($bytes) { [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_') }
$script:cert = @{ id = $certId; kid = $keyId; cer = [Convert]::ToBase64String($leaf.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert)); attributes = @{enabled=$true}; policy = Get-PortalPolicy 12 }
$script:key = @{ key = @{kid=$keyId; kty='RSA'; n=Encode-Public $p.Modulus; e=Encode-Public $p.Exponent; key_ops=@('sign','verify')}; attributes=@{enabled=$true} }
$script:historicalMode = $false
function Invoke-PortalVault {
    param($Method, $Path, $Body)
    if ($Method -ne 'GET') { throw 'Unexpected mutation in certificate validation' }
    if ($Path -ceq "/certificates/$script:Name") {
        if ($script:historicalMode) {
            $latest = $script:cert.Clone()
            $latest.id = "$script:Vault/certificates/$script:Name/$('b' * 32)"
            # A later policy must not substitute for a previously verified version.
            $latest.policy = Get-PortalPolicy 12
            $latest.policy.key_props.exportable = $true
            return @{status=200;value=$latest}
        }
        return @{status=200;value=$script:cert}
    }
    if ($Path.StartsWith('/certificates/')) {
        $versioned = $script:cert.Clone()
        $versioned.Remove('policy')
        return @{status=200;value=$versioned}
    }
    return @{status=200;value=$script:key}
}
function Assert-Rejected([scriptblock]$Run) {
    $failed = $false
    try { $null = & $Run } catch { $failed = $true }
    if (-not $failed) { throw 'Expected rejection did not occur' }
}
try {
    $verified = @{}
    $result = Read-PortalCertificate $certId -Checkpoint $verified
    if ($result -isnot [hashtable] -or $result.keyId -cne $keyId -or $result.certificateThumbprint -cne $leaf.Thumbprint.ToLowerInvariant() -or $result.ContainsKey('privateKey')) { throw 'Public material validation failed' }
    if (-not $verified.policyProofs.ContainsKey($certId)) { throw 'Version-bound policy proof was not recorded' }
    $script:historicalMode = $true
    $null = Read-PortalCertificate $certId -Checkpoint $verified
    $script:Months = 6
    $null = Read-PortalCertificate $certId -Checkpoint $verified
    $script:Months = 12
    Assert-Rejected { Read-PortalCertificate $certId }
    $verified.policyProofs[$certId].keyId = "$script:Vault/keys/$script:Name/$('b' * 32)"
    Assert-Rejected { Read-PortalCertificate $certId -Checkpoint $verified }
    $verified.policyProofs[$certId].keyId = $keyId
    $verified.policyProofs[$certId].certificateThumbprint = 'synthetic-mismatch'
    Assert-Rejected { Read-PortalCertificate $certId -Checkpoint $verified }
    $verified.policyProofs[$certId].certificateThumbprint = $leaf.Thumbprint.ToLowerInvariant()
    $script:historicalMode = $false
    $originalCer = $script:cert.cer
    $corrupted = [Convert]::FromBase64String($originalCer)
    $corrupted[-1] = $corrupted[-1] -bxor 1
    $script:cert.cer = [Convert]::ToBase64String($corrupted)
    Assert-Rejected { Read-PortalCertificate $certId }
    $script:cert.cer = $originalCer
    $script:cert.policy.key_props.exportable = $true
    Assert-Rejected { Read-PortalCertificate $certId }
    $script:cert.policy.key_props.exportable = $false
    $script:cert.policy.issuer.name = 'Other'
    Assert-Rejected { Read-PortalCertificate $certId }
    $script:cert.policy.issuer.name = 'Self'
    $script:cert.policy.key_props.reuse_key = $true
    Assert-Rejected { Read-PortalCertificate $certId }
    $script:cert.policy.key_props.reuse_key = $false
    $script:key.key.d = 'synthetic-private-marker'
    Assert-Rejected { Read-PortalCertificate $certId }
    $script:key.key.Remove('d')
    $original = $script:key.key.n
    $script:key.key.n = 'different-synthetic-public-key'
    Assert-Rejected { Read-PortalCertificate $certId }
    $script:key.key.n = $original
    $script:key.attributes.enabled = $false
    Assert-Rejected { Read-PortalCertificate $certId }
    $script:key.attributes.enabled = $true
    Assert-Rejected { Read-PortalCertificate "$script:Vault/certificates/$script:Name" }
    $checkpoint = @{current=$result}
    Assert-Rejected { Invoke-PortalCertificateAction 'Retire' @{certificateId=$certId;currentKeyId=$keyId} $checkpoint }
    # intent済みの不明POSTを再送せず、GETで結果を照合することを確認します。
    $script:cert.tags = @{requestId='b'*32;purpose='portal-authentication'}
    $checkpoint = @{request=@{requestId='b'*32;baseline='';sent=$true}}
    function Save-PortalCheckpoint { param($Value) $script:saved = $Value | ConvertTo-Json -Depth 20 }
    $result = New-PortalCertificate $checkpoint ('b'*32)
    if ($checkpoint.ContainsKey('request') -or $checkpoint.candidate.keyId -cne $keyId) { throw 'Unknown-response reconciliation failed' }
    Assert-Rejected { New-PortalCertificate @{request=@{requestId='b'*32;baseline=''}} ('c'*32) }
    Write-Output 'Portal certificate offline checks passed (18 cases)'
} finally { $leaf.Dispose(); $rsa.Dispose() }
