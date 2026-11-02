"""azdの各段階を正しい順で実行し、失敗時に止まることを検証する。

偽のCLIとARM transportで事前確認・監視準備・構築後設定・撤去の境界を再現する。秘密の一時入力や保持設定を扱うため、成功系だけでなく中断時の挙動も調べる。
"""
from __future__ import annotations

import copy
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment import lifecycle
from deployment.helpers import cleanup
from deployment.helpers import prepare_secrets as secret_preparation
from deployment.helpers.configuration import normalize_configuration
from configuration_fixtures import connection_fixture, fixture, ssh_fixture_key


from lifecycle_fixtures import LifecycleFixture, ScriptedTransport, response_policy


class LifecycleTests(LifecycleFixture, unittest.TestCase):
    """azdの各段階を正しい順で実行し、失敗時に止まることを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def test_foundation_postprovision_preserves_api_configuration_and_cleans_inputs(self):
        """基盤の再配置は API の説明や公開設定を書き換えず、入口だけを仕上げる。"""
        outputs = self.outputs()
        outputs["endpoints"] = {"public": "https://fixture.example.com"}
        response = {"properties": {"outputs": {key: {"value": value} for key, value in outputs.items()}}}
        receipt = lifecycle.ROOT / ".state/api-catalog-publication.json"
        transport = mock.Mock()
        transport.request.side_effect = AssertionError("Foundation must not update API resources")
        for fails in (False, False, True):
            lifecycle.protected_json(lifecycle.PARAMETERS, {"fixture": "PRIVATE_INPUT"})
            lifecycle.protected_json(receipt, {"status": "explicit-api-publication"})
            with mock.patch.object(lifecycle, "run", side_effect=[{"CORE_DEPLOYMENT_NAME": "owned-core"}, response]), \
                 mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                 mock.patch.object(lifecycle, "configure_grpc_endpoint", side_effect=ValueError("ingress-failed") if fails else None,
                                   return_value={"enabled": False}) as ingress, \
                 contextlib.redirect_stdout(io.StringIO()):
                if fails:
                    with self.assertRaisesRegex(ValueError, "ingress-failed"):
                        lifecycle.postprovision(self.config)
                else:
                    lifecycle.postprovision(self.config)
            ingress.assert_called_once()
            transport.request.assert_not_called()
            self.assertTrue(lifecycle.STATE.exists())
            self.assertFalse(lifecycle.PARAMETERS.exists())
            self.assertEqual(json.loads(receipt.read_text()), {"status": "explicit-api-publication"})

    def test_postprovision_scopes_public_dns_to_its_subscription_and_keeps_azure_tenant(self):
        """DNSを別Subscriptionへ設定し、CMの認証テナントへ接続しない。"""
        self.config["certificate"].update(mode="dnsPlugin", plugin="Azure",
            azureDnsSubscriptionId="22222222-2222-2222-2222-222222222222")
        outputs = self.outputs()
        outputs["endpoints"] = {"public": "https://fixture.example.com"}
        response = {"properties": {"outputs": {key: {"value": value} for key, value in outputs.items()}}}
        core, dns = mock.Mock(), mock.Mock()
        with mock.patch.object(lifecycle, "run", side_effect=[{"CORE_DEPLOYMENT_NAME": "owned-core"}, response]), \
             mock.patch.object(lifecycle, "ArmTransport", side_effect=[core, dns]) as factory, \
             mock.patch.object(lifecycle, "configure_grpc_endpoint", return_value={"enabled": False}), \
             contextlib.redirect_stdout(io.StringIO()):
            lifecycle.postprovision(self.config)
        dns_config = factory.call_args_list[1].args[0]
        self.assertEqual(dns_config["subscriptionId"], "22222222-2222-2222-2222-222222222222")
        self.assertEqual(dns_config["azureTenantId"], self.config["azureTenantId"])
        self.public_dns.assert_called_once_with(self.config, outputs, core, dns)
        self.assertEqual(json.loads((lifecycle.ROOT / ".state/public-dns.json").read_text()), {"status": "configured"})

    def test_public_dns_failure_preserves_deployment_state_and_removes_secret_inputs(self):
        """DNSの保存失敗を成功扱いにせず、構築記録と秘密入力回収を維持する。"""
        outputs = self.outputs()
        response = {"properties": {"outputs": {key: {"value": value} for key, value in outputs.items()}}}
        lifecycle.protected_json(lifecycle.PARAMETERS, {"fixture": "PRIVATE_INPUT"})
        lifecycle.protected_json(lifecycle.ROOT / ".state/public-dns.json", {"status": "old-success"})
        self.public_dns.side_effect = ValueError("Public DNS: record conflict")
        with mock.patch.object(lifecycle, "run", side_effect=[{"CORE_DEPLOYMENT_NAME": "owned-core"}, response]), \
             mock.patch.object(lifecycle, "ArmTransport"), \
             mock.patch.object(lifecycle, "configure_grpc_endpoint", return_value={"enabled": False}), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "record conflict"):
                lifecycle.postprovision(self.config)
        self.assertTrue(lifecycle.STATE.exists())
        self.assertFalse(lifecycle.PARAMETERS.exists())
        self.assertFalse((lifecycle.ROOT / ".state/public-dns.json").exists())

    def test_preprovision_all_mutable_paths_and_ingress_credentials_stay_in_temporary_root(self):
        """実 ROOT の鍵を読まず、gRPC/音声の fixture 鍵も temp だけへ作成する。"""
        self.config["gateway"]["grpc"]["enabled"] = True
        self.config["gateway"]["realtimeControlEnabled"] = True
        for name in ("ROOT", "PARAMETERS", "STATE", "CAPACITY"):
            self.assertTrue(getattr(lifecycle, name).resolve().is_relative_to(self.root.resolve()))
        transport = ScriptedTransport([{"statusCode": 404, "headers": {}, "body": None}] * 14)
        with mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
             mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(lifecycle, "capacity_preflight"), mock.patch.object(lifecycle, "lock_and_verify_policies"), \
             mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
             mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()), \
             mock.patch.object(lifecycle, "configure_app_insights_cap"), \
             contextlib.redirect_stdout(io.StringIO()):
            lifecycle.preprovision(self.config)
        for purpose in ("l7-ingress", "realtime-ticket"):
            path = self.root / ".state" / (purpose + "-" + self.config["resourceStem"] + ".json")
            self.assertEqual(json.loads(path.read_text())["subscriptionId"], self.config["subscriptionId"])
        self.assertTrue(lifecycle.PARAMETERS.exists())
        self.assertTrue(lifecycle.STATE.exists())
        self.assertTrue(all(call.args[0].resolve().is_relative_to(self.root.resolve())
                            for call in lifecycle.protected_json.call_args_list))

    def test_api_center_recovery_runs_after_monitoring_and_before_core_inputs(self):
        """5RGを作る監視段階の出力を使い、復元成功後だけ本体入力を用意する。"""
        events = []
        transport = ScriptedTransport([{"statusCode": 404, "headers": {}, "body": None}] * 14)
        outputs = self.outputs()

        def recovery(config, actual_outputs, actual_transport):
            self.assertEqual(actual_outputs, outputs)
            self.assertIs(actual_transport, transport)
            self.assertEqual(json.loads(lifecycle.STATE.read_text())["outputs"]["names"], outputs["names"])
            self.assertFalse(lifecycle.PARAMETERS.exists())
            events.append("recovery")
            return {"status": "restored"}

        self.api_center.side_effect = recovery
        self.apim_name.side_effect = lambda *args: events.append("apim-name") or {"status": "available-for-create"}
        with mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
             mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(lifecycle, "capacity_preflight"), \
             mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
             mock.patch.object(lifecycle, "deploy_phase", side_effect=lambda *args, **kwargs: events.append("monitoring") or outputs), \
             mock.patch.object(lifecycle, "lock_and_verify_policies"), \
             mock.patch.object(lifecycle, "configure_app_insights_cap", side_effect=lambda *args: events.append("cap")), \
             contextlib.redirect_stdout(io.StringIO()):
            lifecycle.preprovision(self.config)
        self.assertEqual(events, ["apim-name", "monitoring", "cap", "recovery"])
        self.assertEqual(json.loads(lifecycle.PARAMETERS.read_text())["parameters"]["phase"]["value"], "core")
        receipt = json.loads((self.root / ".state/api-center-preparation.json").read_text())
        self.assertEqual(receipt["status"], "restored")
        self.assertEqual(receipt["resourceStem"], self.config["resourceStem"])
        self.assertIn("observedAtUtc", receipt)

    def test_apim_name_conflict_stops_before_paid_deployment_and_does_not_choose_another_name(self):
        """固定名を使えないときは監視・VM等を作らず、同名の原因解消を待つ。"""
        self.apim_name.side_effect = lifecycle.ApimPreflightError("固定したAPIM名は利用できません。")
        with mock.patch.object(lifecycle, "local_preflight"), \
             mock.patch.object(lifecycle, "azure_preflight"), \
             mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
             mock.patch.object(lifecycle, "prepare_grpc_secret", return_value={}), \
             mock.patch.object(lifecycle, "register_providers"), \
             mock.patch.object(lifecycle, "ArmTransport", return_value=object()), \
             mock.patch.object(lifecycle, "deploy_phase") as deploy:
            with self.assertRaisesRegex(lifecycle.DeploymentError, "固定したAPIM名"):
                lifecycle.preprovision(self.config)
        deploy.assert_not_called()
        self.assertFalse(lifecycle.STATE.exists())
        self.assertFalse(lifecycle.PARAMETERS.exists())

    def test_api_center_recovery_failure_clears_core_inputs_and_preserves_cleanup_state(self):
        """復元失敗を無視してcoreへ進まず、監視資源の撤去用記録は残す。"""
        lifecycle.protected_json(lifecycle.PARAMETERS, {"fixture": "PRIVATE_FIXTURE"})
        receipt = self.root / ".state/api-center-preparation.json"
        lifecycle.protected_json(receipt, {"status": "restored", "observedAtUtc": "old-fixture"})
        transport = ScriptedTransport([{"statusCode": 404, "headers": {}, "body": None}])
        self.api_center.side_effect = lifecycle.DeploymentError("API Center restore failed")
        with mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
             mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(lifecycle, "capacity_preflight"), \
             mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
             mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()), \
             mock.patch.object(lifecycle, "lock_and_verify_policies"), mock.patch.object(lifecycle, "configure_app_insights_cap"), \
             self.assertRaisesRegex(lifecycle.DeploymentError, "API Center restore failed"):
            lifecycle.preprovision(self.config)
        self.assertFalse(lifecycle.PARAMETERS.exists())
        self.assertFalse(receipt.exists())
        self.assertTrue(lifecycle.STATE.exists())
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(transport.mutations())

    def test_arm_token_selects_subscription_only_and_verifies_returned_directory(self):
        auth = {"subscription": self.config["subscriptionId"], "tenant": self.config["azureTenantId"], "accessToken": "SECRET_FIXTURE"}
        with mock.patch.object(lifecycle, "run", return_value=auth) as run:
            actual = lifecycle.arm_access_token(self.config["subscriptionId"], self.config["azureTenantId"])
        self.assertEqual(actual, auth)
        self.assertIn("--subscription", run.call_args.args[0])
        self.assertNotIn("--tenant", run.call_args.args[0])
        self.assertNotIn("SECRET_FIXTURE", run.call_args.args[0])
        for invalid in ({**auth, "tenant": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}, {**auth, "subscription": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}, {**auth, "accessToken": ""}, {"accessToken": "SECRET_FIXTURE"}):
            with mock.patch.object(lifecycle, "run", return_value=invalid):
                with self.assertRaises(lifecycle.DeploymentError) as error:
                    lifecycle.arm_access_token(self.config["subscriptionId"], self.config["azureTenantId"])
                self.assertNotIn("SECRET_FIXTURE", str(error.exception))

    @unittest.skipIf(os.name == "nt", "POSIX mode check")
    def test_protected_json_creates_600_and_replaces_preexisting_644(self):
        path = self.root / "nested/parameters.json"
        lifecycle.protected_json(path, {"value": "first-fixture"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        path.chmod(0o644)
        lifecycle.protected_json(path, {"value": "replacement-fixture"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text()), {"value": "replacement-fixture"})
        self.assertEqual(list(path.parent.glob(".core-*")), [])

    def test_public_configuration_and_secret_parameters_are_separate(self):
        values = lifecycle.parameters(self.config, "core", secrets={"sshPublicKey": "PRIVATE_PASSWORD_FIXTURE", "dnsPluginArgumentsJson": '{"CFToken":"PRIVATE_TOKEN_FIXTURE"}'})["parameters"]
        public = json.dumps(values["configuration"]["value"])
        self.assertNotIn("PRIVATE_PASSWORD_FIXTURE", public)
        self.assertNotIn("PRIVATE_TOKEN_FIXTURE", public)
        self.assertNotIn("sshPublicKeyFile", public)
        self.assertNotIn("pluginArgumentsFile", public)
        self.assertEqual(values["sshPublicKey"]["value"], "PRIVATE_PASSWORD_FIXTURE")
        self.assertEqual(values["phase"]["value"], "core")

    def test_monitoring_state_keeps_expected_cleanup_ids_before_services_exist(self):
        outputs = self.outputs()
        self.assertNotIn("services", outputs)
        actual = lifecycle.state_from_outputs(self.config, outputs)
        self.assertEqual(actual["serviceResourceIds"], outputs["cleanup"]["serviceResourceIds"])
        self.assertEqual(set(actual["serviceResourceIds"]), {"keyVault", "foundry", "apim"})
        self.assertEqual(actual["serviceLocations"], {"keyVault": "japaneast", "foundry": "eastus2", "apim": "japaneast"})
        self.assertNotIn("sshPublicKeyFile", json.dumps(actual))

    def test_registered_providers_only_read_and_never_register(self):
        with mock.patch.object(lifecycle, "run", return_value={"registrationState": "Registered"}) as run, \
             mock.patch.object(lifecycle, "ensure_host_encryption") as encryption:
            lifecycle.register_providers(self.config)
        required = lifecycle.PROVIDERS
        self.assertEqual(run.call_count, len(required))
        self.assertTrue(all(call.args[0][:3] == ["az", "provider", "show"] for call in run.call_args_list))
        self.assertEqual({call.args[0][call.args[0].index("--namespace") + 1] for call in run.call_args_list}, set(required))
        encryption.assert_called_once_with(self.config)

    def test_azd_environment_mismatch_is_detected_before_azure_access(self):
        with mock.patch.object(lifecycle.shutil, "which", return_value="/mock/bin/tool"), mock.patch.dict(os.environ, {"AZURE_ENV_NAME": "another-environment"}):
            with self.assertRaisesRegex(lifecycle.DeploymentError, "environment name"):
                lifecycle.local_preflight(self.config, check_power_shell=False)

    def test_azd_subscription_mismatch_is_detected_before_azure_access(self):
        with mock.patch.object(lifecycle.shutil, "which", return_value="/mock/bin/tool"), mock.patch.dict(os.environ, {"AZURE_SUBSCRIPTION_ID": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}):
            with self.assertRaisesRegex(lifecycle.DeploymentError, "AZURE_SUBSCRIPTION_ID"):
                lifecycle.local_preflight(self.config, check_power_shell=False)

    def test_failed_monitoring_preparation_removes_secret_parameters_but_keeps_cleanup_state(self):
        parameter_path, state_path = self.root / "parameters.json", self.root / "state.json"
        lifecycle.protected_json(parameter_path, {"fixture": "PREVIOUS_SECRET_FIXTURE"})
        transport = ScriptedTransport([{"statusCode": 404, "headers": {}, "body": None}])
        with mock.patch.object(lifecycle, "PARAMETERS", parameter_path), mock.patch.object(lifecycle, "STATE", state_path), \
             mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
             mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(lifecycle, "capacity_preflight"), \
             mock.patch.object(lifecycle, "load_secret_inputs", return_value={"sshPublicKey": "NEW_SECRET_FIXTURE"}), \
             mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()), \
             mock.patch.object(lifecycle, "configure_app_insights_cap", side_effect=lifecycle.DeploymentError("mock cap failure")):
            with self.assertRaisesRegex(lifecycle.DeploymentError, "mock cap failure"):
                lifecycle.preprovision(self.config)
        self.assertFalse(parameter_path.exists())
        self.assertTrue(state_path.exists())
        state = json.loads(state_path.read_text())
        self.assertEqual(state["serviceResourceIds"], self.outputs()["cleanup"]["serviceResourceIds"])
        self.assertNotIn("NEW_SECRET_FIXTURE", state_path.read_text())

    def test_first_up_prepares_monitoring_without_a_prior_state_or_seed_deployment(self):
        parameter_path, state_path = self.root / "parameters.json", self.root / "state.json"
        transport = ScriptedTransport([{"statusCode": 404, "headers": {}, "body": None}] * 14)
        self.assertFalse(state_path.exists())
        with mock.patch.object(lifecycle, "PARAMETERS", parameter_path), mock.patch.object(lifecycle, "STATE", state_path), \
             mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
             mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(lifecycle, "capacity_preflight"), mock.patch.object(lifecycle, "lock_and_verify_policies"), \
             mock.patch.object(lifecycle, "load_secret_inputs", return_value={"sshPublicKey": "LOCAL_ONLY_FIXTURE"}), \
             mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()) as deploy, \
             mock.patch.object(lifecycle, "configure_app_insights_cap"):
            lifecycle.preprovision(self.config)
        deploy.assert_called_once_with(self.config, "monitoring", manage_policies=True,
                                       retained_policy_ids=[], preserve_collection=False)
        state = json.loads(state_path.read_text())
        self.assertEqual(state["resourceGroupNames"], self.config["resourceGroupNames"])
        self.assertNotIn("LOCAL_ONLY_FIXTURE", state_path.read_text())
        parameters = json.loads(parameter_path.read_text())["parameters"]
        self.assertEqual(parameters["phase"]["value"], "core")
        self.assertEqual(parameters["sshPublicKey"]["value"], "LOCAL_ONLY_FIXTURE")
        self.assertEqual(parameters["existingNetworkSecurityGroupNames"]["value"], [])
        self.assertEqual(parameters["existingRouteTableRoutes"]["value"],
                         {f"rt-{self.config['resourceStem']}-{suffix}": [] for suffix in ("agw", "apim", "vm", "aca")})

    def test_retry_with_disabled_deleted_zero_policies_reaches_core_parameters(self):
        self.config['monitoring']['auditImmutabilityMode'] = 'Disabled'
        policy = '/subscriptions/fixture/resourceGroups/fixture/providers/Microsoft.Storage/storageAccounts/fixture/blobServices/default/containers/audit/immutabilityPolicies/default'
        previous = {'immutabilityMode': 'Disabled', 'retentionInDays': 2557,
                    'immutabilityPolicyIds': [policy]}
        response = {'statusCode': 200, 'headers': {},
                    'body': {'properties': {'outputs': {'auditStorage': {'value': previous}}}}}
        absent_policy = {'statusCode': 200, 'headers': {}, 'body': {'properties': {
            'state': 'Deleted', 'immutabilityPeriodSinceCreationInDays': 0}}}
        transport = ScriptedTransport([response, absent_policy] +
                                      [{'statusCode': 404, 'headers': {}, 'body': None}] * 13)
        with mock.patch.object(lifecycle, 'local_preflight'), mock.patch.object(lifecycle, 'azure_preflight'), \
             mock.patch.object(lifecycle, 'register_providers'), mock.patch.object(lifecycle, 'ArmTransport', return_value=transport), \
             mock.patch.object(lifecycle, 'capacity_preflight'), \
             mock.patch.object(lifecycle, 'load_secret_inputs', return_value={}), \
             mock.patch.object(lifecycle, 'deploy_phase', return_value=self.outputs()) as deploy, \
             mock.patch.object(lifecycle, 'configure_app_insights_cap'), contextlib.redirect_stdout(io.StringIO()):
            lifecycle.preprovision(self.config)
        deploy.assert_called_once_with(self.config, 'monitoring', manage_policies=True,
                                       retained_policy_ids=[], preserve_collection=False)
        params = json.loads(lifecycle.PARAMETERS.read_text())['parameters']
        self.assertEqual(params['phase']['value'], 'core')
        self.assertFalse(transport.mutations())

    def test_existing_locked_audit_ids_reach_monitoring_without_disabling_new_protection(self):
        self.config['monitoring']['auditImmutabilityMode'] = 'Locked'
        policy = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/"
                  f"{self.config['resourceGroupNames']['monitor']}/providers/Microsoft.Storage/"
                  'storageAccounts/sttestegakumijp/blobServices/default/containers/'
                  'insights-logs-gatewaylogs/immutabilityPolicies/default')
        previous = {'immutabilityMode': 'Locked', 'retentionInDays': 2557,
                    'immutabilityPolicyIds': [policy]}
        response = {'statusCode': 200, 'headers': {},
                    'body': {'properties': {'outputs': {'auditStorage': {'value': previous}}}}}
        transport = ScriptedTransport([response, response_policy('Locked')] +
                                      [{'statusCode': 404, 'headers': {}, 'body': None}] * 13)
        with mock.patch.object(lifecycle, 'local_preflight'), mock.patch.object(lifecycle, 'azure_preflight'), \
             mock.patch.object(lifecycle, 'register_providers'), mock.patch.object(lifecycle, 'ArmTransport', return_value=transport), \
             mock.patch.object(lifecycle, 'capacity_preflight'), \
             mock.patch.object(lifecycle, 'load_secret_inputs', return_value={}), \
             mock.patch.object(lifecycle, 'deploy_phase', return_value=self.outputs()) as deploy, \
             mock.patch.object(lifecycle, 'lock_and_verify_policies'), \
             mock.patch.object(lifecycle, 'configure_app_insights_cap'), contextlib.redirect_stdout(io.StringIO()):
            lifecycle.preprovision(self.config)
        deploy.assert_called_once_with(self.config, 'monitoring', manage_policies=True,
                                       retained_policy_ids=[policy], preserve_collection=False)
        # 監視で保護を揃えた後のnative coreは、LockedへのPUTをしない。
        params = json.loads(lifecycle.PARAMETERS.read_text())['parameters']
        self.assertFalse(params['manageAuditImmutabilityPolicies']['value'])
        self.assertEqual(params['retainedAuditImmutabilityPolicyIds']['value'], [])

    def test_locked_audit_protection_cannot_be_downgraded_even_with_other_missing_policies(self):
        self.config['monitoring']['auditImmutabilityMode'] = 'Disabled'
        policy = '/subscriptions/fixture/resourceGroups/fixture/providers/Microsoft.Storage/storageAccounts/fixture/blobServices/default/containers/audit/immutabilityPolicies/default'
        previous = {'immutabilityMode': 'Locked', 'retentionInDays': 2557,
                    'immutabilityPolicyIds': [policy, policy.replace('/audit/', '/new-category/')]}
        response = {'statusCode': 200, 'headers': {},
                    'body': {'properties': {'outputs': {'auditStorage': {'value': previous}}}}}
        transport = ScriptedTransport([response, response_policy('Locked'),
                                      {'statusCode': 404, 'headers': {}, 'body': None}])
        with mock.patch.object(lifecycle, 'local_preflight'), mock.patch.object(lifecycle, 'azure_preflight'), \
             mock.patch.object(lifecycle, 'register_providers'), mock.patch.object(lifecycle, 'ArmTransport', return_value=transport), \
             mock.patch.object(lifecycle, 'capacity_preflight'), \
             mock.patch.object(lifecycle, 'load_secret_inputs', return_value={}), \
             mock.patch.object(lifecycle, 'deploy_phase') as deploy:
            with self.assertRaisesRegex(lifecycle.DeploymentError, 'cannot weaken'):
                lifecycle.preprovision(self.config)
        deploy.assert_not_called()
        self.assertFalse(transport.mutations())


    def test_failed_capacity_prevents_billable_preparation_after_free_provider_registration(self):
        transport = ScriptedTransport([{"statusCode": 404, "headers": {}, "body": {"error": {"code": "NotFound"}}}])
        with mock.patch.object(lifecycle, "PARAMETERS", self.root / "parameters.json"), \
             mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
             mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
             mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(lifecycle, "capacity_preflight", side_effect=lifecycle.DeploymentError("mock unavailable quota")), \
             mock.patch.object(lifecycle, "register_providers") as registration, mock.patch.object(lifecycle, "deploy_phase") as deploy:
            with self.assertRaisesRegex(lifecycle.DeploymentError, "unavailable quota"):
                lifecycle.preprovision(self.config)
        registration.assert_called_once_with(self.config)
        deploy.assert_not_called()
        self.assertFalse(transport.mutations())

    def test_capacity_404_receives_no_existing_allocation_credit(self):
        transport = ScriptedTransport([{"statusCode": 404, "headers": {}, "body": {"error": {"code": "NotFound"}}}])
        report = {"status": "verified", "issues": []}
        with mock.patch.object(lifecycle, "CAPACITY", self.root / "capacity.json"), \
             mock.patch.object(lifecycle, "verify_capacity", return_value=report) as verify:
            self.assertEqual(lifecycle.capacity_preflight(self.config, transport, {}), report)
        self.assertEqual(verify.call_args.kwargs["existing_resources"], {"vm": None, "modelDeployments": []})
        self.assertFalse(transport.mutations())

    def test_capacity_reads_all_model_pages_only_from_exact_recorded_account(self):
        self.config["services"]["modelDeployments"] = [{"name": "fixture"}]
        account = self.outputs()["cleanup"]["serviceResourceIds"]["foundry"]
        next_link = "https://management.azure.com" + account + "/deployments?api-version=2025-06-01&$skiptoken=fixture"
        items = [{"name": "first"}, {"name": "second"}]
        transport = ScriptedTransport([
            {"statusCode": 404, "body": {}},
            {"statusCode": 200, "body": {"value": items[:1], "nextLink": next_link}},
            {"statusCode": 200, "body": {"value": items[1:]}},
        ])
        with mock.patch.object(lifecycle, "CAPACITY", self.root / "capacity.json"), \
             mock.patch.object(lifecycle, "verify_capacity", return_value={"status": "verified"}) as verify:
            lifecycle.capacity_preflight(self.config, transport, {"cleanup": {"value": self.outputs()["cleanup"]}})
        self.assertEqual(verify.call_args.kwargs["existing_resources"]["modelDeployments"], items)
        self.assertFalse(transport.mutations())

    def test_manually_added_models_are_read_without_deletion_during_capacity_preparation(self):
        self.config["services"]["modelDeployments"] = [{"name": "another-model", "modelFormat": "OpenAI"}]
        transport = ScriptedTransport([
            {"statusCode": 404, "body": {}},
            {"statusCode": 200, "body": {"value": [{"properties": {"model": {"format": "Anthropic", "name": "claude-existing"}}}]}},
        ])
        with mock.patch.object(lifecycle, "CAPACITY", self.root / "capacity.json"), \
             mock.patch.object(lifecycle, "verify_capacity", return_value={"status": "verified"}) as verify:
            lifecycle.capacity_preflight(self.config, transport, {"cleanup": {"value": self.outputs()["cleanup"]}})
        self.assertFalse(transport.mutations())
        verify.assert_called_once()

    def test_capacity_rejects_foreign_account_output_and_continuation(self):
        self.config["services"]["modelDeployments"] = [{"name": "fixture"}]
        cleanup = self.outputs()["cleanup"]
        for foreign_output in (True, False):
            with self.subTest(foreign_output=foreign_output):
                current = copy.deepcopy(cleanup)
                if foreign_output:
                    current["serviceResourceIds"]["foundry"] = current["serviceResourceIds"]["foundry"].replace("rg-test-egakumi-svc", "rg-unrelated-prod-svc")
                transport = ScriptedTransport([
                    {"statusCode": 404, "body": {}},
                    {"statusCode": 200, "body": {"value": [], "nextLink": "https://management.azure.com/subscriptions/another/resourceGroups/other/providers/Microsoft.CognitiveServices/accounts/other/deployments"}},
                ])
                with mock.patch.object(lifecycle, "verify_capacity") as verify:
                    with self.assertRaises(lifecycle.DeploymentError):
                        lifecycle.capacity_preflight(self.config, transport, {"cleanup": {"value": current}})
                verify.assert_not_called()
                self.assertFalse(transport.mutations())

    def test_capacity_rejects_missing_inventory_or_missing_continuation_page(self):
        self.config["services"]["modelDeployments"] = [{"name": "fixture"}]
        cleanup = self.outputs()["cleanup"]
        continuation = cleanup["serviceResourceIds"]["foundry"] + "/deployments?api-version=2025-06-01&$skiptoken=fixture"
        for pages in (
            [{"statusCode": 200, "body": {}}],
            [{"statusCode": 200, "body": {"value": [{"name": "first"}], "nextLink": continuation}}, {"statusCode": 404, "body": {}}],
        ):
            transport = ScriptedTransport([{"statusCode": 404, "body": {}}] + pages)
            with mock.patch.object(lifecycle, "verify_capacity") as verify:
                with self.assertRaises(lifecycle.DeploymentError):
                    lifecycle.capacity_preflight(self.config, transport, {"cleanup": {"value": cleanup}})
            verify.assert_not_called()
            self.assertFalse(transport.mutations())

    def test_early_local_preflight_failure_also_clears_existing_secret_file(self):
        path = self.root / "parameters.json"
        lifecycle.protected_json(path, {"fixture": "SECRET_FIXTURE"})
        with mock.patch.object(lifecycle, "PARAMETERS", path), mock.patch.object(lifecycle, "local_preflight", side_effect=lifecycle.DeploymentError("mock missing tool")):
            with self.assertRaisesRegex(lifecycle.DeploymentError, "mock missing tool"):
                lifecycle.preprovision(self.config)
        self.assertFalse(path.exists())

    def test_deploy_phase_file_is_removed_after_command_failure(self):
        with mock.patch.object(lifecycle, "ROOT", self.root), mock.patch.object(lifecycle, "run", side_effect=lifecycle.DeploymentError("mock deployment failure")):
            with self.assertRaisesRegex(lifecycle.DeploymentError, "mock deployment failure"):
                lifecycle.deploy_phase(self.config, "monitoring")
        self.assertFalse((self.root / ".state/monitoring.parameters.json").exists())

    def test_pre_stage_has_native_azd_ownership_tags_and_waits_for_success(self):
        transport = ScriptedTransport([
            {"statusCode": 201, "body": {}, "headers": {}},
            {"statusCode": 200, "headers": {}, "body": {"properties": {
                "provisioningState": "Succeeded", "outputs": {"resourceGroups": {"value": self.config["resourceGroupNames"]}}
            }}},
        ])
        with mock.patch.object(lifecycle, "ArmTransport", return_value=transport), mock.patch.object(lifecycle, "run", return_value={"resources": []}):
            outputs = lifecycle.deploy_phase(self.config, "monitoring")
        request = transport.calls[0]
        self.assertEqual(request["method"], "PUT")
        self.assertEqual(request["body"]["tags"], {"azd-env-name": "test", "azd-project-name": "ai-workforce-core", "azd-layer-name": ""})
        self.assertEqual(request["body"]["properties"]["parameters"]["phase"]["value"], "monitoring")
        self.assertEqual(request["body"]["properties"]["parameters"]["existingNetworkSecurityGroupNames"]["value"], [])
        self.assertEqual(request["body"]["properties"]["parameters"]["existingRouteTableRoutes"]["value"], {})
        self.assertEqual(outputs["resourceGroups"], self.config["resourceGroupNames"])

    def test_command_failure_suppresses_private_stdout_and_stderr(self):
        result = mock.Mock(returncode=1, stdout="PRIVATE_STDOUT_FIXTURE", stderr="PRIVATE_STDERR_FIXTURE")
        with mock.patch.object(lifecycle.subprocess, "run", return_value=result):
            with self.assertRaises(lifecycle.DeploymentError) as error:
                lifecycle.run(["az", "mock-command"])
        self.assertNotIn("PRIVATE_STDOUT_FIXTURE", str(error.exception))
        self.assertNotIn("PRIVATE_STDERR_FIXTURE", str(error.exception))

    def test_predown_retains_environment_scoped_model_deletion_receipt(self):
        state_path = self.root / "state.json"
        lifecycle.protected_json(state_path, {})
        result = {"phase": "predown", "status": "ready-for-azd-down", "modelDeployments": {
            "modelDeletionVerified": True, "quotaReleaseVerified": False, "quotaStatus": "not-read", "deployments": []}}
        with mock.patch.object(lifecycle, "ROOT", self.root), mock.patch.object(lifecycle, "STATE", state_path), \
             mock.patch.object(lifecycle, "PARAMETERS", self.root / "parameters.json"), \
             mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
             mock.patch.object(lifecycle, "ArmTransport"), mock.patch.object(cleanup, "pre_down", return_value=result), \
             mock.patch("builtins.print"):
            self.assertEqual(lifecycle.main(["predown", "--config", "fixture.json"]), 0)
        receipt = json.loads((self.root / ".state/cleanup-predown.json").read_text())
        self.assertEqual(receipt["resourceStem"], self.config["resourceStem"])
        self.assertEqual(receipt["result"], result)
        self.assertIn("observedAtUtc", receipt)


if __name__ == "__main__":
    unittest.main()
