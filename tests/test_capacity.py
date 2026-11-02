"""配備前のVM・モデル利用枠確認を、合成したAzure応答で検証する。

VMサイズとイメージの互換性、空きクォータ、既存モデルへの割当を照合する。情報を取得できない場合に利用可能と判定しないことが中心で、利用枠の予約は行わない。
"""

import copy
import importlib.util
import json
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlparse


CORE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("core_capacity", CORE / "scripts/deployment/helpers/capacity.py")
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)


def fixtures():
    """Azureから返るメタデータの正常例を作り、各ケースが必要な差分だけを変更できるようにする。"""
    config = {
        "productName": "egakumi", "environmentName": "test",
        "subscriptionId": "11111111-1111-4111-8111-111111111111", "location": "japaneast", "foundryLocation": "eastus2",
        "management": {"vmSize": "Standard_D2as_v6", "diskControllerType": "NVMe", "encryptionAtHost": True,
                       "enableAcceleratedNetworking": True,
                       "imageReference": {"publisher": "Canonical", "offer": "ubuntu-24_04-lts", "sku": "server", "version": "24.04.202610160"}},
        "services": {"modelDeployments": [{"name": "gpt-4.1-mini", "modelName": "gpt-4.1-mini", "modelVersion": "2025-04-14",
                                           "modelFormat": "OpenAI", "skuName": "DataZoneStandard", "capacity": 1}]},
        "apiDefinitions": [{"operations": [{"name": "chat", "urlTemplate": "/chat/completions"}, {"name": "responses", "urlTemplate": "/responses"}],
                            "authorization": {"operationConstraints": {"chat": {"kind": "bodyModel", "allowedModels": ["gpt-4.1-mini"]},
                                                                          "responses": {"kind": "statelessResponses", "allowedModels": ["gpt-4.1-mini"]}}}}],
    }
    responses = {
        "skus": [{"name": "Standard_D2as_v6", "resourceType": "virtualMachines", "family": "standardDav6Family", "locations": ["japaneast"],
                  "restrictions": [{"type": "Zone", "reasonCode": "NotAvailableForSubscription", "values": ["japaneast"],
                                    "restrictionInfo": {"locations": ["japaneast"], "zones": ["1"]}}],
                  "capabilities": [{"name": key, "value": value} for key, value in {
                      "vCPUs": "2", "HyperVGenerations": "V2", "DiskControllerTypes": "NVMe", "EncryptionAtHostSupported": "True",
                      "AcceleratedNetworkingEnabled": "True", "CpuArchitectureType": "x64"}.items()]}],
        "vm-usage": [{"name": {"value": "standardDav6Family"}, "currentValue": 0, "limit": 10},
                     {"name": {"value": "cores"}, "currentValue": 0, "limit": 10}],
        "image": {"name": "24.04.202610160", "hyperVGeneration": "V2", "architecture": "x64", "osDiskImage": {"operatingSystem": "Linux"},
                  "features": [{"name": "DiskControllerTypes", "value": "SCSI,NVMe"}, {"name": "IsAcceleratedNetworkSupported", "value": "True"}]},
        "models": [{"kind": "AIServices", "skuName": "S0", "model": {"format": "OpenAI", "name": "gpt-4.1-mini", "version": "2025-04-14",
                     "capabilities": {"chatCompletion": "true", "responses": "true"},
                     "skus": [{"name": "DataZoneStandard", "usageName": "OpenAI.DataZoneStandard.gpt4.1-mini", "capacity": {"minimum": 1, "maximum": 1000, "step": 1}}]}}],
        "model-usage": [{"name": {"value": "OpenAI.DataZoneStandard.gpt4.1-mini"}, "currentValue": 0, "limit": 16000}],
        "model-capacities": {"value": [{"location": "eastus2", "properties": {
            "model": {"format": "OpenAI", "name": name, "version": "2025-04-14"},
            "skuName": "DataZoneStandard", "availableCapacity": 16000, "availableFinetuneCapacity": 500,
        }} for name in ("gpt-4.1-mini", "gpt-other")]},
    }
    return config, responses


class Runner:
    """Azure CLIの代わりに合成応答を返す。要求内容を記録し、読み取りだけの手順を確かめる。"""
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def __call__(self, arguments):
        self.calls.append(arguments)
        prefix = arguments[:4]
        if prefix[:3] == ["az", "vm", "list-skus"]:
            key = "skus"
            assert "--all" in arguments and "--size" in arguments
        elif prefix[:3] == ["az", "vm", "list-usage"]:
            key = "vm-usage"
        elif prefix == ["az", "vm", "image", "show"]:
            key = "image"
            assert arguments[arguments.index("--urn") + 1].endswith(":24.04.202610160")
        elif prefix == ["az", "cognitiveservices", "model", "list"]:
            key = "models"
        elif prefix == ["az", "cognitiveservices", "usage", "list"]:
            key = "model-usage"
        elif prefix == ["az", "rest", "--method", "get"]:
            key = "model-capacities"
            uri = urlparse(arguments[arguments.index("--url") + 1])
            query = parse_qs(uri.query)
            assert uri.scheme == "https" and uri.netloc == "management.azure.com"
            assert uri.path.endswith("/providers/Microsoft.CognitiveServices/modelCapacities")
            assert query["api-version"] == ["2025-06-01"]
            assert query["modelFormat"] == ["OpenAI"] and query["modelVersion"] == ["2025-04-14"]
        else:
            raise AssertionError("A non-read-only command was requested")
        assert "--subscription" in arguments and arguments[-2:] == ["-o", "json"]
        response = self.responses[key]
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


class CapacityTests(unittest.TestCase):
    """配備前のVM・モデル利用枠確認を、合成したAzure応答で検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def check(self, config, responses, existing=None):
        runner = Runner(responses)
        result = capacity.verify_capacity(config, runner, existing_resources=existing)
        self.assertEqual(result["operations"], "read-only")
        self.assertEqual(result["additionalAzureCostJPY"], 0)
        return result, runner

    def codes(self, report):
        return {issue["code"] for issue in report["issues"]}

    def allocated(self, config):
        prefix = "/subscriptions/" + config["subscriptionId"] + "/resourceGroups/rg-test-egakumi"
        deployment = config["services"]["modelDeployments"][0]
        return {
            "vm": {"id": prefix + "-maint/providers/Microsoft.Compute/virtualMachines/vm-test-egakumi-mgmt",
                   "name": "vm-test-egakumi-mgmt", "type": "Microsoft.Compute/virtualMachines", "location": "japaneast",
                   "properties": {"hardwareProfile": {"vmSize": "Standard_D2as_v6"}}},
            "modelDeployments": [{"id": prefix + "-svc/providers/Microsoft.CognitiveServices/accounts/aif-test-egakumi/deployments/" + deployment["name"],
                                  "name": deployment["name"], "type": "Microsoft.CognitiveServices/accounts/deployments",
                                  "sku": {"name": deployment["skuName"], "capacity": deployment["capacity"]},
                                  "properties": {"provisioningState": "Succeeded", "model": {"format": deployment["modelFormat"], "name": deployment["modelName"], "version": deployment["modelVersion"]}}}],
        }

    def test_reapplying_same_allocations_does_not_need_duplicate_quota(self):
        config, responses = fixtures()
        existing = self.allocated(config)
        for value in responses["vm-usage"] + responses["model-usage"]:
            value["currentValue"] = value["limit"]
        report, _ = self.check(config, responses, existing)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["vm"]["additionalVcpus"], 0)
        self.assertEqual(report["models"][0]["additionalCapacity"], 0)
        self.assertTrue(all(item["requested"] == 0 for item in report["vm"]["quota"]))

    def test_model_growth_uses_delta_but_different_sku_or_version_uses_full_capacity(self):
        for change in ("capacity", "sku", "version"):
            with self.subTest(change=change):
                config, responses = fixtures()
                existing = self.allocated(config)
                if change == "capacity":
                    config["services"]["modelDeployments"][0]["capacity"] = 3
                    responses["model-usage"][0].update(currentValue=15999, limit=16000)
                    expected = 2
                else:
                    if change == "sku":
                        existing["modelDeployments"][0]["sku"]["name"] = "GlobalStandard"
                    else:
                        existing["modelDeployments"][0]["properties"]["model"]["version"] = "previous"
                    responses["model-usage"][0]["currentValue"] = 16000
                    expected = 1
                report, _ = self.check(config, responses, existing)
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(report["models"][0]["additionalCapacity"], expected)
                self.assertIn("MODEL_QUOTA_INSUFFICIENT", self.codes(report))

    def test_unchanged_ready_model_does_not_require_new_capacity_read(self):
        """既存配備を同じ内容で再適用するとき、追加枠の応答欠落で止めない。"""
        for response in (RuntimeError("capacity endpoint unavailable"), {"value": []}):
            with self.subTest(response=type(response).__name__):
                config, responses = fixtures()
                existing = self.allocated(config)
                responses["model-capacities"] = response
                report, runner = self.check(config, responses, existing)
                self.assertEqual(report["status"], "verified")
                self.assertEqual(report["models"][0]["additionalCapacity"], 0)
                self.assertEqual(report["models"][0]["availability"]["status"], "not-required")
                self.assertEqual(report["models"][0]["availability"]["reason"], "existing-deployment-unchanged")
                self.assertFalse(any(call[:4] == ["az", "rest", "--method", "get"] for call in runner.calls))

    def test_changed_model_still_requires_new_capacity_read(self):
        """増減量・SKU・版・形式の変更は既存モデルの再適用として扱わない。"""
        for change in ("growth", "reduction", "sku", "version", "format"):
            with self.subTest(change=change):
                config, responses = fixtures()
                existing = self.allocated(config)
                previous = existing["modelDeployments"][0]
                if change == "growth":
                    config["services"]["modelDeployments"][0]["capacity"] = 3
                elif change == "reduction":
                    previous["sku"]["capacity"] = 3
                elif change == "sku":
                    previous["sku"]["name"] = "GlobalStandard"
                else:
                    previous["properties"]["model"][change] = "different"
                responses["model-capacities"] = {"value": []}
                report, runner = self.check(config, responses, existing)
                self.assertEqual(report["status"], "blocked")
                self.assertIn("MODEL_DEPLOYMENT_CAPACITY_UNVERIFIED", self.codes(report))
                self.assertTrue(any(call[:4] == ["az", "rest", "--method", "get"] for call in runner.calls))

    def test_existing_model_must_be_successfully_provisioned_before_crediting_capacity(self):
        """作成途中・失敗・状態不明の配備で、追加容量の検査を省略しない。"""
        for state in (None, "Failed", "Creating", "Deleting"):
            with self.subTest(state=state):
                config, responses = fixtures()
                existing = self.allocated(config)
                properties = existing["modelDeployments"][0]["properties"]
                if state is None:
                    properties.pop("provisioningState")
                else:
                    properties["provisioningState"] = state
                responses["model-capacities"] = {"value": []}
                report, _ = self.check(config, responses, existing)
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(report["models"][0]["existingCapacity"], 0)
                self.assertEqual(report["models"][0]["additionalCapacity"], 1)
                self.assertIn("MODEL_DEPLOYMENT_CAPACITY_UNVERIFIED", self.codes(report))

    def test_existing_allocations_from_other_scope_or_type_are_never_credited(self):
        for target, change in (("vm", "scope"), ("model", "scope"), ("model", "type")):
            with self.subTest(target=target, change=change):
                config, responses = fixtures()
                existing = self.allocated(config)
                item = existing["vm"] if target == "vm" else existing["modelDeployments"][0]
                if change == "scope":
                    item["id"] = item["id"].replace("rg-test-egakumi", "rg-another-product-test")
                    config["resourceGroupNames"] = {"maint": "rg-another-product-test-maint", "svc": "rg-another-product-test-svc"}
                else:
                    item["type"] = "Microsoft.Compute/virtualMachines"
                report, _ = self.check(config, responses, existing)
                self.assertEqual(report["status"], "blocked")
                self.assertIn("EXISTING_ALLOCATION_UNVERIFIED", self.codes(report))
                self.assertEqual(report["vm"]["additionalVcpus"], 2)
                self.assertEqual(report["models"][0]["additionalCapacity"], 1)

    def test_changed_vm_size_is_checked_at_full_new_size(self):
        config, responses = fixtures()
        existing = self.allocated(config)
        existing["vm"]["properties"]["hardwareProfile"]["vmSize"] = "Standard_D4as_v6"
        responses["vm-usage"][0]["currentValue"] = 10
        report, _ = self.check(config, responses, existing)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["vm"]["additionalVcpus"], 2)
        self.assertIn("VM_QUOTA_INSUFFICIENT", self.codes(report))

    def test_zone_only_restriction_does_not_reject_regional_vm(self):
        config, responses = fixtures()
        before = copy.deepcopy(config)
        report, runner = self.check(config, responses)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["vm"]["zoneOnlyRestrictions"], ["1"])
        self.assertEqual(report["vm"]["quota"][0]["requested"], 2)
        self.assertEqual(report["models"][0]["quota"]["remaining"], 16000)
        self.assertEqual(len(runner.calls), 6)
        self.assertEqual(config, before)

    def test_exact_sku_not_prefix_or_ambiguous_match(self):
        for mode in ("different", "duplicate"):
            config, responses = fixtures()
            if mode == "different":
                responses["skus"][0]["name"] = "Standard_D2as_v6_other"
            else:
                responses["skus"].append(copy.deepcopy(responses["skus"][0]))
            report, _ = self.check(config, responses)
            self.assertEqual(report["status"], "blocked")
            self.assertIn("VM_AVAILABILITY_UNVERIFIED", self.codes(report))

    def test_location_and_requested_zone_restrictions_block(self):
        for kind in ("Location", "Zone"):
            with self.subTest(kind=kind):
                config, responses = fixtures()
                if kind == "Location":
                    responses["skus"][0]["restrictions"][0]["type"] = kind
                else:
                    config["management"]["zones"] = ["1"]
                report, _ = self.check(config, responses)
                self.assertIn("VM_" + kind.upper() + "_RESTRICTED", self.codes(report))

    def test_both_family_and_total_core_quota_are_required(self):
        for index in (0, 1):
            config, responses = fixtures()
            responses["vm-usage"][index]["currentValue"] = 9
            report, _ = self.check(config, responses)
            self.assertIn("VM_QUOTA_INSUFFICIENT", self.codes(report))
        config, responses = fixtures()
        responses["vm-usage"] = responses["vm-usage"][1:]
        report, _ = self.check(config, responses)
        self.assertIn("VM_AVAILABILITY_UNVERIFIED", self.codes(report))

    def test_requested_vm_capability_must_be_verified(self):
        for key in ("DiskControllerTypes", "HyperVGenerations", "EncryptionAtHostSupported", "AcceleratedNetworkingEnabled"):
            with self.subTest(capability=key):
                config, responses = fixtures()
                for entry in responses["skus"][0]["capabilities"]:
                    if entry["name"] == key:
                        entry["value"] = "False"
                report, _ = self.check(config, responses)
                self.assertEqual(report["status"], "blocked")

    def test_fixed_image_must_support_nvme_and_same_generation(self):
        for field in ("generation", "controller", "network", "version"):
            config, responses = fixtures()
            if field == "generation":
                responses["image"]["hyperVGeneration"] = "V1"
            elif field == "version":
                responses["image"]["name"] = "other-version"
            else:
                name = "DiskControllerTypes" if field == "controller" else "IsAcceleratedNetworkSupported"
                responses["image"]["features"] = [value for value in responses["image"]["features"] if value["name"] != name]
            report, _ = self.check(config, responses)
            self.assertEqual(report["status"], "blocked")

    def test_linux_os_is_required_even_when_controller_and_generation_match(self):
        for operating_system in ("Windows", "", None):
            with self.subTest(operating_system=operating_system):
                config, responses = fixtures()
                responses["image"]["osDiskImage"]["operatingSystem"] = operating_system
                report, _ = self.check(config, responses)
                self.assertEqual(report["status"], "blocked")
                self.assertIn("IMAGE_LINUX_REQUIRED", self.codes(report))
        config, responses = fixtures()
        report, _ = self.check(config, responses)
        self.assertEqual(report["status"], "verified")
        self.assertNotIn("IMAGE_LINUX_REQUIRED", self.codes(report))

    def test_image_architecture_must_match_vm(self):
        config, responses = fixtures()
        responses["image"]["architecture"] = "Arm64"
        report, _ = self.check(config, responses)
        self.assertIn("IMAGE_VM_ARCHITECTURE_MISMATCH", self.codes(report))

    def test_other_linux_families_are_not_reported_ready_for_ubuntu_bootstrap(self):
        for field, value in (("publisher", "RedHat"), ("offer", "ubuntu-22_04-lts"), ("sku", "server-arm64")):
            with self.subTest(field=field):
                config, responses = fixtures()
                config["management"]["imageReference"][field] = value
                report, _ = self.check(config, responses)
                self.assertEqual(report["status"], "blocked")
                self.assertIn("IMAGE_UBUNTU_2404_SERVER_REQUIRED", self.codes(report))

    def test_missing_linux_architecture_or_gen1_image_is_not_ready(self):
        for change in ("architecture", "generation"):
            with self.subTest(change=change):
                config, responses = fixtures()
                if change == "architecture":
                    responses["image"].pop("architecture")
                else:
                    responses["image"]["hyperVGeneration"] = "V1"
                report, _ = self.check(config, responses)
                self.assertIn("IMAGE_UBUNTU_2404_X64_GEN2_REQUIRED", self.codes(report))

    def test_explicit_image_id_requires_manual_check(self):
        config, responses = fixtures()
        config["management"]["imageReference"] = {"id": "/subscriptions/example/resourceGroups/images/providers/Microsoft.Compute/images/approved"}
        report, runner = self.check(config, responses)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["vm"]["image"]["status"], "manual-check")
        self.assertFalse(any(call[:4] == ["az", "vm", "image", "show"] for call in runner.calls))

    def test_selected_model_version_sku_and_quota_must_match(self):
        for field in ("version", "sku", "quota"):
            with self.subTest(field=field):
                config, responses = fixtures()
                if field == "version":
                    responses["models"][0]["model"]["version"] = "different"
                elif field == "sku":
                    responses["models"][0]["model"]["skus"][0]["name"] = "GlobalStandard"
                else:
                    responses["model-usage"][0]["currentValue"] = 16000
                report, _ = self.check(config, responses)
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(report["models"][0]["status"], "blocked")

    def test_sku_metadata_on_outer_or_inner_model_is_supported(self):
        config, responses = fixtures()
        model = responses["models"][0]
        model["skus"] = model["model"].pop("skus")
        responses["models"].append(copy.deepcopy(model))
        report, _ = self.check(config, responses)
        self.assertEqual(report["status"], "verified")
        responses["models"][1]["skus"][0]["usageName"] = "OpenAI.Other.pool"
        report, _ = self.check(config, responses)
        self.assertEqual(report["status"], "blocked")

    def test_model_capacity_rules_and_requested_api_capabilities(self):
        config, responses = fixtures()
        responses["models"][0]["model"]["capabilities"]["responses"] = "false"
        report, _ = self.check(config, responses)
        self.assertIn("MODEL_API_UNSUPPORTED", self.codes(report))
        config, responses = fixtures()
        responses["models"][0]["model"]["skus"][0]["capacity"] = {"minimum": 2, "maximum": 100, "step": 2}
        report, _ = self.check(config, responses)
        self.assertIn("MODEL_CAPACITY_UNSUPPORTED", self.codes(report))

    def test_catalog_mismatch_reports_model_version_or_sku_without_deploying(self):
        """一覧の不一致を提供容量不足と混同せず、設定箇所を特定できる理由を返す。"""
        cases = (("modelName", "missing-model", "model-not-listed", []),
                 ("modelVersion", "2025-04-15", "configured-version-not-listed", ["2025-04-14"]),
                 ("skuName", "GlobalStandard", "configured-sku-not-listed", ["2025-04-14"]))
        for field, value, reason, versions in cases:
            with self.subTest(field=field):
                config, responses = fixtures()
                config["services"]["modelDeployments"][0][field] = value
                report, runner = self.check(config, responses)
                self.assertEqual(report["status"], "blocked")
                self.assertIn("MODEL_AVAILABILITY_UNVERIFIED", self.codes(report))
                self.assertEqual(report["models"][0]["catalog"],
                                 {"status": "unmatched", "reason": reason, "availableVersions": versions})
                self.assertEqual(report["models"][0]["availability"]["status"], "unverified")
                self.assertFalse(any(call[:4] == ["az", "rest", "--method", "get"] for call in runner.calls))

    def test_null_bounds_do_not_turn_suggested_default_into_a_minimum(self):
        config, responses = fixtures()
        limits = responses["models"][0]["model"]["skus"][0]["capacity"]
        limits.update(minimum=None, step=None, allowedValues=None, default=10, maximum=1000000)
        report, _ = self.check(config, responses)
        self.assertEqual(report["status"], "verified")
        rules = report["models"][0]["capacityRules"]
        self.assertEqual(rules["status"], "partial")
        self.assertEqual(rules["default"], 10)
        self.assertIsNone(rules["minimum"])
        self.assertIsNone(rules["step"])
        self.assertEqual(rules["notProvided"], ["minimum", "step"])
        self.assertFalse(rules["stepAlignmentVerified"])
        self.assertTrue(rules["sufficient"])

    def test_explicit_quota_usage_selects_base_pool_without_suffix_inference(self):
        config, responses = fixtures()
        metadata = responses["models"][0]["model"]
        base = metadata["skus"][0]
        metadata["skus"].append(dict(copy.deepcopy(base), usageName=base["usageName"] + "-finetune"))
        responses["models"].append(copy.deepcopy(responses["models"][0]))
        responses["models"][1]["kind"] = "OpenAI"
        report, _ = self.check(config, responses)
        self.assertEqual(report["models"][0]["quota"]["status"], "ambiguous")
        self.assertEqual(report["models"][0]["availability"]["status"], "verified")
        self.assertIn("MODEL_QUOTA_USAGE_AMBIGUOUS", self.codes(report))
        config["services"]["modelDeployments"][0]["quotaUsageName"] = base["usageName"]
        report, _ = self.check(config, responses)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["models"][0]["usageName"], base["usageName"])
        self.assertEqual(report["models"][0]["quota"]["status"], "verified")

    def test_explicit_usage_must_exist_and_same_usage_metadata_must_agree(self):
        config, responses = fixtures()
        deployment = config["services"]["modelDeployments"][0]
        deployment["quotaUsageName"] = "OpenAI.Other.pool"
        report, _ = self.check(config, responses)
        self.assertIn("MODEL_QUOTA_USAGE_NOT_FOUND", self.codes(report))
        self.assertEqual(report["models"][0]["availability"]["status"], "verified")
        deployment["quotaUsageName"] = responses["models"][0]["model"]["skus"][0]["usageName"]
        responses["models"].append(copy.deepcopy(responses["models"][0]))
        responses["models"][1]["model"]["skus"][0]["capacity"]["maximum"] = 100
        report, _ = self.check(config, responses)
        self.assertIn("MODEL_CAPACITY_METADATA_CONFLICT", self.codes(report))

    def test_null_and_omitted_capacity_metadata_merge_and_allowed_values_are_checked(self):
        config, responses = fixtures()
        responses["models"][0]["model"]["skus"][0]["capacity"] = {"maximum": 100, "minimum": None, "step": None}
        responses["models"].append(copy.deepcopy(responses["models"][0]))
        responses["models"][1]["model"]["skus"][0]["capacity"] = {"maximum": 100}
        report, _ = self.check(config, responses)
        self.assertEqual(report["status"], "verified")
        for item in responses["models"]:
            item["model"]["skus"][0]["capacity"]["allowedValues"] = [2, 4, 8]
        report, _ = self.check(config, responses)
        self.assertIn("MODEL_CAPACITY_UNSUPPORTED", self.codes(report))

    def test_model_pool_capacity_is_separate_from_quota_and_finetune_pool(self):
        config, responses = fixtures()
        responses["model-capacities"]["value"][0]["properties"].update(availableCapacity=0, availableFinetuneCapacity=500)
        report, _ = self.check(config, responses)
        self.assertEqual(report["models"][0]["capability"]["status"], "verified")
        self.assertEqual(report["models"][0]["quota"]["status"], "verified")
        self.assertEqual(report["models"][0]["availability"]["status"], "insufficient")
        self.assertIn("MODEL_DEPLOYMENT_CAPACITY_INSUFFICIENT", self.codes(report))
        responses["model-capacities"]["value"][0]["location"] = "westus"
        report, _ = self.check(config, responses)
        self.assertIn("MODEL_DEPLOYMENT_CAPACITY_UNVERIFIED", self.codes(report))

    def test_missing_quota_read_does_not_erase_verified_availability(self):
        config, responses = fixtures()
        responses["model-usage"] = RuntimeError("SENTINEL_TOKEN_ERROR_RAW")
        report, _ = self.check(config, responses)
        self.assertEqual(report["models"][0]["capability"]["status"], "verified")
        self.assertEqual(report["models"][0]["quota"]["status"], "unverified")
        self.assertEqual(report["models"][0]["availability"]["status"], "verified")
        self.assertIn("MODEL_QUOTA_UNVERIFIED", self.codes(report))
        self.assertNotIn("SENTINEL", json.dumps(report))

    def test_requested_models_sharing_quota_are_summed(self):
        config, responses = fixtures()
        config["services"]["modelDeployments"].append(dict(config["services"]["modelDeployments"][0], name="gpt-other", modelName="gpt-other"))
        responses["models"].append(copy.deepcopy(responses["models"][0]))
        responses["models"][1]["model"]["name"] = "gpt-other"
        responses["model-usage"][0]["limit"] = 1
        report, _ = self.check(config, responses)
        self.assertTrue(all(value["quota"]["requested"] == 2 for value in report["models"]))
        self.assertTrue(all(value["status"] == "blocked" for value in report["models"]))

    def test_unavailable_or_malformed_read_never_succeeds_or_leaks_error(self):
        for key in ("skus", "vm-usage", "image", "models", "model-usage", "model-capacities"):
            with self.subTest(command=key):
                config, responses = fixtures()
                responses[key] = RuntimeError("SENTINEL_TOKEN_ERROR_RAW")
                report, _ = self.check(config, responses)
                self.assertEqual(report["status"], "blocked")
                self.assertNotIn("SENTINEL", json.dumps(report))
        config, responses = fixtures()
        responses["model-usage"][0]["currentValue"] = "nan"
        report, _ = self.check(config, responses)
        self.assertEqual(report["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
