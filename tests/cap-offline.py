"""Application Insightsの日次上限設定を、偽のAzモジュールで検証する。

実Azureの認証や設定変更をせず、文字列tokenとSecureStringの互換性、対象Subscriptionの照合、エラーに秘密値を含めないことを確かめる。
実行は core の tests を unittest で読み込む。cap-offline.py は単独実行の補助である。
"""
from pathlib import Path
import os, json, subprocess, shutil, tempfile


def main():
    """一時環境で全ケースを実行し、失敗や秘密の出力を検出したら終了する。"""
    executable = os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
    if not executable:
        raise SystemExit("PowerShell 7.2+ is required for this offline test")
    pwsh = Path(executable).resolve()
    script = Path(__file__).resolve().parents[1] / "scripts/deployment/helpers/set-app-insights-cap.ps1"
    with tempfile.TemporaryDirectory(prefix="core-cap-offline-") as temporary:
        root = Path(temporary)
        base_account=r'''
        function Disable-AzContextAutosave { [CmdletBinding()] param([string]$Scope) }
        function Disconnect-AzAccount { [CmdletBinding()] param([string]$Scope) }
        function Connect-AzAccount {
         [CmdletBinding()] param(__TOKEN_TYPE__$AccessToken,[string]$AccountId,[string]$Tenant,[string]$Subscription,[string]$Scope,[switch]$SkipContextPopulation)
         if ($env:MOCK_FAIL_AUTH -eq '1') { throw 'SENTINEL_SECRET_ABC in auth error' }
         if ($AccessToken -is [Security.SecureString]) { $got=[System.Net.NetworkCredential]::new('', $AccessToken).Password } else { $got=[string]$AccessToken }
         if ($got -ne 'SENTINEL_SECRET_ABC') { throw 'Unexpected token' }
         if ($Scope -ne 'Process' -or -not $SkipContextPopulation) { throw 'Context isolation missing' }
         $global:CapMockContext=[pscustomobject]@{ Tenant=[pscustomobject]@{Id=$Tenant};Subscription=[pscustomobject]@{Id=$Subscription};Account=[pscustomobject]@{Id=$AccountId} }
        }
        function Get-AzContext {
         [CmdletBinding()] param()
         if ($env:MOCK_CONTEXT_MISMATCH -eq '1') { return [pscustomobject]@{Tenant=[pscustomobject]@{Id='00000000-0000-0000-0000-000000000001'};Subscription=[pscustomobject]@{Id='00000000-0000-0000-0000-000000000002'}} }
         if (Get-Variable CapMockContext -Scope Global -ErrorAction SilentlyContinue) { return $global:CapMockContext }
         return [pscustomobject]@{Tenant=[pscustomobject]@{Id='11111111-1111-1111-1111-111111111111'};Subscription=[pscustomobject]@{Id='22222222-2222-2222-2222-222222222222'};Account=[pscustomobject]@{Id='operator@example.invalid'}}
        }
        function Invoke-AzRestMethod {
         [CmdletBinding()] param([string]$Method,[string]$Path)
         if ($Method -ne 'GET' -or $Path -notlike '*/components/appi-egakumi-test/currentbillingfeatures?api-version=2015-05-01') { throw 'Readback path/method mismatch' }
         if ($env:MOCK_BAD_STATUS -eq '1') { return [pscustomobject]@{StatusCode=500;Content='SENTINEL_SECRET_ABC'} }
         if ($env:MOCK_MISSING_CAP -eq '1') { return [pscustomobject]@{StatusCode=200;Content='{}'} }
         $cap=if ($env:MOCK_CAP_MISMATCH -eq '1') {99} else {100}
         return [pscustomobject]@{StatusCode=200;Content=(@{DataVolumeCap=@{Cap=$cap}}|ConvertTo-Json -Compress)}
        }
        Export-ModuleMember -Function *
        '''
        ai=r'''
        function Set-AzApplicationInsightsDailyCap {
         [CmdletBinding(SupportsShouldProcess)] param([string]$ResourceGroupName,[string]$Name,[string]$SubscriptionId,[double]$DailyCapGB)
         if ($env:MOCK_FAIL_SET -eq '1') { throw 'SENTINEL_SECRET_ABC in config error' }
         if ($ResourceGroupName -ne 'rg-egakumi-test-monitor' -or $Name -ne 'appi-egakumi-test' -or $DailyCapGB -ne 100) { throw 'Wrong target' }
        }
        Export-ModuleMember -Function *
        '''
        for kind,typ in [('string','[string]'),('secure','[Security.SecureString]'),('unsupported','[int]')]:
         modules=root/('mock-'+kind)
         for name,source in [('Az.Accounts',base_account.replace('__TOKEN_TYPE__',typ)),('Az.ApplicationInsights',ai)]:
          folder=modules/name
          folder.mkdir(parents=True,exist_ok=True)
          (folder/(name+'.psm1')).write_text(source)
        base={'tenantId':'11111111-1111-1111-1111-111111111111','subscriptionId':'22222222-2222-2222-2222-222222222222','accountId':'operator@example.invalid','accessToken':'SENTINEL_SECRET_ABC','resourceGroupName':'rg-egakumi-test-monitor','appInsightsName':'appi-egakumi-test','dailyCapGb':100}
        cases=[
         ('dependencies-ready','string',{},None,['-CheckDependencies'],0,'ready',None),
         ('string-token-success','string',{},base,[],0,'configured',None),
         ('secure-token-success','secure',{},base,[],0,'configured',None),
         ('existing-context-success','string',{},dict(base,useExistingContext=True,accessToken=None),[],0,'configured',None),
         ('auth-error-redacted','string',{'MOCK_FAIL_AUTH':'1'},base,[],1,'failed','authentication'),
         ('wrong-context','string',{'MOCK_CONTEXT_MISMATCH':'1'},base,[],1,'failed','authentication'),
         ('set-error-redacted','string',{'MOCK_FAIL_SET':'1'},base,[],1,'failed','configure'),
         ('cap-mismatch','string',{'MOCK_CAP_MISMATCH':'1'},base,[],1,'failed','readback'),
         ('missing-cap','string',{'MOCK_MISSING_CAP':'1'},base,[],1,'failed','readback'),
         ('bad-http-status','string',{'MOCK_BAD_STATUS':'1'},base,[],1,'failed','readback'),
         ('invalid-json-redacted','string',{},'{SENTINEL_SECRET_ABC',[],1,'failed','input'),
         ('missing-token','string',{},dict(base,accessToken=None),[],1,'failed','authentication'),
         ('invalid-cap','string',{},dict(base,dailyCapGb=0),[],1,'failed','input'),
         ('unsupported-token-type','unsupported',{},None,['-CheckDependencies'],1,'failed','dependencies'),
        ]
        results=[]
        for name,kind,overrides,payload,args,code,status,stage in cases:
         env=dict(os.environ,PSModulePath=str(root/('mock-'+kind))+os.pathsep+str(pwsh.parent/'Modules'),POWERSHELL_TELEMETRY_OPTOUT='1',DOTNET_CLI_TELEMETRY_OPTOUT='1',XDG_CACHE_HOME=str(root/'cache'),**overrides)
         stdin=payload if isinstance(payload,str) else json.dumps(payload) if payload is not None else ''
         proc=subprocess.run([str(pwsh),'-NoLogo','-NoProfile','-NonInteractive','-File',str(script),*args],input=stdin,text=True,capture_output=True,env=env,timeout=20)
         assert 'SENTINEL_SECRET_ABC' not in proc.stdout+proc.stderr,(name,'credential leak')
         assert proc.returncode==code,(name,proc.returncode,proc.stdout,proc.stderr)
         value=json.loads(proc.stdout)
         assert value['status']==status,(name,value)
         if stage is not None: assert value['stage']==stage,(name,value)
         results.append({'name':name,'passed':True})
        print(json.dumps({'azureAccessPerformed':False,'caseCount':len(results),'results':results},indent=2))


if __name__ == "__main__":
    main()
