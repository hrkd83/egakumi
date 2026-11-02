"""azdの各段階を正しい順で実行し、失敗時に止まることを検証する。

偽のCLIとARM transportで事前確認・監視準備・構築後設定・撤去の境界を再現する。秘密の一時入力や保持設定を扱うため、成功系だけでなく中断時の挙動も調べる。
"""
from __future__ import annotations

import copy
import contextlib
import io
import json
import os
from pathlib import Path
import re
import stat
import ssl
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment import lifecycle
from deployment.helpers import cleanup
from deployment.helpers import prepare_secrets as secret_preparation
from deployment.helpers.configuration import normalize_configuration
from configuration_fixtures import connection_fixture, fixture, ssh_fixture_key


from lifecycle_fixtures import LifecycleFixture, ScriptedTransport, response_policy


class LocalPreparationHookTests(LifecycleFixture, unittest.TestCase):
    """秘密の対話と鍵生成はmockし、実hookの順序とAzure前の停止を検査する。"""

    def dependency_command(self, arguments, **kwargs):
        """local PowerShellの依存照合以外を、偽CLIの入口でも許さない。"""
        self.assertEqual(arguments, ["/fixture/pwsh", "-NoLogo", "-NoProfile", "-File",
            str(self.root / "scripts/deployment/helpers/set-app-insights-cap.ps1"), "-CheckDependencies"])
        self.assertEqual(kwargs, {"environment": {"FIXTURE_ONLY": "1"}})
        return {}

    def test_azd_preparation_hooks_bind_the_interactive_terminal_to_the_matching_command(self):
        """実azdを起動せず、対話が有効な2つのhookとPython入口の配線を検査する。

        azdはinteractiveを省略すると標準入出力を捕捉するため、Python側に
        getpass処理があっても利用者へ入力を求められません。YAMLのhooks直下の
        plain mappingだけを取り出し、別の節の同名値を誤って合格にしません。
        """
        path = Path(__file__).resolve().parents[1] / "azure.yaml"
        lines = path.read_text(encoding="utf-8").splitlines()
        headers = [index for index, line in enumerate(lines) if line == "hooks:"]
        self.assertEqual(len(headers), 1, "The azd project must have one root hooks mapping")
        hook_blocks = {}
        active = None
        for line in lines[headers[0] + 1:]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if not line.startswith(" "):
                break
            header = re.fullmatch(r"  ([A-Za-z][A-Za-z0-9_-]*):", line)
            if header:
                active = header.group(1)
                self.assertNotIn(active, hook_blocks, "Duplicate azd hook definitions are ambiguous")
                hook_blocks[active] = []
            elif active is not None:
                hook_blocks[active].append(line)
        for hook in ("preup", "preprovision"):
            with self.subTest(hook=hook):
                self.assertIn(hook, hook_blocks)
                fields = {}
                for line in hook_blocks[hook]:
                    field = re.fullmatch(r"    (interactive|shell|run): (.+)", line)
                    if field:
                        self.assertNotIn(field.group(1), fields, "A hook setting must not be repeated")
                        fields[field.group(1)] = field.group(2)
                self.assertEqual(fields.get("interactive"), "true", "azd must attach the terminal before requesting secrets")
                self.assertEqual(fields.get("shell"), "sh")
                self.assertEqual(fields.get("run"), f"python3 scripts/deployment/lifecycle.py {hook}")

    def test_normal_local_preflight_prepares_secrets_then_isolated_key_after_dependencies(self):
        events = []

        def dependencies(arguments, **kwargs):
            result = self.dependency_command(arguments, **kwargs)
            events.append("dependencies")
            return result

        def prepare(config, root):
            self.assertIs(config, self.config)
            self.assertEqual(root, self.root)
            self.assertEqual(Path(config["management"]["sshPublicKeyFile"]), self.root / "secrets/management-ssh.pub")
            self.assertTrue(Path(config["management"]["sshPublicKeyFile"]).is_relative_to(root))
            events.append("ssh")
            return {"created": False}

        def secrets(config, root):
            self.assertIs(config, self.config)
            self.assertEqual(root, self.root)
            events.append("secret-preparation")
            return {"created": [], "reused": []}

        with mock.patch.object(lifecycle.shutil, "which", return_value="/fixture/tool"), \
             mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh"), \
             mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}), \
             mock.patch.object(lifecycle, "run", side_effect=dependencies), \
             mock.patch.object(lifecycle, "prepare_secrets", side_effect=secrets) as secret_preparation, \
             mock.patch.object(lifecycle, "ensure_management_ssh_key", side_effect=prepare) as key:
            lifecycle.local_preflight(self.config)
        secret_preparation.assert_called_once_with(self.config, self.root)
        key.assert_called_once_with(self.config, self.root)
        self.assertEqual(events, ["dependencies", "secret-preparation", "ssh"])
        self.assertFalse((self.root / "secrets").exists())

    def test_environment_or_dependency_failure_never_attempts_key_preparation(self):
        cases = [
            ("environment", {"AZURE_ENV_NAME": "wrong-environment"}, None, None, None),
            ("subscription", {"AZURE_SUBSCRIPTION_ID": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}, None, None, None),
            ("tenant", {"AZURE_TENANT_ID": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}, None, None, None),
            ("az", {}, "az", None, None),
            ("azd", {}, "azd", None, None),
            ("powershell", {}, None, RuntimeError("fixture PowerShell missing"), None),
            ("module", {}, None, None, lifecycle.DeploymentError("fixture module missing")),
        ]
        for label, environment, missing_cli, powershell_error, dependency_error in cases:
            with self.subTest(failure=label), \
                 mock.patch.dict(os.environ, environment, clear=True), \
                 mock.patch.object(lifecycle.shutil, "which", side_effect=lambda command: None if command == missing_cli else "/fixture/tool"), \
                 mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh", side_effect=powershell_error), \
                 mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}), \
                 mock.patch.object(lifecycle, "run", side_effect=dependency_error or self.dependency_command), \
                 mock.patch.object(lifecycle, "prepare_secrets") as secret_preparation, \
                 mock.patch.object(lifecycle, "ensure_management_ssh_key", return_value={"created": False}) as key:
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.local_preflight(self.config)
                secret_preparation.assert_not_called()
                key.assert_not_called()
        self.assertFalse((self.root / "secrets").exists())

    def test_both_provision_hooks_prepare_secret_then_key_then_load_before_azure_boundary(self):
        """実local_preflightを通し、Azure入口をsentinelで止めるので外部処理はない。"""
        for hook in ("preup", "preprovision"):
            self.dns_selection.reset_mock()
            events = []

            def prepare(config, root):
                self.assertIs(config, self.config)
                self.assertEqual(root, self.root)
                events.append("ssh")
                return {"created": False}

            def azure_boundary(config):
                events.append("azure-boundary")
                raise lifecycle.DeploymentError("fixture stop before Azure")

            def dependencies(arguments, **kwargs):
                result = self.dependency_command(arguments, **kwargs)
                events.append("dependencies")
                return result

            with self.subTest(hook=hook), \
                 mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
                 mock.patch.object(lifecycle.shutil, "which", return_value="/fixture/tool"), \
                 mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh"), \
                 mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}), \
                 mock.patch.object(lifecycle, "run", side_effect=dependencies), \
                 mock.patch.object(lifecycle, "prepare_secrets", side_effect=lambda config, root: events.append("secret-preparation")) as secret_preparation, \
                 mock.patch.object(lifecycle, "ensure_management_ssh_key", side_effect=prepare) as key, \
                 mock.patch.object(lifecycle, "load_secret_inputs", side_effect=lambda _: events.append("secret-inputs") or {}), \
                 mock.patch.object(lifecycle, "prepare_grpc_secret", return_value={}), \
                 mock.patch.object(lifecycle, "azure_preflight", side_effect=azure_boundary), \
                 mock.patch.object(lifecycle, "register_providers") as providers, \
                 mock.patch.object(lifecycle, "ArmTransport") as arm, \
                 mock.patch.object(lifecycle, "deploy_phase") as deploy, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(lifecycle.main([hook]), 1)
                secret_preparation.assert_called_once_with(self.config, self.root)
                key.assert_called_once_with(self.config, self.root)
                if hook == "preup":
                    self.dns_selection.assert_called_once_with(self.root / "params/test.local.json", self.config)
                else:
                    self.dns_selection.assert_not_called()
                providers.assert_not_called()
                arm.assert_not_called()
                deploy.assert_not_called()
            self.assertEqual(events, ["dependencies", "secret-preparation", "ssh", "secret-inputs", "azure-boundary"])
            self.assertFalse(lifecycle.PARAMETERS.exists())

    def test_key_preparation_failure_stops_both_hooks_before_secret_or_azure_access(self):
        for hook in ("preup", "preprovision"):
            error_output = io.StringIO()
            with self.subTest(hook=hook), \
                 mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
                 mock.patch.object(lifecycle.shutil, "which", return_value="/fixture/tool"), \
                 mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh"), \
                 mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}), \
                 mock.patch.object(lifecycle, "run", side_effect=self.dependency_command), \
                 mock.patch.object(lifecycle, "prepare_secrets") as secret_preparation, \
                 mock.patch.object(lifecycle, "ensure_management_ssh_key", side_effect=ValueError("fixture SSH preparation failed")) as key, \
                 mock.patch.object(lifecycle, "load_secret_inputs") as secrets, \
                 mock.patch.object(lifecycle, "prepare_grpc_secret") as ingress, \
                 mock.patch.object(lifecycle, "azure_preflight") as azure, \
                 mock.patch.object(lifecycle, "register_providers") as providers, \
                 mock.patch.object(lifecycle, "ArmTransport") as arm, \
                 mock.patch.object(lifecycle, "deploy_phase") as deploy, \
                 contextlib.redirect_stderr(error_output):
                self.assertEqual(lifecycle.main([hook]), 1)
                secret_preparation.assert_called_once_with(self.config, self.root)
                key.assert_called_once_with(self.config, self.root)
                for downstream in (secrets, ingress, azure, providers, arm, deploy):
                    downstream.assert_not_called()
            self.assertIn("fixture SSH preparation failed", error_output.getvalue())
            self.assertFalse(lifecycle.PARAMETERS.exists())
            self.assertFalse((self.root / "secrets").exists())

    def test_secret_failure_or_cancel_stops_both_hooks_before_key_and_azure_without_secret_output(self):
        """未入力・非対話端末・キャンセルを合成し、値をエラーへ出さないことも調べる。"""
        failures = (
            ("invalid-input", ValueError("PRIVATE_SECRET_FIXTURE"), "Secret preparation did not complete"),
            ("operating-system", OSError("PRIVATE_SECRET_FIXTURE"), "Secret preparation did not complete"),
            ("unicode", UnicodeError("PRIVATE_SECRET_FIXTURE"), "Secret preparation did not complete"),
            ("interrupted", KeyboardInterrupt("PRIVATE_SECRET_FIXTURE"), "Secret input was cancelled"),
            ("end-of-input", EOFError("PRIVATE_SECRET_FIXTURE"), "Secret input was cancelled"),
            ("non-interactive", lifecycle.SecretPreparationError("Secret input requires an interactive terminal"), "requires an interactive terminal"),
            ("configuration", lifecycle.ConfigurationError("certificate.pluginArgumentsFile: protected input required"), "protected input required"),
        )
        for hook in ("preup", "preprovision"):
            for label, failure, message in failures:
                output = io.StringIO()
                with self.subTest(hook=hook, failure=label), \
                     mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
                     mock.patch.object(lifecycle.shutil, "which", return_value="/fixture/tool"), \
                     mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh"), \
                     mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}), \
                     mock.patch.object(lifecycle, "run", side_effect=self.dependency_command), \
                     mock.patch.object(lifecycle, "prepare_secrets", side_effect=failure) as secret_preparation, \
                     mock.patch.object(lifecycle, "ensure_management_ssh_key") as key, \
                     mock.patch.object(lifecycle, "load_secret_inputs") as secrets, \
                     mock.patch.object(lifecycle, "prepare_grpc_secret") as ingress, \
                     mock.patch.object(lifecycle, "azure_preflight") as azure, \
                     mock.patch.object(lifecycle, "register_providers") as providers, \
                     mock.patch.object(lifecycle, "ArmTransport") as arm, \
                     mock.patch.object(lifecycle, "deploy_phase") as deploy, \
                     contextlib.redirect_stderr(output), contextlib.redirect_stdout(output):
                    self.assertEqual(lifecycle.main([hook]), 1)
                    secret_preparation.assert_called_once_with(self.config, self.root)
                    for downstream in (key, secrets, ingress, azure, providers, arm, deploy):
                        downstream.assert_not_called()
                self.assertIn(message, output.getvalue())
                self.assertNotIn("PRIVATE_SECRET_FIXTURE", output.getvalue())
                self.assertFalse(lifecycle.PARAMETERS.exists())
                self.assertFalse((self.root / "secrets").exists())

    def test_validation_and_input_cleanup_never_start_interactive_preparation(self):
        """設定だけの確認や生成入力の掃除で、秘密を求めたり鍵を作ったりしない。"""
        for command in ("validate", "clear-inputs"):
            with self.subTest(command=command), \
                 mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
                 mock.patch.object(lifecycle, "prepare_secrets") as secrets, \
                 mock.patch.object(lifecycle, "ensure_management_ssh_key") as key, \
                 mock.patch.object(lifecycle, "azure_preflight") as azure, \
                 mock.patch.object(lifecycle, "ArmTransport") as arm, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(lifecycle.main([command]), 0)
                for preparation in (secrets, key, azure, arm):
                    preparation.assert_not_called()
            self.assertFalse((self.root / "secrets").exists())

    def test_fresh_clone_secret_input_reaches_real_loader_and_non_tty_retry_reuses_files(self):
        """実際の対話準備と読込をつなぎ、両構築hookをAzure入口の直前で停止する。

        getpassと準備確認だけは合成入力へ差し替える。JSON変換・保護保存・
        公開鍵と秘密の読込は本物を通し、hook間の契約の食い違いも検出する。
        実SSH生成やCLIは起動せず、生成先は試験専用temp内だけに限定する。
        """
        client_secret = "SYNTHETIC-HOOK-ENTRA-DO-NOT-DISPLAY"
        dns_token = "SYNTHETIC-HOOK-DNS-DO-NOT-DISPLAY"
        real_loader = lifecycle.load_secret_inputs
        for hook in ("preup", "preprovision"):
            with self.subTest(hook=hook), tempfile.TemporaryDirectory(dir=self.root) as directory:
                root = Path(directory).resolve()
                raw = connection_fixture()
                dns = root / "secrets/dns-plugin-arguments.json"
                entra = root / "secrets/entra-client-secret.txt"
                public_key = root / "secrets/management-ssh.pub"
                raw["management"]["sshPublicKeyFile"] = str(public_key)
                raw["certificate"]["pluginArgumentsFile"] = str(dns)
                raw["credentialProviders"][0]["clientSecretFile"] = str(entra)
                config = normalize_configuration(raw, base_directory=root)
                self.assertFalse((root / "secrets").exists())

                for first_run in (True, False):
                    events = []
                    output = io.StringIO()

                    def dependencies(arguments, **kwargs):
                        self.assertEqual(arguments, ["/fixture/pwsh", "-NoLogo", "-NoProfile", "-File",
                            str(root / "scripts/deployment/helpers/set-app-insights-cap.ps1"), "-CheckDependencies"])
                        self.assertEqual(kwargs, {"environment": {"FIXTURE_ONLY": "1"}})
                        events.append("dependencies")
                        return {}

                    def confirm(prompt):
                        self.assertIn("Cloudflare API token", output.getvalue())
                        self.assertIn("Entraアプリのシークレットの「値」", output.getvalue())
                        events.append("ready-confirm")
                        return ""

                    def hidden_input(prompt):
                        if "Entraアプリのシークレットの「値」" in prompt:
                            events.append("client-input")
                            return client_secret
                        self.assertIn("Cloudflare", prompt)
                        events.append("dns-input")
                        return dns_token

                    def prepare_key(actual_config, actual_root):
                        self.assertIs(actual_config, config)
                        self.assertEqual(actual_root, root)
                        # 本物の秘密準備が先に完了していなければ失敗させる。
                        self.assertTrue(entra.is_file())
                        self.assertTrue(dns.is_file())
                        if not public_key.exists():
                            public_key.write_text(ssh_fixture_key() + "\n", encoding="utf-8")
                        events.append("ssh")
                        return {"created": False}

                    def load_inputs(actual_config):
                        values = real_loader(actual_config)
                        self.assertEqual(values["credentialProviderSecrets"], {"approved-oauth": client_secret})
                        self.assertEqual(json.loads(values["dnsPluginArgumentsJson"]),
                            {"CFToken": {"type": "secureString", "value": dns_token}})
                        self.assertEqual(values["sshPublicKey"], ssh_fixture_key())
                        events.append("load-inputs")
                        return values

                    def azure_boundary(actual_config):
                        events.append("azure-boundary")
                        raise lifecycle.DeploymentError("Fixture stops before the first Azure operation")

                    before = ({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (entra, dns)}
                              if not first_run else {})
                    with contextlib.ExitStack() as stack:
                        stack.enter_context(mock.patch.object(lifecycle, "ROOT", root))
                        stack.enter_context(mock.patch.object(lifecycle, "load_configuration", return_value=config))
                        stack.enter_context(mock.patch.object(lifecycle.shutil, "which", return_value="/fixture/tool"))
                        stack.enter_context(mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh"))
                        stack.enter_context(mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}))
                        stack.enter_context(mock.patch.object(lifecycle, "run", side_effect=dependencies))
                        prepare = stack.enter_context(mock.patch.object(lifecycle, "prepare_secrets", wraps=secret_preparation.prepare_secrets))
                        stack.enter_context(mock.patch.object(secret_preparation.sys.stdin, "isatty", return_value=first_run))
                        secret_input = stack.enter_context(mock.patch.object(secret_preparation.getpass, "getpass", side_effect=hidden_input))
                        ready = stack.enter_context(mock.patch("builtins.input", side_effect=confirm))
                        stack.enter_context(mock.patch.object(lifecycle, "ensure_management_ssh_key", side_effect=prepare_key))
                        stack.enter_context(mock.patch.object(lifecycle, "load_secret_inputs", side_effect=load_inputs))
                        stack.enter_context(mock.patch.object(lifecycle, "azure_preflight", side_effect=azure_boundary))
                        providers = stack.enter_context(mock.patch.object(lifecycle, "register_providers"))
                        arm = stack.enter_context(mock.patch.object(lifecycle, "ArmTransport"))
                        deploy = stack.enter_context(mock.patch.object(lifecycle, "deploy_phase"))
                        stack.enter_context(contextlib.redirect_stdout(output))
                        stack.enter_context(contextlib.redirect_stderr(output))
                        self.assertEqual(lifecycle.main([hook]), 1)
                        prepare.assert_called_once_with(config, root)
                        if first_run:
                            ready.assert_called_once()
                            self.assertEqual(secret_input.call_count, 2)
                        else:
                            ready.assert_not_called()
                            secret_input.assert_not_called()
                        for downstream in (providers, arm, deploy):
                            downstream.assert_not_called()
                    expected = ["dependencies"]
                    if first_run:
                        expected += ["ready-confirm", "client-input", "dns-input"]
                    self.assertEqual(events, expected + ["ssh", "load-inputs", "azure-boundary"])
                    self.assertNotIn(client_secret, output.getvalue())
                    self.assertNotIn(dns_token, output.getvalue())
                    self.assertEqual(stat.S_IMODE((root / "secrets").stat().st_mode), 0o700)
                    for path in (entra, dns):
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                    if before:
                        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (entra, dns)})
                    self.assertFalse(lifecycle.PARAMETERS.exists())


    def clone_public_inputs(self):
        """公開入力のコピーへ架空IDと参照だけを入れ、実ユーザー設定から独立させる。"""
        core = Path(__file__).resolve().parents[1]
        # macOSの/tmpや/varはsymlinkなので、selectorへ渡す専用入力は実体パスにします。
        source = (self.root / "params/test.local.json").resolve()
        source.parent.mkdir()
        (self.root / "config").mkdir()
        raw = json.loads((core / "params/test.example.json").read_text())
        raw["subscriptionId"] = self.config["subscriptionId"]
        raw["azureTenantId"] = self.config["azureTenantId"]
        raw["entraTenantId"] = self.config["entraTenantId"]
        raw["gateway"]["authentication"] = copy.deepcopy(self.config["gateway"]["authentication"])
        raw["gateway"]["authentication"]["credentialLogin"] = copy.deepcopy(
            connection_fixture()["gateway"]["authentication"]["credentialLogin"])
        raw["credentialProviders"] = connection_fixture()["credentialProviders"]
        raw["credentialProviders"][0]["clientSecretFile"] = "../secrets/entra-client-secret.txt"
        source.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
        (self.root / "config/test.json").write_bytes((core / "config/test.json").read_bytes())
        return source

    def test_dns_selection_and_secret_input_reach_loader_then_preprovision_reuses_without_menu(self):
        """初回選択→秘密準備→実読込を通し、次のhookで再入力させない。"""
        self.exercise_dns_selection_and_secret_preparation(existing_dns=False)

    def test_dns_provider_change_replaces_existing_cloudflare_input_and_keeps_entra(self):
        """旧CF資格情報からR53へ実際に切替え、Entraと次hookの再利用を維持する。"""
        self.exercise_dns_selection_and_secret_preparation(existing_dns=True)

    def exercise_dns_selection_and_secret_preparation(self, *, existing_dns):
        """同じ実関数の結合経路を、新規cloneと既存DNS変更の両方で検査する。"""
        from deployment.helpers import dns_selection
        source = self.clone_public_inputs()
        before_params = json.loads(source.read_text())
        real_configuration = lifecycle.load_configuration
        real_inputs = lifecycle.load_secret_inputs
        client_secret = "SYNTHETIC-SELECTION-ENTRA-DO-NOT-DISPLAY"
        access_key = "SYNTHETIC-SELECTION-ACCESS-ID"
        dns_secret = "SYNTHETIC-SELECTION-DNS-DO-NOT-DISPLAY"
        output = io.StringIO()
        public_key = self.root / "secrets/management-ssh.pub"
        events = []
        preparation_reports = []
        dns_file = (self.root / "secrets/dns-plugin-arguments.json").resolve()
        entra_file = (self.root / "secrets/entra-client-secret.txt").resolve()
        old_dns_token = "SYNTHETIC-OLD-CLOUDFLARE-DO-NOT-REUSE"
        original_entra = None
        if existing_dns:
            dns_file.parent.mkdir(mode=0o700)
            dns_file.write_text(json.dumps({"CFToken": {"type": "secureString", "value": old_dns_token}}))
            dns_file.chmod(0o600)
            entra_file.write_text(client_secret + "\n")
            entra_file.chmod(0o600)
            original_entra = (entra_file.read_bytes(), entra_file.stat().st_ino, entra_file.stat().st_mtime_ns)

        def prepare_existing_or_missing(config, root):
            result = secret_preparation.prepare_secrets(config, root)
            preparation_reports.append(result)
            return result

        def checked_dependencies(arguments, **kwargs):
            result = self.dependency_command(arguments, **kwargs)
            events.append("dependencies")
            return result

        def select(actual_source, actual_config):
            self.assertEqual(events, ["dependencies"])
            events.append("dns-selection")
            self.assertEqual(actual_source, source)
            self.assertEqual(actual_config["certificate"]["plugin"], "Cloudflare")
            self.assertEqual((self.root / "secrets").exists(), existing_dns)
            return dns_selection.select_dns_provider(actual_source, actual_config)

        def prepare_key(actual_config, actual_root):
            self.assertEqual(actual_root, self.root)
            self.assertEqual(actual_config["certificate"]["plugin"], "Route53")
            self.assertTrue((self.root / "secrets/dns-plugin-arguments.json").is_file())
            if not public_key.exists():
                public_key.write_text(ssh_fixture_key() + "\n")
            events.append("ssh")
            return {"created": False}

        def load_inputs(actual_config):
            values = real_inputs(actual_config)
            self.assertEqual(values["credentialProviderSecrets"], {"approved-oauth": client_secret})
            self.assertEqual(json.loads(values["dnsPluginArgumentsJson"]), {
                "R53AccessKey": {"type": "string", "value": access_key},
                "R53SecretKey": {"type": "secureString", "value": dns_secret},
            })
            self.assertEqual(values["sshPublicKey"], ssh_fixture_key())
            events.append("load-inputs")
            return values

        def stop_before_azure(actual_config):
            self.assertEqual(actual_config["certificate"]["plugin"], "Route53")
            allowed = actual_config["network"]["firewall"]["managementAllowedFqdns"]
            self.assertIn("route53.amazonaws.com", allowed)
            self.assertNotIn("api.cloudflare.com", allowed)
            events.append("azure-boundary")
            raise lifecycle.DeploymentError("Fixture stops before Azure")

        for hook in ("preup", "preprovision"):
            first = hook == "preup"
            events.clear()
            before_files = ({path: (path.read_bytes(), path.stat().st_mtime_ns)
                             for path in (self.root / "secrets").iterdir()} if not first else {})
            with self.subTest(hook=hook), contextlib.ExitStack() as stack:
                selector = stack.enter_context(mock.patch.object(lifecycle, "select_dns_provider", side_effect=select))
                stack.enter_context(mock.patch.object(lifecycle, "load_configuration", wraps=real_configuration))
                stack.enter_context(mock.patch.object(lifecycle.shutil, "which", return_value="/fixture/tool"))
                stack.enter_context(mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh"))
                stack.enter_context(mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}))
                stack.enter_context(mock.patch.object(lifecycle, "run", side_effect=checked_dependencies))
                stack.enter_context(mock.patch.object(lifecycle, "prepare_secrets", side_effect=prepare_existing_or_missing))
                stack.enter_context(mock.patch.object(dns_selection.sys.stdin, "isatty", return_value=True))
                menu = stack.enter_context(mock.patch("builtins.input", side_effect=["2", ""] if first else AssertionError("No repeat menu is permitted")))
                hidden = stack.enter_context(mock.patch.object(secret_preparation.getpass, "getpass", side_effect=[access_key, dns_secret] if existing_dns else [client_secret, access_key, dns_secret]))
                stack.enter_context(mock.patch.object(lifecycle, "ensure_management_ssh_key", side_effect=prepare_key))
                stack.enter_context(mock.patch.object(lifecycle, "load_secret_inputs", side_effect=load_inputs))
                stack.enter_context(mock.patch.object(lifecycle, "prepare_grpc_secret"))
                stack.enter_context(mock.patch.object(lifecycle, "azure_preflight", side_effect=stop_before_azure))
                providers = stack.enter_context(mock.patch.object(lifecycle, "register_providers"))
                arm = stack.enter_context(mock.patch.object(lifecycle, "ArmTransport"))
                deploy = stack.enter_context(mock.patch.object(lifecycle, "deploy_phase"))
                stack.enter_context(contextlib.redirect_stdout(output))
                stack.enter_context(contextlib.redirect_stderr(output))
                self.assertEqual(lifecycle.main([hook, "--config", str(source)]), 1)
                if first:
                    selector.assert_called_once()
                    self.assertEqual(menu.call_count, 2)
                    self.assertEqual(hidden.call_count, 2 if existing_dns else 3)
                else:
                    selector.assert_not_called()
                    menu.assert_not_called()
                    hidden.assert_not_called()
                for downstream in (providers, arm, deploy):
                    downstream.assert_not_called()
            expected = ["dependencies", "dns-selection"] if first else ["dependencies"]
            self.assertEqual(events, expected + ["ssh", "load-inputs", "azure-boundary"])
            saved = json.loads(source.read_text())
            expected_params = copy.deepcopy(before_params)
            expected_params["certificate"]["plugin"] = "Route53"
            self.assertEqual(saved, expected_params)
            if before_files:
                self.assertEqual(before_files, {path: (path.read_bytes(), path.stat().st_mtime_ns)
                                              for path in (self.root / "secrets").iterdir()})
            self.assertFalse(lifecycle.PARAMETERS.exists())
            if original_entra:
                self.assertEqual((entra_file.read_bytes(), entra_file.stat().st_ino, entra_file.stat().st_mtime_ns), original_entra)
        if existing_dns:
            self.assertEqual(preparation_reports[0], {"created": [], "reused": [str(entra_file)], "replaced": [str(dns_file)]})
            self.assertNotIn(old_dns_token, dns_file.read_text())
            self.assertNotIn("CFToken", json.loads(dns_file.read_text()))
        else:
            self.assertEqual(preparation_reports[0]["replaced"], [])
        self.assertEqual(preparation_reports[1], {"created": [], "reused": [str(entra_file), str(dns_file)], "replaced": []})
        self.assertEqual(list(dns_file.parent.glob(".secret-input-*")), [])
        self.assertEqual(list(dns_file.parent.glob(".*.replace-lock")), [])
        for value in (client_secret, access_key, dns_secret, old_dns_token):
            self.assertNotIn(value, output.getvalue())
            self.assertNotIn(value, source.read_text())

    def test_dns_selection_cancel_stops_before_secret_key_and_azure(self):
        """DNS選択を中止した時点では、秘密の入力やローカル鍵作成も開始しない。"""
        from deployment.helpers import dns_selection
        source = self.clone_public_inputs()
        before = source.read_bytes()
        for answer in ("q", EOFError()):
            output = io.StringIO()
            with self.subTest(kind=type(answer).__name__), \
                 mock.patch.object(lifecycle, "select_dns_provider", wraps=dns_selection.select_dns_provider), \
                 mock.patch.object(lifecycle.shutil, "which", return_value="/fixture/tool"), \
                 mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh"), \
                 mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}), \
                 mock.patch.object(lifecycle, "run", side_effect=self.dependency_command), \
                 mock.patch.object(dns_selection.sys.stdin, "isatty", return_value=True), \
                 mock.patch("builtins.input", side_effect=[answer]), \
                 mock.patch.object(lifecycle, "prepare_secrets") as secrets, \
                 mock.patch.object(lifecycle, "ensure_management_ssh_key") as ssh, \
                 mock.patch.object(lifecycle, "azure_preflight") as azure, \
                 mock.patch.object(lifecycle, "register_providers") as providers, \
                 mock.patch.object(lifecycle, "ArmTransport") as arm, \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(lifecycle.main(["preup", "--config", str(source)]), 1)
                for downstream in (secrets, ssh, azure, providers, arm):
                    downstream.assert_not_called()
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse((self.root / "secrets").exists())


if __name__ == "__main__":
    unittest.main()
