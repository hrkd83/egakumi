# The Python entry point holds and renews the Blob lease for this process.
param(
    [ValidateSet('tls', 'portal')][string]$Mode,
    [ValidateSet('initial', 'renew', 'check', 'prepare', 'inspect', 'activate', 'retire')][string]$Action,
    [switch]$LibraryOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function New-CertificateHttpClient {
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $handler.UseProxy = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(15)
    return $client
}

function Get-CertificateJobToken(
    [ValidateSet('Vault', 'Storage', 'Arm')][string]$Service,
    [string]$ClientId,
    [Uri]$VaultOrigin = $null
) {
    $identifier = [Guid]::Empty
    if (-not [Guid]::TryParse($ClientId, [ref]$identifier) -or $identifier -eq [Guid]::Empty) {
        throw 'Invalid certificate job identity request'
    }
    $resource = switch ($Service) {
        'Vault' {
            if ($null -eq $VaultOrigin -or $VaultOrigin.Scheme -ne 'https' -or -not $VaultOrigin.IsDefaultPort -or
                $VaultOrigin.UserInfo -or $VaultOrigin.Query -or $VaultOrigin.Fragment -or
                $VaultOrigin.AbsolutePath -ne '/' -or $VaultOrigin.Host -cnotmatch '^[a-z0-9-]{3,24}\.vault\.azure\.net$') {
                throw 'Invalid certificate vault origin'
            }
            # Key Vault token audience is the Azure service DNS suffix,
            # shared by every instance of the validated Vault endpoint.
            'https://' + $VaultOrigin.Host.Substring($VaultOrigin.Host.IndexOf('.') + 1)
        }
        'Storage' {
            $storageAudience = [Uri]$env:CERTIFICATE_STORAGE_AUDIENCE
            if (-not $storageAudience.IsAbsoluteUri -or $storageAudience.Scheme -ne 'https' -or
                -not $storageAudience.IsDefaultPort -or $storageAudience.UserInfo -or $storageAudience.Query -or
                $storageAudience.Fragment -or $storageAudience.AbsolutePath -ne '/') {
                throw 'Invalid certificate storage audience'
            }
            $storageAudience.AbsoluteUri
        }
        'Arm' { 'https://management.azure.com/' }
    }
    $location = [Uri]$env:IDENTITY_ENDPOINT
    $address = $null
    if (-not $location.IsAbsoluteUri -or $location.Scheme -ne 'http' -or $location.UserInfo -or
        $location.Query -or $location.Fragment -or -not $env:IDENTITY_HEADER -or
        ($location.Host -ne 'localhost' -and (-not [Net.IPAddress]::TryParse($location.Host, [ref]$address) -or
        (-not [Net.IPAddress]::IsLoopback($address) -and -not $address.IsIPv6LinkLocal -and $location.Host -notmatch '^169\.254\.')))) {
        throw 'Invalid local managed identity endpoint'
    }
    $resourceValue = [Uri]::EscapeDataString($Resource)
    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, "$($location.AbsoluteUri)?api-version=2019-08-01&resource=$resourceValue&client_id=$identifier")
    $client = New-CertificateHttpClient
    try {
        $request.Headers.Add('X-IDENTITY-HEADER', $env:IDENTITY_HEADER)
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        try {
            if (-not $response.IsSuccessStatusCode) { throw 'Certificate job identity authentication failed' }
            $token = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
            if (-not $token.access_token -or [long]$token.expires_on -le [DateTimeOffset]::UtcNow.AddSeconds(60).ToUnixTimeSeconds()) {
                throw 'Certificate job identity response is unavailable'
            }
            return $token
        } finally { $response.Dispose() }
    } finally { $request.Dispose(); $client.Dispose() }
}

function Set-CertificateJobChainTrust(
    [Security.Cryptography.X509Certificates.X509Chain]$Chain,
    [string]$DirectoryUrl,
    [string]$AnchorsPath = '/app/staging-ca.crt'
) {
    # The four public staging roots are documented by Let's Encrypt:
    # These roots never enter the OS trust store or production verification.
    if ($DirectoryUrl -cne 'https://acme-staging-v02.api.letsencrypt.org/directory') { return }
    $anchors = [Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
    $anchors.ImportFromPemFile($AnchorsPath)
    $expected = @(
        'e70570a989f8565aabdf7cae27abd1621872d6a3f811e3fef27e3dba02912198'
        '9b2a339fe6a3e85585c4cd75536cb8c1cf7cd603b9a64bec2521858ae48da85d'
        'b59bfc0ba52daf849853b7324a91e82f031db3397f35644157352cde6384b56c'
        'ef115fb59e040ff39d15fd8f3ef54063c704321d83cb081213272f77d3091672'
    )
    $actual = @($anchors | ForEach-Object {
        $_.GetCertHashString([Security.Cryptography.HashAlgorithmName]::SHA256).ToLowerInvariant()
    })
    if ($actual.Count -ne $expected.Count -or @(Compare-Object ($actual | Sort-Object) ($expected | Sort-Object)).Count) {
        throw 'Unexpected ACME staging trust roots'
    }
    $Chain.ChainPolicy.TrustMode = [Security.Cryptography.X509Certificates.X509ChainTrustMode]::CustomRootTrust
    $Chain.ChainPolicy.CustomTrustStore.AddRange($anchors)
}

function Invoke-PortalJobCheckpoint([string]$Method, [hashtable]$State = $null) {
    $origin = $env:CERTIFICATE_STORAGE_URL
    $container = $env:CERTIFICATE_PORTAL_STATE_CONTAINER
    $storage = [Uri]$origin
    if (-not $storage.IsAbsoluteUri -or $storage.Scheme -ne 'https' -or -not $storage.IsDefaultPort -or
        $storage.UserInfo -or $storage.Query -or $storage.Fragment -or $storage.AbsolutePath -ne '/' -or
        $storage.Host -cnotmatch '^[a-z0-9]{3,24}\.blob\.core\.windows\.net$' -or
        $container -cnotmatch '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$') { throw 'Invalid job checkpoint endpoint' }
    $stateObject = if ($env:CERTIFICATE_PORTAL_STATE_OBJECT) { $env:CERTIFICATE_PORTAL_STATE_OBJECT } else { 'portal' }
    if ($stateObject -cnotin @('portal', 'gateway-obo')) { throw 'Invalid certificate checkpoint object' }
    $uri = "$origin/$container/state/$stateObject.json"
    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::new($Method), $uri)
    $client = New-CertificateHttpClient
    try {
        $token = Get-CertificateJobToken 'Storage' $env:CERTIFICATE_CLIENT_ID
        $request.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $token.access_token)
        $request.Headers.Add('x-ms-version', '2023-11-03')
        $request.Headers.Add('x-ms-date', [DateTime]::UtcNow.ToString('R', [Globalization.CultureInfo]::InvariantCulture))
        if ($Method -eq 'PUT') {
            $request.Headers.Add('x-ms-blob-type', 'BlockBlob')
            $json = $State | ConvertTo-Json -Depth 15 -Compress
            if ([Text.Encoding]::UTF8.GetByteCount($json) -gt 524288) { throw 'Portal certificate checkpoint is too large' }
            $request.Content = [Net.Http.StringContent]::new($json, [Text.Encoding]::UTF8, 'application/json')
        }
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        try {
            if ($Method -eq 'GET' -and [int]$response.StatusCode -eq 404) { return @{} }
            if (-not $response.IsSuccessStatusCode) { throw 'Portal certificate checkpoint is unavailable' }
            if ($Method -eq 'GET') {
                $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                if ([Text.Encoding]::UTF8.GetByteCount($content) -gt 524288) { throw 'Portal certificate checkpoint is too large' }
                $parsed = $content | ConvertFrom-Json -AsHashtable
                if ($parsed -isnot [System.Collections.IDictionary]) { throw 'Portal certificate checkpoint is malformed' }
                return $parsed
            }
        } finally { $response.Dispose() }
    } finally { $request.Dispose(); $client.Dispose() }
}

function Get-PortalJobConfiguration([string]$Configuration, [string]$ArgumentsJson, [string]$SelectedAction) {
    $settings = $Configuration | ConvertFrom-Json -AsHashtable
    if ($settings -isnot [System.Collections.IDictionary]) { throw 'Invalid Portal certificate job configuration' }
    $arguments = if ($ArgumentsJson) { $ArgumentsJson | ConvertFrom-Json -AsHashtable } else { @{} }
    if ($arguments -isnot [System.Collections.IDictionary]) { throw 'Invalid Portal certificate operation input' }
    $allowed = switch ($SelectedAction) {
        'initial' { @() }
        'prepare' { @('requestId', 'currentKeyId') }
        'inspect' { @('certificateId') }
        'activate' { @('certificateId') }
        'retire' { @('certificateId', 'currentKeyId') }
        default { throw 'Invalid Portal certificate operation' }
    }
    if ($arguments.Count -ne @($allowed | Where-Object { $null -ne $_ }).Count) { throw 'Invalid Portal certificate operation input' }
    foreach ($key in $arguments.Keys) {
        if ($key -cnotin $allowed -or $arguments[$key] -isnot [string] -or -not $arguments[$key]) {
            throw 'Invalid Portal certificate operation input'
        }
        $settings[$key] = $arguments[$key]
    }
    return ($settings | ConvertTo-Json -Depth 15 -Compress)
}

if ($LibraryOnly) { return }
try {
    if ($Mode -eq 'tls') {
        $workerAction = if ($Action -eq 'initial') { 'Install' } else { 'Renew' }
        if ($Action -notin @('initial', 'renew', 'check') -or
            ($Action -ne 'renew' -and $env:CERTIFICATE_ROTATE_FROM_THUMBPRINT)) { throw 'Invalid TLS operation' }
        & '/app/certificate.ps1' -Action $workerAction -PublicConfigJson $env:CERTIFICATE_CONFIG_JSON -ContainerJob -CheckOnly:($Action -eq 'check') -RotateFromThumbprint $env:CERTIFICATE_ROTATE_FROM_THUMBPRINT
        exit $LASTEXITCODE
    }
    $actionMap = @{ initial = 'Initialize'; prepare = 'Prepare'; inspect = 'Inspect'; activate = 'Activate'; retire = 'Retire' }
    $configuration = Get-PortalJobConfiguration $env:CERTIFICATE_CONFIG_JSON $env:CERTIFICATE_ARGUMENTS_JSON $Action
    if ($env:CERTIFICATE_PORTAL_STATE_OBJECT -and ($env:CERTIFICATE_PORTAL_STATE_OBJECT -cne 'gateway-obo' -or
        $Action -cne 'initial' -or ($configuration | ConvertFrom-Json).certificateName -cne 'gateway-obo')) {
        throw 'Invalid Gateway OBO certificate operation'
    }
    & '/app/portal-auth-certificate.ps1' -Action $actionMap[$Action] -ConfigJson $configuration -ContainerJob
    exit $LASTEXITCODE
} catch {
    Write-Output 'CERTIFICATE_STAGE:job-configuration'
    exit 1
}
