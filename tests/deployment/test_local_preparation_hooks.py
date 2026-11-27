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

from deployment import lifecycle
from deployment.helpers import cleanup, configuration
from deployment.helpers import prepare_secrets as secret_preparation
from deployment.helpers.configuration import normalize_configuration
from tests.support.configuration_fixtures import bind_credential_fixture_id, connection_fixture, fixture, ssh_fixture_key


from tests.support.lifecycle_fixtures import LifecycleFixture, ScriptedTransport, response_policy


class LocalPreparationHookTests(LifecycleFixture, unittest.TestCase):
    """秘密の対話と鍵生成はmockし、実hookの順序とAzure前の停止を検査する。"""

    def dependency_command(self, arguments, **kwargs):
        """local PowerShellの依存照合以外を、偽CLIの入口でも許さない。"""
        self.assertEqual(arguments, ["/fixture/pwsh", "-NoLogo", "-NoProfile", "-File",
            str(self.root / "scripts/deployment/helpers/set-app-insights-cap.ps1"), "-CheckDependencies"])
        self.assertEqual(kwargs, {"environment": {"FIXTURE_ONLY": "1"}})
        return {}

    def test_azd_preparation_hook_binds_the_interactive_terminal_to_preprovision(self):
        """実azdを起動せず、対話が有効なpreprovision hookとPython入口の配線を検査する。

        azdはinteractiveを省略すると標準入出力を捕捉するため、Python側に
        getpass処理があっても利用者へ入力を求められません。YAMLのhooks直下の
        plain mappingだけを取り出し、別の節の同名値を誤って合格にしません。
        """
        path = Path(__file__).resolve().parents[2] / "azure.yaml"
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
        self.assertIn("preprovision", hook_blocks)
        fields = {}
        for line in hook_blocks["preprovision"]:
            field = re.fullmatch(r"    (interactive|shell|run): (.+)", line)
            if field:
                self.assertNotIn(field.group(1), fields, "A hook setting must not be repeated")
                fields[field.group(1)] = field.group(2)
        self.assertEqual(fields.get("interactive"), "true", "azd must attach the terminal before requesting secrets")
        self.assertEqual(fields.get("shell"), "sh")
        self.assertEqual(fields.get("run"), "python3 scripts/deployment/lifecycle.py preprovision")

    def test_normal_local_preflight_prepares_secrets_then_isolated_key_after_dependencies(self):
        events = []

        def dependencies(arguments, **kwargs):
            result = self.dependency_command(arguments, **kwargs)
            events.append("dependencies")
            return result

        def prepare(config, root):
            self.assertIs(config, self.config)
            self.assertEqual(root, self.paths.root)
            self.assertEqual(Path(config["management"]["sshPublicKeyFile"]), self.root / ".local/test/secrets/management-ssh.pub")
            self.assertTrue(Path(config["management"]["sshPublicKeyFile"]).is_relative_to(root))
            events.append("ssh")
            return {"created": False}

        def secrets(config, root):
            self.assertIs(config, self.config)
            self.assertEqual(root, self.paths.root)
            events.append("secret-preparation")
            return {"created": [], "reused": []}

        with mock.patch.object(lifecycle.shutil, "which", return_value="/fixture/tool"), \
             mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh"), \
             mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}), \
             mock.patch.object(lifecycle, "run", side_effect=dependencies), \
             mock.patch.object(lifecycle, "prepare_secrets", side_effect=secrets) as secret_preparation, \
             mock.patch.object(lifecycle, "ensure_management_ssh_key", side_effect=prepare) as key:
            lifecycle.local_preflight(self.config)
        secret_preparation.assert_called_once_with(self.config, self.paths.root)
        key.assert_called_once_with(self.config, self.paths.root)
        self.assertEqual(events, ["dependencies", "secret-preparation", "ssh"])
        self.assertEqual(list((self.root / ".local/test/secrets").glob("*")), [])

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
        self.assertEqual(list((self.root / ".local/test/secrets").glob("*")), [])

    def test_preprovision_prepares_secret_and_key_before_azure_boundary(self):
        """実local_preflightを通し、Azure入口をsentinelで止めるので外部処理はない。"""
        self.dns_selection.reset_mock()
        events = []

        def prepare(config, root):
            self.assertIs(config, self.config)
            self.assertEqual(root, self.paths.root)
            events.append("ssh")
            return {"created": False}

        def azure_boundary(config):
            events.append("azure-boundary")
            raise lifecycle.DeploymentError("fixture stop before Azure")

        def dependencies(arguments, **kwargs):
            result = self.dependency_command(arguments, **kwargs)
            events.append("dependencies")
            return result

        with mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
             mock.patch.object(lifecycle.shutil, "which", return_value="/fixture/tool"), \
             mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh"), \
             mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}), \
             mock.patch.object(lifecycle, "run", side_effect=dependencies), \
             mock.patch.object(lifecycle, "prepare_secrets", side_effect=lambda config, root: events.append("secret-preparation")) as secret_preparation, \
             mock.patch.object(lifecycle, "ensure_management_ssh_key", side_effect=prepare) as key, \
             mock.patch.object(lifecycle, "load_secret_inputs", side_effect=lambda _: events.append("secret-inputs") or {}), \
             mock.patch.object(lifecycle, "prepare_ingress_secrets", return_value={}), \
             mock.patch.object(lifecycle, "azure_preflight", side_effect=azure_boundary), \
             mock.patch.object(lifecycle, "register_providers") as providers, \
             mock.patch.object(lifecycle, "ArmTransport") as arm, \
             mock.patch.object(lifecycle, "deploy_monitoring") as deploy, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lifecycle.main(["preprovision"]), 1)
            secret_preparation.assert_called_once_with(self.config, self.paths.root)
            key.assert_called_once_with(self.config, self.paths.root)
            self.dns_selection.assert_not_called()
            providers.assert_not_called()
            arm.assert_not_called()
            deploy.assert_not_called()
        expected = ["dependencies", "secret-preparation", "ssh"]
        self.assertEqual(events, expected + ["azure-boundary"])
        self.assertFalse(self.paths.parameters.exists())

    def test_key_preparation_failure_stops_preprovision_before_secret_or_azure_access(self):
        error_output = io.StringIO()
        with mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
             mock.patch.object(lifecycle.shutil, "which", return_value="/fixture/tool"), \
             mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh"), \
             mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}), \
             mock.patch.object(lifecycle, "run", side_effect=self.dependency_command), \
             mock.patch.object(lifecycle, "prepare_secrets") as secret_preparation, \
             mock.patch.object(lifecycle, "ensure_management_ssh_key", side_effect=ValueError("fixture SSH preparation failed")) as key, \
             mock.patch.object(lifecycle, "load_secret_inputs") as secrets, \
             mock.patch.object(lifecycle, "prepare_ingress_secrets") as ingress, \
             mock.patch.object(lifecycle, "azure_preflight") as azure, \
             mock.patch.object(lifecycle, "register_providers") as providers, \
             mock.patch.object(lifecycle, "ArmTransport") as arm, \
             mock.patch.object(lifecycle, "deploy_monitoring") as deploy, \
             contextlib.redirect_stderr(error_output):
            self.assertEqual(lifecycle.main(["preprovision"]), 1)
            secret_preparation.assert_called_once_with(self.config, self.paths.root)
            key.assert_called_once_with(self.config, self.paths.root)
            for downstream in (secrets, ingress, azure, providers, arm, deploy):
                downstream.assert_not_called()
        self.assertIn("fixture SSH preparation failed", error_output.getvalue())
        self.assertFalse(self.paths.parameters.exists())
        self.assertEqual(list((self.root / ".local/test/secrets").glob("*")), [])

    def test_secret_failure_or_cancel_stops_preprovision_before_key_and_azure_without_secret_output(self):
        """未入力・非対話端末・キャンセルを合成し、値をエラーへ出さないことも調べる。"""
        failures = (
            ("invalid-input", ValueError("PRIVATE_SECRET_FIXTURE"), "Secret preparation did not complete"),
            ("operating-system", OSError("PRIVATE_SECRET_FIXTURE"), "Secret preparation did not complete"),
            ("unicode", UnicodeError("PRIVATE_SECRET_FIXTURE"), "Secret preparation did not complete"),
            ("interrupted", KeyboardInterrupt("PRIVATE_SECRET_FIXTURE"), "Secret input was cancelled"),
            ("end-of-input", EOFError("PRIVATE_SECRET_FIXTURE"), "Secret input was cancelled"),
            ("non-interactive", lifecycle.SecretPreparationError("Secret input requires an interactive terminal"), "requires an interactive terminal"),
            ("configuration", lifecycle.ConfigurationError("credentialProviders.clientSecretFile: protected input required"), "protected input required"),
        )
        for label, failure, message in failures:
            output = io.StringIO()
            with self.subTest(failure=label), \
                 mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
                 mock.patch.object(lifecycle.shutil, "which", return_value="/fixture/tool"), \
                 mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh"), \
                 mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}), \
                 mock.patch.object(lifecycle, "run", side_effect=self.dependency_command), \
                 mock.patch.object(lifecycle, "prepare_secrets", side_effect=failure) as secret_preparation, \
                 mock.patch.object(lifecycle, "ensure_management_ssh_key") as key, \
                 mock.patch.object(lifecycle, "load_secret_inputs") as secrets, \
                 mock.patch.object(lifecycle, "prepare_ingress_secrets") as ingress, \
                 mock.patch.object(lifecycle, "azure_preflight") as azure, \
                 mock.patch.object(lifecycle, "register_providers") as providers, \
                 mock.patch.object(lifecycle, "ArmTransport") as arm, \
                 mock.patch.object(lifecycle, "deploy_monitoring") as deploy, \
                 contextlib.redirect_stderr(output), contextlib.redirect_stdout(output):
                self.assertEqual(lifecycle.main(["preprovision"]), 1)
                secret_preparation.assert_called_once_with(self.config, self.paths.root)
                for downstream in (key, secrets, ingress, azure, providers, arm, deploy):
                    downstream.assert_not_called()
            self.assertIn(message, output.getvalue())
            self.assertNotIn("PRIVATE_SECRET_FIXTURE", output.getvalue())
            self.assertFalse(self.paths.parameters.exists())
            self.assertEqual(list((self.root / ".local/test/secrets").glob("*")), [])

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
            self.assertEqual(list((self.root / ".local/test/secrets").glob("*")), [])

    def clone_public_inputs(self):
        """公開例のコピーへ架空のAzure DNS参照を設定し、利用者の入力から隔離する。"""
        core = Path(__file__).resolve().parents[2]
        source = (self.root / ".local/test/params.local.json").resolve()
        lifecycle.private_parent(source, self.root)
        (self.root / "infra/config").mkdir(parents=True)
        raw = json.loads((core / "params/test.example.json").read_text(encoding="utf-8"))
        bind_credential_fixture_id(raw)
        for field in ("subscriptionId", "azureTenantId", "entraTenantId"):
            raw[field] = self.config[field]
        for field in ("azureDnsSubscriptionId", "azureDnsManagedIdentityResourceId"):
            raw["certificate"][field] = self.config["certificate"][field]
        raw["gateway"]["authentication"]["entra"] = {
            "audience": "33333333-3333-4333-8333-333333333333",
            "obo": {"keyId": "https://" + f"kv-{self.config['resourceStem']}" + ".vault.azure.net/keys/gateway-obo/" + "1" * 32,
                    "certificateThumbprint": "2" * 64},
        }
        raw["gateway"]["authentication"]["credentialLogin"]["identity"]["clientSecretFile"] = "./secrets/entra-credential-secret.txt"
        raw["services"]["apimInstanceId"] = "abc123"
        raw["credentialProviders"]["graphDelegated"]["clientId"] = "44444444-4444-4444-8444-444444444444"
        raw["configFile"] = "../../infra/config/test.json"
        raw["management"]["sshPublicKeyFile"] = "./secrets/management-ssh.pub"
        raw["credentialProviders"]["graphDelegated"]["clientSecretFile"] = "./secrets/entra-client-secret.txt"
        source.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
        (self.root / "infra/config/test.json").write_bytes((core / "infra/config/test.json").read_bytes())
        return source

    def test_fresh_clone_entra_input_and_non_tty_retry_keep_prepared_azure_params(self):
        self.exercise_prepared_core_hook()

    def test_core_accepts_three_registrations_before_certificates_and_rejects_reused_ids(self):
        source = self.clone_public_inputs()
        params = json.loads(source.read_text())
        auth = params["gateway"]["authentication"]
        auth["entra"].pop("obo")
        self.assertNotIn("authentication", params["portal"])
        self.assertNotIn("codex", params)
        source.write_text(json.dumps(params))
        with contextlib.redirect_stdout(io.StringIO()) as valid:
            self.assertEqual(lifecycle.main(["validate", "--config", str(source)]), 0)
        self.assertTrue(json.loads(valid.getvalue())["valid"])
        credential = auth["credentialLogin"]["identity"]
        distinct_id = credential["clientId"]
        credential["clientId"] = auth["entra"]["audience"]
        source.write_text(json.dumps(params))
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(lifecycle.main(["validate", "--config", str(source)]), 1)
            self.assertIn("requires a distinct client ID for each application", error.getvalue())
        credential["clientId"] = distinct_id
        auth.pop("credentialLogin")
        params["portal"]["apiAuthentication"] = {"enabled": True}
        source.write_text(json.dumps(params))
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(lifecycle.main(["validate", "--config", str(source)]), 1)
            self.assertIn("requires the dedicated API, Tools and Credential registrations", error.getvalue())

    def test_existing_entra_secret_is_reused_without_any_input_or_params_write(self):
        self.exercise_prepared_core_hook(existing_secret=True)

    def exercise_prepared_core_hook(self, *, existing_secret=False):
        """証明書登録前の初回Coreで秘密を準備し、最初のAzure操作直前で停止する。"""
        source = self.clone_public_inputs()
        params = json.loads(source.read_text())
        params["gateway"]["authentication"]["entra"].pop("obo")
        self.assertNotIn("authentication", params["portal"])
        source.write_text(json.dumps(params))
        source_before = (source.read_bytes(), source.stat().st_mtime_ns)
        real_inputs = lifecycle.load_secret_inputs
        client_secret = "SYNTHETIC-HOOK-ENTRA-DO-NOT-DISPLAY"
        identity_secret = "SYNTHETIC-HOOK-CREDENTIAL-DO-NOT-DISPLAY"
        entra = self.paths.secrets / "entra-client-secret.txt"
        identity = self.paths.secrets / "entra-credential-secret.txt"
        dns = self.paths.secrets / "dns-plugin-arguments.json"
        public_key = self.paths.secrets / "management-ssh.pub"
        if existing_secret:
            entra.parent.mkdir(mode=0o700)
            entra.write_text(client_secret + "\n")
            entra.chmod(0o600)
            identity.write_text(identity_secret + "\n")
            identity.chmod(0o600)
        input_cases = (False,) if existing_secret else (True, False)
        for needs_input in input_cases:
            events, output, verified_configurations = [], io.StringIO(), []
            secret_before = (entra.read_bytes(), entra.stat().st_mtime_ns) if entra.exists() else None
            identity_before = (identity.read_bytes(), identity.stat().st_mtime_ns) if identity.exists() else None

            def dependencies(arguments, **kwargs):
                result = self.dependency_command(arguments, **kwargs)
                events.append("dependencies")
                return result

            def confirm(prompt):
                self.assertIn("Entraアプリのシークレットの「値」", output.getvalue())
                self.assertNotIn("Cloudflare API token", output.getvalue())
                events.append("ready-confirm")
                return ""

            def hidden_input(prompt):
                self.assertIn("Entraアプリのシークレットの「値」", prompt)
                events.append("client-input")
                return client_secret if events.count("client-input") == 1 else identity_secret

            def prepare_key(config, root):
                self.assertEqual(root, self.paths.root)
                self.assertTrue(entra.is_file())
                self.assertTrue(identity.is_file())
                self.assertFalse(dns.exists())
                self.assertEqual(config["certificate"]["plugin"], "Azure")
                if not public_key.exists():
                    public_key.write_text(ssh_fixture_key() + "\n")
                events.append("ssh")
                return {"created": False}

            def assert_prepared_inputs(config):
                # The TLS CA file and references are validated with a generated
                # certificate in test_configuration. These cases exercise hook order.
                with mock.patch.object(configuration, "load_firewall_tls_inspection", return_value={}):
                    values = real_inputs(config)
                self.assertEqual(values["credentialProviderSecrets"], {"graph-delegated": client_secret})
                self.assertNotIn("dnsPluginArgumentsJson", values)
                self.assertEqual(values["sshPublicKey"], ssh_fixture_key())
                return values

            def stop_before_azure(config):
                self.assertEqual(config["certificate"]["plugin"], "Azure")
                self.assertEqual(config["certificate"]["azureDnsSubscriptionId"], self.config["certificate"]["azureDnsSubscriptionId"])
                self.assertEqual(config["certificate"]["azureDnsManagedIdentityResourceId"], self.config["certificate"]["azureDnsManagedIdentityResourceId"])
                verified_configurations.append(config)
                events.append("azure-boundary")
                raise lifecycle.DeploymentError("Fixture stops before Azure")

            with self.subTest(needs_input=needs_input), contextlib.ExitStack() as stack:
                selector = stack.enter_context(mock.patch.object(lifecycle, "select_dns_provider"))
                stack.enter_context(mock.patch.object(lifecycle.shutil, "which", return_value="/fixture/tool"))
                stack.enter_context(mock.patch.object(lifecycle, "locate_powershell", return_value="/fixture/pwsh"))
                stack.enter_context(mock.patch.object(lifecycle, "powershell_environment", return_value={"FIXTURE_ONLY": "1"}))
                stack.enter_context(mock.patch.object(lifecycle, "run", side_effect=dependencies))
                stack.enter_context(mock.patch.object(lifecycle, "prepare_secrets", wraps=secret_preparation.prepare_secrets))
                stack.enter_context(mock.patch.object(secret_preparation.sys.stdin, "isatty", return_value=needs_input))
                ready = stack.enter_context(mock.patch("builtins.input", side_effect=confirm if needs_input else AssertionError("No interactive input is allowed")))
                hidden = stack.enter_context(mock.patch.object(secret_preparation.getpass, "getpass", side_effect=hidden_input if needs_input else AssertionError("No secret input is allowed")))
                stack.enter_context(mock.patch.object(lifecycle, "ensure_management_ssh_key", side_effect=prepare_key))
                load_inputs = stack.enter_context(mock.patch.object(lifecycle, "load_secret_inputs"))
                stack.enter_context(mock.patch.object(lifecycle, "prepare_ingress_secrets"))
                stack.enter_context(mock.patch.object(lifecycle, "azure_preflight", side_effect=stop_before_azure))
                providers = stack.enter_context(mock.patch.object(lifecycle, "register_providers"))
                arm = stack.enter_context(mock.patch.object(lifecycle, "ArmTransport"))
                deploy = stack.enter_context(mock.patch.object(lifecycle, "deploy_monitoring"))
                stack.enter_context(contextlib.redirect_stdout(output))
                stack.enter_context(contextlib.redirect_stderr(output))
                self.assertEqual(lifecycle.main(["preprovision", "--config", str(source)]), 1)
                selector.assert_not_called()
                self.assertEqual(ready.call_count, 1 if needs_input else 0)
                self.assertEqual(hidden.call_count, 2 if needs_input else 0)
                for downstream in (load_inputs, providers, arm, deploy):
                    downstream.assert_not_called()
            expected = ["dependencies"]
            if needs_input: expected += ["ready-confirm", "client-input", "client-input"]
            expected.append("ssh")
            self.assertEqual(events, expected + ["azure-boundary"])
            # preprovision reads the prepared deployment secrets only after
            # authentication and the read-only backend TLS gate. Verify
            # the real prepared values separately when the sentinel stops it.
            self.assertEqual(len(verified_configurations), 1)
            assert_prepared_inputs(verified_configurations[0])
            self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), source_before)
            self.assertFalse(dns.exists())
            self.assertNotIn(client_secret, output.getvalue())
            self.assertNotIn(client_secret, source.read_text())
            self.assertNotIn(identity_secret, output.getvalue())
            self.assertEqual(stat.S_IMODE(entra.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(entra.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(identity.stat().st_mode), 0o600)
            if secret_before:
                self.assertEqual((entra.read_bytes(), entra.stat().st_mtime_ns), secret_before)
                self.assertEqual((identity.read_bytes(), identity.stat().st_mtime_ns), identity_before)
            self.assertFalse(self.paths.parameters.exists())

    def test_unsupported_or_incomplete_dns_preconditions_stop_before_local_or_azure_changes(self):
        source = self.clone_public_inputs()
        raw = json.loads(source.read_text())
        config_file = self.root / "infra/config/test.json"
        original_options = config_file.read_bytes()
        cases = ("cloudflare", "route53", "unsupported-mode", "dns-file",
                 "missing-subscription", "zero-subscription", "missing-identity", "invalid-identity")
        for case in cases:
            changed, options = copy.deepcopy(raw), json.loads(original_options)
            certificate = changed["certificate"]
            if case == "cloudflare": certificate["plugin"] = "Cloudflare"
            elif case == "route53": certificate["plugin"] = "Route53"
            elif case == "unsupported-mode": options["certificate"]["mode"] = "unsupported"
            elif case == "dns-file": certificate["pluginArgumentsFile"] = "./secrets/unsupported-input.json"
            elif case == "missing-subscription": certificate.pop("azureDnsSubscriptionId")
            elif case == "zero-subscription": certificate["azureDnsSubscriptionId"] = "00000000-0000-0000-0000-000000000000"
            elif case == "missing-identity": certificate.pop("azureDnsManagedIdentityResourceId")
            else: certificate["azureDnsManagedIdentityResourceId"] = "SYNTHETIC_INVALID_DNS_REFERENCE"
            source.write_text(json.dumps(changed))
            config_file.write_text(json.dumps(options))
            before = (source.read_bytes(), source.stat().st_mtime_ns, config_file.read_bytes())
            output = io.StringIO()
            with self.subTest(case=case), contextlib.ExitStack() as stack:
                boundaries = [stack.enter_context(mock.patch.object(lifecycle, name)) for name in (
                    "prepare_secrets", "ensure_management_ssh_key", "load_secret_inputs", "prepare_ingress_secrets",
                    "azure_preflight", "register_providers", "ArmTransport", "deploy_monitoring")]
                input_call = stack.enter_context(mock.patch("builtins.input", side_effect=AssertionError("No input is allowed")))
                hidden = stack.enter_context(mock.patch.object(secret_preparation.getpass, "getpass", side_effect=AssertionError("No secret read is allowed")))
                stack.enter_context(contextlib.redirect_stdout(output))
                stack.enter_context(contextlib.redirect_stderr(output))
                self.assertEqual(lifecycle.main(["preprovision", "--config", str(source)]), 1)
                for boundary in boundaries: boundary.assert_not_called()
                input_call.assert_not_called(); hidden.assert_not_called()
            self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns, config_file.read_bytes()), before)
            self.assertFalse(self.paths.parameters.exists())
            self.assertEqual(list(self.paths.secrets.glob("*")), [])
            self.assertNotIn("SYNTHETIC_INVALID_DNS_REFERENCE", output.getvalue())


if __name__ == "__main__":
    unittest.main()
