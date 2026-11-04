""".localの合成ARM入力をnative azd用bridgeでコンパイルする。

公式Bicep CLIだけをオフラインで実行し、秘密相当の合成値がARM templateへ
埋め込まれず、parameter JSONだけへ渡ることを確認する。Azureへ接続しない。
"""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from bicep_helpers import bicep_executable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from deployment.helpers.azd_parameters import (
    ARM_PARAMETERS_ENV, ARM_PARAMETER_DEFAULTS, arm_parameters_reference,
    complete_arm_parameters,
)
from deployment.helpers.configuration import normalize_configuration
from deployment.lifecycle import parameters as lifecycle_parameters
from configuration_fixtures import fixture


def parameter_document(configuration=None):
    """production producerの正規metadataに、最小の独立した合成入力を組み合わせる。"""
    document = lifecycle_parameters(normalize_configuration(fixture()), "core")
    document["parameters"] = {"configuration": {"value": configuration or {}}}
    return document


class ArmParameterContractTests(unittest.TestCase):
    """生成入力と既存mainの境界を、実値なしで検査する。"""

    def test_reference_is_relative_and_cannot_cross_environment_directories(self):
        self.assertEqual(arm_parameters_reference("test-a"),
                         ".local/test-a/generated/main.parameters.json")
        for invalid in ("", "../other", "/tmp/input", "test/a", "test\\a", "UPPER", "a" * 13, None):
            with self.subTest(case=invalid), self.assertRaises(ValueError):
                arm_parameters_reference(invalid)

    def test_defaults_do_not_mutate_the_input_or_share_nested_values(self):
        raw = parameter_document({"marker": "synthetic"})
        original = copy.deepcopy(raw)
        completed = complete_arm_parameters(raw)
        self.assertEqual(raw, original)
        self.assertEqual(completed["$schema"], raw["$schema"])
        self.assertEqual(completed["contentVersion"], raw["contentVersion"])
        completed["parameters"]["credentialProviderSecrets"]["value"]["synthetic"] = "test-value"
        self.assertEqual(complete_arm_parameters(raw)["parameters"]["credentialProviderSecrets"]["value"], {})

    def test_missing_unknown_or_nonvalue_inputs_are_rejected(self):
        valid = parameter_document()
        invalid = [{}, {**valid, "parameters": {}}]
        for entries in (
            {"configuration": {"value": []}},
            {"configuration": {"value": {}}, "unexpected": {"value": "test"}},
            {"configuration": {"reference": {}}},
        ):
            invalid.append({**valid, "parameters": entries})
        invalid.append({**valid, "unexpected": "test"})
        for raw in invalid:
            with self.subTest(case=raw), self.assertRaises(ValueError):
                complete_arm_parameters(raw)

    def test_producer_metadata_is_required_instead_of_synthesized(self):
        for field in ("$schema", "contentVersion"):
            for replacement in (None, "", "invalid", 1):
                raw = parameter_document()
                raw[field] = replacement
                with self.subTest(field=field, value=replacement), self.assertRaises(ValueError):
                    complete_arm_parameters(raw)
            raw = parameter_document()
            del raw[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                complete_arm_parameters(raw)

    def test_all_main_parameters_are_explicitly_forwarded(self):
        main = (ROOT / "infra/main.bicep").read_text()
        bridge = (ROOT / "infra/main.bicepparam").read_text()
        names = set(re.findall(r"^param (\w+) ", main, re.MULTILINE))
        forwarded = re.findall(r"^param (\w+) =", bridge, re.MULTILINE)
        self.assertEqual(names, {"configuration", *ARM_PARAMETER_DEFAULTS})
        self.assertEqual(set(forwarded), names)
        self.assertEqual(len(forwarded), len(names))
        self.assertNotIn(ARM_PARAMETERS_ENV, main)


class NativeBicepParameterBridgeTests(unittest.TestCase):
    """azdが使うbuild-paramsを、公開mainと一時的な合成入力で実行する。"""

    @classmethod
    def setUpClass(cls):
        cls.compiler = bicep_executable()
        cls.temporary = tempfile.TemporaryDirectory(prefix="core-azd-parameter-bridge-")
        cls.project = Path(cls.temporary.name)
        (cls.project / "infra").mkdir()
        # 本体・module・guestの読取元は公開正本のまま。出力と合成値は一時領域だけ。
        relative_main = Path(os.path.relpath(ROOT / "infra/main.bicep", cls.project / "infra")).as_posix()
        bridge = (ROOT / "infra/main.bicepparam").read_text().replace(
            "using './main.bicep'", f"using '{relative_main}'", 1)
        cls.bridge = cls.project / "infra/main.bicepparam"
        cls.bridge.write_text(bridge)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def write_document(self, environment, document):
        reference = arm_parameters_reference(environment)
        path = self.project / reference
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document))
        return reference

    def compile(self, reference=None):
        environment = {**os.environ,
                       "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(self.project / "compiler-cache"),
                       "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
        environment.pop(ARM_PARAMETERS_ENV, None)
        if reference is not None:
            environment[ARM_PARAMETERS_ENV] = reference
        return subprocess.run(
            [self.compiler, "build-params", str(self.bridge), "--stdout", "--no-restore"],
            env=environment, capture_output=True, text=True, timeout=90,
        )

    def compiled_documents(self, reference):
        result = self.compile(reference)
        self.assertEqual(result.returncode, 0, result.stderr)
        compiled = json.loads(result.stdout)
        return json.loads(compiled["parametersJson"]), json.loads(compiled["templateJson"])

    def test_sparse_input_uses_the_existing_main_defaults(self):
        raw = parameter_document({"marker": "SYNTHETIC_ALPHA"})
        reference = self.write_document("alpha", raw)
        parameters, template = self.compiled_documents(reference)
        self.assertEqual(parameters, complete_arm_parameters(raw))
        self.assertEqual(template["parameters"]["credentialProviderSecrets"]["type"], "secureObject")
        self.assertNotIn("SYNTHETIC_ALPHA", json.dumps(template))

    def test_all_values_are_forwarded_and_two_environments_stay_separate(self):
        for name in ("alpha", "beta"):
            raw = complete_arm_parameters(parameter_document({"marker": name}))
            values = raw["parameters"]
            values["phase"]["value"] = "monitoring"
            values["credentialProviderSecrets"]["value"] = {"synthetic": "SYNTHETIC_TOKEN_" + name}
            values["preserveAuditCollection"]["value"] = True
            values["existingNetworkSecurityGroupNames"]["value"] = ["synthetic-nsg"]
            values["existingRouteTableRoutes"]["value"] = {"synthetic": []}
            reference = self.write_document(name, raw)
            parameters, template = self.compiled_documents(reference)
            self.assertEqual(parameters, raw)
            self.assertNotIn("SYNTHETIC_TOKEN_", json.dumps(template))
            self.assertFalse((self.project / "infra/main.parameters.json").exists())

    def test_missing_reference_or_missing_file_fails_without_creating_infra_input(self):
        for reference in (None, arm_parameters_reference("missing")):
            with self.subTest(reference=reference):
                result = self.compile(reference)
                self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.project / "infra/main.parameters.json").exists())


if __name__ == "__main__":
    unittest.main()
