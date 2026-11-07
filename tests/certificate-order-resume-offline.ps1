<#
.SYNOPSIS
Resume existing ACME orders with synthetic dependency substitutes and no network.
.DESCRIPTION
Covers control flow and order identity guards only. Real PFX/private-key/chain behavior
remains covered by certificate-rotation-offline.ps1 on Linux. No cloud credentials are used.
#>
param([string]$WorkerPath=(Join-Path $PSScriptRoot '../guest/certificate.ps1'))
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop';$WarningPreference='SilentlyContinue'
if(-not $IsLinux){throw 'Run certificate control-flow suites on Linux; cryptographic implementation is not weakened for macOS.'}
$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($WorkerPath,[ref]$t,[ref]$e);if($e.Count){throw 'PROPOSAL_SYNTAX'}
foreach($definition in $ast.EndBlock.Statements|Where-Object {$_-is [Management.Automation.Language.FunctionDefinitionAst]}){. ([scriptblock]::Create($definition.Extent.Text))}
$script:Checks=0;$script:Cases=0
function Check([bool]$value){if(-not $value){throw 'SYNTHETIC_ASSERTION'};$script:Checks++}
$key=[Security.Cryptography.RSA]::Create(2048);$req=[Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=synthetic.example.invalid',$key,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1);$old=$req.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-1),[DateTimeOffset]::UtcNow.AddDays(90));$new=$req.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-1),[DateTimeOffset]::UtcNow.AddDays(91));$script:OldCer=[Convert]::ToBase64String($old.RawData);$script:NewCer=[Convert]::ToBase64String($new.RawData)
$testRoot=Join-Path '/tmp' ('certificate-order-resume-'+[Guid]::NewGuid().ToString('N'));[void][IO.Directory]::CreateDirectory($testRoot);& /bin/chmod 700 $testRoot;$script:FakePfx=Join-Path $testRoot 'synthetic.pfx';[IO.File]::WriteAllBytes($script:FakePfx,[byte[]]@(1,2));& /bin/chmod 600 $script:FakePfx
function Reset([string]$status='valid',[bool]$completed=$true){
 $script:Config=[pscustomobject]@{mode='dnsPlugin';hostname='gateway.example.invalid';hostnames=@('*.example.invalid');requiredHostnames=@('gateway.example.invalid','portal.example.invalid');dnsPlugin='Cloudflare';keyVaultName='kv-synthetic';certificateName='tls-public';directoryUrl='https://acme.invalid/directory';accountEmail='operator@example.invalid';renewBeforeDays=30;minimumValidDays=7}
 $script:Action='Renew';$script:RotateFromThumbprint=$old.Thumbprint;$script:Root=$testRoot;$env:POSHACME_HOME=$testRoot;$script:DnsPluginArgumentsJson='';$script:PreparedPfxBase64='';$script:PreparedPfxPassword='';$script:Current=[pscustomobject]@{cer=$script:OldCer};$script:Account=[pscustomobject]@{location='https://acme.invalid/acct/synthetic';status='valid'}
 $script:Order=[pscustomobject]@{Name='tls-public';location='https://acme.invalid/order/synthetic';MainDomain='*.example.invalid';SANs=@();KeyLength='2048';Profile='classic';Plugin=@('Cloudflare');expires=[DateTimeOffset]::UtcNow.AddDays(2).ToString('o');status=$status;certificate='https://acme.invalid/cert/synthetic';CertExpires=$(if($completed){$old.NotAfter.ToUniversalTime().ToString('o')}else{$null})}
 $script:DelayedChallenge=$false;$script:DelayedFinalize=$false;$script:CompleteReturnsOld=$false;$script:CompleteFails=$false;$script:CompleteLeavesUnusableNew=$false;$script:CacheThumbprintNew=$false;$script:CachePfxMissing=$false;$script:CacheExists=$true;$script:CacheNew=$false;$script:FailImport=$false;$script:RefreshStates=@();$script:Mutate='';$script:WarnRead=$false;$script:Counts=@{force=0;newOrder=0;challenge=0;finalize=0;complete=0;refresh=0;sleep=0;import=0;backup=0};$script:RotationOutcome=''
}
# Real worker control flow; only external IO and PFX behavior are replaced here.
function Get-CurrentCertificate {return $script:Current}
function Test-ReusableCertificate {param($Current,[int]$Days);return $true}
function Ensure-PoshAcme {}
function Restore-AcmeState {}
function Set-PAServer {}
function Get-VaultSecret {param([string]$Name);return [pscustomobject]@{value='SYNTHETIC-NOT-A-CREDENTIAL'}}
function Convert-PluginArguments {param([string]$Json);return @{}}
function Get-PAAccount {return $script:Account}
function Set-PAAccount {}
function Get-PAOrder {[CmdletBinding()]param([string]$Name,[switch]$Refresh);if($Refresh){$script:Counts.refresh++;if($script:WarnRead){Write-Warning 'SYNTHETIC-READ-FAILURE'};if($script:RefreshStates.Count){$script:Order.status=$script:RefreshStates[0];$script:RefreshStates=@($script:RefreshStates|Select-Object -Skip 1)};switch($script:Mutate){'id'{$script:Order.location='https://acme.invalid/order/other'}'domains'{$script:Order.MainDomain='other.invalid'}'key'{$script:Order.KeyLength='4096'}'profile'{$script:Order.Profile='tlsserver'}'account'{$script:Account.location='https://acme.invalid/acct/other'}}};return $script:Order}
function Set-PAOrder {}
function Get-PACertificate {param([string]$Name);if($script:CacheExists){return [pscustomobject]@{PfxFullChain=$(if($script:CachePfxMissing){$script:FakePfx+'.missing'}else{$script:FakePfx});Thumbprint=$(if($script:CacheNew-or $script:CacheThumbprintNew){$new.Thumbprint}else{$old.Thumbprint})}};return $null}
function Test-CachedAcmeCertificate {param($Certificate,[string]$Password,[int]$Days,$RotationReference);return ($null-ne $Certificate-and $script:CacheNew)}
function Assert-NoReparse {param([string]$Path);Check ($Path-ceq $script:FakePfx)}
function Import-Pfx {param([byte[]]$Pfx,[string]$Password);$script:Counts.import++;if($script:FailImport){throw 'SYNTHETIC-IMPORT-FAILURE'};$script:Current=[pscustomobject]@{cer=$script:NewCer}}
function Save-AcmeState {$script:Counts.backup++}
function Submit-Renewal {param([string]$Name,$PluginArgs,[switch]$Force);Check $Force.IsPresent;$script:Counts.force++;$script:CacheExists=$true;$script:CacheNew=$true}
function New-PACertificate {$script:Counts.newOrder++;throw 'UNEXPECTED_NEW_ORDER'}
function Submit-ChallengeValidation {param($Order);$script:Counts.challenge++;if(-not $script:DelayedChallenge){$script:Order.status='ready'}}
function Submit-OrderFinalize {param($Order);$script:Counts.finalize++;if(-not $script:DelayedFinalize){$script:Order.status='valid'}}
function Complete-PAOrder {param($Order);$script:Counts.complete++;if($script:CompleteFails){throw 'SYNTHETIC-COMPLETE-FAILURE'};$script:Order.CertExpires=[DateTimeOffset]::UtcNow.AddDays(91).ToString('o');$script:CacheExists=$true;$script:CachePfxMissing=$false;$script:CacheNew=-not ($script:CompleteReturnsOld-or $script:CompleteLeavesUnusableNew);$script:CacheThumbprintNew=$script:CompleteLeavesUnusableNew}
function Start-Sleep {param([int]$Seconds);Check ($Seconds-eq 2);$script:Counts.sleep++}
function Expect-Stop([scriptblock]$call){$stopped=$false;try{& $call}catch{$stopped=$true};Check $stopped}
try {
 Reset;Ensure-Certificate;Check ($Counts.force-eq 1-and $Counts.finalize-eq 0-and $Counts.complete-eq 0-and $Counts.import-eq 1);$script:Cases++
 Reset 'ready' $false;$script:CacheExists=$false;Ensure-Certificate;Check ($Counts.force-eq 0-and $Counts.finalize-eq 1-and $Counts.complete-eq 1-and $Counts.import-eq 1-and $Counts.newOrder-eq 0);$script:Cases++
 Reset 'pending' $false;$script:CacheExists=$false;Ensure-Certificate;Check ($Counts.challenge-eq 1-and $Counts.finalize-eq 1-and $Counts.complete-eq 1-and $Counts.force-eq 0);$script:Cases++
 Reset 'processing' $false;$script:CacheExists=$false;$script:RefreshStates=@('processing','valid');Ensure-Certificate;Check ($Counts.complete-eq 1-and $Counts.finalize-eq 0-and $Counts.force-eq 0-and $Counts.sleep-eq 1);$script:Cases++
 Reset 'valid' $false;$script:CacheExists=$false;Ensure-Certificate;Check ($Counts.complete-eq 1-and $Counts.force-eq 0-and $Counts.finalize-eq 0);$script:Cases++
 Reset 'pending' $false;$script:Order.expires=[DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o');Expect-Stop {Ensure-Certificate};Check ($Counts.challenge-eq 0-and $Counts.force-eq 0-and $Counts.backup-eq 1);$script:Cases++
 foreach($mutation in @('id','domains','key','profile','account')){Reset 'ready' $false;$script:Mutate=$mutation;Expect-Stop {Ensure-Certificate};Check ($Counts.finalize-eq 0-and $Counts.complete-eq 0-and $Counts.force-eq 0-and $Counts.backup-eq 1);$script:Cases++}
 Reset 'ready' $false;$script:WarnRead=$true;Expect-Stop {Ensure-Certificate};Check ($Counts.force-eq 0-and $Counts.finalize-eq 0-and $Counts.backup-eq 1);$script:Cases++
 Reset 'processing' $false;Expect-Stop {Ensure-Certificate};Check ($Counts.refresh-eq 32-and $Counts.sleep-eq 31-and $Counts.force-eq 0-and $Counts.backup-eq 1);$script:Cases++
 Reset 'ready' $false;$script:CacheNew=$true;Ensure-Certificate;Check ($Counts.import-eq 1-and $Counts.refresh-eq 0-and $Counts.finalize-eq 0-and $Counts.force-eq 0);$script:Cases++
 Reset;$script:Current=[pscustomobject]@{cer=$script:NewCer};Ensure-Certificate;Check ($Counts.force-eq 0-and $Counts.import-eq 0-and $Counts.refresh-eq 0-and $script:RotationOutcome-eq 'current-already-different');$script:Cases++
 Reset 'ready' $false;$script:CacheExists=$false;$script:FailImport=$true;Expect-Stop {Ensure-Certificate};Check ($Counts.complete-eq 1-and $Counts.finalize-eq 1-and $Counts.backup-eq 1);$script:FailImport=$false;Ensure-Certificate;Check ($Counts.complete-eq 1-and $Counts.finalize-eq 1-and $Counts.force-eq 0-and $Counts.import-eq 2-and $script:RotationOutcome-eq 'cached-replacement-imported');$script:Cases++
 Reset 'pending' $false;$script:CacheExists=$false;$script:DelayedChallenge=$true;$script:RefreshStates=@('pending','pending','ready','valid');Ensure-Certificate;Check ($Counts.challenge-eq 1-and $Counts.finalize-eq 1-and $Counts.complete-eq 1-and $Counts.sleep-eq 1-and $Counts.force-eq 0);$script:Cases++
 Reset 'ready' $false;$script:CacheExists=$false;$script:DelayedFinalize=$true;$script:RefreshStates=@('ready','ready','processing','valid');Ensure-Certificate;Check ($Counts.finalize-eq 1-and $Counts.complete-eq 1-and $Counts.sleep-eq 2-and $Counts.force-eq 0);$script:Cases++
 Reset 'ready' $false;$script:CacheExists=$false;$script:DelayedFinalize=$true;Expect-Stop {Ensure-Certificate};Check ($Counts.finalize-eq 1-and $Counts.refresh-eq 32-and $Counts.sleep-eq 30-and $Counts.complete-eq 0-and $Counts.force-eq 0);$script:Cases++
 $syntheticUserInfo=[UriBuilder]::new('https://acme.invalid/order/synthetic');$syntheticUserInfo.UserName='SYNTHETIC_USERINFO'
 foreach($badURL in @($syntheticUserInfo.Uri.AbsoluteUri,'https://acme.invalid/order/synthetic?other=1','https://acme.invalid/order/synthetic#other')){Reset 'ready' $false;$script:Order.location=$badURL;Expect-Stop {Ensure-Certificate};Check ($Counts.refresh-eq 0-and $Counts.finalize-eq 0-and $Counts.complete-eq 0-and $Counts.force-eq 0);$script:Cases++}
 Reset 'valid' $true;$script:CacheExists=$false;Ensure-Certificate;Check ($Counts.complete-eq 1-and $Counts.force-eq 0-and $Counts.finalize-eq 0-and $Counts.import-eq 1);$script:Cases++
 Reset 'valid' $true;$script:CachePfxMissing=$true;Ensure-Certificate;Check ($Counts.complete-eq 1-and $Counts.force-eq 0-and $Counts.finalize-eq 0-and $Counts.import-eq 1);$script:Cases++
 Reset 'valid' $true;$script:CacheExists=$false;$script:CompleteReturnsOld=$true;Ensure-Certificate;Check ($Counts.complete-eq 1-and $Counts.force-eq 1-and $Counts.finalize-eq 0-and $Counts.import-eq 1);$script:Cases++
 Reset 'valid' $true;$script:CacheExists=$false;$script:CompleteFails=$true;Expect-Stop {Ensure-Certificate};Check ($Counts.complete-eq 1-and $Counts.force-eq 0-and $Counts.import-eq 0-and $Counts.backup-eq 1);$script:Cases++
 Reset 'valid' $true;$script:CacheExists=$false;$script:CompleteLeavesUnusableNew=$true;Expect-Stop {Ensure-Certificate};Check ($Counts.complete-eq 1-and $Counts.force-eq 0-and $Counts.import-eq 0-and $Counts.backup-eq 1);$script:Cases++
 Reset 'valid' $true;$script:CacheExists=$false;$script:Order.expires=[DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o');Ensure-Certificate;Check ($Counts.complete-eq 1-and $Counts.force-eq 0-and $Counts.import-eq 1);$script:Cases++
 # Exercise the actual current Save/Restore code with only Key Vault IO replaced.
 foreach($definition in $ast.EndBlock.Statements|Where-Object {$_-is [Management.Automation.Language.FunctionDefinitionAst]}){. ([scriptblock]::Create($definition.Extent.Text))}
 $backupFixture=Join-Path $testRoot 'backup-regression'
 try {
  New-PrivateDirectory $backupFixture;$script:Root=Join-Path $backupFixture 'source';New-PrivateDirectory $script:Root;New-PrivateDirectory (Join-Path $script:Root 'acme');$source=Join-Path $script:Root 'acme/order';New-PrivateDirectory $source
  $script:Config=[pscustomobject]@{keyVaultName='kv-synthetic';certificateName='tls-public'};$script:VaultByName=@{};$script:VaultByURI=@{};$script:VaultWrites=0
  function Get-VaultSecret {param([string]$Name);if($script:VaultByName.ContainsKey($Name)){return $script:VaultByName[$Name]};return $null}
  function Set-VaultSecret {param([string]$Name,[string]$Value);$script:VaultWrites++;$id='https://'+$script:Config.keyVaultName+'.vault.azure.net/secrets/'+$Name+'/'+('{0:x32}'-f $script:VaultWrites);$v=[pscustomobject]@{id=$id;value=$Value};$script:VaultByName[$Name]=$v;$script:VaultByURI[$id]=$v;return $v}
  function Invoke-Json {param([string]$Uri);$u=[Uri]$Uri;Check ($u.Scheme-eq 'https'-and $u.Host-eq ($script:Config.keyVaultName+'.vault.azure.net')-and $u.Query-eq '?api-version=7.4');$id=$u.GetLeftPart([UriPartial]::Path);Check $script:VaultByURI.ContainsKey($id);return $script:VaultByURI[$id]}
  $bytes=[ordered]@{'cert.cer'=$old.RawData;'chain0.cer'=$new.RawData;'chain.cer'=$new.RawData;'fullchain.cer'=$new.RawData;'cert.pfx'=[byte[]]@(1,2,3,4);'fullchain.pfx'=[byte[]]@(5,6,7,8);'order.json'=[Text.Encoding]::UTF8.GetBytes('{"status":"valid"}');'cert.key'=[Text.Encoding]::UTF8.GetBytes('<SYNTHETIC_PRIVATE_KEY_PLACEHOLDER>')}
  foreach($name in $bytes.Keys){Write-PrivateFile (Join-Path $source $name) $bytes[$name]};Write-PrivateFile (Join-Path $source 'ordinary.log') ([Text.Encoding]::UTF8.GetBytes('SYNTHETIC-NOT-STATE'))
  Save-AcmeState;$writes=$script:VaultWrites;Check ($writes-ge 2);Save-AcmeState;Check ($script:VaultWrites-eq $writes)
  $script:Root=Join-Path $backupFixture 'restored';New-PrivateDirectory $script:Root;New-PrivateDirectory (Join-Path $script:Root 'acme');Restore-AcmeState
  foreach($name in $bytes.Keys){$destination=Join-Path $script:Root ('acme/order/'+$name);Check ([IO.File]::Exists($destination));Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($destination))-ceq [Convert]::ToBase64String($bytes[$name]))}
  Check (-not [IO.File]::Exists((Join-Path $script:Root 'acme/order/ordinary.log')));$script:Cases++
 } finally {if([IO.Directory]::Exists($backupFixture)){Remove-Item -LiteralPath $backupFixture -Recurse -Force};$script:VaultByName=$null;$script:VaultByURI=$null}
 [Console]::Out.WriteLine(([ordered]@{status='passed';controlFlowCases=$script:Cases;checks=$script:Checks;actualNetworkCalls=0;actualCAOrders=0;actualCredentialInputs=0;publicSourceEdited=$false;scope='synthetic-control-flow-not-PFX-integration'}|ConvertTo-Json -Compress))
}catch{[Console]::Out.WriteLine(([ordered]@{status='failed';completedCases=$script:Cases;checks=$script:Checks;failureLine=$_.InvocationInfo.ScriptLineNumber;failureType=$_.Exception.GetType().Name;failureInnerType=$(if($_.Exception.InnerException){$_.Exception.InnerException.GetType().Name}else{''});actualNetworkCalls=0;actualCredentialInputs=0}|ConvertTo-Json -Compress));exit 1}
finally{$old.Dispose();$new.Dispose();$key.Dispose();if([IO.File]::Exists($script:FakePfx)){[IO.File]::Delete($script:FakePfx)};if([IO.Directory]::Exists($testRoot)){[IO.Directory]::Delete($testRoot)}}
