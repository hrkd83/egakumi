"""サービスRGのPortalからNWのsubnet参照を解放してからazd downへ渡す。

CA→CAEの非同期削除と、CAEが管理するLBのsubnet参照が残る実際の依存関係を
ARMの偽応答で再現する。配備記録と所有確認が済むまで変更を始めないことも調べる。
"""
from __future__ import annotations

import copy
import unittest
from urllib.parse import parse_qs, urlsplit

import test_cleanup as cleanup_tests

cleanup = cleanup_tests.cleanup


class PortalTransport(cleanup_tests.FakeTransport):
    """CA/CAEの404後も、管理LBによるsubnet占有がしばらく残るARMを再現する。"""

    def __init__(self, resources, *, application_id, environment_id, subnet_id,
                 resource_delete_reads=1, subnet_release_reads=1):
        super().__init__(resources)
        self.application_id = application_id
        self.environment_id = environment_id
        self.subnet_id = subnet_id
        self.resource_delete_reads = resource_delete_reads
        self.subnet_release_reads = subnet_release_reads
        self.pending = {}
        self.responses = []
        self.environment_delete_blocked = False
        self.subnet_release_started = False

    def request(self, method, path, *, body=None, headers=None):
        resource_id = urlsplit(path).path
        if method == "DELETE" and resource_id in (self.application_id, self.environment_id):
            if resource_id == self.environment_id and self.application_id in self.resources:
                self.environment_delete_blocked = True
                response = {"statusCode": 409, "body": {}, "headers": {}}
            elif (method, resource_id) in self.forced:
                response = super().request(method, path, body=body, headers=headers)
                self.responses.append((method, resource_id, response["statusCode"]))
                return response
            else:
                self.pending[resource_id] = self.resource_delete_reads
                if resource_id in self.resources:
                    self.resources[resource_id]["properties"]["provisioningState"] = "Deleting"
                response = {"statusCode": 202, "body": {}, "headers": {}}
            self.calls.append({"method": method, "id": resource_id, "path": path,
                               "headers": headers, "body": body})
        else:
            if method == "GET" and resource_id in self.pending:
                if self.pending[resource_id] <= 0:
                    self.resources.pop(resource_id, None)
                    self.pending.pop(resource_id)
                    if resource_id == self.environment_id:
                        self.subnet_release_started = True
                else:
                    self.pending[resource_id] -= 1
            if method == "GET" and resource_id == self.subnet_id and self.subnet_release_started:
                if self.subnet_release_reads <= 0:
                    if self.subnet_id in self.resources:
                        self.resources[self.subnet_id]["properties"].update(
                            ipConfigurations=[], serviceAssociationLinks=[])
                else:
                    self.subnet_release_reads -= 1
            response = super().request(method, path, body=body, headers=headers)
        self.responses.append((method, resource_id, response["statusCode"]))
        return response


class PortalCleanupTests(unittest.TestCase):
    """新しいsvc CA/CAEと別RGのNWという配置を、公開pre_down入口で検証する。"""

    def setUp(self):
        # 既存fixtureの最小構成を借りる。CleanupTests自体を継承して全試験を二重実行しない。
        self.base = cleanup_tests.CleanupTests(methodName="runTest")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.config = self.base.configuration
        self.state = self.base.state
        self.application_id = self.base.svc_rg + "/providers/Microsoft.App/containerApps/ca-test-egakumi-portal"
        self.environment_id = self.base.svc_rg + "/providers/Microsoft.App/managedEnvironments/cae-test-egakumi"
        self.nw_rg = f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/{self.config['resourceGroupNames']['nw']}"
        self.subnet_id = self.nw_rg + "/providers/Microsoft.Network/virtualNetworks/vnet-test-egakumi/subnets/snet-test-egakumi-aca"
        self.managed_rg = f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/ME-fixture-portal"
        self.lb_ip_id = self.managed_rg + "/providers/Microsoft.Network/loadBalancers/fixture/frontendIPConfigurations/private-ip"
        self.state["outputs"]["portal"] = self.portal_output()

    def portal_output(self):
        return {"applicationId": self.application_id, "applicationName": "ca-test-egakumi-portal",
                "environmentId": self.environment_id, "environmentName": "cae-test-egakumi",
                "resourceGroup": self.config["resourceGroupNames"]["svc"],
                "applicationResourceGroup": self.config["resourceGroupNames"]["svc"],
                "environmentResourceGroup": self.config["resourceGroupNames"]["svc"]}

    def transport(self, **options):
        own_tags = {"product": "egakumi", "environment": "test"}
        resources = {
            self.application_id: {"id": self.application_id, "name": "ca-test-egakumi-portal", "type": "Microsoft.App/containerApps",
                "location": "Japan East", "tags": copy.deepcopy(own_tags),
                "properties": {"managedEnvironmentId": self.environment_id, "provisioningState": "Succeeded"}},
            self.environment_id: {"id": self.environment_id, "name": "cae-test-egakumi", "type": "Microsoft.App/managedEnvironments",
                "location": "japaneast", "tags": copy.deepcopy(own_tags),
                "properties": {"vnetConfiguration": {"infrastructureSubnetId": self.subnet_id, "internal": True},
                               "infrastructureResourceGroup": "ME-fixture-portal", "provisioningState": "Succeeded"}},
            self.subnet_id: {"id": self.subnet_id, "name": "snet-test-egakumi-aca", "type": "Microsoft.Network/virtualNetworks/subnets",
                "properties": {"ipConfigurations": [{"id": self.lb_ip_id}],
                    "serviceAssociationLinks": [{"id": self.subnet_id + "/serviceAssociationLinks/fixture-app"}]}},
            self.managed_rg: {"id": self.managed_rg, "name": "ME-fixture-portal"},
        }
        return PortalTransport(resources, application_id=self.application_id,
            environment_id=self.environment_id, subnet_id=self.subnet_id, **options)

    def pre(self, transport):
        return cleanup.pre_down(self.base.config_path, self.state, transport,
            max_checks=3, wait_seconds=0, apim_max_checks=3, apim_wait_seconds=0, sleep=lambda _: None)

    def assert_error(self, code, transport):
        with self.assertRaises(cleanup.CleanupError) as raised:
            self.pre(transport)
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn("PRIVATE_SENTINEL", str(raised.exception))
        return raised.exception

    def test_waits_for_ca_then_cae_404_and_subnet_release_before_ready(self):
        transport = self.transport()
        result = self.pre(transport)
        self.assertEqual(result["status"], "ready-for-azd-down")
        self.assertFalse(transport.environment_delete_blocked)
        self.assertEqual([(c["method"], c["id"]) for c in transport.mutations()],
                         [("DELETE", self.application_id), ("DELETE", self.environment_id)])
        responses = transport.responses
        ca_absent = responses.index(("GET", self.application_id, 404))
        env_delete = responses.index(("DELETE", self.environment_id, 202))
        env_absent = responses.index(("GET", self.environment_id, 404))
        subnet_reads = [i for i, call in enumerate(responses) if call[:2] == ("GET", self.subnet_id)]
        self.assertLess(ca_absent, env_delete)
        self.assertGreater(subnet_reads[-1], env_absent)
        self.assertGreaterEqual(len(subnet_reads), 2)
        portal = result["portal"]
        self.assertTrue(portal["crossResourceGroupSubnetReferenceRemoved"])
        self.assertEqual(portal["subnet"]["status"], "released")
        self.assertEqual(portal["subnet"]["ipConfigurations"], 0)
        self.assertEqual(portal["subnet"]["serviceAssociationLinks"], 0)
        for kind in ("application", "environment"):
            self.assertEqual(portal[kind]["status"], "absent")
            self.assertEqual(portal[kind]["httpStatus"], 404)
            self.assertTrue(portal[kind]["deleteRequested"])
        self.assertIn(self.managed_rg, transport.resources)
        self.assertFalse(any(c["id"].startswith(self.managed_rg) for c in transport.calls))
        for call in transport.calls:
            if call["id"] in (self.application_id, self.environment_id):
                self.assertEqual(parse_qs(urlsplit(call["path"]).query)["api-version"], ["2025-01-01"])
            elif call["id"] == self.subnet_id:
                self.assertEqual(parse_qs(urlsplit(call["path"]).query)["api-version"], ["2024-05-01"])

    def test_ownership_preflight_finishes_before_any_model_or_policy_mutation(self):
        transport = self.transport()
        model_transport = self.base.model_transport(count=1)
        transport.resources.update(model_transport.resources)
        # 拒否は最初の所有検査で起こるため、Foundryの一覧処理に入ってはいけない。
        transport.resources[self.environment_id]["tags"]["product"] = "foreign"
        self.assert_error("portal-owner-mismatch", transport)
        self.assertFalse(transport.mutations())
        self.assertFalse(any(c["id"].endswith("/deployments") for c in transport.calls))

    def test_portal_jobs_are_deleted_before_shared_environment_with_or_without_saved_output(self):
        for saved_output in (True, False):
            with self.subTest(saved_output=saved_output):
                self.state["outputs"]["portal"] = self.portal_output()
                if not saved_output:
                    self.state["outputs"].pop("portal")
                transport = self.transport()
                jobs = []
                for suffix in ('portal-schema', 'portal-groups'):
                    name = 'caj-' + self.config['resourceStem'] + '-' + suffix
                    resource_id = self.base.svc_rg + '/providers/Microsoft.App/jobs/' + name
                    transport.resources[resource_id] = {'id': resource_id, 'name': name, 'type': 'Microsoft.App/jobs',
                        'location': self.config['location'],
                        'tags': {'product': self.config['productName'], 'environment': self.config['environmentName']},
                        'properties': {'environmentId': self.environment_id}}
                    jobs.append(resource_id)
                result = self.pre(transport)
                self.assertEqual(result['portalJobs'], jobs)
                deleted = [call['id'] for call in transport.mutations()]
                for job in jobs:
                    self.assertLess(deleted.index(job), deleted.index(self.environment_id))

    def test_foreign_portal_job_is_rejected_before_mutations(self):
        transport = self.transport()
        name = 'caj-' + self.config['resourceStem'] + '-portal-groups'
        resource_id = self.base.svc_rg + '/providers/Microsoft.App/jobs/' + name
        transport.resources[resource_id] = {'id': resource_id, 'name': name, 'type': 'Microsoft.App/jobs',
            'location': self.config['location'],
            'tags': {'product': 'foreign', 'environment': self.config['environmentName']},
            'properties': {'environmentId': self.environment_id}}
        self.assert_error('portal-owner-mismatch', transport)
        self.assertFalse(transport.mutations())

    def test_foreign_live_resource_id_type_name_location_and_tags_are_rejected(self):
        cases = (("id", lambda r: r.update(id=r["id"] + "-foreign")),
                 ("name", lambda r: r.update(name=r["name"] + "-foreign")),
                 ("type", lambda r: r.update(type="Microsoft.Network/virtualNetworks")),
                 ("location", lambda r: r.update(location="eastus2")),
                 ("product", lambda r: r["tags"].update(product="other")),
                 ("environment", lambda r: r["tags"].update(environment="production")))
        for target in (self.application_id, self.environment_id):
            for label, mutation in cases:
                with self.subTest(target=target, mismatch=label):
                    transport = self.transport()
                    mutation(transport.resources[target])
                    self.assert_error("portal-owner-mismatch", transport)
                    self.assertFalse(transport.mutations())

    def test_app_environment_and_environment_subnet_bindings_are_checked_before_writes(self):
        for binding in ("application-environment", "environment-subnet"):
            with self.subTest(binding=binding):
                transport = self.transport()
                if binding == "application-environment":
                    transport.resources[self.application_id]["properties"]["managedEnvironmentId"] += "-foreign"
                else:
                    transport.resources[self.environment_id]["properties"]["vnetConfiguration"]["infrastructureSubnetId"] += "-foreign"
                self.assert_error("portal-binding-mismatch", transport)
                self.assertFalse(transport.mutations())

    def test_state_output_cannot_redirect_to_other_subscription_rg_name_or_partial_record(self):
        valid = self.portal_output()
        for field, value in (("applicationId", self.application_id.replace("rg-test-egakumi-svc", "rg-other")),
            ("environmentId", self.environment_id.replace(self.config["subscriptionId"], "22222222-2222-4222-8222-222222222222")),
            ("applicationName", "another-app"), ("environmentName", "another-environment"),
            ("applicationResourceGroup", self.config["resourceGroupNames"]["nw"]),
            ("environmentResourceGroup", self.config["resourceGroupNames"]["nw"]),
            ("resourceGroup", self.config["resourceGroupNames"]["nw"]), ("partial", None)):
            with self.subTest(field=field):
                self.state["outputs"]["portal"] = copy.deepcopy(valid)
                if field == "partial":
                    self.state["outputs"]["portal"] = {"applicationId": self.application_id}
                else:
                    self.state["outputs"]["portal"][field] = value
                transport = self.transport()
                self.assert_error("portal-state-mismatch", transport)
                self.assertFalse(transport.mutations())

    def test_absent_resources_are_idempotent_without_delete(self):
        transport = self.transport()
        for target in (self.application_id, self.environment_id):
            transport.resources.pop(target)
        transport.resources[self.subnet_id]["properties"].update(ipConfigurations=[], serviceAssociationLinks=[])
        result = self.pre(transport)
        self.assertFalse(transport.mutations())
        for kind in ("application", "environment"):
            self.assertEqual(result["portal"][kind]["status"], "absent")
            self.assertFalse(result["portal"][kind]["deleteRequested"])

    def test_missing_records_and_resources_still_detect_dangling_subnet_references(self):
        self.state["outputs"].pop("portal")
        transport = self.transport()
        for target in (self.application_id, self.environment_id):
            transport.resources.pop(target)
        self.assert_error("portal-subnet-release-pending", transport)
        self.assertFalse(transport.mutations())
        self.assertEqual(sum(call["id"] == self.subnet_id for call in transport.calls), 3)

    def test_missing_records_and_resources_accept_verified_absent_subnet(self):
        self.state["outputs"].pop("portal")
        transport = self.transport()
        for target in (self.application_id, self.environment_id, self.subnet_id):
            transport.resources.pop(target)
        result = self.pre(transport)
        self.assertEqual(result["status"], "ready-for-azd-down")
        self.assertFalse(transport.mutations())
        self.assertEqual(result["portal"]["subnet"]["status"], "absent")
        self.assertEqual(result["portal"]["subnet"]["httpStatus"], 404)

    def test_existing_owned_portal_without_saved_or_module_output_is_recovered(self):
        self.state["outputs"].pop("portal")
        transport = self.transport()
        result = self.pre(transport)
        self.assertEqual(result["portal"]["recordSource"], "verified-current-resources")
        self.assertEqual(result["portal"]["subnet"]["status"], "released")
        self.assertEqual([call["id"] for call in transport.mutations()],
                         [self.application_id, self.environment_id])

    def test_partial_main_uses_succeeded_portal_module_output_to_verify_ownership(self):
        self.state["outputs"].pop("portal")
        transport = self.transport()
        module_id = self.base.svc_rg + "/providers/Microsoft.Resources/deployments/test-egakumi-portal"
        transport.resources[module_id] = {"id": module_id, "name": "test-egakumi-portal",
            "type": "Microsoft.Resources/deployments", "properties": {"provisioningState": "Succeeded",
                "outputs": {"platform": {"type": "Object", "value": self.portal_output()}}}}
        result = self.pre(transport)
        self.assertEqual(result["portal"]["recordSource"], "portal-deployment-output")
        self.assertEqual([c["id"] for c in transport.mutations()], [self.application_id, self.environment_id])

    def test_retry_recovers_module_after_ca_and_cae_are_gone_and_still_waits_for_subnet(self):
        """前の撤去でCA/CAEだけ消えた場合も、成功出力からsubnetの解放確認を再開する。"""
        for release_reads in (1, 100):
            with self.subTest(release_reads=release_reads):
                self.state["outputs"].pop("portal", None)
                transport = self.transport(subnet_release_reads=release_reads)
                for target in (self.application_id, self.environment_id):
                    transport.resources.pop(target)
                # このプロセスのDELETEではなく、前の実行でCAEが消えた状態。
                transport.subnet_release_started = True
                module_id = self.base.svc_rg + "/providers/Microsoft.Resources/deployments/test-egakumi-portal"
                transport.resources[module_id] = {"id": module_id, "name": "test-egakumi-portal",
                    "type": "Microsoft.Resources/deployments", "properties": {"provisioningState": "Succeeded",
                        "outputs": {"platform": {"type": "Object", "value": self.portal_output()}}}}
                if release_reads == 100:
                    self.assert_error("portal-subnet-release-pending", transport)
                    self.assertEqual(len([c for c in transport.calls if c["id"] == self.subnet_id]), 3)
                else:
                    result = self.pre(transport)
                    self.assertEqual(result["portal"]["recordSource"], "portal-deployment-output")
                    self.assertEqual(result["portal"]["subnet"]["status"], "released")
                    self.assertTrue(result["portal"]["crossResourceGroupSubnetReferenceRemoved"])
                    self.assertGreaterEqual(len([c for c in transport.calls if c["id"] == self.subnet_id]), 2)
                self.assertTrue(any(c["id"] == module_id for c in transport.calls))
                self.assertFalse(transport.mutations())
                self.assertIn(self.managed_rg, transport.resources)

    def test_null_or_empty_unused_environment_alias_preserves_valid_binding(self):
        for active, unused in (("managedEnvironmentId", "environmentId"), ("environmentId", "managedEnvironmentId")):
            for empty in (None, ""):
                with self.subTest(active=active, unused=unused, empty=empty):
                    transport = self.transport(resource_delete_reads=0)
                    properties = transport.resources[self.application_id]["properties"]
                    properties[active] = self.environment_id
                    properties[unused] = empty
                    result = self.pre(transport)
                    self.assertEqual(result["status"], "ready-for-azd-down")
                    self.assertEqual([c["id"] for c in transport.mutations()], [self.application_id, self.environment_id])

    def test_conflicting_nonempty_or_absent_environment_aliases_stop_before_writes(self):
        for managed, environment in ((self.environment_id, self.environment_id + "-foreign"),
                                    (self.environment_id + "-foreign", self.environment_id),
                                    (None, "")):
            with self.subTest(managed=managed, environment=environment):
                transport = self.transport()
                properties = transport.resources[self.application_id]["properties"]
                properties.update(managedEnvironmentId=managed, environmentId=environment)
                self.assert_error("portal-binding-mismatch", transport)
                self.assertFalse(transport.mutations())

    def test_retry_of_already_deleting_environment_waits_without_second_delete(self):
        transport = self.transport()
        transport.resources.pop(self.application_id)
        transport.resources[self.environment_id]["properties"]["provisioningState"] = "Deleting"
        # 所有確認・実行直前の再読取を経てもDeletingであり、待機中に404となる。
        transport.pending[self.environment_id] = 3
        result = self.pre(transport)
        self.assertFalse(transport.mutations())
        self.assertTrue(result["portal"]["environment"]["alreadyDeleting"])
        self.assertFalse(result["portal"]["environment"]["deleteRequested"])
        self.assertEqual(result["portal"]["environment"]["httpStatus"], 404)

    def test_pending_app_does_not_delete_environment_or_report_ready(self):
        transport = self.transport(resource_delete_reads=100)
        self.assert_error("cleanup-pending", transport)
        self.assertEqual([c["id"] for c in transport.mutations()], [self.application_id])
        self.assertIn(self.environment_id, transport.resources)

    def test_pending_environment_does_not_report_subnet_released(self):
        transport = self.transport(resource_delete_reads=0)
        transport.resources.pop(self.application_id)
        transport.resource_delete_reads = 100
        self.assert_error("cleanup-pending", transport)
        self.assertEqual([c["id"] for c in transport.mutations()], [self.environment_id])
        self.assertFalse(any(c["id"] == self.subnet_id for c in transport.calls))

    def test_lb_or_service_association_reference_must_disappear_before_ready(self):
        for reference in ("ipConfigurations", "serviceAssociationLinks", "resourceNavigationLinks", "ipConfigurationProfiles"):
            with self.subTest(reference=reference):
                transport = self.transport(resource_delete_reads=0, subnet_release_reads=100)
                properties = transport.resources[self.subnet_id]["properties"]
                properties.update(ipConfigurations=[], serviceAssociationLinks=[])
                properties[reference] = [{"id": self.lb_ip_id}]
                self.assert_error("portal-subnet-release-pending", transport)
                self.assertNotIn(self.application_id, transport.resources)
                self.assertNotIn(self.environment_id, transport.resources)
                self.assertIn(self.managed_rg, transport.resources)
                self.assertEqual([c["id"] for c in transport.mutations()], [self.application_id, self.environment_id])

    def test_missing_subnet_after_ca_and_cae_deletion_is_verified_absence(self):
        transport = self.transport(resource_delete_reads=0)
        transport.resources.pop(self.subnet_id)
        result = self.pre(transport)
        self.assertEqual(result["portal"]["subnet"]["status"], "absent")
        self.assertEqual(result["portal"]["subnet"]["httpStatus"], 404)
        self.assertTrue(result["portal"]["crossResourceGroupSubnetReferenceRemoved"])

    def test_subnet_response_with_foreign_id_or_invalid_reference_shape_is_not_success(self):
        for invalid in ("foreign-id", None, {}, "PRIVATE_SENTINEL", [None], [{"name": "without-id"}], [{"id": "relative-id"}]):
            with self.subTest(invalid=invalid):
                transport = self.transport(resource_delete_reads=0, subnet_release_reads=100)
                subnet = transport.resources[self.subnet_id]
                if invalid == "foreign-id":
                    subnet["id"] += "-foreign"
                else:
                    subnet["properties"]["ipConfigurations"] = invalid
                self.assert_error("portal-binding-mismatch", transport)
                self.assertEqual([c["id"] for c in transport.mutations()], [self.application_id, self.environment_id])

    def test_unusable_module_output_falls_back_to_verified_current_resources(self):
        for invalid in ("failed", "foreign-id", "foreign-name", "not-object"):
            with self.subTest(invalid=invalid):
                self.state["outputs"].pop("portal", None)
                transport = self.transport()
                module_id = self.base.svc_rg + "/providers/Microsoft.Resources/deployments/test-egakumi-portal"
                deployment = {"id": module_id, "name": "test-egakumi-portal", "type": "Microsoft.Resources/deployments",
                    "properties": {"provisioningState": "Succeeded", "outputs": {
                        "platform": {"type": "Object", "value": self.portal_output()}}}}
                if invalid == "failed":
                    deployment["properties"]["provisioningState"] = "Failed"
                elif invalid == "foreign-id":
                    deployment["id"] += "-foreign"
                elif invalid == "foreign-name":
                    deployment["name"] += "-foreign"
                else:
                    deployment["properties"]["outputs"]["platform"]["type"] = "String"
                transport.resources[module_id] = deployment
                result = self.pre(transport)
                self.assertEqual(result["portal"]["recordSource"], "verified-current-resources")
                self.assertEqual([call["id"] for call in transport.mutations()],
                                 [self.application_id, self.environment_id])

    def test_malformed_successful_module_output_cannot_override_ownership(self):
        self.state["outputs"].pop("portal")
        transport = self.transport()
        module_id = self.base.svc_rg + "/providers/Microsoft.Resources/deployments/test-egakumi-portal"
        transport.resources[module_id] = {"id": module_id, "name": "test-egakumi-portal",
            "type": "Microsoft.Resources/deployments", "properties": {"provisioningState": "Succeeded",
                "outputs": {"platform": {"type": "Object", "value": {"applicationId": self.application_id}}}}}
        self.assert_error("portal-state-mismatch", transport)
        self.assertFalse(transport.mutations())

    def test_owned_failed_environment_without_application_or_completed_binding_can_be_removed(self):
        self.state["outputs"].pop("portal")
        transport = self.transport()
        transport.resources.pop(self.application_id)
        transport.resources[self.environment_id]["properties"] = {"provisioningState": "Failed"}
        result = self.pre(transport)
        self.assertEqual(result["portal"]["recordSource"], "verified-current-resources")
        self.assertEqual(result["portal"]["subnet"]["status"], "released")
        self.assertEqual([call["id"] for call in transport.mutations()], [self.environment_id])

    def test_failed_resource_with_contradictory_binding_still_stops_before_mutations(self):
        for target, properties in (
            (self.application_id, {"managedEnvironmentId": self.environment_id + "-foreign"}),
            (self.environment_id, {"vnetConfiguration": {"infrastructureSubnetId": self.subnet_id + "-foreign"}}),
        ):
            with self.subTest(target=target):
                self.state["outputs"].pop("portal", None)
                transport = self.transport()
                transport.resources[target]["properties"] = {"provisioningState": "Failed", **properties}
                self.assert_error("portal-binding-mismatch", transport)
                self.assertFalse(transport.mutations())

    def test_healthy_resource_missing_binding_is_not_treated_as_incomplete(self):
        for target in (self.application_id, self.environment_id):
            with self.subTest(target=target):
                self.state["outputs"].pop("portal", None)
                transport = self.transport()
                transport.resources[target]["properties"] = {"provisioningState": "Succeeded"}
                self.assert_error("portal-binding-mismatch", transport)
                self.assertFalse(transport.mutations())

    def test_changed_owner_while_delete_is_pending_stops_before_environment_delete(self):
        transport = self.transport(resource_delete_reads=2)
        original_request = transport.request

        def replace_owner_during_delete(method, path, *, body=None, headers=None):
            target = urlsplit(path).path
            if method == "GET" and target == self.application_id and target in transport.pending:
                transport.resources[target]["tags"]["environment"] = "foreign"
            return original_request(method, path, body=body, headers=headers)

        transport.request = replace_owner_during_delete
        self.assert_error("portal-owner-mismatch", transport)
        self.assertEqual([c["id"] for c in transport.mutations()], [self.application_id])
        self.assertIn(self.environment_id, transport.resources)

    def test_app_delete_failure_preserves_environment_and_propagates_reason(self):
        transport = self.transport()
        transport.forced["DELETE", self.application_id] = 403
        error = self.assert_error("azure-request-failed", transport)
        self.assertEqual(error.resource_id, self.application_id)
        self.assertIn(self.environment_id, transport.resources)
        self.assertEqual([c["id"] for c in transport.mutations()], [self.application_id])


if __name__ == "__main__":
    unittest.main()
