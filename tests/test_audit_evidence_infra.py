"""Verify the opt-in evidence infrastructure without Azure or production inputs."""

import json
from pathlib import Path
import unittest

from bicep_helpers import compile_bicep
from configuration_fixtures import configuration, fixture
from deployment.helpers import fixed_nsg
from schema_helpers import local_schema_validator
from test_parameter_ip_ownership import NetworkArmExpression


ROOT = Path(__file__).resolve().parents[1]


class EvidenceConfigurationTests(unittest.TestCase):
    def test_shipped_profiles_enable_only_their_selected_tier_and_a_dedicated_subnet(self):
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

    def test_enabling_evidence_requires_an_explicit_nonoverlapping_subnet(self):
        raw = fixture()
        raw["auditEvidence"]["enabled"] = True
        with self.assertRaisesRegex(ValueError, r"subnetPrefixes\.audit"):
            configuration.normalize_configuration(raw)
        prefixes = raw["network"]["subnetPrefixes"]
        prefixes["audit"] = prefixes["broker"]
        with self.assertRaisesRegex(ValueError, "subnets overlap"):
            configuration.normalize_configuration(raw)

    def test_retention_validation_accepts_worm_period_without_widening_test_expiry(self):
        schemas = [local_schema_validator(json.loads((ROOT / f"schemas/{name}.schema.json").read_text())
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
        manifest = json.loads((ROOT / "infra/audit-evidence-nsg.json").read_text())["groups"]
        for group in manifest:
            rules = enabled[group]["rules"]
            priorities = {(rule["direction"], rule["priority"]) for rule in rules.values()}
            self.assertEqual(len(priorities), len(rules))
        self.assertEqual(enabled["apim"]["rules"]["allow-audit-relay-https-egress"]["destination"], ["ip:audit"])
        self.assertEqual(enabled["management"]["rules"]["allow-audit-evidence-https-egress"]["destination"], ["ips:evidenceBlob"])
        self.assertEqual({rule["direction"] for rule in enabled["audit"]["rules"].values() if rule["access"] == "Deny"},
                         {"Inbound", "Outbound"})

    def test_transport_and_storage_disable_local_auth_and_separate_checkpoint_privilege(self):
        template = compile_bicep("infra/modules/audit-evidence-data.bicep")
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
        template = compile_bicep("infra/modules/audit-evidence-data.bicep")
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
