"""ローカルPortalの起動境界を合成設定と子プロセスmockで確認する。"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_portal_local_auth as preparation
import run_portal_local as launcher


class LocalPortalLauncherTests(unittest.TestCase):
    """実サービスを起動せず、環境の選択・非公開出力・終了値を検証する。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "repository"
        (self.root / "apps/portal").mkdir(parents=True)
        self.secret = Path(self.temporary.name) / "existing-secret.txt"
        self.secret.write_text("independent-synthetic-client-secret", encoding="utf-8")
        self.secret.chmod(0o600)
        self.configuration = {
            "environmentName": "example",
            "portalAuthentication": {"credentialProviderName": "graph-delegated"},
            "credentialProviders": [{
                "name": "graph-delegated", "clientId": "<ENTRA_CLIENT_ID>",
                "oauthTenantId": "<ENTRA_TENANT_ID>", "clientSecretFile": str(self.secret),
            }],
        }
        with patch.object(preparation, "protect_new_file", lambda path: path.chmod(0o600)):
            result = preparation.prepare(self.configuration, self.root,
                                         execute=True, accept_sql_eula=True)
        self.runtime_path = Path(result["runtimeEnvironmentFile"])
        self.admin_path = Path(result["sqlAdminEnvironmentFile"])
        self.runtime = preparation.read_environment(self.runtime_path)
        self.admin = preparation.read_environment(self.admin_path)

    def child(self, lines=(), returncode=0):
        process = MagicMock()
        process.__enter__.return_value = process
        process.stdout = iter(lines)
        process.wait.return_value = returncode
        return process

    def test_runtime_environment_is_injected_without_shell_or_admin_credentials(self):
        command = ["node", "synthetic-script.ts", "literal$argument"]
        with patch.dict(os.environ, {"PARENT_FIXTURE": "keep"}, clear=True), \
                patch.object(launcher.subprocess, "Popen", return_value=self.child(returncode=17)) as start:
            code = launcher.run(self.configuration, self.root, command)
            self.assertEqual({"PARENT_FIXTURE": "keep"}, dict(os.environ))
        self.assertEqual(17, code)
        arguments, options = start.call_args
        self.assertEqual((command,), arguments)
        self.assertFalse(options["shell"])
        self.assertEqual(self.root / "apps/portal", options["cwd"])
        self.assertEqual({"PARENT_FIXTURE": "keep", **self.runtime}, options["env"])
        self.assertNotIn("MSSQL_SA_PASSWORD", options["env"])
        self.assertEqual(launcher.subprocess.PIPE, options["stdout"])
        self.assertEqual(launcher.subprocess.STDOUT, options["stderr"])

    def test_admin_uses_only_the_admin_file_and_exposes_its_path_to_compose(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(launcher, "read_environment", wraps=preparation.read_environment) as read, \
                patch.object(launcher.subprocess, "Popen", return_value=self.child()) as start:
            self.assertEqual(0, launcher.run(self.configuration, self.root,
                                           ["npm", "run", "db:bootstrap-local"], admin=True))
        read.assert_called_once_with(self.admin_path)
        values = start.call_args.kwargs["env"]
        self.assertEqual("sa", values["PORTAL_SQL_USER"])
        self.assertEqual(self.admin["MSSQL_SA_PASSWORD"], values["PORTAL_SQL_PASSWORD"])
        self.assertEqual(str(self.admin_path), values["PORTAL_LOCAL_SQL_ENV_FILE"])
        self.assertNotIn("MICROSOFT_CLIENT_SECRET", values)

    def test_known_injected_secrets_are_redacted_while_logs_are_relayed(self):
        keys = ("MICROSOFT_CLIENT_SECRET", "BETTER_AUTH_SECRET", "PORTAL_SQL_PASSWORD")
        lines = ["Ready for synthetic requests.\n",
                 " ".join(self.runtime[key] for key in keys) + "\n"]
        output = io.StringIO()
        with patch.object(launcher.subprocess, "Popen", return_value=self.child(lines)), \
                contextlib.redirect_stdout(output):
            self.assertEqual(0, launcher.run(self.configuration, self.root, ["npm", "run", "dev"]))
        text = output.getvalue()
        self.assertIn("Ready for synthetic requests.", text)
        self.assertEqual(3, text.count("[REDACTED]"))
        self.assertTrue(all(self.runtime[key] not in text for key in keys))

    def test_legacy_dotenv_is_not_read_and_no_child_is_started(self):
        legacy = self.root / "apps/portal/.env.local"
        legacy.write_text("OWNER_SETTING=keep\n", encoding="utf-8")
        with patch.object(launcher, "read_environment", side_effect=AssertionError("settings read")), \
                patch.object(launcher.subprocess, "Popen") as start:
            with self.assertRaisesRegex(preparation.PreparationError, "migrate"):
                launcher.run(self.configuration, self.root, ["npm", "run", "dev"])
        start.assert_not_called()
        self.assertEqual("OWNER_SETTING=keep\n", legacy.read_text())

    def test_missing_or_insecure_settings_do_not_start_the_command(self):
        self.runtime_path.chmod(0o644)
        with patch.object(launcher.subprocess, "Popen") as start:
            with self.assertRaisesRegex(preparation.PreparationError, "owner-only"):
                launcher.run(self.configuration, self.root, ["npm", "run", "dev"])
            self.runtime_path.unlink()
            with self.assertRaisesRegex(preparation.PreparationError, "regular files"):
                launcher.run(self.configuration, self.root, ["npm", "run", "dev"])
        start.assert_not_called()

    def test_cli_selects_config_admin_mode_and_preserves_argument_boundaries(self):
        with patch.object(launcher, "load_configuration", return_value=self.configuration) as load, \
                patch.object(launcher, "run", return_value=3) as run:
            self.assertEqual(3, launcher.main([
                "--config", ".local/example/params.local.json", "--admin", "--",
                "node", "synthetic script.ts", "literal$argument",
            ]))
        load.assert_called_once_with(".local/example/params.local.json")
        run.assert_called_once_with(self.configuration, launcher.REPOSITORY,
                                    ["node", "synthetic script.ts", "literal$argument"], admin=True)

    def test_empty_command_is_refused_without_reading_settings(self):
        with patch.object(launcher, "read_environment", side_effect=AssertionError("settings read")), \
                patch.object(launcher.subprocess, "Popen") as start:
            with self.assertRaisesRegex(preparation.PreparationError, "command after"):
                launcher.run(self.configuration, self.root, [])
        start.assert_not_called()

    def test_argument_errors_do_not_repeat_candidate_values(self):
        candidate = "independent-synthetic-secret-argument"
        output = io.StringIO()
        with contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as stopped:
            launcher.main(["--unknown=" + candidate])
        self.assertEqual(2, stopped.exception.code)
        self.assertNotIn(candidate, output.getvalue())
        self.assertIn("invalid arguments", output.getvalue())


class PortalLocalCommandContractTests(unittest.TestCase):
    """公開コマンドが旧設定の自動読込へ戻らないことを確認する。"""

    def test_package_commands_take_settings_from_the_launcher_environment(self):
        root = Path(__file__).resolve().parents[1]
        scripts = json.loads((root / "apps/portal/package.json").read_text())["scripts"]
        for name in ("dev", "start", "db:migrate", "db:bootstrap-local"):
            with self.subTest(command=name):
                self.assertNotIn("--env-file", scripts[name])
                self.assertIn("--conditions=react-server", scripts[name])
        self.assertIn("--use-env-proxy", scripts["dev"])

    def test_compose_requires_the_selected_private_admin_environment_file(self):
        root = Path(__file__).resolve().parents[1]
        compose = (root / "apps/portal/compose.local.yml").read_text()
        self.assertIn("${PORTAL_LOCAL_SQL_ENV_FILE:?", compose)
        self.assertNotIn("../../secrets/", compose)
        self.assertIn('"127.0.0.1:14330:1433"', compose)


if __name__ == "__main__":
    unittest.main()
