"""CAの初期作成とアプリ更新の境界を、合成ARM応答と実Bicepで検証する。"""
from bicep_helpers import compile_bicep

import contextlib
import copy
import io
import json
from pathlib import Path
import unittest
from unittest import mock

from lifecycle_fixtures import LifecycleFixture, ScriptedTransport, lifecycle

CORE = Path(__file__).resolve().parents[1]


class PortalBootstrapLifecycleTests(LifecycleFixture, unittest.TestCase):
    def app_response(self):
        prefix = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/"
                  f"{self.config['resourceGroupNames']['svc']}/providers/Microsoft.App/")
        identity_id = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/"
                       f"{self.config['resourceGroupNames']['id']}/providers/Microsoft.ManagedIdentity/"
                       f"userAssignedIdentities/id-{self.config['resourceStem']}-portal-pull")
        return {"statusCode": 200, "body": {
            "id": prefix + f"containerApps/ca-{self.config['resourceStem']}-portal",
            "name": f"ca-{self.config['resourceStem']}-portal",
            "type": "Microsoft.App/containerApps", "location": self.config["location"],
            "identity": {"type": "UserAssigned", "userAssignedIdentities": {identity_id: {}}},
            "properties": {
                "managedEnvironmentId": prefix + f"managedEnvironments/cae-{self.config['resourceStem']}",
                "provisioningState": "Succeeded",
                "template": {"containers": [{"image": "fixture/portal@sha256:published",
                    "env": [{"name": "PRIVATE_FIXTURE", "value": "PRIVATE_VALUE"}]}]},
                "configuration": {"ingress": {"targetPort": 3000}, "registries": [{
                    "server": "acr" + self.config["resourceStem"].replace("-", "") + ".azurecr.io",
                    "identity": identity_id}]},
            },
        }}

    def test_only_not_found_allows_initial_creation(self):
        absent = ScriptedTransport([{"statusCode": 404, "body": None}])
        self.assertFalse(lifecycle.discover_existing_portal_app(self.config, absent))
        self.assertEqual(absent.calls[0]["method"], "GET")
        self.assertTrue(absent.calls[-1]["path"].endswith(
            f"/containerApps/ca-{self.config['resourceStem']}-portal?api-version=2025-01-01"))
        for status in (401, 403, 409, 429, 500):
            transport = ScriptedTransport([{"statusCode": status, "body": {"message": "PRIVATE_VALUE"}}])
            with self.subTest(status=status), self.assertRaises(lifecycle.DeploymentError) as error:
                lifecycle.discover_existing_portal_app(self.config, transport)
            self.assertNotIn("PRIVATE_VALUE", str(error.exception))
            self.assertFalse(transport.mutations())

    def test_discovery_reads_only_the_configured_application(self):
        transport = ScriptedTransport([self.app_response()])
        self.assertTrue(lifecycle.discover_existing_portal_app(self.config, transport))
        self.assertEqual(len(transport.calls), 1)
        self.assertIn("/containerApps/ca-" + self.config["resourceStem"] + "-portal?", transport.calls[0]["path"])
        self.assertFalse(transport.mutations())

    def test_existing_app_is_preserved_without_copying_its_image_or_settings(self):
        for image in ("mcr.microsoft.com/k8se/quickstart:latest", "fixture/portal@sha256:published"):
            response = self.app_response()
            response["body"]["properties"]["template"]["containers"][0]["image"] = image
            before = copy.deepcopy(response)
            transport = ScriptedTransport([response])
            with self.subTest(image=image):
                found = lifecycle.discover_existing_portal_app(self.config, transport)
                self.assertIs(found, True)
                params = lifecycle.parameters(self.config, "core", existing_portal_app=found)
                self.assertIs(params["parameters"]["existingPortalApp"]["value"], True)
                self.assertNotIn("PRIVATE_VALUE", json.dumps(params))
                self.assertNotIn(image, json.dumps(params))
                self.assertFalse(transport.mutations())
                self.assertEqual(response, before)

    def test_mismatched_or_unready_app_stops_instead_of_overwriting(self):
        mutations = [
            lambda b: b.update(id=b["id"] + "-another"),
            lambda b: b.update(name="another"),
            lambda b: b.update(type="Microsoft.App/jobs"),
            lambda b: b.update(location="westus"),
            lambda b: b.update(properties=None),
            lambda b: b["properties"].update(managedEnvironmentId="/another/environment"),
            lambda b: b["properties"].pop("managedEnvironmentId"),
            lambda b: b["properties"].update(provisioningState="Updating"),
            lambda b: b["properties"].update(provisioningState="Failed"),
            lambda b: b["properties"].pop("provisioningState"),
        ]
        for index, mutate in enumerate(mutations):
            response = self.app_response()
            mutate(response["body"])
            transport = ScriptedTransport([response])
            with self.subTest(index=index), self.assertRaises(lifecycle.DeploymentError) as error:
                lifecycle.discover_existing_portal_app(self.config, transport)
            self.assertNotIn("PRIVATE_VALUE", str(error.exception))
            self.assertFalse(transport.mutations())

    def test_arm_case_and_display_location_are_accepted(self):
        response = self.app_response()
        body = response["body"]
        body["id"] = body["id"].upper()
        body["properties"]["managedEnvironmentId"] = body["properties"]["managedEnvironmentId"].upper()
        body["location"] = "Japan East"
        self.assertTrue(lifecycle.discover_existing_portal_app(self.config,
            ScriptedTransport([response])))

    def test_both_preparation_hooks_pass_actual_existence_into_native_azd_inputs(self):
        for phase in ("preup", "preprovision"):
            for exists in (False, True):
                response = self.app_response() if exists else {"statusCode": 404, "body": None}
                responses = ([{"statusCode": 404, "body": None}] if phase == "preprovision" else []) + [response]
                transport = ScriptedTransport(responses)
                with self.subTest(phase=phase, exists=exists), \
                        mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
                        mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
                        mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
                        mock.patch.object(lifecycle, "prepare_grpc_secret", return_value={}), \
                        mock.patch.object(lifecycle, "run", return_value={}), \
                        mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                        mock.patch.object(lifecycle, "capacity_preflight"), \
                        mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "lock_and_verify_policies"), \
                        mock.patch.object(lifecycle, "configure_app_insights_cap"), \
                        mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()), \
                        mock.patch.object(lifecycle, "discover_existing_network_security_groups", return_value=[]), \
                        mock.patch.object(lifecycle, "discover_existing_route_table_routes", return_value={}), \
                        mock.patch.object(lifecycle, "discover_existing_apim_hostnames", return_value={}), \
                        mock.patch.object(lifecycle, "discover_existing_apim_egress_fqdns", return_value=[]), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(lifecycle.main([phase]), 0)
                    values = json.loads(lifecycle.PARAMETERS.read_text())["parameters"]
                    self.assertIs(values["existingPortalApp"]["value"], exists)
                    self.assertFalse(transport.responses)
                    self.assertFalse(transport.mutations())

    def test_direct_core_checks_existing_app_but_monitoring_does_not(self):
        for phase in ("core", "monitoring"):
            for exists in (False, True):
                response = self.app_response() if exists else {"statusCode": 404, "body": None}
                responses = ([response] if phase == "core" else []) + [
                    {"statusCode": 201, "body": {}}, {"statusCode": 200, "body": {
                        "properties": {"provisioningState": "Succeeded", "outputs": {}}}}]
                transport = ScriptedTransport(responses)
                with self.subTest(phase=phase, exists=exists), \
                        mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                        mock.patch.object(lifecycle, "run", return_value={"resources": []}), \
                        mock.patch.object(lifecycle, "discover_existing_network_security_groups", return_value=[]), \
                        mock.patch.object(lifecycle, "discover_existing_route_table_routes", return_value={}), \
                        mock.patch.object(lifecycle, "discover_existing_apim_hostnames", return_value={}):
                    lifecycle.deploy_phase(self.config, phase)
                    values = transport.mutations()[0]["body"]["properties"]["parameters"]
                    self.assertIs(values["existingPortalApp"]["value"], exists if phase == "core" else False)
                    self.assertEqual([call["method"] for call in transport.calls],
                                     (["GET"] if phase == "core" else []) + ["PUT", "GET"])

    def test_preservation_does_not_require_a_particular_registry_or_identity_layout(self):
        response = self.app_response()
        response["body"].pop("identity")
        response["body"]["properties"].pop("configuration")
        transport = ScriptedTransport([response])
        self.assertTrue(lifecycle.discover_existing_portal_app(self.config, transport))
        self.assertFalse(transport.mutations())


class PortalBootstrapBicepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = {
            'main': compile_bicep('infra/main.bicep'),
            'app': compile_bicep('apps/portal/deploy/application.bicep'),
        }

    def test_core_creates_initial_ca_and_skips_its_put_when_already_present(self):
        main = self.templates["main"]
        self.assertIs(main["parameters"]["existingPortalApp"]["defaultValue"], False)
        portal, = [r for r in main["resources"] if "existingPortalApp" in r.get("properties", {}).get("parameters", {})]
        self.assertIn("core", main["variables"]["deployCore"])
        self.assertEqual(portal["condition"], "[variables('deployCore')]")
        self.assertEqual(portal["properties"]["parameters"]["existingPortalApp"]["value"], "[parameters('existingPortalApp')]")
        self.assertEqual(portal["properties"]["mode"], "Incremental")
        template = portal["properties"]["template"]
        app, = [r for r in template["resources"] if r["type"] == "Microsoft.App/containerApps"]
        self.assertEqual(app["condition"], "[not(parameters('existingPortalApp'))]")
        self.assertEqual(template["variables"]["applicationName"], "[format('ca-{0}-portal', parameters('stem'))]")
        self.assertEqual(app["name"], "[variables('applicationName')]")
        environment, = [r for r in template["resources"] if r["type"] == "Microsoft.App/managedEnvironments"]
        self.assertEqual(environment["name"], "[format('cae-{0}', parameters('stem'))]")
        self.assertEqual(app["properties"]["template"]["containers"][0]["image"], "mcr.microsoft.com/k8se/quickstart:latest")
        self.assertEqual(app["properties"]["template"]["scale"], {"minReplicas": 1, "maxReplicas": 1})
        ingress = app["properties"]["configuration"]["ingress"]
        self.assertEqual(ingress, {"external": True, "targetPort": 80, "transport": "http", "allowInsecure": False})
        self.assertEqual(app["identity"]["type"], "UserAssigned")
        self.assertEqual(template["outputs"]["platform"]["value"]["applicationName"], "[variables('applicationName')]")

    def test_resource_scopes_and_cross_group_references_follow_service_network_and_identity_layers(self):
        main = self.templates['main']
        main_resources = main['resources']
        def module(suffix):
            return next(r for r in main_resources if r.get('type') == 'Microsoft.Resources/deployments'
                        and r['name'] == "[format('{0}-" + suffix + "', variables('stem'))]")
        groups = {layer: next(r['name'] for r in main_resources if r['type'] == 'Microsoft.Resources/resourceGroups'
                             and "-" + layer + "'" in r['name']) for layer in ('svc', 'nw', 'id')}
        for name, layer in (('portal', 'svc'), ('portal-registry', 'svc'), ('portal-identity', 'id'),
                            ('portal-network', 'nw'), ('portal-network-bindings', 'nw')):
            self.assertEqual(module(name)['resourceGroup'], groups[layer])
        registry = module('portal-registry')
        self.assertIn('portal-identity', registry['properties']['parameters']['pullIdentityPrincipalId']['value'])
        nested = registry['properties']['template']['resources']
        self.assertEqual({r['type'] for r in nested},
                         {'Microsoft.ContainerRegistry/registries', 'Microsoft.Authorization/roleAssignments'})
        role = next(r for r in nested if r['type'] == 'Microsoft.Authorization/roleAssignments')
        self.assertEqual(role['properties']['principalId'], "[parameters('pullIdentityPrincipalId')]")
        network = module('portal-network')
        self.assertIn('portal-registry', network['properties']['parameters']['registryId']['value'])
        resources = network['properties']['template']['resources']
        endpoint = next(r for r in resources if r['type'] == 'Microsoft.Network/privateEndpoints')
        self.assertEqual(endpoint['properties']['privateLinkServiceConnections'][0]['properties']['privateLinkServiceId'],
                         "[parameters('registryId')]")
        self.assertTrue(any('portal-network' in item for item in module('portal')['dependsOn']))
        self.assertTrue(any('portal-network-bindings' in item for item in module('gateway')['dependsOn']))
        portal_output = main['outputs']['portal']['value']
        for key in ('registryResourceGroup', 'pullIdentityResourceGroup', 'networkResourceGroup'):
            self.assertIn(key, portal_output)

    def test_real_app_updates_the_same_ca_and_adds_application_health_checks(self):
        app, = [r for r in self.templates["app"]["resources"] if r["type"] == "Microsoft.App/containerApps"]
        self.assertEqual(app["name"], "[format('ca-{0}-portal', parameters('stem'))]")
        self.assertEqual(app["tags"], "[parameters('tags')]")
        self.assertEqual(app["properties"]["configuration"]["ingress"]["targetPort"], 3000)
        container = app["properties"]["template"]["containers"][0]
        self.assertIn("/api/health", json.dumps(container))
        self.assertIn("Readiness", json.dumps(container))
        self.assertEqual(container["name"], "portal")
        self.assertIn("imageDigest", container["image"])
        self.assertEqual(app["properties"]["template"]["scale"], {"minReplicas": 1, "maxReplicas": 2})


if __name__ == "__main__":
    unittest.main()
