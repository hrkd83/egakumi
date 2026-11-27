# Portal認証専用。秘密鍵はKV内で生成し、secret/PFXを取得しません。
[CmdletBinding()]
param(
    [ValidateSet('Initialize', 'Prepare', 'Inspect', 'Activate', 'Retire')][string]$Action,
    [string]$ConfigJson,
    [switch]$LibraryOnly,
    [switch]$ContainerJob
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-PortalVault {
    param([string]$Method, [string]$Path, $Body = $null)
    # 対象KVの相対パスだけを受け付け、redirectと変更要求の再送を禁止します。
    if ($Path -notmatch '^/(certificates|keys)/[a-zA-Z0-9/-]+(?:/pending)?$') { throw 'Invalid vault operation' }
    $attempts = if ($ContainerJob) { 31 } else { 1 }
    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new($Method), "$script:Vault$Path`?api-version=7.4")
        $request.Headers.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $script:Token)
        if ($null -ne $Body) {
            $request.Content = [System.Net.Http.StringContent]::new(($Body | ConvertTo-Json -Depth 20 -Compress), [Text.Encoding]::UTF8, 'application/json')
        }
        try {
            $response = $script:Http.SendAsync($request).GetAwaiter().GetResult()
            try {
                $status = [int]$response.StatusCode
                $value = $null
                if ($status -eq 403 -and $attempt -lt $attempts) { Start-Sleep -Seconds 10; continue }
                if ($status -in 200,201,202) { $value = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json -AsHashtable }
                if ($status -notin 200,201,202,404) { throw 'Vault operation was rejected' }
                return @{ status = $status; value = $value }
            } finally { $response.Dispose() }
        } finally { $request.Dispose() }
    }
}

function Get-PortalPolicy {
    param([int]$Months)
    return @{
        issuer = @{ name = 'Self' }
        key_props = @{ exportable = $false; kty = 'RSA'; key_size = 3072; reuse_key = $false }
        secret_props = @{ contentType = 'application/x-pkcs12' }
        x509_props = @{ subject = 'CN=Portal authentication'; validity_months = $Months; key_usage = @('digitalSignature'); ekus = @('1.3.6.1.5.5.7.3.2') }
        # Entra登録は手動。KVだけで自動更新してcurrentを置き換えないようにします。
        lifetime_actions = @()
    }
}

function Read-PortalCertificate {
    param([string]$CertificateId, [switch]$AllowDisabled, [hashtable]$Checkpoint = $null)
    $prefix = "$script:Vault/certificates/$script:Name/"
    if (-not $CertificateId.StartsWith($prefix, [StringComparison]::Ordinal) -or $CertificateId.Substring($prefix.Length) -notmatch '^[a-f0-9]{32}$') { throw 'Invalid certificate version' }
    # Get Latest returns policy; Get Version returns certificate material only.
    # Bind a verified policy to its immutable version before saving the checkpoint.
    $latest = (Invoke-PortalVault GET "/certificates/$script:Name").value
    $proof = $null
    if ($null -ne $latest -and $latest.id -ceq $CertificateId) {
        $cert = $latest
        if (-not $cert.ContainsKey('policy')) { throw 'Latest certificate policy is unavailable' }
        $policy = $cert.policy
    } else {
        $cert = (Invoke-PortalVault GET $CertificateId.Substring($script:Vault.Length)).value
        if ($null -eq $Checkpoint -or -not $Checkpoint.ContainsKey('policyProofs') -or -not $Checkpoint.policyProofs.ContainsKey($CertificateId)) { throw 'Historical certificate requires its verified policy record' }
        $proof = $Checkpoint.policyProofs[$CertificateId]
        if ($proof.certificateId -cne $CertificateId -or $proof.keyId -cne $cert.kid) { throw 'Historical certificate policy record differs' }
        $policy = $proof.policy
    }
    if ($null -eq $cert -or $cert.id -cne $CertificateId) { throw 'Certificate identity differs' }
    $expectedMonths = $script:Months
    if ($null -ne $proof) {
        $expectedMonths = $proof.policy.x509_props.validity_months
        if ($expectedMonths -notin 1..24) { throw 'Historical certificate validity record differs' }
    }
    if ($policy.issuer.name -cne 'Self' -or $policy.key_props.exportable -ne $false -or $policy.key_props.reuse_key -ne $false -or $policy.key_props.kty -cne 'RSA' -or $policy.key_props.key_size -ne 3072 -or $policy.x509_props.validity_months -ne $expectedMonths -or @($policy.lifetime_actions).Count -ne 0) { throw 'Certificate policy differs' }
    $keyPrefix = "$script:Vault/keys/$script:Name/"
    if (-not $cert.kid.StartsWith($keyPrefix, [StringComparison]::Ordinal) -or $cert.kid.Substring($keyPrefix.Length) -notmatch '^[a-f0-9]{32}$') { throw 'Invalid key version' }
    $key = (Invoke-PortalVault GET $cert.kid.Substring($script:Vault.Length)).value
    if ($null -eq $key -or $key.key.kid -cne $cert.kid -or $key.key.kty -cne 'RSA' -or $key.key.ContainsKey('d') -or 'sign' -notin $key.key.key_ops) { throw 'Key identity differs' }
    if (-not $AllowDisabled -and ($cert.attributes.enabled -ne $true -or $key.attributes.enabled -ne $true)) { throw 'Certificate or key is disabled' }
    $der = [Convert]::FromBase64String($cert.cer)
    $leaf = [Security.Cryptography.X509Certificates.X509Certificate2]::new($der)
    try {
        if ($leaf.HasPrivateKey -or $leaf.Subject -cne 'CN=Portal authentication' -or $leaf.Issuer -cne $leaf.Subject) { throw 'Unexpected certificate' }
        $rsa = [Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($leaf)
        try {
            $parameters = $rsa.ExportParameters($false)
            $n = [Convert]::ToBase64String($parameters.Modulus).TrimEnd('=').Replace('+','-').Replace('/','_')
            $e = [Convert]::ToBase64String($parameters.Exponent).TrimEnd('=').Replace('+','-').Replace('/','_')
            if ($n -cne $key.key.n -or $e -cne $key.key.e) { throw 'Certificate and key do not match' }
            $reader = [System.Formats.Asn1.AsnReader]::new([ReadOnlyMemory[byte]]::new($der), [System.Formats.Asn1.AsnEncodingRules]::DER)
            $sequence = $reader.ReadSequence()
            $tbs = $sequence.ReadEncodedValue().ToArray()
            $algorithm = $sequence.ReadSequence()
            if ($algorithm.ReadObjectIdentifier() -cne '1.2.840.113549.1.1.11') { throw 'Unexpected certificate signature algorithm' }
            if ($algorithm.HasData) { $algorithm.ReadNull() }
            $algorithm.ThrowIfNotEmpty()
            $unusedBits = 0
            $signature = $sequence.ReadBitString([ref]$unusedBits)
            $sequence.ThrowIfNotEmpty()
            $reader.ThrowIfNotEmpty()
            if ($unusedBits -ne 0 -or -not $rsa.VerifyData($tbs, $signature, [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)) { throw 'Invalid self-signed certificate signature' }
            # 自己署名を実際に検証します。公開証明書の一致だけで発行元を推測しません。
            $chain = [Security.Cryptography.X509Certificates.X509Chain]::new()
            try {
                $chain.ChainPolicy.TrustMode = [Security.Cryptography.X509Certificates.X509ChainTrustMode]::CustomRootTrust
                $null = $chain.ChainPolicy.CustomTrustStore.Add($leaf)
                $chain.ChainPolicy.RevocationMode = [Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
                if (-not $AllowDisabled -and -not $chain.Build($leaf)) { throw 'Self-signed certificate validation failed' }
            } finally { $chain.Dispose() }
        } finally { $rsa.Dispose() }
        $thumbprint = $leaf.Thumbprint.ToLowerInvariant()
        if ($null -ne $proof -and $proof.certificateThumbprint -cne $thumbprint) { throw 'Historical certificate material differs from its policy record' }
        if ($null -ne $Checkpoint) {
            if (-not $Checkpoint.ContainsKey('policyProofs')) { $Checkpoint.policyProofs = @{} }
            $Checkpoint.policyProofs[$CertificateId] = @{ certificateId = $cert.id; keyId = $cert.kid; certificateThumbprint = $thumbprint; policy = $policy }
        }
        return @{ certificateId = $cert.id; keyId = $cert.kid; certificateThumbprint = $thumbprint; notBefore = [DateTimeOffset]::new($leaf.NotBefore.ToUniversalTime()).ToUnixTimeSeconds(); expires = [DateTimeOffset]::new($leaf.NotAfter.ToUniversalTime()).ToUnixTimeSeconds(); publicCertificate = $cert.cer }
    } finally { $leaf.Dispose() }
}

function Save-PortalCheckpoint {
    param($Value)
    if ($ContainerJob) { Invoke-PortalJobCheckpoint 'PUT' $Value; return }
    $temporary = "$script:StatePath.tmp"
    if (Test-Path $temporary) { throw 'Unrecovered certificate checkpoint' }
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 10 -Compress))
    [IO.File]::SetUnixFileMode($temporary, [IO.UnixFileMode]::UserRead -bor [IO.UnixFileMode]::UserWrite)
    [IO.File]::Move($temporary, $script:StatePath, $true)
}

function New-PortalCertificate {
    param($Checkpoint, [string]$RequestId)
    if ($Checkpoint.ContainsKey('request') -and $Checkpoint.request.requestId -cne $RequestId) { throw 'Another certificate request requires recovery' }
    if (-not $Checkpoint.ContainsKey('request')) {
        $latest = Invoke-PortalVault GET "/certificates/$script:Name"
        $pending = Invoke-PortalVault GET "/certificates/$script:Name/pending"
        if ($pending.status -eq 200 -and $pending.value.status -cne 'completed') { throw 'An existing certificate operation requires review' }
        $Checkpoint.request = @{ requestId = $RequestId; baseline = $(if ($latest.status -eq 200) { $latest.value.id } else { '' }); sent = $true }
        # POST前にintentを永続化。通信結果不明ならPOSTを再送しません。
        Save-PortalCheckpoint $Checkpoint
        $null = Invoke-PortalVault POST "/certificates/$script:Name/create" @{ policy = Get-PortalPolicy $script:Months; tags = @{ purpose = 'portal-authentication'; requestId = $RequestId } }
    }
    $deadline = [DateTime]::UtcNow.AddMinutes(10)
    while ([DateTime]::UtcNow -lt $deadline) {
        $latest = Invoke-PortalVault GET "/certificates/$script:Name"
        if ($latest.status -eq 200 -and $latest.value.id -cne $Checkpoint.request.baseline) {
            if ($latest.value.tags.requestId -cne $RequestId -or $latest.value.tags.purpose -cne 'portal-authentication') { throw 'An unrelated certificate was created' }
            $result = Read-PortalCertificate $latest.value.id -Checkpoint $Checkpoint
            if ($Checkpoint.ContainsKey('current') -and $result.keyId -ceq $Checkpoint.current.keyId) { throw 'Renewal reused the current private key' }
            if ($Checkpoint.ContainsKey('current')) {
                $oldKey = (Invoke-PortalVault GET $Checkpoint.current.keyId.Substring($script:Vault.Length)).value.key
                $newKey = (Invoke-PortalVault GET $result.keyId.Substring($script:Vault.Length)).value.key
                if ($oldKey.n -ceq $newKey.n -and $oldKey.e -ceq $newKey.e) { throw 'Renewal reused the current public key' }
            }
            $Checkpoint.candidate = $result
            $Checkpoint.Remove('request')
            Save-PortalCheckpoint $Checkpoint
            return $result
        }
        $pending = Invoke-PortalVault GET "/certificates/$script:Name/pending"
        if ($pending.status -eq 404 -or $pending.value.status -in 'failed','canceled') { throw 'Certificate creation is not confirmed; inspect the saved request' }
        Start-Sleep -Seconds 5
    }
    throw 'Certificate creation remains pending'
}

function Invoke-PortalCertificateAction {
    param([string]$SelectedAction, $Config, $Checkpoint)
    if ($SelectedAction -eq 'Initialize') {
        if ($Checkpoint.ContainsKey('current')) {
            $result = Read-PortalCertificate $Checkpoint.current.certificateId -Checkpoint $Checkpoint
            Save-PortalCheckpoint $Checkpoint
            return $result
        }
        if ($Checkpoint.ContainsKey('candidate')) { $result = Read-PortalCertificate $Checkpoint.candidate.certificateId -Checkpoint $Checkpoint }
        else {
            $existing = Invoke-PortalVault GET "/certificates/$script:Name"
            # 由来不明の既存鍵を自動採用しません。再開は保存intentに限定します。
            if ($existing.status -eq 200 -and -not $Checkpoint.ContainsKey('request')) { throw 'Existing authentication certificate needs explicit recovery' }
            $result = New-PortalCertificate $Checkpoint 'initial'
        }
        $Checkpoint.current = $result
        $Checkpoint.Remove('candidate')
        Save-PortalCheckpoint $Checkpoint
        return $result
    }
    if (-not $Checkpoint.ContainsKey('current')) { throw 'Initialize the certificate first' }
    if ($SelectedAction -eq 'Prepare') {
        if ($Config.requestId -notmatch '^[a-f0-9]{32}$') { throw 'Invalid renewal request' }
        if ($Config.currentKeyId -cne $Checkpoint.current.keyId) { throw 'Current certificate differs from the selected state' }
        if ($Checkpoint.ContainsKey('candidate')) { return Read-PortalCertificate $Checkpoint.candidate.certificateId -Checkpoint $Checkpoint }
        return New-PortalCertificate $Checkpoint $Config.requestId
    }
    $result = Read-PortalCertificate $Config.certificateId -AllowDisabled:($SelectedAction -eq 'Retire') -Checkpoint $Checkpoint
    if ($SelectedAction -eq 'Activate') {
        $Checkpoint.current = $result
        if ($Checkpoint.ContainsKey('candidate') -and $Checkpoint.candidate.keyId -ceq $result.keyId) { $Checkpoint.Remove('candidate') }
        Save-PortalCheckpoint $Checkpoint
    } elseif ($SelectedAction -eq 'Retire') {
        if ($result.keyId -ceq $Checkpoint.current.keyId -or $Config.currentKeyId -cne $Checkpoint.current.keyId) { throw 'Refusing to disable the current key' }
        $null = Invoke-PortalVault PATCH $result.keyId.Substring($script:Vault.Length) @{ attributes = @{ enabled = $false } }
        $key = (Invoke-PortalVault GET $result.keyId.Substring($script:Vault.Length)).value
        if ($key.attributes.enabled -ne $false) { throw 'Old key disabling is not confirmed' }
        return @{ retired = $true; keyId = $result.keyId }
    }
    return $result
}

if ($LibraryOnly) { return }
try {
    if (-not $IsLinux -or (-not $ContainerJob -and [Environment]::UserName -cne 'root')) { throw 'Management VM root execution is required' }
    $config = $ConfigJson | ConvertFrom-Json -AsHashtable
    if ($config.vaultName -notmatch '^[a-z0-9-]{3,24}$' -or $config.certificateName -notmatch '^[a-z0-9-]{1,127}$' -or $config.validityMonths -notin 1..24) { throw 'Invalid certificate configuration' }
    $script:Vault = "https://$($config.vaultName).vault.azure.net"
    $script:Name = $config.certificateName
    $script:Months = $config.validityMonths
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $handler.UseProxy = $false
    $script:Http = [System.Net.Http.HttpClient]::new($handler)
    $script:Http.Timeout = [TimeSpan]::FromSeconds(30)
    if ($ContainerJob) {
        $script:Token = (Get-CertificateJobToken 'Vault' $env:CERTIFICATE_CLIENT_ID $script:Vault).access_token
        $checkpoint = Invoke-PortalJobCheckpoint 'GET'
    } else {
        $directory = "/var/lib/egakumi-portal-auth/$($config.vaultName)-$script:Name"
        foreach ($path in @('/var/lib/egakumi-portal-auth', $directory, "$directory/state.json", "$directory/state.json.tmp", "$directory/lock")) {
            if (Test-Path $path) { if ((Get-Item -Force $path).LinkType) { throw 'Certificate state must not use links' } }
        }
        $null = [IO.Directory]::CreateDirectory($directory)
        [IO.File]::SetUnixFileMode($directory, [IO.UnixFileMode]::UserRead -bor [IO.UnixFileMode]::UserWrite -bor [IO.UnixFileMode]::UserExecute)
        $script:StatePath = "$directory/state.json"
        $lock = [IO.File]::Open("$directory/lock", [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        [IO.File]::SetUnixFileMode("$directory/lock", [IO.UnixFileMode]::UserRead -bor [IO.UnixFileMode]::UserWrite)
        $imds = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net')
        $imds.Headers.Add('Metadata','true')
        try {
            $response = $script:Http.SendAsync($imds).GetAwaiter().GetResult()
            try {
                if (-not $response.IsSuccessStatusCode) { throw 'Managed identity authentication failed' }
                $script:Token = ($response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json -AsHashtable).access_token
            } finally { $response.Dispose() }
        } finally { $imds.Dispose() }
        $checkpoint = $(if (Test-Path $script:StatePath) { Get-Content -Raw $script:StatePath | ConvertFrom-Json -AsHashtable } else { @{} })
    }
    $result = Invoke-PortalCertificateAction $Action $config $checkpoint
    $prefix = if ($ContainerJob) { 'EGAKUMI_CERTIFICATE_RESULT:' } else { 'EGAKUMI_PORTAL_AUTH:' }
    Write-Output ($prefix + ($result | ConvertTo-Json -Depth 10 -Compress))
} catch {
    if ($ContainerJob) { Write-Output 'CERTIFICATE_STAGE:portal-certificate' }
    else { Write-Output 'Portal authentication certificate operation failed; inspect protected state before retrying' }
    exit 1
}
finally {
    $script:Token = $null
    if (Test-Path variable:script:Http) { $script:Http.Dispose() }
    if (Test-Path variable:lock) { $lock.Dispose() }
}
