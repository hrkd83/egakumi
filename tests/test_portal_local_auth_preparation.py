"""ローカル認証の準備が秘密値・既存設定・Azureへ影響しないことを検査する。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_portal_local_auth as preparation


class LocalPortalPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.secret = self.root / "entra-client-secret.txt"
        self.secret.write_text('test.secret$with"special\\characters', encoding="utf-8")
        self.secret.chmod(0o600)
        self.configuration = {
            "portalAuthentication": {"credentialProviderName": "graph-delegated"},
            "credentialProviders": [{
                "name": "graph-delegated",
                "clientId": "00000000-0000-0000-0000-000000000001",
                "oauthTenantId": "00000000-0000-0000-0000-000000000002",
                "clientSecretFile": str(self.secret),
            }],
        }
        # ACLのOS差を避け、実ファイルは各テストの一時フォルダーだけに限定する。
        self.protection = patch.object(preparation, "protect_new_file", lambda path: path.chmod(0o600))
        self.protection.start()
        self.addCleanup(self.protection.stop)

    def prepare(self, **arguments):
        return preparation.prepare(self.configuration, self.root, **arguments)

    def test_plan_does_not_read_secret_or_create_files(self):
        self.secret.unlink()
        result = self.prepare(execute=False, accept_sql_eula=False)
        self.assertEqual("create", result["action"])
        self.assertEqual(0, result["azureWrites"])
        self.assertFalse((self.root / "apps").exists())
        self.assertFalse((self.root / "secrets").exists())

    def test_execute_requires_explicit_license_acceptance(self):
        with self.assertRaisesRegex(preparation.PreparationError, "EULA"):
            self.prepare(execute=True, accept_sql_eula=False)
        self.assertFalse((self.root / "apps").exists())

    def test_created_pair_separates_runtime_and_administrator_credentials(self):
        result = self.prepare(execute=True, accept_sql_eula=True)
        runtime = preparation.read_environment(Path(result["runtimeEnvironmentFile"]))
        admin = preparation.read_environment(Path(result["sqlAdminEnvironmentFile"]))
        self.assertEqual("portal_app", runtime["PORTAL_SQL_USER"])
        self.assertEqual("sa", admin["PORTAL_SQL_USER"])
        self.assertEqual(runtime["PORTAL_SQL_PASSWORD"], admin["PORTAL_SQL_RUNTIME_PASSWORD"])
        self.assertNotEqual(runtime["PORTAL_SQL_PASSWORD"], admin["MSSQL_SA_PASSWORD"])
        self.assertEqual(self.secret.read_text(), runtime["MICROSOFT_CLIENT_SECRET"])
        self.assertNotIn("MICROSOFT_CLIENT_SECRET", admin)
        self.assertNotIn(self.secret.read_text(), json.dumps(result))
        self.assertGreaterEqual(len(runtime["BETTER_AUTH_SECRET"]), 32)
        self.assertFalse(result["containerStarted"])

    def test_repeated_execution_preserves_every_byte(self):
        first = self.prepare(execute=True, accept_sql_eula=True)
        paths = [Path(first[key]) for key in ("runtimeEnvironmentFile", "sqlAdminEnvironmentFile")]
        before = [path.read_bytes() for path in paths]
        second = self.prepare(execute=True, accept_sql_eula=True)
        self.assertEqual("reuse", second["action"])
        self.assertEqual(before, [path.read_bytes() for path in paths])

    def test_reuse_preserves_optional_gateway_settings_without_exposing_values(self):
        first = self.prepare(execute=True, accept_sql_eula=True)
        paths = [Path(first[key]) for key in ("runtimeEnvironmentFile", "sqlAdminEnvironmentFile")]
        runtime = preparation.read_environment(paths[0])
        runtime.update({
            "PORTAL_GATEWAY_URL": "https://gateway.example.invalid",
            "PORTAL_GATEWAY_SUBSCRIPTION_KEY_FILE": str(self.root / "private-model-users.key"),
        })
        paths[0].write_text(preparation.encode_environment(runtime), encoding="utf-8")
        before = [path.read_bytes() for path in paths]
        second = self.prepare(execute=True, accept_sql_eula=True)
        self.assertEqual("reuse", second["action"])
        self.assertEqual(before, [path.read_bytes() for path in paths])
        self.assertNotIn(runtime["PORTAL_GATEWAY_URL"], json.dumps(second))
        self.assertNotIn(runtime["PORTAL_GATEWAY_SUBSCRIPTION_KEY_FILE"], json.dumps(second))

    def test_reuse_still_rejects_unexpected_or_missing_runtime_settings(self):
        first = self.prepare(execute=True, accept_sql_eula=True)
        path = Path(first["runtimeEnvironmentFile"])
        original = preparation.read_environment(path)
        for unknown in ("UNEXPECTED_SETTING", "PORTAL_GATEWAY_SUBSCRIPTION_KEY"):
            with self.subTest(unknown=unknown):
                path.write_text(preparation.encode_environment({**original, unknown: "private-value"}), encoding="utf-8")
                previous = path.read_bytes()
                with self.assertRaisesRegex(preparation.PreparationError, "generated local format"):
                    self.prepare(execute=True, accept_sql_eula=True)
                self.assertEqual(previous, path.read_bytes())
        incomplete = dict(original)
        incomplete.pop("BETTER_AUTH_SECRET")
        path.write_text(preparation.encode_environment(incomplete), encoding="utf-8")
        previous = path.read_bytes()
        with self.assertRaisesRegex(preparation.PreparationError, "generated local format"):
            self.prepare(execute=True, accept_sql_eula=True)
        self.assertEqual(previous, path.read_bytes())

    def test_incomplete_pair_is_not_repaired_or_overwritten(self):
        target = self.root / "apps" / "portal" / ".env.local"
        target.parent.mkdir(parents=True)
        target.write_text("USER_SETTING=keep\n", encoding="utf-8")
        with self.assertRaisesRegex(preparation.PreparationError, "Only one"):
            self.prepare(execute=True, accept_sql_eula=True)
        self.assertEqual("USER_SETTING=keep\n", target.read_text())
        self.assertFalse((self.root / "secrets").exists())

    def test_configuration_change_is_reported_without_rewriting_files(self):
        first = self.prepare(execute=True, accept_sql_eula=True)
        path = Path(first["runtimeEnvironmentFile"])
        previous = path.read_bytes()
        self.configuration["credentialProviders"][0]["clientId"] = "different-client"
        with self.assertRaisesRegex(preparation.PreparationError, "selected configuration"):
            self.prepare(execute=True, accept_sql_eula=True)
        self.assertEqual(previous, path.read_bytes())

    def test_dotenv_round_trip_preserves_dollars_quotes_and_backslashes(self):
        value = 'literal$VALUE \\"double"'
        path = self.root / "roundtrip.env"
        path.write_text(preparation.encode_environment({"VALUE": value}), encoding="utf-8")
        path.chmod(0o600)
        self.assertEqual({"VALUE": value}, preparation.read_environment(path))
        self.assertIn("\\$VALUE", path.read_text())

    def test_partial_creation_failure_cleans_only_new_files(self):
        first, second = self.root / "first.env", self.root / "second.env"
        with patch.object(preparation, "protect_new_file", side_effect=[None, OSError("no permission")]):
            with self.assertRaises(OSError):
                preparation.write_new_pair({first: {"VALUE": "one"}, second: {"VALUE": "two"}})
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())


if __name__ == "__main__":
    unittest.main()
