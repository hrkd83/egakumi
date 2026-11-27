# 同じACME注文の再開を、順番に返る合成状態だけで検証します。
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'certificate-fixtures.ps1')
$script:Config=[pscustomobject]@{
    certificateName='tls-public';dnsPlugin='Azure';directoryUrl='https://acme.invalid/directory'
}

function Reset-Order([string[]]$States) {
    $script:Account=[pscustomobject]@{location='https://acme.invalid/acct/synthetic';status='valid'}
    $script:Order=[pscustomobject]@{
        Name='tls-public';location='https://acme.invalid/order/synthetic';MainDomain='*.example.invalid';SANs=@()
        KeyLength='2048';Profile='classic';Plugin=@('Azure');expires=[DateTimeOffset]::UtcNow.AddDays(2).ToString('o')
        status='pending';certificate='https://acme.invalid/cert/synthetic'
    }
    $script:States=$States
    $script:Reads=0
    $script:WarnOnRefresh=$false
    $script:ChangeOnRefresh=$null
    $script:Events=[Collections.Generic.List[string]]::new()
}

function Get-PAAccount {return $script:Account}
function Get-PAOrder {
    [CmdletBinding()]
    param([string]$Name,[switch]$Refresh)
    if (-not $Refresh -or $Name -cne 'tls-public') {throw 'Unexpected ACME order lookup'}
    $script:Reads++
    if ($script:WarnOnRefresh) {Write-Warning 'SYNTHETIC_READ_FAILURE'}
    if ($script:States.Count) {
        $script:Order.status=$script:States[0]
        $script:States=@($script:States | Select-Object -Skip 1)
    }
    if ($script:ChangeOnRefresh) {& $script:ChangeOnRefresh}
    return $script:Order
}
function Submit-ChallengeValidation($Order) {$script:Events.Add('challenge')}
function Submit-OrderFinalize($Order) {$script:Events.Add('finalize')}
function Complete-PAOrder($Order) {$script:Events.Add('complete')}
function Start-Sleep([int]$Seconds) {
    if ($Seconds -ne 2) {throw 'Unexpected ACME polling interval'}
    $script:Events.Add('wait')
}

function Expect-Events([string]$Expected,[int]$Reads) {
    if (($script:Events -join ',') -cne $Expected -or $script:Reads -ne $Reads) {
        throw "Unexpected ACME resume events: $($script:Events -join ',') ($script:Reads reads)"
    }
}
function Expect-Stop([string]$Message) {
    try {Resume-ExistingAcmeOrder $script:Order}
    catch {
        if ($_.Exception.Message -cne $Message) {throw}
        return
    }
    throw "Expected ACME resume to stop: $Message"
}

Reset-Order @('pending','pending','ready','ready','processing','valid')
Resume-ExistingAcmeOrder $script:Order
Expect-Events 'challenge,wait,finalize,wait,wait,complete' 6

Reset-Order @('valid')
$script:Order.expires=[DateTimeOffset]::UtcNow.AddMinutes(-1).ToString('o')
Resume-ExistingAcmeOrder $script:Order
Expect-Events 'complete' 1

Reset-Order @('pending')
$script:Order.expires=[DateTimeOffset]::UtcNow.AddMinutes(-1).ToString('o')
Expect-Stop 'Existing ACME order cannot be resumed after expiry'
Expect-Events '' 1

Reset-Order @('invalid')
Expect-Stop 'Existing ACME order did not reach a resumable state'
Expect-Events '' 1

Reset-Order @('valid')
$script:Order.certificate=''
Expect-Stop 'Existing ACME certificate was not confirmed'
Expect-Events '' 1

Reset-Order @('ready')
$script:WarnOnRefresh=$true
Expect-Stop 'Existing ACME order refresh failed'
Expect-Events '' 1

foreach ($location in @('http://acme.invalid/order/synthetic','https://foreign.invalid/order/synthetic',
    'https://acme.invalid/order/synthetic?other=1','https://acme.invalid/order/synthetic#other')) {
    Reset-Order @('ready')
    $script:Order.location=$location
    Expect-Stop 'Existing ACME order identity is invalid'
    Expect-Events '' 0
}

foreach ($change in @(
    {$script:Order.location='https://acme.invalid/order/other'},
    {$script:Order.MainDomain='other.invalid'},
    {$script:Order.KeyLength='4096'},
    {$script:Order.Profile='tlsserver'},
    {$script:Order.Plugin=@('unexpected')},
    {$script:Account.location='https://acme.invalid/acct/other'}
)) {
    Reset-Order @('ready')
    $script:ChangeOnRefresh=$change
    Expect-Stop 'Existing ACME order changed during resume'
    Expect-Events '' 1
}

Reset-Order @('ready')
Expect-Stop 'Existing ACME order processing did not complete'
Expect-Events ((@('finalize') + (@('wait') * 30)) -join ',') 32

'Certificate ACME resume PASS; no Azure, DNS or CA requests.'
