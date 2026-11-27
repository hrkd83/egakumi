"""Verify required evidence infrastructure without Azure or production inputs."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.support.bicep_helpers import compile_bicep
from tests.support.configuration_fixtures import configuration, fixture, public_fixture
from deployment.helpers import fixed_nsg
from tests.support.schema_helpers import local_schema_validator, read_schema
from tests.network.test_parameter_ip_ownership import NetworkArmExpression
from tests import REPOSITORY


ROOT = REPOSITORY


class EvidenceConfigurationTests(unittest.TestCase):
    def test_shipped_profiles_require_evidence_with_their_selected_tier_and_a_dedicated_subnet(self):
        profiles = (("test", "Standard", "Disabled", 30), ("production", "Premium", "Locked", 2557))
        for profile, sku, mode, retention in profiles:
            with self.subTest(profile=profile):
                raw = configuration.load_configuration_source(ROOT / f"params/{profile}.example.json")
                self.assertTrue(raw["auditEvidence"]["enabled"])
                self.assertEqual(raw["auditEvidence"]["eventHubsSku"], sku)
                self.assertEqual(raw["auditEvidence"]["payloadImmutabilityMode"], mode)
                self.assertEqual(raw["auditEvidence"]["payloadRetentionDays"], retention)
                network = configuration._network(raw["network"], audit_evidence_enabled=True)
                self.assertIn("audit", network["subnetPrefixes"])

    def test_evidence_requires_an_explicit_nonoverlapping_subnet(self):
        raw = fixture(audit_evidence=True)
        prefixes = raw["network"]["subnetPrefixes"]
        prefixes.pop("audit")
        with self.assertRaisesRegex(ValueError, r"subnetPrefixes\.audit"):
            configuration.normalize_configuration(raw)
        prefixes["audit"] = prefixes["broker"]
        with self.assertRaisesRegex(ValueError, "subnets overlap"):
            configuration.normalize_configuration(raw)

    def validate_file(self, source, value):
        source.write_text(json.dumps(value), encoding="utf-8")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error:
            status = configuration.main(["validate", "--config", str(source)])
        return status, error.getvalue()

    def test_cli_requires_audit_subnet_and_refuses_explicit_switch_in_split_inputs(self):
        parameters, options = public_fixture()
        with TemporaryDirectory(prefix="audit-config-") as temp:
            root = Path(temp)
            source = root / "params.json"
            standard = root / parameters["configFile"]
            standard.write_text(json.dumps(options), encoding="utf-8")
            self.assertEqual(self.validate_file(source, parameters), (0, ""))
            self.assertIs(configuration.load_configuration(source)["auditEvidence"]["enabled"], True)
            audit = parameters["network"]["subnetPrefixes"].pop("audit")
            status, error = self.validate_file(source, parameters)
            self.assertEqual(status, 2)
            self.assertIn("subnetPrefixes.audit", error)
            parameters["network"]["subnetPrefixes"]["audit"] = audit

            for enabled in (True, False):
                with self.subTest(enabled=enabled, owner="params"):
                    status, error = self.validate_file(source, {**parameters, "auditEvidence": {"enabled": enabled}})
                    self.assertEqual(status, 2)
                    self.assertIn("auditEvidence.enabled", error)
                with self.subTest(enabled=enabled, owner="config"):
                    standard.write_text(json.dumps({**options, "auditEvidence": {
                        **options["auditEvidence"], "enabled": enabled}}), encoding="utf-8")
                    status, error = self.validate_file(source, parameters)
                    self.assertEqual(status, 2)
                    self.assertIn("auditEvidence.enabled", error)
                standard.write_text(json.dumps(options), encoding="utf-8")

    def test_cli_defaults_combined_input_to_audit_and_rejects_disabled_or_invalid_values(self):
        raw = fixture(audit_evidence=True)
        raw["auditEvidence"].pop("enabled")
        with TemporaryDirectory(prefix="audit-config-") as temp:
            source = Path(temp) / "configuration.json"
            self.assertEqual(self.validate_file(source, raw), (0, ""))
            self.assertIs(configuration.load_configuration(source)["auditEvidence"]["enabled"], True)
            for enabled in (False, None, 0, "true"):
                with self.subTest(enabled=enabled):
                    raw["auditEvidence"]["enabled"] = enabled
                    status, error = self.validate_file(source, raw)
                    self.assertEqual(status, 2)
                    self.assertIn("auditEvidence.enabled", error)

    def test_schema_requires_audit_subnet_and_keeps_enabled_internal(self):
        parameters, options = public_fixture()
        for schema_name, data in (("params", parameters), ("configuration", fixture(audit_evidence=True))):
            with self.subTest(schema=schema_name):
                schema = local_schema_validator(read_schema(schema_name))
                self.assertTrue(schema.is_valid(data))
                data["network"]["subnetPrefixes"].pop("audit")
                self.assertFalse(schema.is_valid(data))
        options_schema = local_schema_validator(read_schema("options"))
        self.assertTrue(options_schema.is_valid(options))
        options["auditEvidence"]["enabled"] = True
        self.assertFalse(options_schema.is_valid(options))

    def test_retention_validation_accepts_worm_period_without_widening_test_expiry(self):
        schemas = [local_schema_validator(read_schema(name)
                                          ["properties"]["auditEvidence"])
                   for name in ("configuration", "options")]
        for mode, accepted in (("Locked", True), ("Disabled", False)):
            with self.subTest(mode=mode):
                body = {"payloadImmutabilityMode": mode, "payloadRetentionDays": 2557}
                for schema in schemas:
                    self.assertEqual(schema.is_valid(body), accepted)
                raw = fixture()
                raw["auditEvidence"].update(body)
                if accepted:
                    self.assertEqual(configuration.normalize_configuration(raw)["auditEvidence"]["payloadRetentionDays"], 2557)
                else:
                    with self.assertRaisesRegex(ValueError, r"auditEvidence\.payloadRetentionDays"):
                        configuration.normalize_configuration(raw)


class EvidenceBoundaryTests(unittest.TestCase):
    def test_core_deployment_always_provisions_audit_subnet_and_evidence_resources(self):
        resources = compile_bicep("infra/main.bicep")["resources"]
        resources = resources.values() if isinstance(resources, dict) else resources
        modules = {item["name"]: item for item in resources if item.get("type") == "Microsoft.Resources/deployments"}
        for suffix in ("identities", "data", "network", "environment", "ingress"):
            module, = [item for name, item in modules.items() if f"-audit-evidence-{suffix}'" in name]
            with self.subTest(module=suffix):
                self.assertEqual(module["condition"], "[variables('deployCore')]")
        network, = [item["properties"]["parameters"] for item in modules.values()
                    if "auditEvidenceEnabled" in item["properties"]["parameters"]]
        self.assertEqual(network["auditEvidenceEnabled"], {"value": True})

    def test_runtime_apps_share_only_the_internal_environment_and_image(self):
        template = compile_bicep("apps/audit-relay/deploy/application.bicep")
        relay, worker = template["resources"]
        self.assertTrue(relay["properties"]["configuration"]["ingress"]["external"])
        self.assertFalse(relay["properties"]["configuration"]["ingress"]["allowInsecure"])
        self.assertEqual(relay["properties"]["configuration"]["ingress"]["ipSecurityRestrictions"], [{
            "name": "allow-apim-subnet", "action": "Allow", "ipAddressRange": "[parameters('foundation').apimSubnetPrefix]",
        }])
        self.assertNotIn("ingress", worker["properties"]["configuration"])
        self.assertEqual(worker["properties"]["template"]["scale"]["minReplicas"], 1)
        self.assertNotEqual(relay["identity"], worker["identity"])
        self.assertEqual(relay["properties"]["template"]["containers"][0]["image"],
                         worker["properties"]["template"]["containers"][0]["image"])

    def test_evidence_readback_catalog_keeps_private_data_flows_narrow(self):
        base = fixed_nsg._fixed_catalog(False)
        enabled = fixed_nsg._fixed_catalog(True)
        self.assertNotIn("audit", base)
        manifest = json.loads((ROOT / "infra/modules/network/nsg/audit-evidence-rules.json").read_text())["groups"]
        for group in manifest:
            rules = enabled[group]["rules"]
            priorities = {(rule["direction"], rule["priority"]) for rule in rules.values()}
            self.assertEqual(len(priorities), len(rules))
        self.assertEqual(enabled["apim"]["rules"]["allow-audit-relay-https-egress"]["destination"], ["ip:audit"])
        self.assertEqual(enabled["management"]["rules"]["allow-audit-evidence-https-egress"]["destination"], ["ips:evidenceBlob"])
        self.assertEqual({rule["direction"] for rule in enabled["audit"]["rules"].values() if rule["access"] == "Deny"},
                         {"Inbound", "Outbound"})

    def test_transport_and_storage_disable_local_auth_and_separate_checkpoint_privilege(self):
        template = compile_bicep("infra/modules/audit-evidence/audit-evidence-data.bicep")
        resources = template["resources"]
        namespace = next(r for r in resources if r["type"] == "Microsoft.EventHub/namespaces")
        self.assertTrue(namespace["properties"]["disableLocalAuth"])
        self.assertEqual(namespace["properties"]["publicNetworkAccess"], "Disabled")
        accounts = [r for r in resources if r["type"] == "Microsoft.Storage/storageAccounts"]
        self.assertEqual(len(accounts), 2)
        for account in accounts:
            self.assertEqual(account["properties"], "[variables('privateStorage')]")
        storage = template["variables"]["privateStorage"]
        self.assertEqual(storage["publicNetworkAccess"], "Disabled")
        self.assertFalse(storage["allowSharedKeyAccess"])
        creator = next(r for r in resources if r["type"] == "Microsoft.Authorization/roleDefinitions")
        self.assertEqual(creator["properties"]["permissions"], [
            {"dataActions": ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action"]},
        ])

    def test_payload_lifecycle_and_worm_are_exclusive_and_locked_reapply_skips_put(self):
        template = compile_bicep("infra/modules/audit-evidence/audit-evidence-data.bicep")
        resources = template["resources"]
        expiry = next(r for r in resources if r["type"].endswith("/managementPolicies"))
        worm = next(r for r in resources if r["type"].endswith("/immutabilityPolicies"))
        evidence = template["outputs"]["data"]["value"]
        cases = [
            ("Disabled", True, False, True, False),
            ("Locked", True, False, False, True),
            ("Locked", False, False, False, False),
            ("Locked", True, True, False, False),
        ]
        for mode, manage, retained, expected_expiry, expected_worm in cases:
            with self.subTest(mode=mode, manage=manage, retained=retained):
                evaluator = NetworkArmExpression("[true()]", template)
                parameters = {"stem": "test-fixture", "payloadImmutabilityMode": mode,
                              "payloadRetentionDays": 2557 if mode == "Locked" else 30,
                              "manageAuditImmutabilityPolicies": manage}
                coordinates = evaluator.resolve(evidence["payloadImmutability"], parameters)
                expected_policy = evaluator.resolve(evidence["evidenceContainerId"], parameters) + "/immutabilityPolicies/default"
                self.assertEqual(coordinates, {"policyId": expected_policy, "mode": mode,
                                               "retentionInDays": parameters["payloadRetentionDays"]})
                parameters["retainedAuditImmutabilityPolicyIds"] = [expected_policy.upper()] if retained else []
                self.assertIs(evaluator.resolve(expiry["condition"], parameters), expected_expiry)
                self.assertIs(evaluator.resolve(worm["condition"], parameters), expected_worm)
                self.assertFalse(worm["properties"]["allowProtectedAppendWrites"])
