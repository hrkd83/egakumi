<#
.SYNOPSIS
Azure DNSのUAMI選択と初回・定期更新を、外部APIへ接続せず検証します。
#>
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'certificate-fixtures.ps1')
$subscription='11111111-1111-4111-8111-111111111111'
$identity='/subscriptions/'+$subscription+'/resourceGroups/rg-dns/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-dns'
$script:Config=[pscustomobject]@{
    hostname='gateway.example.invalid';hostnames=@('gateway.example.invalid');mode='dnsPlugin';dnsPlugin='Azure'
    certificateName='tls-public';keyVaultName='kv-fixture';accountEmail='test@example.invalid'
    directoryUrl='https://acme-staging-v02.api.letsencrypt.org/directory'
    azureDnsSubscriptionId=$subscription;azureDnsManagedIdentityResourceId=$identity
}
$script:Token=$null;$script:Requests=@();$script:Issued=@();$script:Renewed=@();$script:FixtureOrder=$null
function Check([bool]$Condition,[string]$Message) {if (-not $Condition) {throw $Message}}
function Invoke-Json([string]$Uri) {
    $script:Requests+=@($Uri)
    return [pscustomobject]@{access_token=('fixture-token-'+$script:Requests.Count);expires_on=[DateTimeOffset]::UtcNow.AddHours(1).ToUnixTimeSeconds()}
}
[void](Get-VaultToken)
Check ($script:Requests[-1] -notmatch 'mi_res_id' -and $script:Requests[-1] -match 'vault.azure.net') 'Key Vault identity changed'
function Get-PlannedRotationThumbprint {return ''}
function Test-ReusableCertificate {return $false}
function Ensure-PoshAcme {}
function Restore-AcmeState {}
function Get-CurrentCertificate {return $null}
function Get-VaultSecret([string]$Name) {
    if ($Name -like '*dns-plugin-args') {throw 'Azure read legacy DNS credentials'}
    return [pscustomobject]@{value='fixture-password'}
}
function Set-VaultSecret {throw 'Azure saved DNS credentials'}
function Set-PAServer {}
function Get-PAAccount {return [pscustomobject]@{id='fixture-account'}}
function Set-PAAccount {}
function Get-PAOrder {return $script:FixtureOrder}
function New-PACertificate($Plugin,$PluginArgs) {$script:Issued+=@($PluginArgs)}
function Set-PAOrder($Plugin,$PluginArgs) {$script:OrderArguments=$PluginArgs}
function Submit-Renewal($PluginArgs) {$script:Renewed+=@($PluginArgs)}
function Test-CachedAcmeCertificate {return $false}
function Get-PACertificate {return [pscustomobject]@{PfxFullChain=$script:CachedPath}}
function Import-Pfx {}
function Save-AcmeState {}
$fixture=Join-Path $PSScriptRoot ('certificate-azure-identity-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($fixture)
$script:CachedPath=Join-Path $fixture 'fixture.pfx'
[IO.File]::WriteAllBytes($script:CachedPath,[byte[]]@(1))
$previousHome=$env:POSHACME_HOME
$env:POSHACME_HOME=$fixture
$Action='Install';$DnsPluginArgumentsJson='legacy-secret-must-not-be-used'
try {
    Ensure-Certificate
    Check ($script:Issued.Count -eq 1) 'Initial issuance did not run'
    $script:FixtureOrder=[pscustomobject]@{MainDomain='gateway.example.invalid';SANs=@();status='valid'}
    $Action='Renew'
    Ensure-Certificate
    Check ($script:Renewed.Count -eq 1) 'Renewal did not run'
    Check ($script:Requests.Count -eq 3) 'Unexpected certificate identity request count'
    foreach ($request in $script:Requests[1..2]) {
        $uri=[Uri]$request
        Check ($uri.Scheme -eq 'http' -and $uri.Host -eq '169.254.169.254' -and $uri.IsDefaultPort -and
            $uri.AbsolutePath -eq '/metadata/identity/oauth2/token') 'DNS token requested outside IMDS'
        $query=@{}
        foreach ($part in $uri.Query.TrimStart('?').Split('&')) {
            $pair=$part.Split('=',2)
            Check ($pair.Count -eq 2 -and -not $query.ContainsKey($pair[0])) 'Ambiguous IMDS query'
            $query[$pair[0]]=[Uri]::UnescapeDataString($pair[1])
        }
        Check ($query.Count -eq 3 -and $query['api-version'] -eq '2018-02-01' -and
            $query['resource'] -eq 'https://management.azure.com/' -and $query['mi_res_id'] -eq $identity) 'DNS token requested for wrong identity or audience'
    }
    foreach ($arguments in @($script:Issued[0],$script:OrderArguments,$script:Renewed[0])) {
        Check ($arguments.AZSubscriptionId -eq $subscription -and $arguments.AZAccessTokenSecure -is [Security.SecureString]) 'Plugin did not receive managed identity credentials'
        Check (-not $arguments.ContainsKey('AZAppCred') -and -not $arguments.ContainsKey('AZUseIMDS')) 'Plugin could select an unintended identity'
    }
    $first=[pscredential]::new('fixture',$script:Issued[0].AZAccessTokenSecure).GetNetworkCredential().Password
    $second=[pscredential]::new('fixture',$script:Renewed[0].AZAccessTokenSecure).GetNetworkCredential().Password
    Check ($first -ne $second) 'Renewal reused persisted token'
    $script:Config.azureDnsManagedIdentityResourceId=$subscription
    $beforeRequests=$script:Requests.Count
    $failed=$false;try {Get-AzureDnsPluginArguments | Out-Null} catch {$failed=$true}
    Check $failed 'Client ID was accepted instead of resource ID'
    Check ($script:Requests.Count -eq $beforeRequests) 'Invalid identity reached IMDS'
} finally {
    $env:POSHACME_HOME=$previousHome
    if (Test-Path -LiteralPath $script:CachedPath) {Remove-Item -LiteralPath $script:CachedPath -Force}
    if (Test-Path -LiteralPath $fixture) {Remove-Item -LiteralPath $fixture -Force}
}
'Azure DNS identity: issuance, renewal, explicit UAMI selection and Key Vault identity separation PASS; no external requests.'
