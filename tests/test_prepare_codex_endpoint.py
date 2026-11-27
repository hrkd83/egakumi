"""Exercise the protected Codex bundle CLI with independent synthetic values."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest

from configuration_fixtures import CORE
from deployment.helpers.paths import LocalPaths, private_parent
from deployment.helpers.private_permissions import protect_new_path


SCRIPT = CORE / "scripts" / "prepare_codex_endpoint.py"
TENANT = "11111111-1111-1111-1111-111111111111"
API = "22222222-2222-2222-2222-222222222222"
CODEX = "33333333-3333-3333-3333-333333333333"
CATALOG_PATH = r"C:\ProgramData\OpenAI\Codex\models.json"


class CodexEndpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = LocalPaths(Path(temporary.name), "test")
        self.output = self.runtime.root / "generated" / "codex" / "pilot01"
        self.params = {
            "environmentName": "test",
            "entraTenantId": TENANT,
            "gateway": {"publicHostName": "gateway.example.com", "authentication": {"entra": {"audience": API}}},
            "codex": {"clientId": CODEX},
        }
        self.save_json(self.runtime.params, self.params)

    def save_json(self, destination, data):
        private_parent(destination, self.runtime.repository)
        with destination.open("x", encoding="utf-8") as stream:
            protect_new_path(destination)
            json.dump(data, stream)

    def prepare(self, mode, *options):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--runtime-root", str(self.runtime.repository),
             "--environment", "test", "--revision", "pilot01", "--mode", mode,
             "--model", "gpt-6-sol", *options],
            capture_output=True, text=True, check=False,
        )

    def read_toml(self, name):
        return tomllib.loads((self.output / name).read_text(encoding="utf-8"))

    def test_shared_defaults_keep_provider_selection_and_catalog_in_company_profile(self):
        catalog = {"models": [{"slug": "gpt-6-sol"}, {"slug": "codex-auto-review"}]}
        self.save_json(self.runtime.secrets / "codex-models.json", catalog)
        self.save_json(self.runtime.secrets / "codex-endpoint.json", {"catalogInstallPath": CATALOG_PATH})
        result = self.prepare("side-by-side", "--windows-catalog", "--redirect-port", "43128")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "CODEX_BUNDLE_READY\n", ""))

        base = self.read_toml("config.toml")
        profile = self.read_toml("aigateway.config.toml")
        self.assertEqual(set(base), {"model_providers"})
        self.assertEqual(profile, {"model_provider": "aigateway", "model": "gpt-6-sol", "model_catalog_json": CATALOG_PATH})
        self.assertEqual(base["model_providers"]["aigateway"], {
            "name": "Egakumi Gateway",
            "base_url": "https://gateway.example.com/models/openai/v1",
            "wire_api": "responses",
            "requires_openai_auth": False,
            "gateway_oauth": {
                "authorization_url": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize",
                "token_url": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
                "client_id": CODEX,
                "scopes": [f"api://{API}/Gateway.Invoke", "offline_access"],
                "redirect_port": 43128,
            },
        })
        self.assertEqual(set(file.name for file in self.output.iterdir()), {"config.toml", "aigateway.config.toml", "models.json"})
        self.assertEqual(json.loads((self.output / "models.json").read_text(encoding="utf-8")), catalog)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in self.output.iterdir()))

    def test_dedicated_requirements_restate_entire_provider_and_preserve_existing_revision(self):
        result = self.prepare("dedicated", "--enforce-provider")
        self.assertEqual(result.returncode, 0, result.stderr)
        base = self.read_toml("config.toml")
        self.assertEqual(self.read_toml("requirements.toml"), {
            "model_provider": "aigateway", "model_providers": base["model_providers"],
        })
        self.assertEqual(base["model"], "gpt-6-sol")
        self.assertEqual(base["model_providers"]["aigateway"]["gateway_oauth"]["redirect_port"], 43127)
        result = self.prepare("dedicated", "--redirect-port", "43129")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.read_toml("config.toml"), base)

    def test_rejects_nonexistent_responses_model_and_shared_provider_enforcement(self):
        for options in (("--model", "other-model"), ("--enforce-provider",), ("--redirect-port", "0")):
            with self.subTest(options=options):
                self.assertEqual(self.prepare("side-by-side", *options).returncode, 1)
                self.assertFalse(self.output.exists())

    def test_catalog_cannot_carry_credentials_in_nested_metadata(self):
        catalog = {"models": [{"slug": "gpt-6-sol", "metadata": {"client-secret": "fixture"}}]}
        self.save_json(self.runtime.secrets / "codex-models.json", catalog)
        self.save_json(self.runtime.secrets / "codex-endpoint.json", {"catalogInstallPath": CATALOG_PATH})
        result = self.prepare("dedicated", "--windows-catalog")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertFalse(self.output.exists())

    def test_omitted_entra_tenant_uses_same_azure_tenant_as_deployment(self):
        self.runtime.params.unlink()
        self.params["azureTenantId"] = self.params.pop("entraTenantId")
        self.save_json(self.runtime.params, self.params)
        result = self.prepare("dedicated")
        self.assertEqual(result.returncode, 0, result.stderr)
        oauth = self.read_toml("config.toml")["model_providers"]["aigateway"]["gateway_oauth"]
        self.assertEqual(oauth["token_url"], f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token")

    def test_rejects_wrong_oauth_role_and_does_not_print_protected_values(self):
        self.runtime.params.unlink()
        self.params["codex"]["clientId"] = API
        self.save_json(self.runtime.params, self.params)
        result = self.prepare("dedicated")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertNotIn(API, result.stderr)
        self.assertNotIn(str(self.runtime.repository), result.stderr)
        self.assertFalse(self.output.exists())

    def test_rejects_parameters_from_another_environment(self):
        self.runtime.params.unlink()
        self.params["environmentName"] = "prod"
        self.save_json(self.runtime.params, self.params)
        self.assertEqual(self.prepare("dedicated").returncode, 1)
        self.assertFalse(self.output.exists())

    @unittest.skipIf(os.name == "nt", "POSIX mode check")
    def test_rejects_readable_input_and_generated_directory_link(self):
        self.runtime.params.chmod(0o644)
        self.assertEqual(self.prepare("dedicated").returncode, 1)
        self.runtime.params.chmod(0o600)
        outside = self.runtime.repository / "outside"
        outside.mkdir()
        (self.runtime.root / "generated").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.prepare("dedicated").returncode, 1)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
