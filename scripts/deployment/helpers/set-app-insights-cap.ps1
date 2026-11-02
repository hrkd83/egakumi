#Requires -Version 7.2
<#
.SYNOPSIS
Application Insightsの日次取込上限を設定し、別の読取操作で反映を確認する。

.DESCRIPTION
azdの構築処理から呼ばれる補助スクリプト。対象を間違えないよう、テナントと
サブスクリプションを照合してから、指定されたApplication Insightsだけを変更する。
設定要求が成功しただけでは完了とせず、ARMのGETで実際の日次上限を読み直す。
設定操作はAzureを変更するが、追加リソースの作成やモデル推論は行わない。
日次上限は取込量の設定であり、基盤全体の課金額を保証する予算上限ではない。

入力JSONは標準入力で受け取る。トークンをコマンド引数へ含めるとプロセス一覧に
現れ得るため、accessTokenは引数にしない。認証コンテキストをディスクへ自動保存せず、
作成した接続はこのプロセス内だけで使い、最後に解放する。

.PARAMETER CheckDependencies
必要なモジュールと公開コマンドの有無・引数型だけをローカルで確認する。
このモードでは認証、Azureへの通信、モジュールのインストールを行わない。

.INPUTS
標準入力のJSONオブジェクト:
tenantId, subscriptionId, accountId, accessToken, resourceGroupName,
appInsightsName, dailyCapGb。
useExistingContext=trueの場合はaccessTokenの代わりに現在の接続を使うが、
その場合もテナントとサブスクリプションの一致を必ず確認する。

.OUTPUTS
設定前後の日次上限と検査結果をJSONで返す。失敗時は処理段階と固定コードだけを
返し、認証情報を含み得る例外本文や入力JSONは表示しない。

.EXAMPLE
pwsh -NoLogo -NoProfile -File ./set-app-insights-cap.ps1 -CheckDependencies
有料資源の構築前に、手元のPowerShell依存関係だけを確認する。

.NOTES
設定には公開コマンドSet-AzApplicationInsightsDailyCapを使用する。
対応する公開Getコマンドはないため、Invoke-AzRestMethodで独立して再読取する。
https://learn.microsoft.com/powershell/module/az.applicationinsights/set-azapplicationinsightsdailycap
https://learn.microsoft.com/powershell/module/az.accounts/connect-azaccount
https://github.com/Azure/azure-rest-api-specs/blob/main/specification/applicationinsights/resource-manager/Microsoft.Insights/ApplicationInsights/stable/2015-05-01/examples/CurrentBillingFeaturesGet.json
#>
[CmdletBinding()]
param([switch] $CheckDependencies)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$VerbosePreference = 'SilentlyContinue'
$DebugPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$stage = 'dependencies'
$request = $null
$rawRequest = $null
$tokenParameter = $null
$secureToken = $null
$connectedFromToken = $false
$exitCode = 1
$requiredDependency = 'Az.Accounts'

try {
    # 先に全依存関係を調べる。構築途中でモジュール不足に気付き、資源を保持したまま
    # 待つ状況を避けるため、同じ検査を-CheckDependenciesでも実行できる。
    Import-Module Az.Accounts -ErrorAction Stop
    $requiredDependency = 'Az.ApplicationInsights'
    Import-Module Az.ApplicationInsights -ErrorAction Stop
    $commandNames = @(
        'Connect-AzAccount', 'Disable-AzContextAutosave', 'Get-AzContext',
        'Disconnect-AzAccount', 'Invoke-AzRestMethod',
        'Set-AzApplicationInsightsDailyCap'
    )
    foreach ($commandName in $commandNames) {
        $requiredDependency = $commandName
        $null = Get-Command $commandName -ErrorAction Stop
    }
    $requiredDependency = 'Connect-AzAccount.AccessToken'
    $connectCommand = Get-Command Connect-AzAccount -ErrorAction Stop
    $tokenType = $connectCommand.Parameters['AccessToken'].ParameterType
    if ($tokenType -notin @([string], [System.Security.SecureString])) {
        throw 'UnsupportedAccessTokenParameterType'
    }
    $requiredDependency = 'Connect-AzAccount.Scope'
    if (-not $connectCommand.Parameters.ContainsKey('Scope')) {
        throw 'ProcessScopedAuthenticationUnavailable'
    }
    $setCommand = Get-Command Set-AzApplicationInsightsDailyCap -ErrorAction Stop
    foreach ($parameterName in @('ResourceGroupName', 'Name', 'SubscriptionId', 'DailyCapGB')) {
        $requiredDependency = "Set-AzApplicationInsightsDailyCap.$parameterName"
        if (-not $setCommand.Parameters.ContainsKey($parameterName)) {
            throw 'RequiredDailyCapParameterUnavailable'
        }
    }

    if ($CheckDependencies) {
        [ordered]@{
            status = 'ready'
            powershellVersion = $PSVersionTable.PSVersion.ToString()
            azAccountsVersion = (Get-Module Az.Accounts).Version.ToString()
            azApplicationInsightsVersion = (Get-Module Az.ApplicationInsights).Version.ToString()
            accessTokenParameterType = $tokenType.FullName
            azureAccessPerformed = $false
        } | ConvertTo-Json -Compress
        $exitCode = 0
    }
    else {
        # 秘密を含むJSONを標準入力から受け、空入力・対象ID・日次上限の形式を検査する。
        # 生の入力文字列は解析後に参照を外し、出力に転載しない。
        $stage = 'input'
        $rawRequest = [Console]::In.ReadToEnd()
        if ([string]::IsNullOrWhiteSpace($rawRequest)) { throw 'MissingStdinJson' }
        $request = $rawRequest | ConvertFrom-Json -AsHashtable -ErrorAction Stop
        $rawRequest = $null
        foreach ($field in @('tenantId', 'subscriptionId', 'accountId', 'resourceGroupName', 'appInsightsName', 'dailyCapGb')) {
            if (-not $request.ContainsKey($field) -or [string]::IsNullOrWhiteSpace([string]$request[$field])) {
                throw 'MissingRequiredInput'
            }
        }
        $tenantId = [Guid]::Parse([string]$request.tenantId).ToString()
        $subscriptionId = [Guid]::Parse([string]$request.subscriptionId).ToString()
        $dailyCapGb = [double]$request.dailyCapGb
        if ([double]::IsNaN($dailyCapGb) -or [double]::IsInfinity($dailyCapGb) -or $dailyCapGb -le 0) {
            throw 'InvalidDailyCap'
        }
        $useExistingContext = $request.ContainsKey('useExistingContext') -and $request.useExistingContext -eq $true

        $stage = 'authentication'
        # 認証はプロセス内だけに限定する。利用者の永続的なAzure接続設定を変更しない。
        $null = Disable-AzContextAutosave -Scope Process -ErrorAction Stop
        if (-not $useExistingContext) {
            if (-not $request.ContainsKey('accessToken') -or [string]::IsNullOrWhiteSpace([string]$request.accessToken)) {
                throw 'MissingAccessToken'
            }
            if ($tokenType -eq [System.Security.SecureString]) {
                $secureToken = ConvertTo-SecureString -String ([string]$request.accessToken) -AsPlainText -Force
                $tokenParameter = $secureToken
            }
            else {
                # モジュールの版によってAccessTokenの引数型が異なるため、検査済みの型に
                # 合わせる。文字列を使う版でも、トークンはこのプロセス内にだけ保持する。
                $tokenParameter = [string]$request.accessToken
            }
            $null = Connect-AzAccount -AccessToken $tokenParameter `
                -AccountId ([string]$request.accountId) -Tenant $tenantId `
                -Subscription $subscriptionId -Scope Process `
                -SkipContextPopulation -ErrorAction Stop
            $connectedFromToken = $true
            $request.accessToken = $null
            $tokenParameter = $null
        }
        $context = Get-AzContext -ErrorAction Stop
        if ($null -eq $context -or $context.Tenant.Id -ne $tenantId -or $context.Subscription.Id -ne $subscriptionId) {
            throw 'TenantOrSubscriptionMismatch'
        }

        $stage = 'configure'
        # 変更対象は上で照合したサブスク・RG・Application Insightsの1件だけ。
        $null = Set-AzApplicationInsightsDailyCap `
            -ResourceGroupName ([string]$request.resourceGroupName) `
            -Name ([string]$request.appInsightsName) -SubscriptionId $subscriptionId `
            -DailyCapGB $dailyCapGb -Confirm:$false -ErrorAction Stop

        $stage = 'readback'
        # 設定APIの受付結果とは別に実値を読む。名前はURLのパス要素としてエスケープし、
        # 設定要求が成功していても再読取値が違えば合格として報告しない。
        $resourceGroupSegment = [Uri]::EscapeDataString([string]$request.resourceGroupName)
        $componentSegment = [Uri]::EscapeDataString([string]$request.appInsightsName)
        $resourceId = "/subscriptions/$subscriptionId/resourceGroups/$resourceGroupSegment/providers/Microsoft.Insights/components/$componentSegment"
        $path = "${resourceId}/currentbillingfeatures?api-version=2015-05-01"
        $readback = Invoke-AzRestMethod -Method GET -Path $path -ErrorAction Stop
        if ([int]$readback.StatusCode -ne 200) { throw 'ReadbackFailed' }
        $billing = $readback.Content | ConvertFrom-Json -AsHashtable -ErrorAction Stop
        if (-not $billing.ContainsKey('DataVolumeCap') -or -not $billing.DataVolumeCap.ContainsKey('Cap')) {
            throw 'ReadbackCapMissing'
        }
        $observedCapGb = [double]$billing.DataVolumeCap.Cap
        if ([Math]::Abs($observedCapGb - $dailyCapGb) -gt 0.000001) { throw 'ReadbackCapMismatch' }

        [ordered]@{
            status = 'configured'
            resourceId = $resourceId
            expectedDailyCapGb = $dailyCapGb
            observedDailyCapGb = $observedCapGb
            verified = $true
            checkedAt = [DateTime]::UtcNow.ToString('o')
        } | ConvertTo-Json -Compress
        $exitCode = 0
    }
}
catch {
    # ErrorRecordやHTTP例外にはトークンや標準入力の一部が入る可能性がある。
    # そのままJSON化せず、段階と固定コードだけを返す。
    $failure = [ordered]@{ status = 'failed'; stage = $stage; code = 'ApplicationInsightsDailyCapFailed' }
    if ($stage -eq 'dependencies') { $failure.requiredDependency = $requiredDependency }
    $failure | ConvertTo-Json -Compress
}
finally {
    # 成否にかかわらず秘密への参照とSecureStringを解放し、今回だけの接続を閉じる。
    if ($null -ne $request -and $request.ContainsKey('accessToken')) { $request.accessToken = $null }
    $rawRequest = $null
    $tokenParameter = $null
    if ($null -ne $secureToken) { $secureToken.Dispose() }
    if ($connectedFromToken) {
        $null = Disconnect-AzAccount -Scope Process -ErrorAction SilentlyContinue
    }
}
exit $exitCode
