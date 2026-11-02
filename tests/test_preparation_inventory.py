"""事前確認の一覧取得が、読み取り専用で秘密を漏らさないことを検証する。

偽のAzure CLIを子プロセスとして起動し、コマンド引数、timeout、異常応答と出力の抑制を確かめる。資源登録や推論へ進まないことが目的である。
"""
from __future__ import annotations

from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import preparation_inventory as inventory

SUBSCRIPTION = "00000000-0000-0000-0000-000000000001"
TENANT = "00000000-0000-0000-0000-000000000002"
PRIVATE = "PRIVATE-ERROR-OR-TOKEN-MUST-NOT-BE-RECORDED"


def configuration():
    """確認対象のSubscription等を持つ合成設定を作る。個人の実環境へ依存させない。"""
    return {"productName": "egakumi", "environmentName": "test",
        "subscriptionId": SUBSCRIPTION, "azureTenantId": TENANT, "location": "japaneast",
        "foundryLocation": "eastus2", "apiCenterLocation": "eastus",
        "management": {"vmSize": "Standard_B2s"}, "secretPath": PRIVATE,
        "clientSecret": PRIVATE, "certificatePassword": PRIVATE}


class InventorySafetyTests(unittest.TestCase):
    """事前確認の一覧取得が、読み取り専用で秘密を漏らさないことを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def test_local_inventory_never_calls_cli_and_omits_private_config(self):
        calls = []
        result = inventory.inventory(configuration(), reader=lambda args: calls.append(args))
        self.assertEqual(calls, [])
        self.assertEqual(result["mode"], "localOnly")
        self.assertEqual(result["resourceMutations"], 0)
        self.assertEqual(result["inferenceRequests"], 0)
        self.assertFalse(result["quotaReservation"])
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_wrong_or_unverified_account_prevents_all_followup_calls(self):
        fixtures = [
            {"state": "unavailable", "reason": "AzureReadTimeout"},
            {"state": "read", "data": {"subscription": "wrong", "tenant": TENANT}},
            {"state": "read", "data": {"subscription": SUBSCRIPTION, "tenant": "wrong"}},
            {"state": "read", "data": {}},
            {"state": "read", "data": None},
            {"state": "read", "data": []},
            {"state": "read", "data": "malformed"},
        ]
        for account in fixtures:
            with self.subTest(account=account):
                calls = []

                def reader(arguments):
                    calls.append(arguments)
                    self.assertEqual(arguments[:2], ["account", "show"])
                    self.assertEqual(len(calls), 1, "must stop before catalog/SKU/provider reads")
                    return copy.deepcopy(account)
                result = inventory.inventory(configuration(), azure=True, reader=reader)
                self.assertIn("blocked", result["azureChecks"])
                self.assertEqual(len(calls), 1)
                self.assertNotIn("regions", result)

    def whitelist_reader(self, config, calls):
        """Only exact read-only command shapes are accepted by this fake reader."""
        allowed = {
            ("account", "show"): ["account", "show", "--subscription", SUBSCRIPTION,
                "--query", "{subscription:id,tenant:tenantId,state:state}"],
            ("provider", "list"): ["provider", "list", "--subscription", SUBSCRIPTION,
                "--query", "[].{namespace:namespace,registrationState:registrationState}"],
            ("webapp", "list-runtimes"): ["webapp", "list-runtimes", "--os", "linux"],
            ("vm", "list-skus"): ["vm", "list-skus", "--subscription", SUBSCRIPTION,
                "--location", config["location"], "--resource-type", "virtualMachines", "--size", "Standard_B2s",
                "--query", "[].{name:name,restrictions:restrictions,locations:locations,locationInfo:locationInfo}"],
        }

        def reader(arguments):
            calls.append(arguments)
            prefix = tuple(arguments[:2])
            if prefix in allowed:
                self.assertEqual(arguments, allowed[prefix])
            elif arguments[:3] in (["cognitiveservices", "model", "list"], ["cognitiveservices", "usage", "list"]):
                self.assertIn(arguments[-1], {config["location"], config["foundryLocation"]})
                self.assertEqual(arguments[3:-1], ["--subscription", SUBSCRIPTION, "--location"])
            else:
                self.fail(f"Unapproved command: {arguments}")
            forbidden = {"create", "delete", "update", "register", "set", "login", "logout", "rest", "deploy", "invoke", "get-access-token"}
            self.assertFalse(forbidden.intersection(arguments), "inventory must never mutate or invoke data plane")
            if prefix == ("account", "show"):
                return {"state": "read", "data": {"subscription": SUBSCRIPTION, "tenant": TENANT, "state": "Enabled"}}
            if arguments[:3] == ["cognitiveservices", "model", "list"]:
                return {"state": "read", "data": [
                    {"model": {"name": "gpt-image-2.5-flare", "version": "2026-09-08", "format": "OpenAI",
                               "lifecycleStatus": "GenerallyAvailable", "capabilities": {"imageGeneration": "true"}, "apiKey": PRIVATE},
                     "skus": [{"name": "GlobalStandard", "usageName": "imageQuota", "capacity": {"minimum": 1, "maximum": 5}, "accessToken": PRIVATE}],
                     "clientSecret": PRIVATE},
                    {"name": "text-embedding-3-small", "version": "1", "privateEndpointCredential": PRIVATE},
                    {"model": {"name": "unrequested-model", "apiKey": PRIVATE}},
                    None, "malformed", {"model": None}, {"model": {"name": 17}},
                ]}
            if arguments[:3] == ["cognitiveservices", "usage", "list"]:
                return {"state": "read", "data": [
                    {"name": {"value": "OpenAI.Standard.gpt-image-2.5", "localizedValue": "image quota", "accessToken": PRIVATE},
                     "unit": "Count", "currentValue": 1, "limit": 5, "quotaPeriod": "Minute", "apiKey": PRIVATE},
                    {"name": "Cohere-rerank-limit", "unit": "Count", "currentValue": 2, "limit": 20, "secret": PRIVATE},
                    {"name": {"value": "unrelated-model"}, "currentValue": 1, "limit": 99}, None,
                ]}
            if prefix == ("provider", "list"):
                return {"state": "read", "data": [
                    {"namespace": "Microsoft.Web", "registrationState": "NotRegistered"},
                    {"namespace": "Microsoft.CognitiveServices", "registrationState": "Registered"},
                    {"namespace": "Unrelated.Provider", "registrationState": "Registered"},
                ]}
            if prefix == ("vm", "list-skus"):
                return {"state": "read", "data": [{"name": "Standard_B2s", "restrictions": [{"reasonCode": "NotAvailableForSubscription"}], "locations": [config["location"]]}]}
            return {"state": "read", "data": [{"runtime": "Python", "config": "PYTHON|3.13", "support": "Active", "accessToken": PRIVATE},
                {"runtime": "Node", "config": "NODE|24-lts"}, "PYTHON|3.14", "NODE|24-lts"]}
        return reader

    def test_verified_inventory_only_uses_whitelisted_metadata_reads(self):
        config, calls = configuration(), []
        result = inventory.inventory(config, azure=True, reader=self.whitelist_reader(config, calls))
        self.assertEqual(len(calls), 8)  # account + two regions*2 + SKU + providers + runtime
        self.assertEqual(result["resourceMutations"], 0)
        self.assertEqual(result["inferenceRequests"], 0)
        self.assertFalse(result["quotaReservation"])
        # A quota limit is not converted to a reservation or deployment guarantee.
        self.assertTrue(any("not reserved" in value for value in result["limits"]))
        self.assertEqual(result["managementVmSku"]["data"][0]["restrictions"][0]["reasonCode"], "NotAvailableForSubscription")
        self.assertEqual(result["providers"]["data"][0]["registrationState"], "NotRegistered")

    def test_catalog_and_quota_only_preserve_selected_safe_metadata(self):
        config, calls = configuration(), []
        result = inventory.inventory(config, azure=True, reader=self.whitelist_reader(config, calls))
        self.assertNotIn(PRIVATE, json.dumps(result))
        for region in result["regions"].values():
            models = region["catalog"]["data"]
            self.assertEqual([model["name"] for model in models], ["gpt-image-2.5-flare", "text-embedding-3-small"])
            self.assertEqual(set(models[0]), {"name", "version", "format", "lifecycleStatus", "capabilities", "skus"})
            self.assertEqual(set(models[0]["skus"][0]), {"name", "usageName", "capacity"})
            quota = region["quota"]["data"]
            self.assertEqual(len(quota), 2)
            self.assertEqual(quota[0]["name"], {"value": "OpenAI.Standard.gpt-image-2.5", "localizedValue": "image quota"})
            self.assertEqual(set(quota[0]), {"name", "unit", "currentValue", "limit", "quotaPeriod"})
        self.assertNotIn("Unrelated.Provider", json.dumps(result["providers"]))
        self.assertEqual(result["appServicePythonRuntimes"]["data"], [{"config": "PYTHON|3.13", "support": "Active"}, "PYTHON|3.14"])

    def test_shared_region_is_only_read_once_and_missing_vm_does_not_guess_sku(self):
        config, calls = configuration(), []
        config["foundryLocation"] = config["location"]
        config["management"] = {}
        result = inventory.inventory(config, azure=True, reader=self.whitelist_reader(config, calls))
        self.assertEqual(len(result["regions"]), 1)
        self.assertEqual(len(calls), 5)  # account + one catalog/usage + providers + runtime
        self.assertNotIn("managementVmSku", result)

    def test_unavailable_metadata_is_recorded_without_retry_or_inference(self):
        calls = []

        def reader(arguments):
            calls.append(arguments)
            if arguments[:2] == ["account", "show"]:
                return {"state": "read", "data": {"subscription": SUBSCRIPTION, "tenant": TENANT}}
            return {"state": "unavailable", "reason": "AzureReadTimeout"}
        result = inventory.inventory(configuration(), azure=True, reader=reader)
        self.assertEqual(len(calls), 8)
        self.assertTrue(all(value["state"] == "unavailable" for region in result["regions"].values() for value in region.values()))
        self.assertEqual(result["inferenceRequests"], 0)
        self.assertEqual(result["resourceMutations"], 0)


class FakeAzureCliTests(unittest.TestCase):
    """An actual local child executable named az, with PATH overriding the real CLI."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="inventory-fake-az-")
        self.directory = Path(self.temporary.name)
        self.record = self.directory / "arguments.json"
        fake = self.directory / "az"
        fake.write_text("#!" + sys.executable + "\n" + textwrap.dedent("""
            import json, os, sys, time
            from pathlib import Path
            Path(os.environ['WF_INVENTORY_FAKE_AZ_RECORD']).write_text(json.dumps({
                'arguments': sys.argv[1:], 'telemetry': os.environ.get('AZURE_CORE_COLLECT_TELEMETRY')}))
            mode = os.environ.get('WF_INVENTORY_FAKE_AZ_MODE', 'success')
            marker = 'PRIVATE-ERROR-OR-TOKEN-MUST-NOT-BE-RECORDED'
            if mode == 'timeout':
                print(marker, flush=True)
                print(marker, file=sys.stderr, flush=True)
                time.sleep(2)
            elif mode == 'failure':
                print(marker)
                print(marker, file=sys.stderr)
                sys.exit(19)
            elif mode == 'invalid':
                print(marker)
            else:
                print(json.dumps({'subscription': 'synthetic', 'tenant': 'synthetic'}))
        """))
        fake.chmod(0o700)
        self.environment = patch.dict(os.environ, {"PATH": str(self.directory) + os.pathsep + os.environ.get("PATH", ""),
            "WF_INVENTORY_FAKE_AZ_RECORD": str(self.record), "WF_INVENTORY_FAKE_AZ_MODE": "success"})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def test_real_fake_child_receives_only_read_and_json_flags(self):
        arguments = ["account", "show", "--subscription", SUBSCRIPTION, "--query", "{subscription:id,tenant:tenantId}"]
        result = inventory.read_cli(arguments)
        self.assertEqual(result["state"], "read")
        recorded = json.loads(self.record.read_text())
        self.assertEqual(recorded["arguments"], arguments + ["--only-show-errors", "--output", "json"])
        self.assertEqual(recorded["telemetry"], "false")

    def test_timeout_stdout_stderr_never_appear_in_result_or_console(self):
        os.environ["WF_INVENTORY_FAKE_AZ_MODE"] = "timeout"
        console = io.StringIO()
        with redirect_stdout(console):
            result = inventory.read_cli(["account", "show", "--subscription", SUBSCRIPTION], timeout=0.1)
        self.assertEqual(result, {"state": "unavailable", "reason": "AzureReadTimeout"})
        self.assertNotIn(PRIVATE, json.dumps(result) + console.getvalue())

    def test_cli_failure_and_invalid_json_discard_all_body_text(self):
        for mode, expected in (("failure", "AzureReadFailed"), ("invalid", "InvalidMetadataResponse")):
            with self.subTest(mode=mode):
                os.environ["WF_INVENTORY_FAKE_AZ_MODE"] = mode
                console = io.StringIO()
                with redirect_stdout(console):
                    result = inventory.read_cli(["account", "show", "--subscription", SUBSCRIPTION])
                self.assertEqual(result["state"], "unavailable")
                self.assertEqual(result["reason"], expected)
                self.assertNotIn(PRIVATE, json.dumps(result) + console.getvalue())
                if mode == "failure":
                    self.assertEqual(result["exitCode"], 19)

    def test_main_errors_only_report_class_never_error_message_or_private_config(self):
        config = self.directory / "config.json"
        config.write_text(json.dumps(configuration()))
        output = self.directory / "out.json"
        console = io.StringIO()
        with patch.object(sys, "argv", ["inventory", "--config", str(config), "--output", str(output)]), \
             patch.object(inventory, "inventory", side_effect=OSError(PRIVATE)), redirect_stdout(console):
            status = inventory.main()
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(console.getvalue())["failureClass"], "OSError")
        self.assertNotIn(PRIVATE, console.getvalue())
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
