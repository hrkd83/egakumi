#requires -Version 7.4
<#
.SYNOPSIS
公開 Gateway の証明書を取得・検査し、Key Vault に保存して自動更新を設定します。
.DESCRIPTION
初回の Install は azd up の Managed Run Command から呼ばれます。DNS-01 認証では
DNS プロバイダーの API で一時 TXT を作り、認証後に同じ注文の TXT だけを片付けます。
DNS ゾーン全体や通常の公開 A レコードを削除する処理ではありません。

２回目以降の Renew は Linux の systemd timer が実行します。有効期間が十分な証明書は
再利用し、更新が必要なときだけ注文します。証明書と必要な更新状態を Key Vault に保持し、
AppGW / APIM がバージョンを固定しない secret URI で更新後の証明書を参照します。

実行には Linux の root、Key Vault へのプライベート通信、VM のマネージド ID の権限、
CA・Azure DNS API・初回の依存ライブラリ取得先への許可が必要です。
公開の実行入口は Azure DNS とマネージド ID による DNS-01 発行・更新だけを受け付けます。
.PARAMETER Action
Install は初回設定と timer の登録、Renew は登録済み設定を使った継続保守です。
.PARAMETER PublicConfigJson
公開 FQDN・Key Vault 名・更新日数等。資格情報を含めない設定 JSON です。
hostname はGatewayの実名、hostnames は発行SANの配列、requiredHostnames は
Gatewayと管理画面などの実ホスト名です。例: hostnames=['*.example.com'] は
gateway.example.com とportal.example.comをカバーし、example.com自体は含みません。
.PARAMETER ConfigFile
Renew が使う root 専用の設定ファイル。Install から保存したものを読みます。
.PARAMETER DnsPluginArgumentsJson
DNS API 資格情報を含む引数 JSON。ログに出さず、対象 Key Vault の secret に保存します。
.PARAMETER L7IngressSecret
AppGW の HTTP 経路を APIM が識別するための共有値。利用者認証とは別の境界です。
.PARAMETER RealtimeTicketKey
Realtime セッション所有者チケットの署名鍵。Key Vault に保持します。
.PARAMETER RotateFromThumbprint
Renewだけで使う任意の計画更新です。現在の公開証明書のSHA-1 thumbprint（40桁）を指定します。
一致する既存証明書だけを期限前に交換し、既に別の正常な証明書なら再発行しません。
この一回の要求を設定ファイルや定期更新timerへ保存しません。
.PARAMETER ReadProtectedEnvironment
Azure Linux Run Command の環境変数から値を受け取り、子プロセスへ継承される前に消します。
.OUTPUTS
成功・失敗と処理段階だけを表示し、終了コード 0 / 1 を返します。鍵・本文・token は出しません。
.NOTES
状態は /var/lib/egakumi-certificates の専用領域へ保存します。ディレクトリは 0700、
ファイルは 0600、所有者は root とし、シンボリックリンク経由の読書きを拒否します。
同時起動をロックで抑制し、例外の詳細を公開ログへ流さず、秘密情報への参照を finally で捨てます。
#>
[CmdletBinding()]
param(
    [ValidateSet('Install','Renew')][string]$Action='Install',
    [string]$PublicConfigJson='',
    [string]$ConfigFile='',
    [string]$DnsPluginArgumentsJson='',
    [string]$L7IngressSecret='',
    [string]$RealtimeTicketKey='',
    [string]$RotateFromThumbprint='',
    [switch]$ReadProtectedEnvironment
)

Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$VerbosePreference='SilentlyContinue'
$DebugPreference='SilentlyContinue'
$WarningPreference='SilentlyContinue'
$InformationPreference='SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$script:Config=$null
$script:Token=$null
$script:Root=$null
$script:ModuleVersion='4.34.0'
$script:LockStream=$null
$script:Stage='configuration'
$script:RotationOutcome=''

<#
.SYNOPSIS
公開設定から項目を読み、未指定の場合だけ既定値を返します。
.DESCRIPTION
設定名と既定値を受け取り、JSON に明示された値を優先します。明示 false / 0 を未指定と取り違えません。
.OUTPUTS
指定項目の値、または既定値。
#>
function Get-Setting([string]$Name,$Default) {
    if ($script:Config.PSObject.Properties.Name -contains $Name) {return $script:Config.$Name}
    return $Default
}
<#
.SYNOPSIS
公開された証明書実行方式だけを、状態領域や外部 API を使う前に受け付けます。
.DESCRIPTION
DNS プラグインの内部変換関数とは分け、直接の worker 起動でも Azure DNS 以外や
外部証明書の持込みへ迂回させません。対応方式を推測して補完しません。
.OUTPUTS
出力なし。未対応の設定は例外で停止します。
#>
function Assert-SupportedCertificateProvider {
    if ($script:Config.mode -cne 'dnsPlugin' -or $script:Config.dnsPlugin -cne 'Azure') {
        throw 'Only Azure DNS certificate issuance and renewal are supported'
    }
}
<#
.SYNOPSIS
発行SANと、実際にTLSで使うホスト名を分けて検査します。
.DESCRIPTION
hostnamesは証明書に入れるDNS名で、左端の一階層だけを表す*.example.comも指定できます。
requiredHostnamesはgateway/portal等の実ホスト名です。省略した旧設定はhostname一個とします。
配列の重複・空値・不正な名前を拒否し、ACMEの注文へ曖昧な入力を渡しません。
.OUTPUTS
検査した文字列配列。主ホストhostnameはrequiredHostnamesへ必ず含めます。
#>
function Get-CertificateHostnames([ValidateSet('hostnames','requiredHostnames')][string]$Setting='hostnames') {
    $primary=$script:Config.hostname
    if ($primary -isnot [string] -or -not (Test-CertificateDnsPattern $primary $false)) {throw 'Invalid public certificate hostname'}
    $values=@($primary)
    if ($script:Config.PSObject.Properties.Name -contains $Setting) {
        if ($script:Config.$Setting -isnot [array]) {throw 'Certificate hostnames must be an array'}
        $values=@($script:Config.$Setting)
    }
    if ($values.Count -lt 1 -or $values.Count -gt 100) {throw 'Invalid certificate hostname count'}
    foreach ($value in $values) {
        if ($value -isnot [string] -or -not (Test-CertificateDnsPattern $value ($Setting -eq 'hostnames'))) {throw 'Invalid certificate hostname'}
    }
    if (@($values | Select-Object -Unique).Count -ne $values.Count) {throw 'Duplicate certificate hostname'}
    if ($Setting -eq 'requiredHostnames' -and $values -notcontains $primary) {throw 'Required hostnames must include the gateway hostname'}
    return $values
}
<#
.SYNOPSIS
FQDNまたは左端一個だけのwildcardを判定します。
.DESCRIPTION
URL・IP・途中の*・複数の*・空ラベルを受け付けません。発行対象だけwildcardを許可します。
.OUTPUTS
形式を満たす場合true。
#>
function Test-CertificateDnsPattern([string]$Name,[bool]$AllowWildcard) {
    if ([string]::IsNullOrEmpty($Name) -or $Name.Length -gt 253) {return $false}
    $dns=$Name
    if ($AllowWildcard -and $dns.StartsWith('*.',[StringComparison]::Ordinal)) {$dns=$dns.Substring(2)}
    $labels=$dns.Split('.')
    if ($labels.Count -lt 2 -or $Name -cne $Name.ToLowerInvariant()) {return $false}
    foreach ($label in $labels) {if ($label.Length -gt 63 -or $label -notmatch '^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$') {return $false}}
    $address=$null
    if ([Net.IPAddress]::TryParse($dns,[ref]$address)) {return $false}
    return $true
}
<#
.SYNOPSIS
SANのDNS名が実ホスト名に利用できるか判定します。
.DESCRIPTION
*.example.comはa.example.comにだけ一致します。example.comやa.b.example.comには一致しません。
文字列の末尾一致だけでは階層が増えたホストを誤って許可するため、左側のラベルも調べます。
.OUTPUTS
完全一致または一階層wildcard一致ならtrue。
#>
function Test-CertificateHostnameCoverage([string]$Pattern,[string]$Hostname) {
    if ($Pattern.Equals($Hostname,[StringComparison]::OrdinalIgnoreCase)) {return $true}
    if (-not $Pattern.StartsWith('*.',[StringComparison]::Ordinal)) {return $false}
    $suffix=$Pattern.Substring(1)
    if (-not $Hostname.EndsWith($suffix,[StringComparison]::OrdinalIgnoreCase)) {return $false}
    $label=$Hostname.Substring(0,$Hostname.Length-$suffix.Length)
    return $label.Length -gt 0 -and -not $label.Contains('.')
}
<#
.SYNOPSIS
別の公開Gatewayの証明書状態を、同じ名前で流用しないようにします。
.DESCRIPTION
実ホストhostnameを変更するときは新しいcertificateNameを使います。同じGatewayの
SAN追加・削除やwildcard化は許可し、実証明書と注文のSAN比較で再発行を選びます。
.OUTPUTS
同じ公開Gatewayなら出力なし。別のGatewayなら例外。
#>
function Assert-PreviousCertificateHostname($Previous) {
    if ($Previous.hostname -ne $script:Config.hostname) {throw 'Use a new certificate name when changing the public hostname'}
}
<#
.SYNOPSIS
書込み先と親ディレクトリにシンボリックリンクがないことを検査します。
.DESCRIPTION
root が外部の指す場所へ秘密ファイルを書かないため、最終ファイルだけでなく親も順にたどります。
.OUTPUTS
成功時は出力なし。安全でない場所なら例外。
#>
function Assert-NoReparse([string]$Path) {
    $current=[IO.Path]::GetFullPath($Path)
    while ($current) {
        if ((Test-Path -LiteralPath $current) -and ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {throw 'Unsafe filesystem location'}
        $parent=[IO.Directory]::GetParent($current)
        if ($null -eq $parent) {break}; $current=$parent.FullName
    }
}
<#
.SYNOPSIS
Unix ファイルの所有者 ID を取得します。
.DESCRIPTION
stat にファイル名を１個の引数として渡します。シェル文字列として組み立てず、失敗時は処理を止めます。
.OUTPUTS
数値の uid。
#>
function Get-UnixOwnerId([string]$Path) {
    # stat receives a path as one argument. No shell expansion or secret data.
    $owner=if ($IsMacOS) {& /usr/bin/stat -f '%u' -- $Path 2>$null} else {& /usr/bin/stat -c '%u' -- $Path 2>$null}
    if ($LASTEXITCODE -ne 0 -or [string]$owner -notmatch '^[0-9]+$') {throw 'Unable to inspect private filesystem ownership'}
    return [int]$owner
}
<#
.SYNOPSIS
現在の実行ユーザーの Unix ID を取得します。
.DESCRIPTION
root 実行の前提と、ファイルの所有者が実行者と一致することの検査に使います。
.OUTPUTS
数値の uid。
#>
function Get-UnixIdentity {
    $identity=& /usr/bin/id -u
    if ($LASTEXITCODE -ne 0 -or [string]$identity -notmatch '^[0-9]+$') {throw 'Unable to inspect certificate service identity'}
    return [int]$identity
}
<#
.SYNOPSIS
秘密情報の格納先が専用権限と正しい所有者か確認します。
.DESCRIPTION
ディレクトリ 0700 / ファイル 0600 を厳密に要求します。存在するファイルを勝手に信頼せず、読書き前にも確認します。
.OUTPUTS
成功時は出力なし。権限・所有者が不適切なら例外。
#>
function Assert-PrivatePath([string]$Path) {
    Assert-NoReparse $Path
    $item=Get-Item -LiteralPath $Path -Force
    $mode=[IO.File]::GetUnixFileMode($Path)
    $expected=if ($item.PSIsContainer) {448} else {384} # directory 0700; file 0600
    if ([int]$mode -ne $expected -or (Get-UnixOwnerId $Path) -ne (Get-UnixIdentity)) {throw 'Private filesystem permissions required'}
}
<#
.SYNOPSIS
秘密状態の保存用ディレクトリを作り、再利用時も検査します。
.DESCRIPTION
最初から 0700 で作成し、作成後に所有者とリンクの有無を検査します。公開ディレクトリを後から狭める方式にはしません。
.OUTPUTS
出力なし。
#>
function New-PrivateDirectory([string]$Path) {
    Assert-NoReparse $Path
    if (-not (Test-Path -LiteralPath $Path)) {[void][IO.Directory]::CreateDirectory($Path,[IO.UnixFileMode]448)}
    Assert-PrivatePath $Path
}
<#
.SYNOPSIS
秘密ファイルを最初の書込みから 0600 で保存します。
.DESCRIPTION
同じディレクトリの一時ファイルに書き、ディスクへ反映してから置換します。失敗時も一時ファイルを回収し、中途半端な更新を残しにくくします。
.OUTPUTS
出力なし。
#>
function Write-PrivateFile([string]$Path,[byte[]]$Bytes) {
    Assert-NoReparse $Path
    Assert-PrivatePath ([IO.Path]::GetDirectoryName($Path))
    if (Test-Path -LiteralPath $Path) {Assert-PrivatePath $Path}
    $temp=$Path+'.'+[Guid]::NewGuid().ToString('N')+'.tmp'
    try {
        $options=[IO.FileStreamOptions]::new()
        $options.Mode=[IO.FileMode]::CreateNew; $options.Access=[IO.FileAccess]::Write; $options.Share=[IO.FileShare]::None
        $options.UnixCreateMode=[IO.UnixFileMode]384
        $stream=[IO.FileStream]::new($temp,$options)
        try {$stream.Write($Bytes,0,$Bytes.Length);$stream.Flush($true)} finally {$stream.Dispose()}
        Assert-PrivatePath $temp
        [IO.File]::Move($temp,$Path,$true)
    } finally {if (Test-Path -LiteralPath $temp) {Remove-Item -LiteralPath $temp -Force}}
}
<#
.SYNOPSIS
Posh-ACME が作った注文・秘密鍵ファイルの権限を整えます。
.DESCRIPTION
専用ツリーだけを走査し、他ユーザー所有やリンクを見つけた場合は止めます。バックアップ前にも適用します。
.OUTPUTS
出力なし。
#>
function Protect-AcmeState {
    $state=Join-Path $script:Root 'acme'
    foreach ($item in @(Get-ChildItem -LiteralPath $state -Recurse -Force)) {
        Assert-NoReparse $item.FullName
        if ((Get-UnixOwnerId $item.FullName) -ne (Get-UnixIdentity)) {throw 'Unexpected certificate state owner'}
        $mode=if ($item.PSIsContainer) {448} else {384}
        [IO.File]::SetUnixFileMode($item.FullName,[IO.UnixFileMode]$mode)
    }
}
<#
.SYNOPSIS
Run Command が渡した値を取り込み、環境変数を直ちに消します。
.DESCRIPTION
Linux では protectedParameters も環境変数で届きます。コマンド引数にコピーせず、PowerShell のメモリー内へ取り込みます。
.OUTPUTS
スクリプト変数へ入力を設定。標準出力には返しません。
#>
function Read-ManagedInput {
    # Managed Run Command Linux named parameters arrive in environment values,
    # including protectedParameters. Never copy them into command-line arguments.
    foreach ($name in @('Action','PublicConfigJson','DnsPluginArgumentsJson','L7IngressSecret','RealtimeTicketKey','RotateFromThumbprint')) {
        $value=[Environment]::GetEnvironmentVariable($name)
        [Environment]::SetEnvironmentVariable($name,$null)
        if ($null -ne $value) {Set-Variable -Name $name -Value $value -Scope Script}
    }
    if ($Action -notin @('Install','Renew')) {throw 'Invalid certificate action'}
}
<#
.SYNOPSIS
公開してよい範囲だけで保守結果を記録します。
.DESCRIPTION
例外本文や plugin の生出力には秘密が混ざり得るため、制御した段階名と定型文だけを journald / Run Command へ出します。
.OUTPUTS
秘密を含まない短い状態メッセージ。
#>
function Write-MaintenanceEvent([bool]$Succeeded,[string]$Stage) {
    # Standard output is collected by journald and Azure Run Command. Only
    # controlled stage labels may be logged; exception text is never emitted.
    $safeStage=if ($Stage -match '^[a-z-]+$') {$Stage} else {'maintenance'}
    if ($Succeeded) {
        Write-Output 'Public gateway certificate is ready; renewal maintenance is configured.'
        if ($script:RotationOutcome -in @('current-already-different','cached-replacement-imported','replacement-issued-and-imported')) {
            Write-Output ('Planned certificate rotation result: '+$script:RotationOutcome+'.')
        }
    }
    else {Write-Output ('Public gateway certificate maintenance failed at '+$safeStage+'. Review certificate configuration and dependency access.')}
}
<#
.SYNOPSIS
初回実行と定期更新の同時書込みを防ぎます。
.DESCRIPTION
両方の実行元が共通の状態ディレクトリ内のファイルを排他で開きます。systemd の PrivateTmp があっても同じロックを参照できます。
.OUTPUTS
排他を維持する FileStream。呼出元の finally で破棄します。
#>
function Enter-MaintenanceLock {
    $path=Join-Path $script:Root 'maintenance.lock'
    Assert-NoReparse $path
    if (Test-Path -LiteralPath $path) {Assert-PrivatePath $path}
    $options=[IO.FileStreamOptions]::new()
    $options.Mode=[IO.FileMode]::OpenOrCreate; $options.Access=[IO.FileAccess]::ReadWrite; $options.Share=[IO.FileShare]::None
    $options.UnixCreateMode=[IO.UnixFileMode]384
    # The lock file is in the shared state directory, so both Run Command and
    # a PrivateTmp systemd service coordinate on the same underlying inode.
    try {$stream=[IO.FileStream]::new($path,$options)} catch {throw 'Certificate maintenance is already running'}
    try {Assert-PrivatePath $path;return $stream} catch {$stream.Dispose();throw}
}
<#
.SYNOPSIS
ACME 状態バックアップの同一性を確認する SHA-256 を計算します。
.DESCRIPTION
ダイジェストはバックアップの破損検出と、同じ内容を再保存しない判定に使います。暗号化の代わりではありません。
.OUTPUTS
小文字の16進文字列。
#>
function Get-Sha256([byte[]]$Bytes) {
    $hash=[Security.Cryptography.SHA256]::Create()
    try {return [BitConverter]::ToString($hash.ComputeHash($Bytes)).Replace('-','').ToLowerInvariant()} finally {$hash.Dispose()}
}
<#
.SYNOPSIS
VM の ID と対象 Key Vault だけに JSON 要求を送ります。
.DESCRIPTION
許可する endpoint を固定し、自動 redirect を禁止します。初回 RBAC の伝搬待ちや一時障害だけを回数限定で再試行し、HTTP の生エラーは出しません。
.OUTPUTS
JSON 応答。明示許可した 404 は null。
#>
function Invoke-Json([string]$Uri,[string]$Method='GET',$Body=$null,[bool]$AllowNotFound=$false) {
    $parsed=[Uri]$Uri
    $imds=$parsed.Scheme -eq 'http' -and $parsed.Host -eq '169.254.169.254' -and $parsed.AbsolutePath -eq '/metadata/identity/oauth2/token'
    if (-not $imds -and ($parsed.Scheme -ne 'https' -or $parsed.Host -ne ($script:Config.keyVaultName+'.vault.azure.net') -or -not $parsed.IsDefaultPort)) {throw 'Unexpected certificate service endpoint'}
    for ($attempt=0; $attempt -lt 32; $attempt++) {
        $request=[Net.HttpWebRequest]::Create($Uri)
        $request.Method=$Method; $request.AllowAutoRedirect=$false; $request.Timeout=30000; $request.ReadWriteTimeout=30000
        if ($imds) {$request.Proxy=$null; $request.Headers.Add('Metadata','true')}
        else {$request.Headers.Add('Authorization','Bearer '+(Get-VaultToken))}
        if ($null -ne $Body) {
            $bytes=[Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 30 -Compress))
            $request.ContentType='application/json'; $request.ContentLength=$bytes.Length
            $stream=$request.GetRequestStream(); try {$stream.Write($bytes,0,$bytes.Length)} finally {$stream.Dispose(); $bytes=$null}
        }
        $response=$null
        try {
            $response=$request.GetResponse()
            $reader=New-Object IO.StreamReader($response.GetResponseStream())
            try {return ($reader.ReadToEnd() | ConvertFrom-Json)} finally {$reader.Dispose()}
        } catch [Net.WebException] {
            $status=0
            if ($null -ne $_.Exception.Response) {$status=[int]$_.Exception.Response.StatusCode; $_.Exception.Response.Dispose()}
            if ($AllowNotFound -and $status -eq 404) {return $null}
            # A newly created direct Azure RBAC assignment can take minutes to
            # reach the Vault data plane. Allow this normal first-install
            # propagation without redeploying the infrastructure.
            $retryLimit=if ($status -eq 403 -and $Action -eq 'Install') {31} else {7}
            if ($attempt -lt $retryLimit -and $status -in @(0,403,408,429,500,502,503,504) -and ($Method -ne 'POST' -or $status -in @(403,429))) {Start-Sleep -Seconds ([Math]::Min(5*($attempt+1),20)); continue}
            throw 'Certificate service request failed'
        } finally {if ($null -ne $response) {$response.Dispose()}}
    }
    throw 'Certificate service request failed'
}
<#
.SYNOPSIS
VM のシステム割り当て ID で Key Vault 用 token を取得します。
.DESCRIPTION
IMDS は VM 内の特別なアドレスです。期限の５分前に取り直し、Azure CLI のログインや Entra の client secret を必要としません。
.OUTPUTS
メモリー内の access token。ログ出力しないでください。
#>
function Get-VaultToken {
    if ($null -eq $script:Token -or $script:Token.expires -le [DateTimeOffset]::UtcNow.AddMinutes(5).ToUnixTimeSeconds()) {
        # IMDS chooses this VM's SystemAssigned identity. Do not reject a valid
        # token because Azure represents the vault audience as its service GUID.
        $value=Invoke-Json 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net'
        if (-not $value.access_token -or [long]$value.expires_on -le [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) {throw 'Managed identity token unavailable'}
        $script:Token=@{value=[string]$value.access_token; expires=[long]$value.expires_on}
    }
    return $script:Token.value
}
<#
.SYNOPSIS
対象 Key Vault の secret を１件読みます。
.DESCRIPTION
存在しない場合だけ null を返します。認証・DNS・ネットワーク失敗を「未作成」と誤認しないよう例外を維持します。
.OUTPUTS
Key Vault の secret 応答、または未作成時 null。
#>
function Get-VaultSecret([string]$Name) {
    return (Invoke-Json ('https://'+$script:Config.keyVaultName+'.vault.azure.net/secrets/'+$Name+'?api-version=7.4') 'GET' $null $true)
}
<#
.SYNOPSIS
事前作成したユーザー割り当てIDでAzure DNS用の引数を作ります。
.DESCRIPTION
固定版プラグインのIMDS認証にはID選択引数がないため、mi_res_idで明示したIDの
ARMトークンを毎回取得します。Key Vault用のシステム割り当てIDは変更しません。
SPや保存済みトークンへフォールバックしません。
.OUTPUTS
実行時に取得したSecureStringトークンとDNS側Subscription ID。
#>
function Get-AzureDnsPluginArguments {
    $subscription=[string](Get-Setting 'azureDnsSubscriptionId' '')
    $identity=[string](Get-Setting 'azureDnsManagedIdentityResourceId' '')
    $parsedSubscription=[Guid]::Empty
    if (-not [Guid]::TryParse($subscription,[ref]$parsedSubscription) -or $parsedSubscription -eq [Guid]::Empty -or
        $identity -notmatch '^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/Microsoft\.ManagedIdentity/userAssignedIdentities/[^/]+$') {
        throw 'Azure DNS requires a subscription and user-assigned managed identity resource ID'
    }
    $uri='http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fmanagement.azure.com%2F&mi_res_id='+[Uri]::EscapeDataString($identity)
    $token=Invoke-Json $uri
    if (-not $token.access_token -or [long]$token.expires_on -le [DateTimeOffset]::UtcNow.AddMinutes(5).ToUnixTimeSeconds()) {
        throw 'Azure DNS managed identity token unavailable'
    }
    return @{AZSubscriptionId=$subscription; AZAccessTokenSecure=(ConvertTo-SecureString ([string]$token.access_token) -AsPlainText -Force)}
}
<#
.SYNOPSIS
対象 Key Vault に更新用情報を secret として保存します。
.DESCRIPTION
値の上限を事前に検査します。返る ID はバックアップの版を固定するために使います。
.OUTPUTS
Key Vault の secret 応答。値は標準出力に表示しません。
#>
function Set-VaultSecret([string]$Name,[string]$Value) {
    # Each chunk is capped below the Key Vault 25 KiB secret-value limit.
    if ([Text.Encoding]::UTF8.GetByteCount($Value) -gt 24000) {throw 'Certificate secret exceeds supported size'}
    return (Invoke-Json ('https://'+$script:Config.keyVaultName+'.vault.azure.net/secrets/'+$Name+'?api-version=7.4') 'PUT' @{value=$Value; attributes=@{enabled=$true}})
}
<#
.SYNOPSIS
JSON の DNS 接続設定を Posh-ACME の引数へ変換します。
.DESCRIPTION
型を明示して変換し、Cloudflare token は SecureString を必須にします。入力を PowerShell のコードとして実行しません。
.OUTPUTS
プラグインへ渡す hashtable。
#>
function Convert-PluginArguments([string]$Json) {
    $inputObject=$Json | ConvertFrom-Json
    $arguments=@{}
    foreach ($property in $inputObject.PSObject.Properties) {
        if ($property.Name -notmatch '^[A-Za-z][A-Za-z0-9_]*$') {throw 'Invalid DNS plugin parameter'}
        $entry=$property.Value
        switch ([string]$entry.type) {
            'secureString' {$arguments[$property.Name]=ConvertTo-SecureString ([string]$entry.value) -AsPlainText -Force}
            'credential' {$arguments[$property.Name]=[pscredential]::new([string]$entry.username,(ConvertTo-SecureString ([string]$entry.password) -AsPlainText -Force))}
            'string' {$arguments[$property.Name]=[string]$entry.value}
            'boolean' {if ($entry.value -isnot [bool]) {throw 'Invalid DNS plugin boolean'}; $arguments[$property.Name]=[bool]$entry.value}
            'integer' {if ($entry.value -isnot [int] -and $entry.value -isnot [long]) {throw 'Invalid DNS plugin integer'}; $arguments[$property.Name]=[int]$entry.value}
            'stringArray' {$arguments[$property.Name]=[string[]]@($entry.value)}
            default {throw 'Unsupported DNS plugin argument type'}
        }
    }
    if ($script:Config.dnsPlugin -eq 'Cloudflare' -and (-not $arguments.ContainsKey('CFToken') -or $arguments.CFToken -isnot [Security.SecureString])) {throw 'Cloudflare CFToken must be a SecureString'}
    return $arguments
}
<#
.SYNOPSIS
固定版の Posh-ACME をロードし、専用状態ディレクトリへ切り替えます。
.DESCRIPTION
モジュール取得は初回 Install だけに限定します。定期更新で依存コードが無断更新されないよう、指定版を照合します。
.OUTPUTS
出力なし。
#>
function Ensure-PoshAcme {
    $script:Stage='module-installation'
    if (-not (Get-Module -ListAvailable -Name Posh-ACME | Where-Object {$_.Version -eq [version]$script:ModuleVersion})) {
        if ($Action -ne 'Install') {throw 'Pinned certificate module must be installed by azd up'}
        Install-Module -Name Posh-ACME -RequiredVersion $script:ModuleVersion -Repository PSGallery -Scope AllUsers -Force -AllowClobber -Confirm:$false *> $null
    }
    # Posh-ACME は POSHACME_HOME を読む前に OS の HOME を参照します。
    # 初回 bootstrap と更新 service が systemd の root login 環境を用意し、
    # worker は HOME を変更せず、ACME 状態の保存先だけを専用化します。
    if ([string]::IsNullOrEmpty($env:HOME) -or -not [IO.Path]::IsPathFullyQualified($env:HOME) -or -not [IO.Directory]::Exists($env:HOME)) {throw 'Certificate module requires the OS login environment'}
    $env:POSHACME_HOME=Join-Path $script:Root 'acme'
    New-PrivateDirectory $env:POSHACME_HOME
    Import-Module Posh-ACME -RequiredVersion $script:ModuleVersion -Force -ErrorAction Stop *> $null
    if ((Get-Module Posh-ACME).Version -ne [version]$script:ModuleVersion) {throw 'Certificate module version mismatch'}
}
<#
.SYNOPSIS
証明書 SAN の DER データから長さを安全に読みます。
.DESCRIPTION
配列範囲と長さ表現を検査し、壊れたデータが後続のホスト名照合をすり抜けないようにします。
.OUTPUTS
読み取った長さ。Offset は次のデータ位置へ進みます。
#>
function Read-DerLength([byte[]]$Bytes,[ref]$Offset,[int]$End) {
    if ($Offset.Value -ge $End) {throw 'Invalid certificate SAN'}
    $first=[int]$Bytes[$Offset.Value]; $Offset.Value++
    if ($first -lt 128) {return $first}
    $count=$first-band 127
    if ($count -lt 1 -or $count -gt 4 -or $Offset.Value+$count -gt $End) {throw 'Invalid certificate SAN'}
    $length=0
    for ($i=0; $i -lt $count; $i++) {$length=($length*256)+[int]$Bytes[$Offset.Value]; $Offset.Value++}
    return $length
}
<#
.SYNOPSIS
証明書のSAN拡張からDNS名の一覧を読みます。
.DESCRIPTION
SAN拡張自体は一個ですが、その中には複数のDNS名を含められます。DERの長さも検査します。
.OUTPUTS
DNS名の配列。壊れたSAN拡張なら例外。
#>
function Get-CertificateDnsNames($Leaf) {
    $san=@($Leaf.Extensions | Where-Object {$_.Oid.Value -eq '2.5.29.17'})
    if ($san.Count -ne 1) {throw 'Certificate SAN is required'}
    $bytes=$san[0].RawData; $offset=0
    if ($bytes[$offset] -ne 48) {throw 'Invalid certificate SAN'}; $offset++
    $length=Read-DerLength $bytes ([ref]$offset) $bytes.Length; $end=$offset+$length; $names=@()
    if ($end -ne $bytes.Length) {throw 'Invalid certificate SAN'}
    while ($offset -lt $end) {
        $tag=$bytes[$offset]; $offset++; $length=Read-DerLength $bytes ([ref]$offset) $end
        if ($length -lt 0 -or $offset+$length -gt $end) {throw 'Invalid certificate SAN'}
        if ($tag -eq 130) {$names+=,[Text.Encoding]::ASCII.GetString($bytes,$offset,$length)}
        $offset+=$length
    }
    return $names
}
<#
.SYNOPSIS
実証明書のSAN集合と、全公開ホストへの適用可否を確認します。
.DESCRIPTION
設定変更前の証明書を期限だけで再利用せず、発行対象の集合が一致することも要求します。
Key Vaultの再利用、ACMEキャッシュ、更新後の取り込みで同じ検査を使います。
.OUTPUTS
設定されたSAN集合と全requiredHostnamesを満たす場合true。壊れたSAN形式は例外。
#>
function Test-CertificateHostnames($Leaf) {
    $names=@(Get-CertificateDnsNames $Leaf)
    $expected=@(Get-CertificateHostnames 'hostnames')
    $actualSet=($names | Sort-Object -Unique) -join ','
    $expectedSet=($expected | Sort-Object -Unique) -join ','
    if ($actualSet -ine $expectedSet) {return $false}
    foreach ($required in @(Get-CertificateHostnames 'requiredHostnames')) {
        $covered=$false
        foreach ($pattern in $names) {if (Test-CertificateHostnameCoverage $pattern $required) {$covered=$true;break}}
        if (-not $covered) {return $false}
    }
    return $true
}
<#
.SYNOPSIS
秘密鍵・有効期限・全SAN・用途・公開CAチェーンをまとめて検査します。
.DESCRIPTION
SANが一個という制限ではありません。SAN拡張は一個、その中の全DNS名を上の共通処理で照合します。
.OUTPUTS
成功時は出力なし。不適合なら例外。
#>
function Assert-Certificate($Leaf,$Collection,[bool]$CheckPrivateKey) {
    if ($CheckPrivateKey -and -not $Leaf.HasPrivateKey) {throw 'Certificate private key is required'}
    if ($Leaf.NotBefore.ToUniversalTime() -gt [DateTime]::UtcNow -or $Leaf.NotAfter.ToUniversalTime() -le [DateTime]::UtcNow.AddDays([int](Get-Setting 'minimumValidDays' 7))) {throw 'Certificate validity is insufficient'}
    if (-not (Test-CertificateHostnames $Leaf)) {throw 'Certificate SAN does not match the configured public hostnames'}
    $eku=@($Leaf.Extensions | Where-Object {$_.Oid.Value -eq '2.5.29.37'})
    if ($eku.Count -ne 1 -or @($eku[0].EnhancedKeyUsages | Where-Object {$_.Value -eq '1.3.6.1.5.5.7.3.1'}).Count -ne 1) {throw 'TLS server authentication usage is required'}
    $chain=New-Object Security.Cryptography.X509Certificates.X509Chain
    try {
        $chain.ChainPolicy.RevocationMode=[Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
        $chain.ChainPolicy.VerificationFlags=[Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
        $chain.ChainPolicy.UrlRetrievalTimeout=[TimeSpan]::FromSeconds(15)
        foreach ($item in $Collection) {[void]$chain.ChainPolicy.ExtraStore.Add($item)}
        if (-not $chain.Build($Leaf)) {throw 'Certificate trust chain verification failed'}
    } finally {$chain.Dispose()}
}
<#
.SYNOPSIS
Key Vault に登録済みの公開証明書情報を取得します。
.DESCRIPTION
secret 本文を読む前に、証明書の期限や版を確認する入口です。未登録だけを null として返します。
.OUTPUTS
証明書情報、または未登録時 null。
#>
function Get-CurrentCertificate {
    return (Invoke-Json ('https://'+$script:Config.keyVaultName+'.vault.azure.net/certificates/'+$script:Config.certificateName+'?api-version=7.4') 'GET' $null $true)
}
<#
.SYNOPSIS
登録済み証明書を再注文なしで使えるか判断します。
.DESCRIPTION
期限が十分なら対応する版の PFX も読み、公開証明書・秘密鍵・チェーンの一致を検査します。通信失敗は再発行理由にしません。
.OUTPUTS
再利用できる場合 true、未作成や期限接近なら false。不正・操作失敗は例外。
#>
function Test-ReusableCertificate($Current,[int]$Days) {
    if ($null -eq $Current) {return $false}
    if (-not $Current.attributes.enabled) {throw 'The existing certificate was disabled by an operator'}
    if (-not $Current.cer -or -not $Current.sid -or -not $Current.policy.key_props.exportable -or $Current.policy.secret_props.contentType -ne 'application/x-pkcs12') {throw 'The existing certificate is unsuitable for Application Gateway'}
    $publicLeaf=[Security.Cryptography.X509Certificates.X509Certificate2]::new([Convert]::FromBase64String($Current.cer))
    try {
        if (-not (Test-CertificateHostnames $publicLeaf)) {return $false}
        if ($publicLeaf.NotAfter.ToUniversalTime() -le [DateTime]::UtcNow.AddDays($Days)) {return $false}
    } finally {$publicLeaf.Dispose()}
    # Failed inspection is not evidence that a new certificate is needed. In
    # particular, RBAC/DNS/network failures must not trigger a new ACME order.
    $secret=Invoke-Json ([string]$Current.sid+'?api-version=7.4')
    if (-not $secret.attributes.enabled) {throw 'The existing certificate secret was disabled by an operator'}
    $certificates=[Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
    try {
        # Key Vault exports its certificate secret as an unencrypted PFX. Read
        # the version associated with this certificate, with intermediates, so
        # reuse does not depend on an opportunistic AIA download.
        $certificates.Import([Convert]::FromBase64String($secret.value),'',[Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet)
        $leaves=@($certificates | Where-Object {$_.HasPrivateKey})
        if ($leaves.Count -ne 1 -or [Convert]::ToBase64String($leaves[0].RawData) -ne $Current.cer) {throw 'Existing certificate and secret do not match'}
        Assert-Certificate $leaves[0] $certificates $true
        return $true
    } finally {foreach ($certificate in $certificates) {$certificate.Dispose()};$secret=$null}
}
<#
.SYNOPSIS
検査済み PFX を Key Vault の証明書として取り込みます。
.DESCRIPTION
同じ有効証明書なら追加登録を省略します。取り込み応答が失われた場合も実際の登録結果を照合し、秘密鍵は呼出元へ返しません。
.OUTPUTS
出力なし。
#>
function Import-Pfx([byte[]]$Pfx,[string]$Password) {
    $script:Stage='certificate-import'
    $collection=New-Object Security.Cryptography.X509Certificates.X509Certificate2Collection
    try {
        $collection.Import($Pfx,$Password,[Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet)
        $leaves=@($collection | Where-Object {$_.HasPrivateKey})
        if ($leaves.Count -ne 1) {throw 'Exactly one certificate private key is required'}
        Assert-Certificate $leaves[0] $collection $true
        $current=Get-CurrentCertificate
        if ($null -ne $current -and $current.cer -eq [Convert]::ToBase64String($leaves[0].RawData) -and $current.attributes.enabled) {return}
        $body=@{value=[Convert]::ToBase64String($Pfx);pwd=$Password;attributes=@{enabled=$true};policy=@{secret_props=@{contentType='application/x-pkcs12'};key_props=@{exportable=$true;reuse_key=$false}}}
        try {$imported=Invoke-Json ('https://'+$script:Config.keyVaultName+'.vault.azure.net/certificates/'+$script:Config.certificateName+'/import?api-version=7.4') 'POST' $body}
        catch {$imported=Get-CurrentCertificate}
        if ($null -eq $imported) {throw 'Certificate import was not confirmed'}
        if ($imported.cer -ne [Convert]::ToBase64String($leaves[0].RawData) -or -not $imported.attributes.enabled) {throw 'Certificate import was not confirmed'}
        # Application Gateway reads the unversioned certificate secret directly;
        # no private key, PFX or password is returned to the deployment caller.
    } finally {foreach ($item in $collection) {$item.Dispose()}; $body=$null; $Password=$null; $Pfx=$null}
}
<#
.SYNOPSIS
直前の ACME 注文で取得済みの証明書を再利用できるか確認します。
.DESCRIPTION
CA 発行後に Key Vault 登録だけ失敗しても再発行しないための判定です。専用状態ディレクトリ外やリンクからの読込みは拒否します。
.OUTPUTS
再利用できる場合 true。
#>
function Test-CachedAcmeCertificate($Certificate,[string]$Password,[int]$Days,$RotationReference=$null) {
    if ($null -eq $Certificate -or -not $Certificate.PfxFullChain) {return $false}
    $path=[IO.Path]::GetFullPath([string]$Certificate.PfxFullChain)
    if (-not $path.StartsWith($env:POSHACME_HOME+[IO.Path]::DirectorySeparatorChar,[StringComparison]::Ordinal)) {throw 'Unexpected certificate file location'}
    Assert-NoReparse $path
    if (-not (Test-Path -LiteralPath $path)) {return $false}
    $collection=[Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
    try {
        $collection.Import([IO.File]::ReadAllBytes($path),$Password,[Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet)
        $leaves=@($collection | Where-Object {$_.HasPrivateKey})
        if ($leaves.Count -ne 1) {throw 'Cached certificate private key is invalid'}
        if (-not (Test-CertificateHostnames $leaves[0])) {return $false}
        if ($leaves[0].NotAfter.ToUniversalTime() -le [DateTime]::UtcNow.AddDays($Days)) {return $false}
        Assert-Certificate $leaves[0] $collection $true
        if ($null -ne $RotationReference) {
            # Posh-ACMEの文字列metadataではなく、保護PFXから読んだ実際の公開leafを比較します。
            # 同じleafの再取り込みや、別の古いleafへの巻戻しを計画更新の成功にしません。
            if ($leaves[0].Thumbprint -ieq $RotationReference.thumbprint -or
                $leaves[0].NotBefore.ToUniversalTime() -lt $RotationReference.notBeforeUtc -or
                $leaves[0].NotAfter.ToUniversalTime() -lt $RotationReference.notAfterUtc) {return $false}
        }
        return $true
    } finally {foreach ($item in $collection) {$item.Dispose()}}
}
<#
.SYNOPSIS
管理 VM を作り直した場合、ACME のアカウントと注文を Key Vault から復元します。
.DESCRIPTION
バージョン付きの分割 secret を読み、サイズと SHA-256 を検査します。ZIP の親ディレクトリ参照や絶対パスを拒否します。
.OUTPUTS
専用状態ディレクトリへ復元。標準出力なし。
#>
function Restore-AcmeState {
    $script:Stage='acme-state-restore'
    $acmeStatePath=Join-Path $script:Root 'acme'
    if (@(Get-ChildItem -LiteralPath $acmeStatePath -Recurse -File -Force).Count -gt 0) {return}
    $index=Get-VaultSecret ($script:Config.certificateName+'-acme-state')
    if ($null -eq $index) {return}
    $metadata=$index.value | ConvertFrom-Json
    if ($metadata.format -ne 'posh-acme-zip-v1' -or @($metadata.chunks).Count -gt 100) {throw 'Unsupported ACME state backup'}
    $encoded=''
    foreach ($chunk in $metadata.chunks) {
        $uri=[Uri][string]$chunk
        if ($uri.Scheme -ne 'https' -or $uri.Host -ne ($script:Config.keyVaultName+'.vault.azure.net') -or $uri.AbsolutePath -notmatch ('^/secrets/'+[regex]::Escape($script:Config.certificateName)+'-acme-part-[0-9]{3}/[a-f0-9]+$')) {throw 'Invalid ACME state reference'}
        $part=Invoke-Json ($uri.AbsoluteUri+'?api-version=7.4')
        $encoded+=[string]$part.value
    }
    $bytes=[Convert]::FromBase64String($encoded)
    if ($bytes.Length -gt 1048576 -or (Get-Sha256 $bytes) -ne $metadata.sha256) {throw 'ACME state backup verification failed'}
    Add-Type -AssemblyName System.IO.Compression
    $memory=New-Object IO.MemoryStream(,$bytes)
    $archive=New-Object IO.Compression.ZipArchive($memory,[IO.Compression.ZipArchiveMode]::Read,$false)
    try {
        $total=0
        foreach ($entry in $archive.Entries) {
            if ($entry.FullName -match '(^/|:|(^|/)\.\.(/|$)|\\)' -or $entry.Length -gt 1048576) {throw 'Invalid ACME state entry'}
            $destination=Join-Path $acmeStatePath $entry.FullName
            $directory=[IO.Path]::GetDirectoryName($destination)
            $relative=$directory.Substring($acmeStatePath.Length).TrimStart('/')
            $cursor=$acmeStatePath
            foreach ($segment in $relative.Split('/')) {if ($segment) {$cursor=Join-Path $cursor $segment; New-PrivateDirectory $cursor}}
            $stream=$entry.Open(); $content=New-Object IO.MemoryStream
            try {
                $buffer=New-Object byte[] 8192
                while (($read=$stream.Read($buffer,0,$buffer.Length)) -gt 0) {
                    $total+=$read
                    if ($total -gt 1048576) {throw 'ACME state exceeds supported size'}
                    $content.Write($buffer,0,$read)
                }
                Write-PrivateFile $destination $content.ToArray()
            } finally {$stream.Dispose();$content.Dispose()}
        }
    } finally {$archive.Dispose();$memory.Dispose();$bytes=$null;$encoded=$null}
}
<#
.SYNOPSIS
ACME の更新に必要な状態を Key Vault にバックアップします。
.DESCRIPTION
専用ツリーの対象拡張子だけを上限付きでまとめます。分割 secret を先に保存し、最後に索引を更新するので途中失敗でも前の完成版を参照できます。
.OUTPUTS
出力なし。
#>
function Save-AcmeState {
    $script:Stage='acme-state-backup'
    Protect-AcmeState
    Add-Type -AssemblyName System.IO.Compression
    $acmeStatePath=Join-Path $script:Root 'acme'
    $memory=New-Object IO.MemoryStream
    $archive=New-Object IO.Compression.ZipArchive($memory,[IO.Compression.ZipArchiveMode]::Create,$true)
    try {
        $total=0
        foreach ($file in @(Get-ChildItem -LiteralPath $acmeStatePath -Recurse -File -Force)) {
            # Only the dedicated Posh-ACME state tree is backed up. Module code,
            # installation packages, temporary files and ordinary logs are not.
            if ($file.Extension -notin @('.json','.key','.pem','.cer','.pfx','.csr','.txt','.xml')) {continue}
            $total+=$file.Length
            if ($total -gt 1048576) {throw 'ACME state exceeds supported size'}
            Assert-NoReparse $file.FullName
            $relative=$file.FullName.Substring($acmeStatePath.Length+1).Replace('\','/')
            $entry=$archive.CreateEntry($relative,[IO.Compression.CompressionLevel]::Optimal)
            $entry.LastWriteTime=[DateTimeOffset]$file.LastWriteTimeUtc
            $writer=$entry.Open(); $reader=[IO.File]::OpenRead($file.FullName)
            try {$reader.CopyTo($writer)} finally {$reader.Dispose();$writer.Dispose()}
        }
    } finally {$archive.Dispose()}
    try {
        $bytes=$memory.ToArray()
        if ($bytes.Length -gt 1048576) {throw 'ACME state exceeds supported size'}
        $digest=Get-Sha256 $bytes
        $previous=Get-VaultSecret ($script:Config.certificateName+'-acme-state')
        if ($null -ne $previous -and ($previous.value | ConvertFrom-Json).sha256 -eq $digest) {return}
        $encoded=[Convert]::ToBase64String($bytes); $ids=@()
        for ($offset=0; $offset -lt $encoded.Length; $offset+=20000) {
            $part=$offset/20000
            $value=$encoded.Substring($offset,[Math]::Min(20000,$encoded.Length-$offset))
            $secret=Set-VaultSecret ($script:Config.certificateName+('-acme-part-{0:000}' -f $part)) $value
            $ids+=,[string]$secret.id
        }
        # Publish the small manifest last. Its versioned chunk IDs permit an
        # interrupted backup to retain the preceding complete backup.
        $manifest=@{format='posh-acme-zip-v1';sha256=$digest;chunks=$ids} | ConvertTo-Json -Depth 5 -Compress
        [void](Set-VaultSecret ($script:Config.certificateName+'-acme-state') $manifest)
    } finally {$memory.Dispose();$bytes=$null;$encoded=$null}
}
<#
.SYNOPSIS
一回限りの計画更新要求を検査し、比較用thumbprintを返します。
.DESCRIPTION
期限判定やシステム時刻を変更する機能ではありません。DNS自動管理の既存証明書を
運用者が明示的に交換するときだけ指定します。Install・未対応方式・不正な値は停止します。
.OUTPUTS
未指定なら空文字、指定時は正規化した公開thumbprint。
#>
function Get-PlannedRotationThumbprint {
    $requested=Get-Variable -Name RotateFromThumbprint -Scope Script -ValueOnly -ErrorAction SilentlyContinue
    if ($null -eq $requested -or $requested -eq '') {return ''}
    if ($requested -isnot [string] -or $requested -notmatch '\A[A-Fa-f0-9]{40}\z' -or
        $Action -ne 'Renew' -or $script:Config.mode -ne 'dnsPlugin') {throw 'Invalid planned certificate rotation request'}
    return $requested.ToUpperInvariant()
}
<#
.SYNOPSIS
未完了の同じ ACME 注文を、新しい注文に置き換えずに再開します。
.DESCRIPTION
注文・アカウント・SAN・鍵長・profile を固定し、fresh read の失敗は停止します。
検証と finalize はそれぞれ一回だけ行い、反映待ちは上限付きの読み取りで待ちます。
valid の公開証明書と保護 PFX が欠けた場合も Complete で同じ証明書を復元します。
.OUTPUTS
専用 ACME cache を更新。標準出力なし。
#>
function Resume-ExistingAcmeOrder($Order) {
    # Only unfinished work is resumed here. Do not route it through a forced
    # renewal, which replaces an existing ready order in Posh-ACME 4.34.0.
    $name=[string]$Order.Name
    $location=[string]$Order.location
    $domains=(@($Order.MainDomain)+@($Order.SANs) | Where-Object {$_} | Sort-Object -Unique) -join ','
    $keyLength=[string]$Order.KeyLength
    $profile=if ($Order.PSObject.Properties['Profile']) {[string]$Order.Profile} else {''}
    $account=Get-PAAccount
    $accountLocation=[string]$account.location
    $directory=[Uri]$script:Config.directoryUrl
    $uri=$null
    if (-not [Uri]::TryCreate($location,[UriKind]::Absolute,[ref]$uri) -or
        $uri.Scheme -ne 'https' -or $uri.Host -ne $directory.Host -or -not $uri.IsDefaultPort -or
        $uri.UserInfo -ne '' -or $uri.Query -ne '' -or $uri.Fragment -ne '' -or
        $account.status -ne 'valid' -or $name -ne $script:Config.certificateName) {
        throw 'Existing ACME order identity is invalid'
    }
    $challenged=$false;$finalized=$false
    for ($attempt=0; $attempt -lt 32; $attempt++) {
        $warnings=@()
        $fresh=Get-PAOrder -Name $name -Refresh -WarningVariable warnings -WarningAction SilentlyContinue
        if ($warnings.Count -gt 0 -or $null -eq $fresh) {throw 'Existing ACME order refresh failed'}
        $freshDomains=(@($fresh.MainDomain)+@($fresh.SANs) | Where-Object {$_} | Sort-Object -Unique) -join ','
        $freshProfile=if ($fresh.PSObject.Properties['Profile']) {[string]$fresh.Profile} else {''}
        if ([string]$fresh.Name -cne $name -or [string]$fresh.location -cne $location -or
            $freshDomains -cne $domains -or [string]$fresh.KeyLength -cne $keyLength -or
            $freshProfile -cne $profile -or (Get-PAAccount).location -cne $accountLocation -or
            (@($fresh.Plugin | Sort-Object -Unique) -join ',') -cne [string]$script:Config.dnsPlugin) {
            throw 'Existing ACME order changed during resume'
        }
        $expiry=[DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse([string]$fresh.expires,[ref]$expiry) -or
            ($fresh.status -ne 'valid' -and $expiry -le [DateTimeOffset]::UtcNow)) {
            throw 'Existing ACME order cannot be resumed after expiry'
        }
        if ($fresh.status -eq 'valid') {
            if ([string]::IsNullOrWhiteSpace([string]$fresh.certificate)) {throw 'Existing ACME certificate was not confirmed'}
            Complete-PAOrder -Order $fresh *> $null
            return
        }
        if ($fresh.status -eq 'pending') {
            if (-not $challenged) {
                Submit-ChallengeValidation -Order $fresh *> $null
                $challenged=$true
            } elseif ($attempt -lt 31) {Start-Sleep -Seconds 2}
        } elseif ($fresh.status -eq 'ready') {
            if (-not $finalized) {
                Submit-OrderFinalize -Order $fresh *> $null
                $finalized=$true
            } elseif ($attempt -lt 31) {Start-Sleep -Seconds 2}
        } elseif ($fresh.status -eq 'processing') {
            if ($attempt -lt 31) {Start-Sleep -Seconds 2}
        } else {
            throw 'Existing ACME order did not reach a resumable state'
        }
    }
    throw 'Existing ACME order processing did not complete'
}

<#
.SYNOPSIS
モード・期限に応じて再利用、持込み、取得または更新を選びます。
.DESCRIPTION
DNS-01 の TXT は各注文の検証時だけ作成・回収します。azd down まで放置する構造ではありません。失敗時も注文状態を保存し、再注文の重複を抑えます。
.OUTPUTS
対象 Key Vault の証明書を利用可能な状態にします。標準出力なし。
#>
function Ensure-Certificate {
    $script:Stage='certificate-inspection'
    $script:RotationOutcome=''
    $rotationThumbprint=Get-PlannedRotationThumbprint
    $rotate=-not [string]::IsNullOrEmpty($rotationThumbprint)
    $rotationReference=$null
    $renewDays=[int](Get-Setting 'renewBeforeDays' 30)
    $current=Get-CurrentCertificate
    if ($rotate) {
        # 期限前更新でも、現在のleaf・対応する秘密鍵・chain・SAN・最低残日数を先に検査します。
        # 読取/権限/通信の失敗を再発行の理由にせず、存在しない証明書の新規作成も行いません。
        if ($null -eq $current -or -not (Test-ReusableCertificate $current ([int](Get-Setting 'minimumValidDays' 7)))) {
            throw 'A valid existing certificate is required for planned rotation'
        }
        $leaf=[Security.Cryptography.X509Certificates.X509Certificate2]::new([Convert]::FromBase64String($current.cer))
        try {
            if ($leaf.Thumbprint -ine $rotationThumbprint) {
                # 前の実行でKV登録まで終わった場合も含め、別の正常なleafへ重ねて発行しません。
                $script:RotationOutcome='current-already-different'
                return
            }
            $rotationReference=@{thumbprint=$leaf.Thumbprint;notBeforeUtc=$leaf.NotBefore.ToUniversalTime();notAfterUtc=$leaf.NotAfter.ToUniversalTime()}
        } finally {$leaf.Dispose()}
    }
    Ensure-PoshAcme
    Restore-AcmeState
    if ($script:Config.dnsPlugin -ne 'Azure') {
        $secretName=$script:Config.certificateName+'-dns-plugin-args'
        if ($Action -eq 'Install' -and $DnsPluginArgumentsJson) {
            $script:Stage='dns-credential-storage'
            [void](Convert-PluginArguments $DnsPluginArgumentsJson)
            [void](Set-VaultSecret $secretName $DnsPluginArgumentsJson)
        }
        $secret=Get-VaultSecret $secretName
        if ($null -eq $secret) {throw 'DNS plugin configuration is required'}
        $pluginArguments=Convert-PluginArguments ([string]$secret.value)
    }
    $passwordSecret=Get-VaultSecret ($script:Config.certificateName+'-acme-pfx-password')
    if ($null -eq $passwordSecret) {
        if ($rotate) {throw 'Restore the existing ACME PFX password before planned rotation'}
        $random=New-Object byte[] 48; $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
        try {$rng.GetBytes($random);$password=[Convert]::ToBase64String($random)} finally {$rng.Dispose()}
        $passwordSecret=Set-VaultSecret ($script:Config.certificateName+'-acme-pfx-password') $password
    }
    $password=[string]$passwordSecret.value
    # Reapplying azd up reuses a sufficiently valid certificate. Credentials
    # have still been rotated in the Vault and will be reread by the next task.
    if (-not $rotate -and (Test-ReusableCertificate $current $renewDays)) {
        if (@(Get-ChildItem -LiteralPath $env:POSHACME_HOME -Recurse -File -Force).Count -gt 0) {Save-AcmeState}
        return
    }
    Set-PAServer -DirectoryUrl $script:Config.directoryUrl -DisableTelemetry *> $null
    if (-not (Get-PAAccount)) {
        if ($rotate) {throw 'Restore the existing ACME account before planned rotation'}
        New-PAAccount -Contact $script:Config.accountEmail -AcceptTOS -UseAltPluginEncryption *> $null
        Set-PAAccount -UseAltPluginEncryption *> $null
        Save-AcmeState
    } else {Set-PAAccount -UseAltPluginEncryption *> $null}
    $name=$script:Config.certificateName
    $domains=@(Get-CertificateHostnames 'hostnames')
    try {
        if ($script:Config.dnsPlugin -eq 'Azure') {
            $script:Stage='dns-managed-identity-authentication'
            $pluginArguments=Get-AzureDnsPluginArguments
        }
        $script:Stage='acme-issuance-or-renewal'
        $order=Get-PAOrder -Name $name
        $sameDomains=$false
        if ($null -ne $order) {
            $orderDomains=@($order.MainDomain)+@($order.SANs)
            $sameDomains=(($orderDomains | Where-Object {$_} | Sort-Object -Unique) -join ',') -ieq (($domains | Sort-Object -Unique) -join ',')
        }
        if ($rotate -and ($null -eq $order -or -not $sameDomains)) {throw 'Planned rotation requires the existing matching ACME order'}
        if ($null -eq $order -or -not $sameDomains) {
            # SANが増減したときだけ既存注文を置き換えます。通常更新・再試行ではForceしません。
            # Posh-ACME 4.34.0はMainDomainだけの変更をSAN差分と判定しないため、
            # 例: 単一gateway名→*.example.comの変更も、こちらで集合を照合します。
            New-PACertificate -Domain $domains -Name $name -Contact $script:Config.accountEmail -AcceptTOS -DirectoryUrl $script:Config.directoryUrl -Plugin $script:Config.dnsPlugin -PluginArgs $pluginArguments -CertKeyLength '2048' -PfxPassSecure (ConvertTo-SecureString $password -AsPlainText -Force) -DnsSleep ([int](Get-Setting 'dnsSleepSeconds' 60)) -ValidationTimeout ([int](Get-Setting 'validationTimeoutSeconds' 120)) -Force:($null -ne $order) *> $null
        } else {
            $cached=Get-PACertificate -Name $name
            $cacheDays=if ($rotate) {[int](Get-Setting 'minimumValidDays' 7)} else {$renewDays}
            if (Test-CachedAcmeCertificate $cached $password $cacheDays $rotationReference) {
                # A preceding issuance may have succeeded before a Vault import
                # or connection failed. Reimport that certificate, not a second
                # ACME order, on retry or after restoring the management VM.
                Import-Pfx ([IO.File]::ReadAllBytes([string]$cached.PfxFullChain)) $password
                if ($rotate) {$script:RotationOutcome='cached-replacement-imported'}
                return
            }
            # 通常は実KV証明書が更新期限に入ったときだけ、計画更新は検査した旧leafが
            # 指定thumbprintと一致するときだけ、この一つの既存注文を更新します。
            Set-PAOrder -Name $name -Plugin $script:Config.dnsPlugin -PluginArgs $pluginArguments -PfxPassSecure (ConvertTo-SecureString $password -AsPlainText -Force) -DnsSleep ([int](Get-Setting 'dnsSleepSeconds' 60)) -ValidationTimeout ([int](Get-Setting 'validationTimeoutSeconds' 120)) *> $null
            $status=[string]$order.status
            $hasCompletedCertificate=$order.PSObject.Properties['CertExpires'] -and $order.CertExpires
            $cacheFilesPresent=$null -ne $cached -and $cached.PfxFullChain -and [IO.File]::Exists([string]$cached.PfxFullChain)
            $resume=$status -in @('pending','ready','processing') -or
                ($status -eq 'valid' -and (-not $hasCompletedCertificate -or -not $cacheFilesPresent))
            if ($resume) {
                Resume-ExistingAcmeOrder $order
                $recovered=Get-PACertificate -Name $name
                if ($status -eq 'valid' -and -not (Test-CachedAcmeCertificate $recovered $password $cacheDays $rotationReference)) {
                    # Complete can restore the same old certificate after a VM
                    # restore. Only a leaf confirmed to be the current Vault leaf
                    # may proceed to the ordinary new renewal below. A newer but
                    # unusable cache never falls back to another ACME order.
                    $sameCurrentLeaf=$false
                    if ($null -ne $recovered -and $recovered.Thumbprint -and $null -ne $current -and $current.cer) {
                        $currentLeaf=[Security.Cryptography.X509Certificates.X509Certificate2]::new([Convert]::FromBase64String($current.cer))
                        try {$sameCurrentLeaf=[string]$recovered.Thumbprint -ieq $currentLeaf.Thumbprint} finally {$currentLeaf.Dispose()}
                    }
                    if (-not $sameCurrentLeaf) {throw 'Recovered ACME certificate is not a usable replacement'}
                    Submit-Renewal -Name $name -PluginArgs $pluginArguments -Force *> $null
                }
            } else {
                Submit-Renewal -Name $name -PluginArgs $pluginArguments -Force *> $null
            }
        }
        $certificate=Get-PACertificate -Name $name
        if ($null -eq $certificate -or -not $certificate.PfxFullChain) {throw 'Certificate issuance was not confirmed'}
        if ($rotate -and -not (Test-CachedAcmeCertificate $certificate $password ([int](Get-Setting 'minimumValidDays' 7)) $rotationReference)) {
            throw 'Planned rotation did not produce a valid newer certificate'
        }
        $path=[IO.Path]::GetFullPath([string]$certificate.PfxFullChain)
        if (-not $path.StartsWith($env:POSHACME_HOME+[IO.Path]::DirectorySeparatorChar,[StringComparison]::Ordinal)) {throw 'Unexpected certificate file location'}
        Assert-NoReparse $path
        Import-Pfx ([IO.File]::ReadAllBytes($path)) $password
        if ($rotate) {$script:RotationOutcome='replacement-issued-and-imported'}
    } finally {
        # Preserve the account/order after failures as well. The DNS plugin's
        # normal cleanup removes only challenge values created for its order;
        # no zone-wide TXT deletion is performed by this service.
        $operationStage=$script:Stage
        Save-AcmeState
        $script:Stage=$operationStage
        $pluginArguments=$null; $secret=$null; $password=$null; $passwordSecret=$null
    }
}
<#
.SYNOPSIS
HTTP 入口を見分ける共有値を対象 Key Vault に登録します。
.DESCRIPTION
利用者には渡しません。ただし AppGW の制御設定を参照できる管理者は書換え値を読めるため、その管理権限を信頼する境界です。
.OUTPUTS
出力なし。
#>
function Set-L7IngressProof {
    if ($Action -ne 'Install' -or -not (Get-Setting 'l7IngressEnabled' $false)) {return}
    $script:Stage='l7-ingress-proof-storage'
    # Client applications never receive this credential. It authenticates the
    # L7 rewrite path to APIM; Azure readers of the AppGW configuration are a
    # trusted control-plane boundary and can see the rewrite's literal value.
    if ($L7IngressSecret -notmatch '^[a-f0-9]{128}$') {throw 'Invalid L7 ingress proof'}
    $previous=Get-VaultSecret 'core-l7-ingress-secret'
    if ($null -ne $previous -and $previous.attributes.enabled -and $previous.value -eq $L7IngressSecret) {return}
    [void](Set-VaultSecret 'core-l7-ingress-secret' $L7IngressSecret)
}
<#
.SYNOPSIS
Realtime セッションの所有者チケット用署名鍵を登録します。
.DESCRIPTION
Install かつ機能有効時だけ動作し、同じ鍵の再登録を省略します。署名鍵自体をクライアントへ返しません。
.OUTPUTS
出力なし。
#>
function Set-RealtimeTicketKey {
    if ($Action -ne 'Install' -or -not (Get-Setting 'realtimeTicketEnabled' $false)) {return}
    $script:Stage='realtime-ticket-key-storage'
    if ($RealtimeTicketKey -notmatch '^[a-f0-9]{128}$') {throw 'Invalid realtime ticket key'}
    $previous=Get-VaultSecret 'core-realtime-ticket-key'
    if ($null -ne $previous -and $previous.attributes.enabled -and $previous.value -eq $RealtimeTicketKey) {return}
    [void](Set-VaultSecret 'core-realtime-ticket-key' $RealtimeTicketKey)
}
<#
.SYNOPSIS
APIM が必要とするルート CA を含む証明書チェーンを整えます。
.DESCRIPTION
CA の fullchain は root を含まない場合があります。管理 VM 内で補完して Key Vault に再登録し、証明書本体を ARM の出力に載せません。
.OUTPUTS
出力なし。
#>
function Ensure-ApimCertificateChain {
    if (-not (Get-Setting 'l7IngressEnabled' $false)) {return}
    $script:Stage='apim-certificate-chain'
    # APIM's Key Vault certificate contract requires intermediates AND root.
    # Posh-ACME fullchain commonly omits the trust anchor. Complete the PFX
    # inside the management VM; no certificate bytes enter ARM or local state.
    $current=Get-CurrentCertificate
    if ($null -eq $current) {throw 'Public certificate is unavailable'}
    $secret=Invoke-Json ([string]$current.sid+'?api-version=7.4')
    $collection=[Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
    $chain=[Security.Cryptography.X509Certificates.X509Chain]::new()
    try {
        $flags=[Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet -bor [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable
        $collection.Import([Convert]::FromBase64String($secret.value),'',$flags)
        $leaves=@($collection | Where-Object {$_.HasPrivateKey})
        if ($leaves.Count -ne 1) {throw 'Public certificate private key is invalid'}
        $chain.ChainPolicy.RevocationMode=[Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
        $chain.ChainPolicy.VerificationFlags=[Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
        foreach ($item in $collection) {[void]$chain.ChainPolicy.ExtraStore.Add($item)}
        if (-not $chain.Build($leaves[0])) {throw 'Public certificate trust chain is incomplete'}
        $changed=$false
        foreach ($element in $chain.ChainElements) {
            $raw=[Convert]::ToBase64String($element.Certificate.RawData)
            if (@($collection | Where-Object {[Convert]::ToBase64String($_.RawData) -eq $raw}).Count -eq 0) {
                [void]$collection.Add([Security.Cryptography.X509Certificates.X509Certificate2]::new($element.Certificate.RawData))
                $changed=$true
            }
        }
        if ($changed) {
            $pfx=$collection.Export([Security.Cryptography.X509Certificates.X509ContentType]::Pkcs12,'')
            $body=@{value=[Convert]::ToBase64String($pfx);pwd='';attributes=@{enabled=$true};policy=@{secret_props=@{contentType='application/x-pkcs12'};key_props=@{exportable=$true;reuse_key=$false}}}
            $imported=Invoke-Json ('https://'+$script:Config.keyVaultName+'.vault.azure.net/certificates/'+$script:Config.certificateName+'/import?api-version=7.4') 'POST' $body
            if ($imported.cer -ne $current.cer -or -not $imported.attributes.enabled) {throw 'APIM certificate chain import was not confirmed'}
        }
    } finally {
        $chain.Dispose()
        foreach ($item in $collection) {$item.Dispose()}
        $secret=$null;$body=$null;$pfx=$null
    }
}
<#
.SYNOPSIS
Key Vault 名と証明書名から systemd unit 名を決めます。
.DESCRIPTION
証明書ごとに名前を分け、複数の証明書更新が同じ timer を上書きしないようにします。
.OUTPUTS
service / timer に共通の unit 名。
#>
function Get-RenewalUnitName {
    return 'egakumi-certificate-'+$script:Config.keyVaultName+'-'+$script:Config.certificateName
}
<#
.SYNOPSIS
証明書を継続保守する systemd service と timer の定義を作ります。
.DESCRIPTION
root と専用書込み領域に限定します。起動後と指定間隔で Renew を呼び、出力は認証系 journald に保存します。timer 自体は環境を撤去しません。
.OUTPUTS
service と timer の設定文字列を持つ hashtable。
#>
function Get-RenewalUnitContent {
    $worker=Join-Path $script:Root 'certificate.ps1'
    $publicFile=Join-Path $script:Root 'config.json'
    $interval=[int](Get-Setting 'renewIntervalHours' 12)
    $unit=Get-RenewalUnitName
    $service=@"
[Unit]
Description=Maintain public gateway certificate in Key Vault
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
User=root
Group=root
UMask=0077
SetLoginEnvironment=yes
Environment=XDG_CACHE_HOME=$($script:Root)/cache
Environment=XDG_CONFIG_HOME=$($script:Root)/config
Environment=XDG_DATA_HOME=$($script:Root)/data
ExecStart=/usr/bin/pwsh -NoLogo -NoProfile -NonInteractive -File $worker -Action Renew -ConfigFile $publicFile
TimeoutStartSec=3600
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$($script:Root)
StandardOutput=journal
StandardError=journal
SyslogFacility=authpriv
SyslogIdentifier=$unit
"@
    $timer=@"
[Unit]
Description=Check public gateway certificate renewal periodically
[Timer]
OnBootSec=5min
OnUnitActiveSec=$($interval)h
AccuracySec=1min
Unit=$unit.service
[Install]
WantedBy=timers.target
"@
    return @{service=$service;timer=$timer}
}
<#
.SYNOPSIS
保守コードと公開設定を保存し、systemd timer を登録・再読込みします。
.DESCRIPTION
更新時も同じ worker を呼びます。秘密情報は設定 JSON へ残さず、VM の MI で Key Vault から都度取得します。
.OUTPUTS
出力なし。
#>
function Register-RenewalTask {
    $script:Stage='renewal-task-installation'
    foreach ($directory in @('cache','config','data')) {New-PrivateDirectory (Join-Path $script:Root $directory)}
    $worker=Join-Path $script:Root 'certificate.ps1'
    Write-PrivateFile $worker ([IO.File]::ReadAllBytes($PSCommandPath))
    $publicFile=Join-Path $script:Root 'config.json'
    Write-PrivateFile $publicFile ([Text.Encoding]::UTF8.GetBytes(($script:Config | ConvertTo-Json -Depth 10 -Compress)))
    $unit=Get-RenewalUnitName
    $contents=Get-RenewalUnitContent
    # Unit contents are public settings. Write using a private staging tree and
    # atomically replace root-owned files; existing system directories stay 0755.
    foreach ($kind in @('service','timer')) {
        $staged=Join-Path $script:Root ($unit+'.'+$kind)
        Write-PrivateFile $staged ([Text.Encoding]::UTF8.GetBytes($contents[$kind]))
        $destination=Join-Path '/etc/systemd/system' ($unit+'.'+$kind)
        Assert-NoReparse $destination
        if (Test-Path -LiteralPath $destination) {Assert-PrivatePath $destination}
        [IO.File]::Move($staged,$destination,$true)
    }
    & /usr/bin/systemctl daemon-reload *> $null
    if ($LASTEXITCODE -ne 0) {throw 'Certificate service reload failed'}
    & /usr/bin/systemctl enable --now ($unit+'.timer') *> $null
    if ($LASTEXITCODE -ne 0) {throw 'Certificate renewal timer installation failed'}
    & /usr/bin/systemctl restart ($unit+'.timer') *> $null
    if ($LASTEXITCODE -ne 0) {throw 'Certificate renewal timer activation failed'}
}

# 入口処理：入力検証 → 専用領域の準備 → 排他 → 証明書 → 共有鍵 → 定期更新登録。
# 途中失敗なら成功扱いにせず、finally で token と秘密入力を捨てて排他を解放します。
try {
    if (-not $IsLinux -or (Get-UnixIdentity) -ne 0) {throw 'Run certificate maintenance as root on Linux'}
    if ($ReadProtectedEnvironment) {Read-ManagedInput}
    if ($Action -eq 'Renew') {
        Assert-PrivatePath $ConfigFile
        $PublicConfigJson=[IO.File]::ReadAllText($ConfigFile)
    }
    $script:Config=$PublicConfigJson | ConvertFrom-Json
    Assert-SupportedCertificateProvider
    [void](Get-PlannedRotationThumbprint)
    if ($script:Config.keyVaultName -notmatch '^[a-zA-Z][a-zA-Z0-9-]{1,22}[a-zA-Z0-9]$' -or $script:Config.certificateName -notmatch '^[a-zA-Z0-9-]{1,80}$') {throw 'Invalid public certificate configuration'}
    $domains=@(Get-CertificateHostnames 'hostnames')
    foreach ($required in @(Get-CertificateHostnames 'requiredHostnames')) {
        $covered=$false
        foreach ($pattern in $domains) {if (Test-CertificateHostnameCoverage $pattern $required) {$covered=$true;break}}
        if (-not $covered) {throw 'Certificate domains do not cover all public hostnames'}
    }
    if ([int](Get-Setting 'renewBeforeDays' 30) -lt 1 -or [int](Get-Setting 'renewBeforeDays' 30) -gt 60 -or [int](Get-Setting 'renewIntervalHours' 12) -lt 1 -or [int](Get-Setting 'renewIntervalHours' 12) -gt 24) {throw 'Invalid certificate renewal interval'}
    if ([int](Get-Setting 'minimumValidDays' 7) -lt 1 -or [int](Get-Setting 'minimumValidDays' 7) -gt [int](Get-Setting 'renewBeforeDays' 30) -or (Get-Setting 'moduleVersion' '4.34.0') -ne $script:ModuleVersion) {throw 'Invalid certificate validation settings'}
    if ($script:Config.directoryUrl -notin @('https://acme-v02.api.letsencrypt.org/directory','https://acme-staging-v02.api.letsencrypt.org/directory')) {throw 'Unsupported automated ACME configuration'}
    if ($script:Config.accountEmail -notmatch '^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$' -or [int](Get-Setting 'dnsSleepSeconds' 60) -lt 0 -or [int](Get-Setting 'validationTimeoutSeconds' 120) -lt 1) {throw 'Invalid DNS validation settings'}
    New-PrivateDirectory '/var/lib/egakumi-certificates'
    $script:Root=Join-Path '/var/lib/egakumi-certificates' ($script:Config.keyVaultName+'-'+$script:Config.certificateName)
    New-PrivateDirectory $script:Root
    $previousConfig=Join-Path $script:Root 'config.json'
    if (Test-Path -LiteralPath $previousConfig) {
        Assert-PrivatePath $previousConfig
        $previous=[IO.File]::ReadAllText($previousConfig) | ConvertFrom-Json
        Assert-PreviousCertificateHostname $previous
    }
    $script:LockStream=Enter-MaintenanceLock
    Ensure-Certificate
    Ensure-ApimCertificateChain
    Set-L7IngressProof
    Set-RealtimeTicketKey
    if ($Action -eq 'Install') {Register-RenewalTask}
    Write-MaintenanceEvent $true $script:Stage
    exit 0
} catch {
    # Never echo exception bodies, protected input, plugin output, PFX data or
    # HTTP request headers to Managed Run Command status or journald.
    Write-MaintenanceEvent $false $script:Stage
    exit 1
} finally {
    $DnsPluginArgumentsJson=$null; $L7IngressSecret=$null; $RealtimeTicketKey=$null; $RotateFromThumbprint=''; $script:Token=$null
    if ($null -ne $script:LockStream) {$script:LockStream.Dispose()}
}
