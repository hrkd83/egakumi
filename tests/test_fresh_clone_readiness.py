"""公開資材だけの隔離コピーから、構築入力を組み立てられることを確認する。

既存端末のparams・秘密・状態・portable tool・Python packageを使わない。
別Pythonプロセスで本物の検査とARM引数生成を通すが、Azure構築や疎通の
合格を示す試験ではない。ネットワークと外部コマンドは監査hookで禁止する。
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


CORE = Path(__file__).resolve().parents[1]
SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
TENANT = "22222222-2222-4222-8222-222222222222"
GATEWAY = "33333333-3333-4333-8333-333333333333"
PUBLIC_HOST = "gateway.fresh-clone.example"
PROTECTED_DIRECTORIES = ("secrets", ".state", ".tools", ".azure", "Archives")

# サイトパッケージも親のPYTHONPATHも使わず、このコピーのscriptsだけをimportする。
# 予期しないAzure要求・CLI起動が将来追加されても、実行前に試験を失敗させる。
ISOLATED_CHECK = r"""
import contextlib
import io
import json
from pathlib import Path
import sys

root, source, rendered, arguments = map(Path, sys.argv[1:])
allowed_runtime_files = {source, rendered, arguments}
sys.path.insert(0, str(root / "scripts"))

def local_only(event, arguments):
    if event.startswith("socket.") or event in ("subprocess.Popen", "os.system"):
        raise AssertionError("Fresh-clone input validation must stay local")
    if event == "open" and arguments and isinstance(arguments[0], (str, bytes)):
        value = arguments[0].decode() if isinstance(arguments[0], bytes) else arguments[0]
        path = Path(value)
        if path.is_absolute() and path.is_relative_to(root):
            relative = path.relative_to(root)
            if relative.parts and relative.parts[0] in ("secrets", ".state", ".tools", ".azure", "Archives"):
                raise AssertionError("Public input validation must not read protected local inputs")
            if relative.parts and relative.parts[0] == ".local" and path not in allowed_runtime_files:
                raise AssertionError("Fresh validation must not read another runtime input")

sys.addaudithook(local_only)
from deployment.helpers import configuration
from deployment import lifecycle
from deployment.helpers import prepare_secrets

for module in (configuration, lifecycle):
    if not Path(module.__file__).resolve().is_relative_to(root):
        raise AssertionError("The isolated copy must provide its own implementation")
if any(actual != root for actual in (lifecycle.ROOT, prepare_secrets.ROOT)):
    raise AssertionError("Moved deployment modules must keep using the repository root")
normalized = configuration.load_configuration(source)
workspace = lifecycle.local_paths(normalized, root)
if (workspace.params != source or workspace.parameters != root / ".local" / normalized["environmentName"] / "generated/main.parameters.json"
        or workspace.state != source.parent / "state"):
    raise AssertionError("Deployment inputs and state must stay in the selected environment")
messages = io.StringIO()
with contextlib.redirect_stdout(messages), contextlib.redirect_stderr(messages):
    if lifecycle.main(["validate", "--config", str(source)]) != 0:
        raise AssertionError("Fresh-clone lifecycle validation failed: " + messages.getvalue())
    if configuration.main(["render", "--config", str(source), "--output", str(rendered)]) != 0:
        raise AssertionError("Fresh-clone public rendering failed: " + messages.getvalue())
arm = lifecycle.parameters(normalized, "core")
if arm["parameters"]["configuration"]["value"] != json.loads(rendered.read_text()):
    raise AssertionError("Rendered public configuration differs from the actual ARM arguments")
arguments.write_text(json.dumps(arm), encoding="utf-8")
print(json.dumps({"valid": True, "resourceStem": normalized["resourceStem"],
    "apiNames": [api["name"] for api in normalized["apiDefinitions"]],
    "modelNames": [item["name"] for item in normalized["services"]["modelDeployments"]],
    "apiPublicationStatus": normalized["apiPublicationStatus"]}))
"""


class FreshCloneReadinessTests(unittest.TestCase):
    """新しい利用者が公開例を編集した時の、入口から出力までの契約を検査する。"""

    def setUp(self):
        self.enterContext(mock.patch.dict("os.environ", {"PYTHONDONTWRITEBYTECODE": "1"}))
        self.directory = tempfile.TemporaryDirectory(prefix="core-public-clone-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        # 読む元を公開ソースの種類へ限定する。*.local.jsonやprivate領域を探索しない。
        patterns = ("scripts/**/*.py", "config/test.json", "config/production.json",
                    "params/*.example.json", "schemas/*.schema.json", "infra/log-privacy.json",
                    "apps/gateway/apim/apis/*.json", "apps/gateway/apim/model-capabilities.json",
                    "apps/gateway/apim/public-bindings.json")
        for pattern in patterns:
            for source in CORE.glob(pattern):
                target = self.root / source.relative_to(CORE)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        self.standards_before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (self.root / "config").glob("*.json")}
        self.assert_no_private_inputs()

    def assert_no_private_inputs(self):
        """既存端末の入力を持ち込まず、検査だけで秘密や状態を生成させない。"""
        for name in PROTECTED_DIRECTORIES:
            self.assertFalse((self.root / name).exists(), name + " must not be required for public validation")
        self.assertFalse((self.root / ".git").exists())
        for environment in (self.root / ".local").glob("*"):
            for name in ("secrets", "state", "generated"):
                self.assertFalse((environment / name).exists(),
                                 "Input validation must not create runtime secrets, state or parameters")

    def enterprise_parameters(self, profile):
        """公開例へ架空の企業情報だけを設定し、標準configは編集しない。"""
        raw = json.loads((self.root / "params" / (profile + ".example.json")).read_text())
        raw.update(subscriptionId=SUBSCRIPTION, azureTenantId=TENANT, entraTenantId=TENANT)
        self.assertEqual(raw["gateway"]["authentication"]["subscriptionKeyHeader"], "api-key")
        self.assertEqual(raw["gateway"]["authentication"]["credentialLogin"]["providerNames"], ["graph-delegated"])
        self.assertNotIn("oauthTenantId", raw["credentialProviders"][0])
        raw["credentialProviders"][0]["clientId"] = GATEWAY
        raw["portal"]["publicHostName"] = "portal.fresh-clone.example"
        raw["gateway"]["publicHostName"] = PUBLIC_HOST
        raw["gateway"]["grpc"]["publicHostName"] = PUBLIC_HOST
        raw["certificate"]["domainNames"] = ["*.fresh-clone.example"]
        raw["certificate"]["contactEmail"] = "operator@fresh-clone.example"
        raw["services"].update(publisherName="Fresh clone organization", publisherEmail="operator@fresh-clone.example")
        raw["monitoring"]["operatorEmailAddresses"] = ["operator@fresh-clone.example"]
        raw["configFile"] = "../../config/" + profile + ".json"
        raw["management"]["sshPublicKeyFile"] = "./secrets/management-ssh.pub"
        raw["certificate"]["pluginArgumentsFile"] = "./secrets/dns-plugin-arguments.json"
        raw["credentialProviders"][0]["clientSecretFile"] = "./secrets/entra-client-secret.txt"
        return raw

    def check_in_isolated_process(self, profile, parameters):
        """-I/-Sの別プロセスで実loader・CLI・ARM引数生成を一度ずつ通す。"""
        source = self.root / ".local" / parameters["environmentName"] / "params.local.json"
        (self.root / ".local").mkdir(mode=0o700, exist_ok=True)
        source.parent.mkdir(mode=0o700, exist_ok=True)
        source.write_text(json.dumps(parameters, ensure_ascii=False, indent=2) + "\n")
        evidence = source.parent / "evidence"
        evidence.mkdir(mode=0o700, exist_ok=True)
        rendered, arguments = evidence / "rendered.json", evidence / "arm-arguments.json"
        process = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", ISOLATED_CHECK,
            str(self.root), str(source), str(rendered), str(arguments)],
            cwd=self.root, text=True, capture_output=True, timeout=30,
            env={"PATH": str(self.root / "no-external-tools"), "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        report = json.loads(process.stdout)
        public = json.loads(rendered.read_text())
        arm = json.loads(arguments.read_text())
        self.assertTrue(report["valid"])
        self.assertEqual(arm["parameters"]["configuration"]["value"], public)
        self.assertEqual(public["subscriptionId"], SUBSCRIPTION)
        self.assertEqual(public["azureTenantId"], TENANT)
        self.assertEqual(public["entraTenantId"], TENANT)
        self.assertEqual(public["gateway"]["publicHostName"], PUBLIC_HOST)
        self.assertEqual(public["credentialProviders"][0]["name"], "graph-delegated")
        self.assertEqual(public["credentialProviders"][0]["clientId"], GATEWAY)
        self.assertEqual(public["credentialProviders"][0]["oauthTenantId"], TENANT)
        self.assertEqual(public["credentialProviders"][0]["identityEndpoint"], "https://graph.microsoft.com/v1.0/me")
        self.assertEqual(public["credentialProviders"][0]["connectionNames"], [])
        self.assertEqual(set(public["resourceGroupNames"]), {"id", "nw", "monitor", "svc", "maint"})
        self.assertEqual(len(public["network"]["addressSpaces"]), 4)
        self.assertNotIn("configFile", public)
        for field in ("sshPublicKeyFile", "pluginArgumentsFile", "clientSecretFile", "preparedPfxFile", "preparedPfxPasswordFile"):
            self.assertNotIn('"' + field + '"', arguments.read_text())
        for secure_input in ("sshPublicKey", "dnsPluginArgumentsJson", "credentialProviderSecrets"):
            self.assertNotIn(secure_input, arm["parameters"])
        self.assertEqual({path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (self.root / "config").glob("*.json")}, self.standards_before)
        self.assert_no_private_inputs()
        return report, public

    def test_test_and_production_public_params_validate_and_render_without_local_assets(self):
        """両profileとも新規paramsだけで検査でき、初期モデルを正本から取り込める。"""
        for profile in ("test", "production"):
            with self.subTest(profile=profile):
                report, public = self.check_in_isolated_process(profile, self.enterprise_parameters(profile))
                standard = json.loads((self.root / "config" / (profile + ".json")).read_text())
                self.assertEqual(report["modelNames"], [item["name"] for item in standard["services"]["modelDeployments"]])
                self.assertEqual(report["apiNames"], [])
                self.assertTrue(public["publishApis"])
                self.assertEqual(public["network"]["firewall"]["sku"], "Standard" if profile == "test" else "Premium")

    def test_api_definitions_project_only_registration_fields_into_enterprise_params(self):
        """APIごとの定義を登録しても、モデル・CMはparams/configで管理する。"""
        # 登録用JSONは正本のモデル上限を展開してからparamsへ渡す。
        prepare = subprocess.run([sys.executable, "-I", "-S", "-B", "-c",
            "import json, sys; from pathlib import Path; sys.path.insert(0, 'scripts'); "
            "from standard_apis import load_api_definitions; "
            "print(json.dumps({'apiDefinitions': [api for name in "
            "('openai-inference-v1', 'pii-v1', 'graph-profile-v1') for api in "
            "load_api_definitions(Path('apps/gateway/apim/apis') / (name + '.json'))]}))"],
            cwd=self.root, text=True, capture_output=True, timeout=30, check=True)
        template = json.loads(prepare.stdout)
        replacements = {"TENANT_ID": TENANT, "CM_CLIENT_ID": GATEWAY,
            "CM_CLIENT_SECRET_FILE": "./secrets/entra-client-secret.txt",
            "PUBLIC_HOST_NAME": self.enterprise_parameters("test")["gateway"]["publicHostName"]}
        payload = json.dumps(template)
        for name, value in replacements.items():
            payload = payload.replace("__" + name + "__", value)
        self.assertIsNone(re.search(r"__[A-Z][A-Z0-9_]+__", payload))
        bound = json.loads(payload)
        for profile in ("test", "production"):
            with self.subTest(profile=profile):
                params = self.enterprise_parameters(profile)
                original_services = copy.deepcopy(params["services"])
                params["apiDefinitions"] = copy.deepcopy(bound["apiDefinitions"])
                params["publishApis"] = True
                params["network"]["firewall"]["apimAllowedFqdns"] = ["graph.microsoft.com"]
                self.assertEqual(params["services"], original_services)
                report, public = self.check_in_isolated_process(profile, params)
                self.assertTrue(public["publishApis"])
                self.assertEqual(report["apiNames"], [item["name"] for item in bound["apiDefinitions"]])
                self.assertEqual(public["credentialProviders"][0]["clientId"], GATEWAY)
                self.assertEqual(public["gateway"]["authentication"]["subscriptionKeyHeader"], "api-key")
                self.assertEqual(public["gateway"]["authentication"]["credentialLogin"]["providerNames"], ["graph-delegated"])
                self.assertTrue(all(api["authorization"]["mode"] == "subscriptionKey" for api in public["apiDefinitions"]))
                standard = json.loads((self.root / "config" / (profile + ".json")).read_text())
                self.assertEqual(public["services"]["modelDeployments"], standard["services"]["modelDeployments"])


if __name__ == "__main__":
    unittest.main()
