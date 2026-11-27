"""Exercise the active public TLS certificate worker offline."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


CORE = Path(__file__).resolve().parents[1]


class CertificateWorkerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required for worker entry validation")
    def test_worker_entry_rejects_unsupported_providers_before_state_or_secret_access(self):
        """実入口のOS条件だけを代替し、未対応方式が副作用へ届かないことを確認する。"""
        source = (CORE / "infra/guest/certificate.ps1").read_text()
        platform_gate = "if (-not $IsLinux -or (Get-UnixIdentity) -ne 0) {throw 'Run certificate maintenance as root on Linux'}"
        self.assertEqual(source.count(platform_gate), 1)
        source = source.replace(platform_gate, "# Controlled offline platform gate")
        entry = "# 入口処理：入力検証 → 専用領域の準備 → 排他 → 証明書 → 共有鍵 → 定期更新登録。"
        self.assertEqual(source.count(entry), 1)
        source = source.replace(entry, """
function New-PrivateDirectory { Write-Output 'UNEXPECTED_STATE_ACCESS'; throw 'Controlled side-effect boundary' }
function Invoke-Json { Write-Output 'UNEXPECTED_SECRET_ACCESS'; throw 'Controlled side-effect boundary' }
""" + entry)
        configuration = {
            "mode": "dnsPlugin", "dnsPlugin": "Azure", "hostname": "gateway.example.invalid",
            "hostnames": ["gateway.example.invalid"], "requiredHostnames": ["gateway.example.invalid"],
            "keyVaultName": "kv-fixture-tls", "certificateName": "tls-public",
            "accountEmail": "operator@example.invalid",
            "directoryUrl": "https://acme-v02.api.letsencrypt.org/directory",
        }
        with tempfile.TemporaryDirectory() as temporary:
            worker = Path(temporary) / "worker.ps1"
            worker.write_text(source)
            for mode, plugin in (("dnsPlugin", "Cloudflare"),
                                 ("dnsPlugin", "Route53"), ("dnsPlugin", "Manual"),
                                 ("dnsPlugin", "Other"), ("dnsPlugin", "azure"),
                                 ("other", "Azure"), ("dnsPlugin", "Azure")):
                with self.subTest(mode=mode, plugin=plugin):
                    selected = {**configuration, "mode": mode, "dnsPlugin": plugin}
                    result = subprocess.run(
                        [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive",
                         "-File", str(worker), "-PublicConfigJson", json.dumps(selected)],
                        capture_output=True, text=True, check=False, timeout=20,
                    )
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertNotIn("UNEXPECTED_SECRET_ACCESS", result.stdout + result.stderr)
                    self.assertEqual("UNEXPECTED_STATE_ACCESS" in result.stdout,
                                     mode == "dnsPlugin" and plugin == "Azure")
                    if plugin != "Azure" or mode != "dnsPlugin":
                        self.assertIn("failed at configuration", result.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required for the module environment fixture")
    def test_pinned_module_import_keeps_os_home_and_rejects_missing_home(self):
        """実際の Ensure 関数を使い、モジュール・ネットワークだけを偽物へ限定する。"""
        fixture_source = r'''param([string]$SourcePath,[string]$FixtureRoot,[switch]$MissingHome)
$ErrorActionPreference='Stop'
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($SourcePath,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw 'Worker parse failed'}
$definition=@($ast.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq 'Ensure-PoshAcme'})
if($definition.Count -ne 1){throw 'Pinned module function missing'}
. ([scriptblock]::Create($definition[0].Extent.Text))
$script:Root=$FixtureRoot;$script:ModuleVersion='4.34.0';$Action='Renew'
$script:Imports=0;$beforeHome=$env:HOME;$beforeModulePath=$env:PSModulePath
function Get-Module {param([switch]$ListAvailable,[string]$Name);if($Name -ne 'Posh-ACME'){throw 'Unexpected module'};[pscustomobject]@{Version=[version]'4.34.0'}}
function Install-Module {throw 'Unexpected module installation'}
function New-PrivateDirectory([string]$Path){if($Path -cne (Join-Path $script:Root 'acme')){throw 'Unexpected ACME state path'}}
function Import-Module {param([string]$Name,[version]$RequiredVersion,[switch]$Force,[string]$ErrorAction);if($Name -ne 'Posh-ACME' -or $RequiredVersion -ne [version]'4.34.0' -or $env:HOME -cne $beforeHome){throw 'Module import environment changed'};$script:Imports++}
$blocked=$false
try {Ensure-PoshAcme} catch {if($_.Exception.Message -cne 'Certificate module requires the OS login environment'){throw};$blocked=$true}
if($MissingHome){if(-not $blocked -or $script:Imports -ne 0){throw 'Missing OS HOME did not close module import'}}
else {if($blocked -or $script:Imports -ne 1 -or $env:POSHACME_HOME -cne (Join-Path $script:Root 'acme')){throw 'Module import contract failed'}}
if($env:HOME -cne $beforeHome -or $env:PSModulePath -cne $beforeModulePath){throw 'Common environment was changed'}
'OS_LOGIN_MODULE_FIXTURE_PASS'
'''
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            script = fixture / "module-fixture.ps1"
            script.write_text(fixture_source)
            for missing in (False, True):
                environment = dict(os.environ)
                if missing:
                    environment.pop("HOME", None)
                command = [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script), "-SourcePath", str(CORE / "infra/guest/certificate.ps1"), "-FixtureRoot", str(fixture / "private-acme-state")]
                if missing:
                    command.append("-MissingHome")
                result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=20, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.strip(), "OS_LOGIN_MODULE_FIXTURE_PASS")
