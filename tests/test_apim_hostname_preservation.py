"""再 up の APIM 本体 PUT でも、発行済み証明書の公開参照を消さないことを調べる。

Azure は呼び出さない。合成した所有資源の応答、隔離した一時入力、コンパイル済みの
Bicep を使い、初回・再開・未知状態の停止を確認する。実証明書や秘密ファイルは読まない。
"""
from __future__ import annotations

from bicep_helpers import compile_bicep

import contextlib
import copy
import io
import json
import unittest
from unittest import mock

from lifecycle_fixtures import LifecycleFixture, ScriptedTransport, lifecycle
from deployment.helpers.apim_naming import apim_name


class ApimHostnamePreservationTests(LifecycleFixture, unittest.TestCase):
    """所有する APIM の安全な公開値だけを、Bicep の内部入力へ渡す。"""

    def setUp(self):
        super().setUp()
        self.name = apim_name(self.config)
        self.resource_id = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/"
                            f"{self.config['resourceGroupNames']['svc']}/providers/Microsoft.ApiManagement/service/{self.name}")
        self.default = {"type": "Proxy", "hostName": self.name + ".azure-api.net",
                        "certificateSource": "BuiltIn", "defaultSslBinding": True,
                        "negotiateClientCertificate": False}
        self.custom = {"type": "Proxy", "hostName": "api.example.com", "certificateSource": "KeyVault",
                       "keyVaultId": "https://kv-fixture.vault.azure.net/secrets/gateway-tls",
                       "identityClientId": None, "defaultSslBinding": False, "negotiateClientCertificate": False,
                       "certificateStatus": "Completed", "certificate": {
                           "expiry": "2027-01-01T00:00:00Z", "subject": "CN=api.example.com", "thumbprint": "OBSERVED_METADATA"}}
        self.body = {"id": self.resource_id, "name": self.name, "type": "Microsoft.ApiManagement/service",
                     "location": self.config["location"], "tags": {
                         "product": self.config["productName"], "environment": self.config["environmentName"],
                         "azd-env-name": self.config["environmentName"]},
                     "properties": {"provisioningState": "Succeeded", "hostnameConfigurations": [self.default, self.custom]}}

    def transport(self, body=None):
        """一覧から名前を見つけ、対象本体を読み直す二つの GET を再現する。"""
        return ScriptedTransport([{"statusCode": 200, "body": {"value": [{"name": self.name}]}},
                                  {"statusCode": 200, "body": copy.deepcopy(self.body if body is None else body)}])

    def expected(self):
        return {self.name: [copy.deepcopy(self.default),
                           {key: copy.deepcopy(value) for key, value in self.custom.items()
                            if key not in ("certificate", "certificateStatus")} ]}

    def policy_response(self):
        """policy 本文は秘密を想定した合成値とし、入力へ複写されないことを確認する。"""
        return {"statusCode": 200, "body": {"id": self.resource_id + "/policies/policy",
            "name": "policy", "type": "Microsoft.ApiManagement/service/policies",
            "properties": {"format": "xml", "value": "PRIVATE_POLICY_VALUE"}}}


    def test_first_up_and_unrelated_service_return_empty_without_a_service_put(self):
        for response in ({"statusCode": 404, "body": None},
                         {"statusCode": 200, "body": {"value": []}},
                         {"statusCode": 200, "body": {"value": [{"name": "apim-unrelated-service"}]}}):
            with self.subTest(response=response):
                transport = ScriptedTransport([response])
                self.assertEqual(lifecycle.discover_existing_apim_hostnames(self.config, transport), {})
                self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_standard_apim_name_selects_only_that_service_and_preserves_its_policy(self):
        """同じ資源グループでも所有するサービスの公開設定だけを保全する。"""
        unrelated_name = "apim-unrelated-service"
        transport = ScriptedTransport([
            {"statusCode": 200, "body": {"value": [{"name": unrelated_name}, {"name": self.name}]}},
            {"statusCode": 200, "body": self.body}, self.policy_response()])
        owned = lifecycle.discover_existing_apim_hostnames(self.config, transport)
        self.assertEqual(owned, self.expected())
        self.assertTrue(all(unrelated_name not in call["path"] for call in transport.calls))
        self.assertFalse(transport.mutations())


    def test_standard_name_still_requires_the_configured_subscription_and_group(self):
        for old, new in ((self.config["subscriptionId"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
                         (self.config["resourceGroupNames"]["svc"], "rg-other-svc")):
            body = copy.deepcopy(self.body)
            body["id"] = body["id"].replace(old, new)
            transport = self.transport(body)
            with self.subTest(scope=old), self.assertRaises(lifecycle.DeploymentError):
                lifecycle.discover_existing_apim_hostnames(self.config, transport)
            self.assertFalse(transport.mutations())

    def test_reup_preserves_builtin_and_kv_sami_but_drops_observed_certificate_metadata(self):
        transport = self.transport()
        snapshots = lifecycle.discover_existing_apim_hostnames(self.config, transport)
        self.assertEqual(snapshots, self.expected())
        self.assertNotIn("OBSERVED_METADATA", json.dumps(snapshots))
        self.assertEqual([call["method"] for call in transport.calls], ["GET", "GET"])
        self.assertEqual(transport.calls[1]["path"], self.resource_id + "?api-version=2024-05-01")
        self.assertNotIn("/secrets/", transport.calls[1]["path"])
        values = lifecycle.parameters(self.config, "core", existing_apim_hostname_configurations=snapshots)
        snapshots[self.name].clear()
        self.assertEqual(values["parameters"]["existingApimHostnameConfigurations"]["value"], self.expected())
        self.assertEqual(lifecycle.parameters(self.config, "monitoring")["parameters"]["existingApimHostnameConfigurations"]["value"], {})

    def test_arm_location_display_name_preserves_the_same_owned_apim(self):
        """実ARMのJapan East表記を受け入れ、場所の変更やタグ不一致は許可しない。"""
        for label in ("Japan East", "JAPAN EAST", "japaneast"):
            body = copy.deepcopy(self.body)
            body["location"] = label
            transport = self.transport(body)
            with self.subTest(location=label):
                self.assertEqual(lifecycle.discover_existing_apim_hostnames(self.config, transport), self.expected())
                self.assertFalse(transport.mutations())
        for actual in ("Japan West", "japan-east", None, {}, "Japan\nEast"):
            self.assertFalse(lifecycle.same_azure_location(actual, "japaneast"))

    def test_arm_redacted_null_secret_fields_are_dropped_from_the_preserved_input(self):
        """実GETのnull欄は許可して捨て、秘密値をBicepへコピーしない。"""
        body = copy.deepcopy(self.body)
        for domain in body['properties']['hostnameConfigurations']:
            domain.update(encodedCertificate=None, certificatePassword=None)
        result = lifecycle.discover_existing_apim_hostnames(self.config, self.transport(body))
        self.assertEqual(result, self.expected())
        for domains in result.values():
            for domain in domains:
                self.assertNotIn('encodedCertificate', domain)
                self.assertNotIn('certificatePassword', domain)

    def test_unknown_inventory_permissions_ambiguity_and_wrong_ownership_stop_without_mutation(self):
        inventories = [{"statusCode": 403, "body": {"error": {"message": "PRIVATE_FIXTURE"}}},
                       {"statusCode": 200, "body": {}},
                       {"statusCode": 200, "body": {"value": [], "nextLink": "PRIVATE_FIXTURE"}},
                       {"statusCode": 200, "body": {"value": [None]}},
                       {"statusCode": 200, "body": {"value": [{"name": self.name}, {"name": self.name}]}}]
        for response in inventories:
            transport = ScriptedTransport([response])
            with self.assertRaises(lifecycle.DeploymentError) as error:
                lifecycle.discover_existing_apim_hostnames(self.config, transport)
            self.assertNotIn("PRIVATE_FIXTURE", str(error.exception))
            self.assertFalse(transport.mutations())
        for field in ("id", "name", "type", "location", "tags"):
            body = copy.deepcopy(self.body)
            body[field] = {} if field == "tags" else "PRIVATE_FIXTURE"
            transport = self.transport(body)
            with self.subTest(field=field), self.assertRaises(lifecycle.DeploymentError) as error:
                lifecycle.discover_existing_apim_hostnames(self.config, transport)
            self.assertNotIn("PRIVATE_FIXTURE", str(error.exception))
            self.assertFalse(transport.mutations())

    def test_private_certificate_and_unsafe_reference_stop_without_logging_values(self):
        changes = [{"encodedCertificate": "PRIVATE_FIXTURE"}, {"certificatePassword": "PRIVATE_FIXTURE"},
                   {"encodedCertificate": ""}, {"certificatePassword": ""},
                   {"keyVaultId": "https://user:PRIVATE_FIXTURE@kv.vault.azure.net/secrets/cert"},
                   {"keyVaultId": "https://kv.vault.azure.net/secrets/cert?sig=PRIVATE_FIXTURE"},
                   {"keyVaultId": "http://kv.vault.azure.net/secrets/cert"},
                   {"certificateSource": "Managed"}, {"certificateSource": "Custom"}]
        for change in changes:
            body = copy.deepcopy(self.body)
            body["properties"]["hostnameConfigurations"][1].update(change)
            transport = self.transport(body)
            with self.subTest(fields=list(change)), self.assertRaises(lifecycle.DeploymentError) as error:
                lifecycle.discover_existing_apim_hostnames(self.config, transport)
            self.assertNotIn("PRIVATE_FIXTURE", str(error.exception))
            self.assertFalse(transport.mutations())

    def test_extra_response_metadata_is_ignored_in_preserved_hostname_settings(self):
        body = copy.deepcopy(self.body)
        for domain in body["properties"]["hostnameConfigurations"]:
            domain.update(responseMetadata={"observation": "PRIVATE_FIXTURE"},
                          certificate={"observation": "PRIVATE_FIXTURE", "expiry": None})
        observed = lifecycle.discover_existing_apim_hostnames(self.config, self.transport(body))
        self.assertEqual(observed, self.expected())
        self.assertNotIn("PRIVATE_FIXTURE", json.dumps(observed))

    def test_pending_and_duplicate_domains_are_not_treated_as_an_empty_configuration(self):
        for change in ("pending", "duplicate", "missing", "unknown-certificate"):
            body = copy.deepcopy(self.body)
            if change == "pending":
                body["properties"]["provisioningState"] = "Updating"
            elif change == "duplicate":
                body["properties"]["hostnameConfigurations"].append(copy.deepcopy(self.custom))
            elif change == "missing":
                del body["properties"]["hostnameConfigurations"]
            else:
                body["properties"]["hostnameConfigurations"][0]["certificateSource"] = "Custom"
            with self.subTest(change=change), self.assertRaises(lifecycle.DeploymentError):
                lifecycle.discover_existing_apim_hostnames(self.config, self.transport(body))

    def test_preup_and_final_preprovision_write_the_same_owned_snapshot_into_private_inputs(self):
        for phase in ("preup", "preprovision"):
            with self.subTest(phase=phase), mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
                    mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
                    mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
                    mock.patch.object(lifecycle, "prepare_grpc_secret", return_value={}), \
                    mock.patch.object(lifecycle, "run", return_value={}), \
                    mock.patch.object(lifecycle, "ArmTransport", return_value=ScriptedTransport([{"statusCode": 404, "body": None}])), \
                    mock.patch.object(lifecycle, "capacity_preflight"), \
                    mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "lock_and_verify_policies"), \
                    mock.patch.object(lifecycle, "configure_app_insights_cap"), \
                    mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()), \
                    mock.patch.object(lifecycle, "discover_existing_network_security_groups", return_value=[]), \
                    mock.patch.object(lifecycle, "discover_existing_route_table_routes", return_value={}), \
                    mock.patch.object(lifecycle, "discover_existing_portal_app", return_value=False), \
                    mock.patch.object(lifecycle, "discover_existing_apim_egress_fqdns", return_value=[]), \
                    mock.patch.object(lifecycle, "discover_existing_apim_hostnames", return_value=self.expected()) as discover, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(lifecycle.main([phase]), 0)
                discover.assert_called_once()
                values = json.loads(self.paths.parameters.read_text())["parameters"]
                self.assertEqual(values["existingApimHostnameConfigurations"]["value"], self.expected())

    def test_direct_core_put_includes_snapshot_and_monitoring_does_not_discover_domains(self):
        for phase in ("core", "monitoring"):
            transport = ScriptedTransport([{"statusCode": 201, "body": {}},
                {"statusCode": 200, "body": {"properties": {"provisioningState": "Succeeded", "outputs": {}}}}])
            with self.subTest(phase=phase), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                    mock.patch.object(lifecycle, "run", return_value={"resources": []}), \
                    mock.patch.object(lifecycle, "discover_existing_network_security_groups", return_value=[]), \
                    mock.patch.object(lifecycle, "discover_existing_route_table_routes", return_value={}), \
                    mock.patch.object(lifecycle, "discover_existing_portal_app", return_value=False), \
                    mock.patch.object(lifecycle, "discover_existing_apim_hostnames", return_value=self.expected()) as discover:
                lifecycle.deploy_phase(self.config, phase)
                self.assertEqual(discover.call_count, 1 if phase == "core" else 0)
                values = transport.mutations()[0]["body"]["properties"]["parameters"]
                self.assertEqual(values["existingApimHostnameConfigurations"]["value"], self.expected() if phase == "core" else {})


class ApimHostnameBicepTests(unittest.TestCase):
    """APIM 本体 PUT の配備テンプレートへ、保持用内部入力が届くことを確認する。"""

    def test_compiled_main_passes_named_snapshot_to_normal_apim_resource(self):
        template = compile_bicep('infra/main.bicep')
        self.assertEqual(template["parameters"]["existingApimHostnameConfigurations"]["defaultValue"], {})
        self.assertNotIn("existingApimPolicyNames", template["parameters"])
        resources = template["resources"]
        service_module = next(resource for resource in (resources.values() if isinstance(resources, dict) else resources)
                              if "template" in resource.get("properties", {})
                              and "existingApimHostnameConfigurations" in resource["properties"].get("parameters", {}))
        self.assertEqual(service_module["properties"]["parameters"]["existingApimHostnameConfigurations"]["value"],
                         "[parameters('existingApimHostnameConfigurations')]")
        nested = service_module["properties"]["template"]["resources"]
        nested_resources = list(nested.values()) if isinstance(nested, dict) else nested
        apim = next(resource for resource in nested_resources
                    if resource["type"] == "Microsoft.ApiManagement/service")
        self.assertNotIn("existing", apim)
        hostname_field = apim["properties"]["hostnameConfigurations"]
        self.assertEqual(hostname_field,
            "[coalesce(tryGet(parameters('existingApimHostnameConfigurations'), parameters('names').apim), createArray())]")
        self.assertIn("sku", apim)
        self.assertIn("virtualNetworkConfiguration", apim["properties"])
        self.assertFalse(any(resource["type"].startswith("Microsoft.ApiManagement/service/apis")
                             or resource["type"] == "Microsoft.ApiManagement/service/policies"
                             for resource in nested_resources))
        all_resources = list(resources.values()) if isinstance(resources, dict) else resources
        self.assertFalse(any("apim-baseline" in resource.get("name", "")
                             for resource in all_resources))
        gateway_module = next(resource for resource in all_resources
                              if "-gateway" in resource.get("name", ""))
        self.assertFalse(any("apim-baseline" in item for item in gateway_module["dependsOn"]))
        self.assertFalse(any(resource.get("type") == "Microsoft.Resources/deployments"
                             and "api-publication" in resource.get("name", "")
                             for resource in all_resources))


if __name__ == "__main__":
    unittest.main()
