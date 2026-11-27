"""Verify the daily Application Insights cap with synthetic PowerShell modules."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.support.powershell import PWSH


@unittest.skipUnless(PWSH, "PowerShell is required for the Application Insights cap")
class ApplicationInsightsCapTests(unittest.TestCase):
    def test_validates_identity_applies_cap_and_confirms_readback(self):
        pwsh = Path(PWSH).resolve()
        script = Path(__file__).resolve().parents[2] / "scripts/deployment/helpers/set-app-insights-cap.ps1"
        base_account = r'''
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
        insights = r'''
        function Set-AzApplicationInsightsDailyCap {
         [CmdletBinding(SupportsShouldProcess)] param([string]$ResourceGroupName,[string]$Name,[string]$SubscriptionId,[double]$DailyCapGB)
         if ($env:MOCK_FAIL_SET -eq '1') { throw 'SENTINEL_SECRET_ABC in config error' }
         if ($ResourceGroupName -ne 'rg-egakumi-test-monitor' -or $Name -ne 'appi-egakumi-test' -or $DailyCapGB -ne 100) { throw 'Wrong target' }
        }
        Export-ModuleMember -Function *
        '''
        harness = r'''
        param([string]$Subject, [string]$ModuleDirectory, [switch]$CheckDependencies)
        function Import-Module {
            [CmdletBinding()]
            param([string]$Name)
            if ($Name -cnotin @('Az.Accounts', 'Az.ApplicationInsights')) { throw 'Unexpected module import' }
            $mock = Join-Path $ModuleDirectory "$Name/$Name.psm1"
            Microsoft.PowerShell.Core\Import-Module -Name $mock -Global -ErrorAction Stop
        }
        & $Subject -CheckDependencies:$CheckDependencies
        exit $LASTEXITCODE
        '''
        with tempfile.TemporaryDirectory(prefix="core-cap-offline-") as temporary:
            root = Path(temporary)
            runner = root / "cap-fixture.ps1"
            runner.write_text(harness, encoding="utf-8")
            token_types = {"string": "[string]", "secure": "[Security.SecureString]", "unsupported": "[int]"}
            for kind, token_type in token_types.items():
                modules = root / ("mock-" + kind)
                for name, source in (
                    ("Az.Accounts", base_account.replace("__TOKEN_TYPE__", token_type)),
                    ("Az.ApplicationInsights", insights),
                ):
                    folder = modules / name
                    folder.mkdir(parents=True)
                    (folder / (name + ".psm1")).write_text(source, encoding="utf-8")

            request = {
                "tenantId": "11111111-1111-1111-1111-111111111111",
                "subscriptionId": "22222222-2222-2222-2222-222222222222",
                "accountId": "operator@example.invalid",
                "accessToken": "SENTINEL_SECRET_ABC",
                "resourceGroupName": "rg-egakumi-test-monitor",
                "appInsightsName": "appi-egakumi-test",
                "dailyCapGb": 100,
            }
            cases = (
                ("dependencies-ready", "string", {}, None, ["-CheckDependencies"], 0, "ready", None),
                ("string-token-success", "string", {}, request, [], 0, "configured", None),
                ("secure-token-success", "secure", {}, request, [], 0, "configured", None),
                ("existing-context-success", "string", {}, dict(request, useExistingContext=True, accessToken=None), [], 0, "configured", None),
                ("auth-error-redacted", "string", {"MOCK_FAIL_AUTH": "1"}, request, [], 1, "failed", "authentication"),
                ("wrong-context", "string", {"MOCK_CONTEXT_MISMATCH": "1"}, request, [], 1, "failed", "authentication"),
                ("set-error-redacted", "string", {"MOCK_FAIL_SET": "1"}, request, [], 1, "failed", "configure"),
                ("cap-mismatch", "string", {"MOCK_CAP_MISMATCH": "1"}, request, [], 1, "failed", "readback"),
                ("missing-cap", "string", {"MOCK_MISSING_CAP": "1"}, request, [], 1, "failed", "readback"),
                ("bad-http-status", "string", {"MOCK_BAD_STATUS": "1"}, request, [], 1, "failed", "readback"),
                ("invalid-json-redacted", "string", {}, "{SENTINEL_SECRET_ABC", [], 1, "failed", "input"),
                ("missing-token", "string", {}, dict(request, accessToken=None), [], 1, "failed", "authentication"),
                ("invalid-cap", "string", {}, dict(request, dailyCapGb=0), [], 1, "failed", "input"),
                ("unsupported-token-type", "unsupported", {}, None, ["-CheckDependencies"], 1, "failed", "dependencies"),
            )
            for name, kind, overrides, payload, arguments, exit_code, status, stage in cases:
                with self.subTest(case=name):
                    environment = {
                        **os.environ,
                        "POWERSHELL_TELEMETRY_OPTOUT": "1",
                        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                        "XDG_CACHE_HOME": str(root / "cache"),
                        **overrides,
                    }
                    stdin = json.dumps(payload) if isinstance(payload, dict) else payload or ""
                    result = subprocess.run(
                        [str(pwsh), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(runner),
                         "-Subject", str(script), "-ModuleDirectory", str(root / ("mock-" + kind)), *arguments],
                        input=stdin, capture_output=True, text=True, env=environment, check=False, timeout=20,
                    )
                    self.assertNotIn(request["accessToken"], result.stdout + result.stderr)
                    self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
                    response = json.loads(result.stdout)
                    self.assertEqual(response["status"], status)
                    if stage is not None:
                        self.assertEqual(response["stage"], stage)
