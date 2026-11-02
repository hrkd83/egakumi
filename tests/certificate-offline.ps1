<#
.SYNOPSIS
証明書処理を、Azure・DNS・認証局へ接続せずに検証する。
.DESCRIPTION
本番用certificate.ps1から関数だけを読み、外部APIと発行コマンドは偽物へ差し替える。
前半は実際のファイル権限・ロック・ACME状態の分割保存と復元を一時フォルダーで確認する。
後半は有効な証明書の再利用と、再発行せず再取り込みできるケースを調べる。
この成功はUbuntu上のsystemd起動・信頼された公開証明書・AppGW反映の実機合格とは別である。
.EXAMPLE
pwsh -NoProfile -File tests/certificate-offline.ps1
#>
# 関数を抽出するだけなので、配備スクリプト末尾の実行処理は起動しない。
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'certificate-fixtures.ps1')
$script:Config=[pscustomobject]@{dnsPlugin='Cloudflare';keyVaultName='kv-fixture';certificateName='tls-public';mode='dnsPlugin';hostname='test.example.invalid';accountEmail='test@example.invalid';directoryUrl='https://acme-v02.api.letsencrypt.org/directory';renewBeforeDays=30;minimumValidDays=7}
$checks=0
function Check([bool]$Condition,[string]$Message){if(-not $Condition){throw $Message};$script:checks++}
# 型付きのDNS入力を、実行時に必要なSecureString等へ戻す境界を確認する。
$converted=Convert-PluginArguments '{"CFToken":{"type":"secureString","value":"fixture-token"},"enabled":{"type":"boolean","value":true},"attempts":{"type":"integer","value":3}}'
Check ($converted.CFToken -is [Security.SecureString]) 'SecureString conversion'
Check ($converted.enabled -eq $true -and $converted.attempts -eq 3) 'Typed values conversion'
$failed=$false;try{Convert-PluginArguments '{"CFToken":{"type":"string","value":"fixture-token"}}'|Out-Null}catch{$failed=$true}
Check $failed 'Cloudflare plaintext token was accepted'
$script:Config.dnsPlugin='Other'
$converted=Convert-PluginArguments '{"login":{"type":"credential","username":"fixture","password":"fixture-password"},"names":{"type":"stringArray","value":["one","two"]}}'
Check ($converted.login -is [pscredential] -and $converted.login.UserName -eq 'fixture') 'Credential conversion'
Check ($converted.names.Count -eq 2) 'StringArray conversion'
$script:Config.dnsPlugin='Cloudflare'
$offset=0;$length=Read-DerLength ([byte[]]@(130,1,0)) ([ref]$offset) 3
Check ($length -eq 256 -and $offset -eq 3) 'DER long length'
$failed=$false;$offset=0;try{Read-DerLength ([byte[]]@(130,1)) ([ref]$offset) 2|Out-Null}catch{$failed=$true}
Check $failed 'Invalid DER accepted'
$script:Secrets=@{};$script:SetCalls=0
# Key Vaultの代役。入力をメモリー内で保管し、ネットワークへ送らない。
function Invoke-Json([string]$Uri,[string]$Method='GET',$Body=$null,[bool]$AllowNotFound=$false){
 $parsed=[Uri]$Uri;$parts=$parsed.AbsolutePath.Split('/');$name=$parts[2]
 if($Method -eq 'PUT'){
  $script:SetCalls++;$id='https://kv-fixture.vault.azure.net/secrets/'+$name+'/'+([Guid]::NewGuid().ToString('N'))
  $value=[pscustomobject]@{id=$id;value=$Body.value;attributes=[pscustomobject]@{enabled=$true}}
  $script:Secrets[$name]=$value;$script:Secrets[$id]=$value;return $value
 }
 if($parts.Count -gt 3 -and $script:Secrets.ContainsKey($parsed.GetLeftPart([UriPartial]::Path))){return $script:Secrets[$parsed.GetLeftPart([UriPartial]::Path)]}
 if($script:Secrets.ContainsKey($name)){return $script:Secrets[$name]}
 return $null
}
# macOSの/tmp等はsymlinkなので、権限試験には実体のある一時パスを使う。
# 既存の証明書・鍵は触らず、この試験で作ったフォルダーだけを最後に削除する。
$fixture=Join-Path $(if ($IsMacOS) {'/private/tmp'} else {[IO.Path]::GetTempPath()}) ('core-certificate-fixture-'+[Guid]::NewGuid().ToString('N'))
New-PrivateDirectory $fixture
Check ([int][IO.File]::GetUnixFileMode($fixture) -eq 448) 'Private directory is not 0700'
$privateFile=Join-Path $fixture 'private.json'
Write-PrivateFile $privateFile ([Text.Encoding]::UTF8.GetBytes('fixture-private-value'))
Check ([int][IO.File]::GetUnixFileMode($privateFile) -eq 384) 'Private file is not 0600'
Write-PrivateFile $privateFile ([Text.Encoding]::UTF8.GetBytes('rotated-private-value'))
Check ([IO.File]::ReadAllText($privateFile) -eq 'rotated-private-value') 'Atomic private file replacement failed'
[IO.File]::SetUnixFileMode($privateFile,[IO.UnixFileMode]420)
$failed=$false;try{Assert-PrivatePath $privateFile}catch{$failed=$true}
Check $failed 'World-readable secret file accepted'
[IO.File]::SetUnixFileMode($privateFile,[IO.UnixFileMode]384)
$link=Join-Path $fixture 'linked.json'
[void][IO.File]::CreateSymbolicLink($link,$privateFile)
$failed=$false;try{Write-PrivateFile $link ([byte[]]@(1))}catch{$failed=$true}
Check $failed 'Symlink secret file accepted'
Remove-Item -LiteralPath $link -Force
$wide=Join-Path $fixture 'wide';[void][IO.Directory]::CreateDirectory($wide);[IO.File]::SetUnixFileMode($wide,[IO.UnixFileMode]493)
$failed=$false;try{New-PrivateDirectory $wide}catch{$failed=$true}
Check $failed 'Public certificate directory accepted'
Remove-Item -LiteralPath $wide -Force
$script:Root=Join-Path $fixture 'locking';New-PrivateDirectory $script:Root
# 更新処理の重複実行を拒否し、正常終了後は次回の更新がロックを取れることを確認する。
$lock=Enter-MaintenanceLock
$failed=$false;try{$overlap=Enter-MaintenanceLock;$overlap.Dispose()}catch{$failed=$true}
Check $failed 'Overlapping certificate maintenance accepted'
$lock.Dispose();$lock=Enter-MaintenanceLock;$lock.Dispose()
Check ([int][IO.File]::GetUnixFileMode((Join-Path $script:Root 'maintenance.lock')) -eq 384) 'Persistent renewal lock mode incorrect'
$script:Root=Join-Path $fixture 'units'
# systemdの定期更新は本番運用にも使う。環境を自動撤去するtimerとは用途が異なる。
$unit=Get-RenewalUnitContent
Check ($unit.service -match 'User=root' -and $unit.service -match 'UMask=0077') 'Renewal service identity/umask missing'
Check ($unit.service -match 'pwsh .*-Action Renew' -and $unit.service -match 'StandardOutput=journal') 'Linux renewal worker/log target missing'
Check ($unit.service -notmatch 'fixture-token|fixture-password') 'Renewal unit contains protected input'
Check ($unit.timer -match 'OnUnitActiveSec=12h' -and $unit.timer -notmatch 'OnCalendar') 'Renewal periodic timer incorrect'
$script:Config|Add-Member -NotePropertyName renewIntervalHours -NotePropertyValue 6
$unit=Get-RenewalUnitContent
Check ($unit.timer -match 'OnUnitActiveSec=6h') 'Configured renewal interval ignored'
[Environment]::SetEnvironmentVariable('Action','Install')
[Environment]::SetEnvironmentVariable('DnsPluginArgumentsJson','protected-fixture-value')
[Environment]::SetEnvironmentVariable('L7IngressSecret',('a'*128))
Read-ManagedInput
Check ($DnsPluginArgumentsJson -eq 'protected-fixture-value' -and $L7IngressSecret -eq ('a'*128)) 'Protected environment was not consumed'
Check ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('DnsPluginArgumentsJson')) -and [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('L7IngressSecret'))) 'Protected environment was retained for child processes'
$logged=Write-MaintenanceEvent $false 'fixture-token value';Check ($logged -notmatch 'fixture-token') 'Unsafe stage text reached ordinary logs'
$DnsPluginArgumentsJson='';$L7IngressSecret=''
$script:Root=Join-Path $fixture 'first';$acmeStatePath=Join-Path $script:Root 'acme';New-PrivateDirectory $acmeStatePath
# 大きな合成鍵で分割保存を試す。chunkの改ざん・欠落を成功扱いしない。
$rng=[Security.Cryptography.RandomNumberGenerator]::Create();$data=New-Object byte[] 90000;$rng.GetBytes($data);$rng.Dispose()
[IO.File]::WriteAllBytes((Join-Path $acmeStatePath 'account.key'),$data)
[IO.File]::WriteAllText((Join-Path $acmeStatePath 'ignored.log'),'not-backed-up')
New-PrivateDirectory (Join-Path $acmeStatePath 'nested')
[IO.File]::WriteAllText((Join-Path $acmeStatePath 'nested/order.json'),'{"fixture":true}')
Save-AcmeState
$metadata=$script:Secrets['tls-public-acme-state'].value|ConvertFrom-Json
Check ($metadata.chunks.Count -gt 1) 'Backup was not split'
Check (@($script:Secrets.GetEnumerator()|Where-Object{$_.Key -notmatch '^https://' -and [Text.Encoding]::UTF8.GetByteCount($_.Value.value) -gt 24000}).Count -eq 0) 'KV secret limit exceeded'
$count=$script:SetCalls;Save-AcmeState
Check ($script:SetCalls -eq $count) 'Unchanged state wrote new versions'
$script:Root=Join-Path $fixture 'restored';New-PrivateDirectory (Join-Path $script:Root 'acme')
Restore-AcmeState
Check ((Get-Sha256 ([IO.File]::ReadAllBytes((Join-Path $script:Root 'acme/account.key')))) -eq (Get-Sha256 $data)) 'Restored key differs'
Check (Test-Path -LiteralPath (Join-Path $script:Root 'acme/nested/order.json')) 'Nested state absent'
Check (-not (Test-Path -LiteralPath (Join-Path $script:Root 'acme/ignored.log'))) 'Log was archived'
$script:Root=Join-Path $fixture 'corrupt';New-PrivateDirectory (Join-Path $script:Root 'acme')
$script:Secrets[[string]$metadata.chunks[0]].value='AAAA'+$script:Secrets[[string]$metadata.chunks[0]].value.Substring(4)
$failed=$false;try{Restore-AcmeState}catch{$failed=$true}
Check $failed 'Corrupted backup accepted'
Remove-Item -LiteralPath $fixture -Recurse -Force
"Certificate local fixture progress: $checks checks completed; no Azure, DNS or CA requests."

# 以下は発行フローの試験。外部応答は偽物だが、再利用するかの判断は実際の関数を使う。
# Ubuntuサービスの有効化と、公開CAのPFX取り込みは別途実機で確かめる。
function Test-ReusableCertificate($Current,[int]$Days){return $script:ReuseFixture}
$script:ReuseFixture=$true;$script:Current=[pscustomobject]@{}
$script:IssueCalls=0;$script:RenewCalls=0;$script:ImportCalls=0
function New-PACertificate{$script:IssueCalls++;throw 'Unexpected CA request'}
function Submit-Renewal{$script:RenewCalls++;throw 'Unexpected CA request'}
function Get-CurrentCertificate{return $script:Current}
$script:FixtureAcmePath=Join-Path ([IO.Path]::GetTempPath()) ('core-no-acme-orders-'+[Guid]::NewGuid().ToString('N'))
function Ensure-PoshAcme{$env:POSHACME_HOME=$script:FixtureAcmePath;[void][IO.Directory]::CreateDirectory($env:POSHACME_HOME)}
function Restore-AcmeState{}
$Action='Install';$DnsPluginArgumentsJson='{"CFToken":{"type":"secureString","value":"rotated-fixture-token"}}'
Ensure-Certificate
Check ($script:IssueCalls -eq 0 -and $script:RenewCalls -eq 0) 'Valid certificate reapply contacted the CA'
Check ($script:Secrets['tls-public-dns-plugin-args'].value -eq $DnsPluginArgumentsJson) 'DNS credential was not rotated'
Check ($script:Secrets.ContainsKey('tls-public-acme-pfx-password')) 'PFX export password was not persisted'
$script:ReuseFixture=$false
function Set-PAServer{}
function Get-PAAccount{return [pscustomobject]@{id='fixture-account'}}
function Set-PAAccount{}
function Get-PAOrder{return [pscustomobject]@{status='valid';MainDomain='test.example.invalid';SANs=@()}}
function Get-PACertificate{return [pscustomobject]@{PfxFullChain=(Join-Path $env:POSHACME_HOME 'cached.pfx')}}
function Test-CachedAcmeCertificate{return $true}
function Import-Pfx{$script:ImportCalls++}
function Save-AcmeState{}
[IO.File]::WriteAllBytes((Join-Path $env:POSHACME_HOME 'cached.pfx'),[byte[]]@(1,2,3))
Ensure-Certificate
Check ($script:ImportCalls -eq 1) 'A previously issued certificate was not reimported'
Check ($script:IssueCalls -eq 0 -and $script:RenewCalls -eq 0) 'Import recovery created a duplicate CA order'
Remove-Item -LiteralPath $env:POSHACME_HOME -Recurse -Force
"Certificate portable unit fixtures: $checks total checks PASS; no Azure, DNS or CA requests."
