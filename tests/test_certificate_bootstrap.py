"""Ubuntu用の証明書初期化shellを、偽の外部コマンドで実行検証する。

秘密の入力がaptやcurlへ渡らないこと、ファイル権限、導入失敗時の終了と一時入力の回収を確認する。パッケージの実インストールや認証局への発行要求は行わない。
"""
import base64
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest


CORE = Path(__file__).resolve().parents[1]
INPUTS = {
    "Action": "Install",
    "PublicConfigJson": '{"hostname":"fixture.invalid"}',
    "DnsPluginArgumentsJson": '{"CFToken":{"value":"private DNS fixture $(touch SHOULD_NOT_EXIST)\nwith trailing line\n"}}',
    "L7IngressSecret": "a" * 128,
    "RealtimeTicketKey": "b" * 128,
}


STUB = r'''
import base64, json, os, pathlib, pwd, shutil, stat, subprocess, sys, tempfile
name = pathlib.Path(sys.argv[0]).name
arguments = sys.argv[1:]
fixture = pathlib.Path(os.environ["BOOTSTRAP_FIXTURE"])
expected = json.loads((fixture / "expected.json").read_text())
worker = name == "pwsh" and "-File" in arguments
present = {key: os.environ[key] for key in expected if key in os.environ}
if worker:
    assert present == expected, "Certificate worker did not get exact protected inputs"
    work = pathlib.Path(arguments[arguments.index("-File") + 1]).parent
    assert stat.S_IMODE(work.stat().st_mode) == 0o700, "Temporary directory mode is not 0700"
    for key, value in expected.items():
        private_file = work / ("input." + key)
        assert stat.S_IMODE(private_file.stat().st_mode) == 0o600, "Temporary input mode is not 0600"
        assert private_file.read_bytes() == value.encode(), "Protected input changed in temporary storage"
    assert (work / "certificate.ps1").read_text() == "# harmless worker fixture\n"
else:
    assert not present, "Dependency inherited protected environment variable names"
for value in expected.values():
    assert all(value not in argument for argument in arguments), "Private input leaked into argv"
record = {"command": name, "worker": worker, "input_names": sorted(present), "arguments": arguments}
if worker:
    assert os.environ["HOME"] == pwd.getpwuid(os.getuid()).pw_dir, "Worker HOME is not the OS account home"
    assert os.environ["HOME"] != str(work), "Private work path was used as HOME"
    record["os_login_home"] = True
if name == "install" and arguments[-1].endswith("egakumi-azure-cli.sources"):
    record["repository"] = pathlib.Path(arguments[-2]).read_text()
with (fixture / "trace.jsonl").open("a") as stream:
    stream.write(json.dumps(record) + "\n")
if name == "systemd":
    assert arguments == ["--version"]
    print("systemd " + ("254" if os.environ.get("BOOTSTRAP_FAIL_STAGE") == "systemd-version" else "255"))
elif name == "systemctl":
    if arguments[:3] == ["show", "--property=LoadState", "--value"]:
        print("loaded" if os.environ.get("BOOTSTRAP_FAIL_STAGE") == "unit-collision" else "not-found")
        if os.environ.get("BOOTSTRAP_FAIL_STAGE") != "unit-collision":
            sys.exit(1) # real systemctl show returns nonzero for an absent unit
    else:
        assert arguments[:1] == ["stop"] and len(arguments) == 2
        assert arguments[1].startswith("egakumi-certificate-install-")
elif name == "systemd-run":
    for required in ("--system", "--quiet", "--wait", "--pipe", "--collect", "--service-type=exec", "--uid=root", "--gid=root", "--property=SetLoginEnvironment=yes", "--property=UMask=0077", "--property=RuntimeMaxSec=3300", "--property=TimeoutStopSec=30", "--expand-environment=no"):
        assert required in arguments, "Root login/timeout/collection contract missing"
    assert not any(argument.startswith(("--setenv", "--property=Environment", "--property=PassEnvironment")) for argument in arguments)
    if os.environ.get("BOOTSTRAP_FAIL_STAGE") == "systemd-start":
        sys.exit(45)
    command = arguments[arguments.index("/bin/bash"):]
    launcher = pathlib.Path(command[1])
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o600
    assert launcher.name == "launch-worker.sh" and launcher.parent == pathlib.Path(command[2])
    # Model the service manager's clean account environment. The OS account's
    # home is used, with no certificate-specific HOME or protected unit env.
    environment = {key: os.environ[key] for key in ("PATH", "BOOTSTRAP_FIXTURE", "BOOTSTRAP_FAIL_STAGE")}
    environment["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    sys.exit(subprocess.run(command, env=environment, check=False).returncode)
elif name == "mktemp":
    print(tempfile.mkdtemp(prefix="egakumi-certificate.", dir=fixture / "run"))
elif name == "chmod":
    os.chmod(arguments[1], int(arguments[0], 8))
elif name == "dpkg":
    if arguments == ["--print-architecture"]:
        print("amd64")
elif name == "apt-get":
    if os.environ.get("BOOTSTRAP_FAIL_STAGE") == "install":
        sys.exit(42)
    if any(argument.startswith("azure-cli=") for argument in arguments):
        if os.environ.get("BOOTSTRAP_FAIL_STAGE") == "azure-cli-install":
            sys.exit(44)
        (fixture / "cli-installed").write_text("2.90.0")
    if any(argument.startswith("powershell=") for argument in arguments):
        (fixture / "installed").write_text("7.6.7")
elif name == "curl":
    pathlib.Path(arguments[arguments.index("-o") + 1]).write_bytes(b"harmless package fixture")
elif name == "gpg":
    assert "--dearmor" in arguments
    pathlib.Path(arguments[arguments.index("--output") + 1]).write_bytes(b"harmless repository key fixture")
elif name == "install":
    if "-d" in arguments:
        pathlib.Path(arguments[-1]).mkdir(parents=True, exist_ok=True)
    else:
        shutil.copyfile(arguments[-2], arguments[-1])
    os.chmod(arguments[-1], int(arguments[arguments.index("-m") + 1], 8))
elif name == "az":
    assert arguments == ["version", "--query", '"azure-cli"', "--output", "tsv", "--only-show-errors"]
    assert os.environ["AZURE_CONFIG_DIR"].startswith(str(fixture / "run"))
    good = (fixture / "cli-installed").exists() and os.environ.get("BOOTSTRAP_FAIL_STAGE") != "azure-cli-version"
    print("2.90.0" if good else "0.0.0")
elif name == "pwsh":
    if worker:
        if os.environ.get("BOOTSTRAP_FAIL_STAGE") == "worker":
            sys.exit(43)
        print("Public gateway certificate is ready; renewal maintenance is configured.")
    else:
        print("7.6.7" if (fixture / "installed").exists() else "0.0.0")
elif name == "base64":
    sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))
elif name == "rm":
    shutil.rmtree(arguments[-1])
else:
    raise AssertionError("Unexpected stub command")
'''


class CertificateBootstrapTests(unittest.TestCase):
    """Ubuntu用の証明書初期化shellを、偽の外部コマンドで実行検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def run_fixture(self, fail_stage="", already_installed=False, cli_installed=False):
        source = (CORE / "guest/certificate-bootstrap.sh").read_text()
        # Keep the executable bootstrap intact apart from platform-only gates
        # and virtual filesystem mount locations needed to run on a nonroot Mac.
        self.assertIn('[ "$EUID" = 0 ] || exit 1', source)
        self.assertIn('[ "${OSTYPE:-}" = linux-gnu ] || exit 1', source)
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            (fixture / "bin").mkdir()
            (fixture / "run").mkdir()
            (fixture / "etc/apt/sources.list.d").mkdir(parents=True)
            (fixture / "expected.json").write_text(json.dumps(INPUTS))
            (fixture / "os-release").write_text("ID=ubuntu\nVERSION_ID=24.04\n")
            (fixture / "mounts").write_text("tmpfs /run tmpfs rw 0 0\n")
            if already_installed:
                (fixture / "installed").write_text("7.6.7")
            if cli_installed:
                (fixture / "cli-installed").write_text("2.90.0")
            stub = fixture / "stub.py"
            # Fixed Python interpreter avoids invoking another dependency stub.
            stub.write_text("#!" + shutil.which("python3") + "\n" + textwrap.dedent(STUB))
            stub.chmod(0o700)
            for name in ("mktemp", "chmod", "dpkg", "apt-get", "curl", "pwsh", "base64", "rm", "gpg", "install", "az", "systemd", "systemd-run", "systemctl"):
                (fixture / "bin" / name).symlink_to(stub)
            source = source.replace('[ "$EUID" = 0 ] || exit 1', ": # offline platform gate")
            source = source.replace('[ "${OSTYPE:-}" = linux-gnu ] || exit 1', ": # offline platform gate")
            source = source.replace(". /etc/os-release", ". '" + str(fixture / "os-release") + "'")
            source = source.replace("< /proc/mounts", "< '" + str(fixture / "mounts") + "'")
            source = source.replace("/run/egakumi-certificate.XXXXXXXX", str(fixture / "run/egakumi-certificate.XXXXXXXX"))
            source = source.replace("/etc/apt/", str(fixture / "etc/apt") + "/")
            source = source.replace("exec /usr/bin/pwsh", "exec " + str(fixture / "bin/pwsh"))
            source = source.replace("__CERTIFICATE_WORKER_BASE64__", base64.b64encode(b"# harmless worker fixture\n").decode())
            script = fixture / "bootstrap.sh"
            script.write_text(source)
            environment = dict(os.environ, **INPUTS)
            environment.update(PATH=str(fixture / "bin") + os.pathsep + os.environ["PATH"],
                               BOOTSTRAP_FIXTURE=str(fixture), BOOTSTRAP_FAIL_STAGE=fail_stage)
            result = subprocess.run(["bash", str(script)], env=environment, capture_output=True,
                                    text=True, timeout=20, check=False)
            records = [json.loads(line) for line in (fixture / "trace.jsonl").read_text().splitlines()]
            self.assertEqual(list((fixture / "run").iterdir()), [], "Private temporary input files survived cleanup")
            output = result.stdout + result.stderr
            for value in INPUTS.values():
                self.assertNotIn(value, output)
            self.assertNotIn("SHOULD_NOT_EXIST", "\n".join(str(item) for item in fixture.rglob("*")))
            for record in records:
                self.assertEqual(record["input_names"], sorted(INPUTS) if record["worker"] else [])
            for target in (fixture / "etc/apt").rglob("*"):
                if target.is_file():
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
            return result, records

    def test_installers_never_inherit_inputs_and_worker_receives_exact_values(self):
        result, records = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any(record["command"] == "apt-get" for record in records))
        self.assertEqual(sum(record["worker"] for record in records), 1)
        self.assertEqual(records[-1]["command"], "rm")

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required for worker entry validation")
    def test_worker_entry_rejects_unsupported_providers_before_state_or_secret_access(self):
        """実入口のOS条件だけを代替し、未対応方式が副作用へ届かないことを確認する。"""
        source = (CORE / "guest/certificate.ps1").read_text()
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
            "keyVaultName": "kv-fixture", "certificateName": "tls-public",
            "accountEmail": "operator@example.invalid",
            "directoryUrl": "https://acme-v02.api.letsencrypt.org/directory",
        }
        with tempfile.TemporaryDirectory() as temporary:
            worker = Path(temporary) / "worker.ps1"
            worker.write_text(source)
            for mode, plugin in (("prepared", "Azure"), ("dnsPlugin", "Cloudflare"),
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
                command = [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script), "-SourcePath", str(CORE / "guest/certificate.ps1"), "-FixtureRoot", str(fixture / "private-acme-state")]
                if missing:
                    command.append("-MissingHome")
                result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=20, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.strip(), "OS_LOGIN_MODULE_FIXTURE_PASS")

    def test_root_service_preserves_os_home_and_never_exposes_inputs_in_unit_environment(self):
        result, records = self.run_fixture(already_installed=True, cli_installed=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        launcher, = [row for row in records if row["command"] == "systemd-run"]
        worker, = [row for row in records if row["worker"]]
        self.assertTrue(worker["os_login_home"])
        self.assertEqual(launcher["input_names"], [])
        self.assertFalse(any(argument.startswith(("--property=Environment=", "--property=PassEnvironment=", "--setenv")) for argument in launcher["arguments"]))

    def test_unsupported_systemd_stops_before_install_or_worker(self):
        result, records = self.run_fixture(fail_stage="systemd-version")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("certificate-runtime-environment", result.stdout)
        self.assertFalse(any(row["command"] in ("apt-get", "systemd-run") or row["worker"] for row in records))

    def test_existing_transient_unit_is_never_reused_or_stopped(self):
        result, records = self.run_fixture(fail_stage="unit-collision", already_installed=True, cli_installed=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(row["command"] == "systemd-run" or row["worker"] for row in records))
        self.assertFalse(any(row["command"] == "systemctl" and row["arguments"][0] == "stop" for row in records))

    def test_transient_start_failure_stops_only_the_own_unit_and_removes_inputs(self):
        result, records = self.run_fixture(fail_stage="systemd-start", already_installed=True, cli_installed=True)
        self.assertEqual(result.returncode, 45, result.stdout + result.stderr)
        self.assertFalse(any(row["worker"] for row in records))
        self.assertEqual(sum(row["command"] == "systemctl" and row["arguments"][0] == "stop" for row in records), 1)
        self.assertEqual(records[-1]["command"], "rm")

    def test_installer_failure_cleans_private_inputs_without_exposing_them(self):
        result, records = self.run_fixture(fail_stage="install")
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertFalse(any(record["worker"] for record in records))
        self.assertIn("management-tool-dependencies", result.stdout)
        self.assertEqual(records[-1]["command"], "rm")

    def test_worker_failure_unexports_inputs_before_cleanup(self):
        result, records = self.run_fixture(fail_stage="worker")
        self.assertEqual(result.returncode, 43, result.stdout + result.stderr)
        self.assertIn("certificate-maintenance", result.stdout)
        self.assertEqual(records[-1]["input_names"], [])

    def test_reapply_reuses_runtime_without_installers(self):
        result, records = self.run_fixture(already_installed=True, cli_installed=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(record["command"] in ("apt-get", "curl") for record in records))
        self.assertEqual(sum(record["worker"] for record in records), 1)

    def test_existing_powershell_does_not_skip_missing_management_cli(self):
        """証明書実行環境が完成済みでも、人の運用ツールは独立に準備する。"""
        result, records = self.run_fixture(already_installed=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        packages = [argument for record in records if record["command"] == "apt-get"
                    for argument in record["arguments"] if "=" in argument]
        self.assertIn("azure-cli=2.90.0-1~noble", packages)
        self.assertNotIn("powershell=7.6.7-1.deb", packages)
        self.assertEqual(sum(record["worker"] for record in records), 1)

    def test_existing_cli_does_not_skip_missing_powershell_or_reconfigure_cli(self):
        result, records = self.run_fixture(cli_installed=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        arguments = [argument for record in records for argument in record["arguments"]]
        self.assertIn("powershell=7.6.7-1.deb", arguments)
        self.assertNotIn("azure-cli=2.90.0-1~noble", arguments)
        self.assertFalse(any(record["command"] == "gpg" for record in records))

    def test_signed_noble_repository_fixed_version_and_apt_lock_are_used(self):
        """実際に生成されたAPT設定で署名・OS・CPUの限定とロック待機を確認する。"""
        result, records = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        repository, = [record["repository"] for record in records if "repository" in record]
        fields = dict(line.split(": ", 1) for line in repository.splitlines())
        self.assertEqual(fields["URIs"], "https://packages.microsoft.com/repos/azure-cli/")
        self.assertEqual(fields["Suites"], "noble")
        self.assertEqual(fields["Architectures"], "amd64")
        self.assertTrue(fields["Signed-By"].endswith("/etc/apt/keyrings/egakumi-azure-cli.gpg"))
        self.assertNotIn("Trusted", fields)
        apt = [record["arguments"] for record in records if record["command"] == "apt-get"]
        self.assertTrue(apt)
        self.assertTrue(all("DPkg::Lock::Timeout=300" in arguments for arguments in apt))
        self.assertTrue(any("azure-cli=2.90.0-1~noble" in arguments for arguments in apt))
        self.assertFalse(any(record["command"] == "dpkg" and "-i" in record["arguments"] for record in records))

    def test_cli_installation_failure_has_safe_stage_and_cleans_inputs(self):
        result, records = self.run_fixture(fail_stage="azure-cli-install", already_installed=True)
        self.assertEqual(result.returncode, 44, result.stdout + result.stderr)
        self.assertIn("azure-cli-management-tool-installation", result.stdout)
        self.assertFalse(any(record["worker"] for record in records))
        self.assertEqual(records[-1]["command"], "rm")

    def test_cli_version_mismatch_is_not_reported_as_ready(self):
        result, records = self.run_fixture(fail_stage="azure-cli-version", already_installed=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("azure-cli-management-tool-installation", result.stdout)
        self.assertFalse(any(record["worker"] for record in records))


if __name__ == "__main__":
    unittest.main()
