# 現行の証明書Jobが利用するファイル保護とACME状態の退避を検証します。
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'certificate-fixtures.ps1')
$script:Config=[pscustomobject]@{keyVaultName='kv-fixture';certificateName='tls-public'}
$script:checks=0
function Check([bool]$Condition,[string]$Message) {if (-not $Condition) {throw $Message};$script:checks++}

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
try {
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

    $script:Root=Join-Path $fixture 'first';$acmeStatePath=Join-Path $script:Root 'acme';New-PrivateDirectory $acmeStatePath
    # 大きな合成鍵で分割保存を試す。chunkの改ざん・欠落を成功扱いしない。
    $rng=[Security.Cryptography.RandomNumberGenerator]::Create();$data=New-Object byte[] 90000;$rng.GetBytes($data);$rng.Dispose()
    [IO.File]::WriteAllBytes((Join-Path $acmeStatePath 'account.key'),$data)
    [IO.File]::WriteAllText((Join-Path $acmeStatePath 'ignored.log'),'not-backed-up')
    New-PrivateDirectory (Join-Path $acmeStatePath 'nested')
    [IO.File]::WriteAllText((Join-Path $acmeStatePath 'nested/order.json'),'{"fixture":true}')
    $certificateBytes=[byte[]]@(2,7,11,19)
    Write-PrivateFile (Join-Path $acmeStatePath 'nested/fullchain.pfx') $certificateBytes
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
    $restoredCertificate=[IO.File]::ReadAllBytes((Join-Path $script:Root 'acme/nested/fullchain.pfx'))
    Check ((Get-Sha256 $restoredCertificate) -ceq (Get-Sha256 $certificateBytes)) 'Restored PFX differs'
    Check (-not (Test-Path -LiteralPath (Join-Path $script:Root 'acme/ignored.log'))) 'Log was archived'
    $script:Root=Join-Path $fixture 'corrupt';New-PrivateDirectory (Join-Path $script:Root 'acme')
    $chunk=$script:Secrets[[string]$metadata.chunks[0]]
    $replacement=if ($chunk.value.StartsWith('A')) {'B'} else {'A'}
    $chunk.value=$replacement+$chunk.value.Substring(1)
    $failed=$false
    try {Restore-AcmeState}
    catch {if ($_.Exception.Message -cne 'ACME state backup verification failed') {throw};$failed=$true}
    Check $failed 'Corrupted backup accepted'
} finally {
    if (Test-Path -LiteralPath $fixture) {Remove-Item -LiteralPath $fixture -Recurse -Force}
}
"Certificate private state: $script:checks checks PASS; no Azure, DNS or CA requests."
