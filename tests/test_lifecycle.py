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
        receipt = lifecycle.ROOT / ".local/test/state/api-catalog-publication.json"
        transport = mock.Mock()
        transport.request.side_effect = AssertionError("Foundation must not update API resources")
        for fails in (False, False, True):
            lifecycle.protected_json(self.paths.parameters, {"fixture": "PRIVATE_INPUT"})
            lifecycle.protected_json(receipt, {"status": "explicit-api-publication"})
            with mock.patch.object(lifecycle, "run", side_effect=[{"CORE_DEPLOYMENT_NAME": "owned-core"}, response]), \
                 mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
                 mock.patch.object(lifecycle, "configure_grpc_endpoint", side_effect=ValueError("ingress-failed") if fails else None,
                                   return_value={"enabled": True, "l7IngressEnabled": True, "runtimeVerified": False}) as ingress, \
                 contextlib.redirect_stdout(io.StringIO()):
                if fails:
                    with self.assertRaisesRegex(ValueError, "ingress-failed"):
                        lifecycle.postprovision(self.config)
                else:
                    lifecycle.postprovision(self.config)
            ingress.assert_called_once()
            transport.request.assert_not_called()
            self.assertTrue((self.paths.state / "latest.json").exists())
            self.assertFalse(self.paths.parameters.exists())
            self.assertEqual(json.loads(receipt.read_text(encoding="utf-8")), {"status": "explicit-api-publication"})

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
             mock.patch.object(lifecycle, "configure_grpc_endpoint", return_value={"enabled": True, "l7IngressEnabled": True}), \
             contextlib.redirect_stdout(io.StringIO()):
            lifecycle.postprovision(self.config)
        dns_config = factory.call_args_list[1].args[0]
        self.assertEqual(dns_config["subscriptionId"], "22222222-2222-2222-2222-222222222222")
        self.assertEqual(dns_config["azureTenantId"], self.config["azureTenantId"])
        self.public_dns.assert_called_once_with(self.config, outputs, core, dns)
        self.assertEqual(json.loads((lifecycle.ROOT / ".local/test/state/public-dns.json").read_text(encoding="utf-8")), {"status": "configured"})

    def test_public_dns_failure_preserves_deployment_state_and_removes_secret_inputs(self):
        """DNSの保存失敗を成功扱いにせず、構築記録と秘密入力回収を維持する。"""
        outputs = self.outputs()
        response = {"properties": {"outputs": {key: {"value": value} for key, value in outputs.items()}}}
        lifecycle.protected_json(self.paths.parameters, {"fixture": "PRIVATE_INPUT"})
        lifecycle.protected_json(lifecycle.ROOT / ".local/test/state/public-dns.json", {"status": "old-success"})
        self.public_dns.side_effect = ValueError("Public DNS: record conflict")
        with mock.patch.object(lifecycle, "run", side_effect=[{"CORE_DEPLOYMENT_NAME": "owned-core"}, response]), \
             mock.patch.object(lifecycle, "ArmTransport"), \
             mock.patch.object(lifecycle, "configure_grpc_endpoint", return_value={"enabled": True, "l7IngressEnabled": True}), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "record conflict"):
                lifecycle.postprovision(self.config)
        self.assertTrue((self.paths.state / "latest.json").exists())
        self.assertFalse(self.paths.parameters.exists())
        self.assertFalse((lifecycle.ROOT / ".local/test/state/public-dns.json").exists())

    def test_preprovision_all_mutable_paths_and_ingress_credentials_stay_in_temporary_root(self):
        """実 ROOT の鍵を読まず、常設L7入口/所有者チケット鍵をtempだけへ作成する。"""
        self.assertNotIn('grpc', self.config['gateway'])
        self.config["gateway"]["realtimeControlEnabled"] = True
        for path in (lifecycle.ROOT, self.paths.parameters, self.paths.state / "latest.json",
                     self.paths.state / "capacity.json"):
            self.assertTrue(path.resolve().is_relative_to(self.root.resolve()))
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
            path = self.paths.secrets / (purpose + "-" + self.config["resourceStem"] + ".json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["subscriptionId"], self.config["subscriptionId"])
        self.assertTrue(self.paths.parameters.exists())
        self.assertTrue((self.paths.state / "latest.json").exists())
        self.assertTrue(all(call.args[0].resolve().is_relative_to(self.root.resolve())
                            for call in lifecycle.protected_json.call_args_list))

    def test_preprovision_hands_only_initial_generation_to_native_azd_after_recording_provenance(self):
        transport = ScriptedTransport([{"statusCode": 404, "headers": {}, "body": None}] * 14)
        with mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
             mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(lifecycle, "capacity_preflight"), mock.patch.object(lifecycle, "lock_and_verify_policies"), \
             mock.patch.object(lifecycle, "load_secret_inputs", return_value={}), \
             mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()), \
             mock.patch.object(lifecycle, "configure_app_insights_cap"), \
             mock.patch.object(lifecycle, "_prepare_management_serial_password", return_value="PRIVATE_SERIAL_FIXTURE") as prepare, \
             mock.patch.object(lifecycle, "_confirm_management_serial_target") as confirm, \
             mock.patch.object(lifecycle, "_management_serial_generation", return_value="INITIAL_SERIAL_GENERATION_FIXTURE"), \
             mock.patch.object(lifecycle, "_record_management_serial_creation") as record, \
             contextlib.redirect_stdout(io.StringIO()):
            lifecycle.preprovision(self.config)
        prepare.assert_called_once_with(self.config, transport)
        confirm.assert_called_once_with(self.config, transport, "PRIVATE_SERIAL_FIXTURE")
        values = json.loads(self.paths.parameters.read_text(encoding="utf-8"))["parameters"]
        record.assert_called_once_with(self.config, "PRIVATE_SERIAL_FIXTURE", native=True)
        self.assertNotIn("managementAdminPassword", values)
        self.assertEqual(values["managementInitialSerialGeneration"]["value"], "INITIAL_SERIAL_GENERATION_FIXTURE")
        self.assertNotIn("PRIVATE_SERIAL_FIXTURE", json.dumps(values))

    def test_api_center_recovery_runs_after_monitoring_and_before_core_inputs(self):
        """5RGを作る監視段階の出力を使い、復元成功後だけ本体入力を用意する。"""
        events = []
        transport = ScriptedTransport([{"statusCode": 404, "headers": {}, "body": None}] * 14)
        outputs = self.outputs()

        def recovery(config, actual_outputs, actual_transport):
            self.assertEqual(actual_outputs, outputs)
            self.assertIs(actual_transport, transport)
            self.assertEqual(json.loads((self.paths.state / "latest.json").read_text(encoding="utf-8"))["outputs"]["names"], outputs["names"])
            self.assertFalse(self.paths.parameters.exists())
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
        self.assertEqual(json.loads(self.paths.parameters.read_text(encoding="utf-8"))["parameters"]["phase"]["value"], "core")
        receipt = json.loads((self.root / ".local/test/state/api-center-preparation.json").read_text(encoding="utf-8"))
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
        self.assertFalse((self.paths.state / "latest.json").exists())
        self.assertFalse(self.paths.parameters.exists())

    def test_api_center_recovery_failure_clears_core_inputs_and_preserves_cleanup_state(self):
        """復元失敗を無視してcoreへ進まず、監視資源の撤去用記録は残す。"""
        lifecycle.protected_json(self.paths.parameters, {"fixture": "PRIVATE_FIXTURE"})
        receipt = self.root / ".local/test/state/api-center-preparation.json"
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
        self.assertFalse(self.paths.parameters.exists())
        self.assertFalse(receipt.exists())
        self.assertTrue((self.paths.state / "latest.json").exists())
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
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"value": "replacement-fixture"})
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
        parameter_path, state_path = self.paths.parameters, self.paths.state / "latest.json"
        lifecycle.protected_json(parameter_path, {"fixture": "PREVIOUS_SECRET_FIXTURE"})
        transport = ScriptedTransport([{"statusCode": 404, "headers": {}, "body": None}])
        with mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
             mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(lifecycle, "capacity_preflight"), \
             mock.patch.object(lifecycle, "load_secret_inputs", return_value={"sshPublicKey": "NEW_SECRET_FIXTURE"}), \
             mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()), \
             mock.patch.object(lifecycle, "configure_app_insights_cap", side_effect=lifecycle.DeploymentError("mock cap failure")):
            with self.assertRaisesRegex(lifecycle.DeploymentError, "mock cap failure"):
                lifecycle.preprovision(self.config)
        self.assertFalse(parameter_path.exists())
        self.assertTrue(state_path.exists())
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["serviceResourceIds"], self.outputs()["cleanup"]["serviceResourceIds"])
        self.assertNotIn("NEW_SECRET_FIXTURE", state_path.read_text(encoding="utf-8"))

    def test_first_up_prepares_monitoring_without_a_prior_state_or_seed_deployment(self):
        parameter_path, state_path = self.paths.parameters, self.paths.state / "latest.json"
        transport = ScriptedTransport([{"statusCode": 404, "headers": {}, "body": None}] * 14)
        self.assertFalse(state_path.exists())
        with mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
             mock.patch.object(lifecycle, "register_providers"), mock.patch.object(lifecycle, "ArmTransport", return_value=transport), \
             mock.patch.object(lifecycle, "capacity_preflight"), mock.patch.object(lifecycle, "lock_and_verify_policies"), \
             mock.patch.object(lifecycle, "load_secret_inputs", return_value={"sshPublicKey": "LOCAL_ONLY_FIXTURE"}), \
             mock.patch.object(lifecycle, "deploy_phase", return_value=self.outputs()) as deploy, \
             mock.patch.object(lifecycle, "configure_app_insights_cap"):
            lifecycle.preprovision(self.config)
        deploy.assert_called_once_with(self.config, "monitoring", manage_policies=True,
                                       retained_policy_ids=[], preserve_collection=False,
                                       diagnostic_setting_properties=mock.ANY)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["resourceGroupNames"], self.config["resourceGroupNames"])
        self.assertNotIn("LOCAL_ONLY_FIXTURE", state_path.read_text(encoding="utf-8"))
        parameters = json.loads(parameter_path.read_text(encoding="utf-8"))["parameters"]
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
                                       retained_policy_ids=[], preserve_collection=False,
                                       diagnostic_setting_properties=mock.ANY)
        params = json.loads(self.paths.parameters.read_text(encoding="utf-8"))['parameters']
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
                                       retained_policy_ids=[policy], preserve_collection=False,
                                       diagnostic_setting_properties=mock.ANY)
        # 監視で保護を揃えた後のnative coreは、LockedへのPUTをしない。
        params = json.loads(self.paths.parameters.read_text(encoding="utf-8"))['parameters']
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
        with mock.patch.object(lifecycle, "local_preflight"), mock.patch.object(lifecycle, "azure_preflight"), \
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
        with mock.patch.object(lifecycle, "verify_capacity", return_value=report) as verify:
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
        with mock.patch.object(lifecycle, "verify_capacity", return_value={"status": "verified"}) as verify:
            lifecycle.capacity_preflight(self.config, transport, {"cleanup": {"value": self.outputs()["cleanup"]}})
        self.assertEqual(verify.call_args.kwargs["existing_resources"]["modelDeployments"], items)
        self.assertFalse(transport.mutations())

    def test_manually_added_models_are_read_without_deletion_during_capacity_preparation(self):
        self.config["services"]["modelDeployments"] = [{"name": "another-model", "modelFormat": "OpenAI"}]
        transport = ScriptedTransport([
            {"statusCode": 404, "body": {}},
            {"statusCode": 200, "body": {"value": [{"properties": {"model": {"format": "Anthropic", "name": "claude-existing"}}}]}},
        ])
        with mock.patch.object(lifecycle, "verify_capacity", return_value={"status": "verified"}) as verify:
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
        path = self.paths.parameters
        lifecycle.protected_json(path, {"fixture": "SECRET_FIXTURE"})
        with mock.patch.object(lifecycle, "local_preflight", side_effect=lifecycle.DeploymentError("mock missing tool")):
            with self.assertRaisesRegex(lifecycle.DeploymentError, "mock missing tool"):
                lifecycle.preprovision(self.config)
        self.assertFalse(path.exists())

    def test_deploy_phase_file_is_removed_after_command_failure(self):
        with mock.patch.object(lifecycle, "ROOT", self.root), mock.patch.object(lifecycle, "run", side_effect=lifecycle.DeploymentError("mock deployment failure")):
            with self.assertRaisesRegex(lifecycle.DeploymentError, "mock deployment failure"):
                lifecycle.deploy_phase(self.config, "monitoring")
        self.assertFalse((self.root / ".local/test/state/monitoring.parameters.json").exists())

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
        self.backend_tls_gate.assert_not_called()
        self.prepare_nsg.assert_not_called()
        self.complete_nsg.assert_not_called()

    def test_new_build_and_https_reup_check_portal_before_staging_and_core_inputs(self):
        for existing in (False, True):
            events = []
            if existing:
                lifecycle._save_deployment_state(self.config, self.outputs())
            else:
                (self.paths.state / 'latest.json').unlink(missing_ok=True)
            transport = ScriptedTransport([{'statusCode': 404, 'body': None}] * 13)
            self.backend_tls_gate.side_effect = lambda *a, **kw: events.append('portal-gate') or {
                'newGateway': not existing, 'httpBackendVerified': existing}
            self.prepare_nsg.side_effect = lambda *a, **k: events.append('nsg-stage') or {'azureWrites': 0}
            original_write = lifecycle.write_arm_parameters
            def write_core(*args):
                events.append('core-inputs')
                return original_write(*args)
            with self.subTest(existing=existing), \
                 mock.patch.object(lifecycle, 'local_preflight'), \
                 mock.patch.object(lifecycle, 'azure_preflight', side_effect=lambda *a: events.append('authentication')), \
                 mock.patch.object(lifecycle, 'register_providers', side_effect=lambda *a: events.append('providers')), \
                 mock.patch.object(lifecycle, 'ArmTransport', return_value=transport), \
                 mock.patch.object(lifecycle, 'capacity_preflight'), \
                 mock.patch.object(lifecycle, 'load_secret_inputs', side_effect=lambda *a: events.append('secret-inputs') or {}), \
                 mock.patch.object(lifecycle, 'deploy_phase', side_effect=lambda *a, **k: events.append('monitoring') or self.outputs()), \
                 mock.patch.object(lifecycle, 'lock_and_verify_policies'), \
                 mock.patch.object(lifecycle, 'configure_app_insights_cap'), \
                 mock.patch.object(lifecycle, 'discover_existing_apim_egress_fqdns', return_value=[]), \
                 mock.patch.object(lifecycle, 'write_arm_parameters', side_effect=write_core), \
                 contextlib.redirect_stdout(io.StringIO()):
                lifecycle.preprovision(self.config)
            self.assertEqual(events, ['authentication', 'providers', 'portal-gate', 'secret-inputs',
                                      'monitoring', 'portal-gate', 'nsg-stage', 'core-inputs'])
            self.assertEqual(json.loads(self.paths.parameters.read_text(encoding="utf-8"))['parameters']['phase']['value'], 'core')
            self.assertFalse(transport.mutations())

    def test_old_http_preprovision_stops_before_secret_preparation_monitoring_and_nsg_changes(self):
        lifecycle.protected_json(self.paths.parameters, {'old': 'PRIVATE_INPUT_FIXTURE'})
        prior = lifecycle._save_deployment_state(self.config, self.outputs())
        self.backend_tls_gate.side_effect = ValueError('Migrate the existing HTTP backend to inspected HTTPS first')
        with mock.patch.object(lifecycle, 'local_preflight'), mock.patch.object(lifecycle, 'azure_preflight'), \
             mock.patch.object(lifecycle, 'register_providers'), mock.patch.object(lifecycle, 'ArmTransport'), \
             mock.patch.object(lifecycle, 'load_secret_inputs') as secrets, \
             mock.patch.object(lifecycle, 'prepare_grpc_secret') as ingress, \
             mock.patch.object(lifecycle, 'deploy_phase') as deploy:
            with self.assertRaisesRegex(ValueError, 'Migrate'):
                lifecycle.preprovision(self.config)
        for boundary in (secrets, ingress, deploy, self.prepare_nsg):
            boundary.assert_not_called()
        self.assertFalse(self.paths.parameters.exists())
        self.assertEqual(json.loads((self.paths.state / 'latest.json').read_text(encoding="utf-8")), prior)

    def test_old_http_preup_stops_before_azd_environment_or_ingress_mutation(self):
        lifecycle.protected_json(self.paths.params, {})
        self.backend_tls_gate.side_effect = ValueError('Migrate the existing HTTP backend to inspected HTTPS first')
        with mock.patch.object(lifecycle, 'load_configuration', return_value=self.config), \
             mock.patch.object(lifecycle, 'local_preflight'), mock.patch.object(lifecycle, 'azure_preflight'), \
             mock.patch.object(lifecycle, 'register_providers'), mock.patch.object(lifecycle, 'ArmTransport'), \
             mock.patch.object(lifecycle, 'load_secret_inputs', return_value={}), \
             mock.patch.object(lifecycle, 'prepare_grpc_secret') as ingress, \
             mock.patch.object(lifecycle, 'write_arm_parameters') as write, \
             mock.patch.object(lifecycle, 'run') as cli, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lifecycle.main(['preup', '--config', str(self.paths.params)]), 1)
        for boundary in (cli, ingress, write, self.prepare_nsg):
            boundary.assert_not_called()

    def test_preup_new_build_and_reup_gate_precedes_environment_and_input_registration(self):
        lifecycle.protected_json(self.paths.params, {})
        for existing in (False, True):
            events = []
            self.backend_tls_gate.side_effect = lambda *a, **kw: events.append('portal-gate') or {'newGateway': not existing}
            names = ['nsg-' + self.config['resourceStem'] + '-' + suffix
                     for suffix in ('agw', 'apim', 'pep', 'vm', 'aca')] if existing else []
            def environment(arguments, **kwargs):
                self.assertEqual(arguments[:3], ['azd', 'env', 'set'])
                events.append('input-registration' if arguments[3] == 'EGAKUMI_ARM_PARAMETERS_FILE' else 'environment')
                return {}
            def hostnames(*args, custom_properties_out=None):
                if existing and custom_properties_out is not None:
                    custom_properties_out['apim-' + self.config['resourceStem'] + '-abc123'] = {
                        'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls11': 'False'}
                return {}
            with self.subTest(existing=existing), \
                 mock.patch.object(lifecycle, 'load_configuration', return_value=self.config), \
                 mock.patch.object(lifecycle, 'local_preflight'), mock.patch.object(lifecycle, 'azure_preflight'), \
                 mock.patch.object(lifecycle, 'register_providers'), mock.patch.object(lifecycle, 'ArmTransport'), \
                 mock.patch.object(lifecycle, '_prepare_management_serial_password', side_effect=lambda *a:
                     events.append('serial-preparation') or ('' if existing else 'PRIVATE_SERIAL_FIXTURE')), \
                 mock.patch.object(lifecycle, '_confirm_management_serial_target', side_effect=lambda *a:
                     events.append('serial-confirmation')), \
                 mock.patch.object(lifecycle, '_management_serial_generation', side_effect=lambda config,password:
                     'INITIAL_SERIAL_GENERATION_FIXTURE' if password else ''), \
                 mock.patch.object(lifecycle, '_record_management_serial_creation', side_effect=lambda *a,**kw:
                     events.append('serial-provenance')), \
                 mock.patch.object(lifecycle, 'load_secret_inputs', return_value={}), \
                 mock.patch.object(lifecycle, 'run', side_effect=environment), \
                 mock.patch.object(lifecycle, 'discover_existing_network_security_groups', return_value=names), \
                 mock.patch.object(lifecycle, 'discover_existing_route_table_routes', return_value={}), \
                 mock.patch.object(lifecycle, 'discover_existing_apim_hostnames', side_effect=hostnames), \
                 mock.patch.object(lifecycle, 'discover_existing_portal_app', return_value=existing):
                self.assertEqual(lifecycle.main(['preup', '--config', str(self.paths.params)]), 0)
            self.assertEqual(events, ['portal-gate', 'environment', 'environment', 'environment',
                                     'serial-preparation', 'serial-confirmation', 'serial-provenance', 'input-registration'])
            parameters = json.loads(self.paths.parameters.read_text(encoding="utf-8"))['parameters']
            self.assertEqual(parameters['existingNetworkSecurityGroupNames']['value'], names)
            self.assertEqual(parameters['existingPortalApp']['value'], existing)
            self.assertEqual(parameters['existingApimCustomProperties']['value'], {
                'apim-' + self.config['resourceStem'] + '-abc123': {
                    'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls11': 'False'}} if existing else {})
            self.assertTrue(parameters['l7IngressSecret']['value'])
            self.assertNotIn('managementAdminPassword', parameters)
            self.assertEqual(parameters['managementInitialSerialGeneration']['value'], '' if existing else 'INITIAL_SERIAL_GENERATION_FIXTURE')
            self.assertNotIn('PRIVATE_SERIAL_FIXTURE', json.dumps(parameters))
            self.assertNotIn('grpc', parameters['configuration']['value']['gateway'])

    def test_native_core_failed_completion_keeps_outputs_and_cleans_secret_input(self):
        outputs = self.outputs()
        response = {'properties': {'outputs': {key: {'value': value} for key, value in outputs.items()}}}
        lifecycle.protected_json(self.paths.parameters, {'value': 'PRIVATE_INPUT_FIXTURE'})
        self.complete_nsg.side_effect = lifecycle.FixedNsgError('NSG tuple readback failed')
        with mock.patch.object(lifecycle, 'run', side_effect=[{'CORE_DEPLOYMENT_NAME': 'owned-core'}, response]), \
             mock.patch.object(lifecycle, 'ArmTransport'), \
             mock.patch.object(lifecycle, 'configure_grpc_endpoint') as ingress:
            with self.assertRaisesRegex(lifecycle.DeploymentError, 'tuple readback failed'):
                lifecycle.postprovision(self.config)
        ingress.assert_not_called()
        self.public_dns.assert_not_called()
        self.assertEqual(json.loads((self.paths.state / 'latest.json').read_text(encoding="utf-8"))['outputs'], outputs)
        self.assertFalse(self.paths.parameters.exists())

    def direct_core(self, *, completion_failure=False, provided_secrets=None):
        """Coreだけを合成ARMで配備し、実state保存と完了確認の境界を通す。"""
        events, outputs = [], self.outputs()
        transport = ScriptedTransport([
            {'statusCode': 202, 'body': None},
            {'statusCode': 200, 'body': {'properties': {'provisioningState': 'Succeeded',
                'outputs': {key: {'value': value} for key, value in outputs.items()}}}},
        ])
        existing_names = ['nsg-' + self.config['resourceStem'] + '-' + suffix
                          for suffix in ('agw', 'apim', 'pep', 'vm', 'aca')]
        self.backend_tls_gate.side_effect = lambda *a, **kw: events.append('portal-gate') or {'httpBackendVerified': True}
        self.prepare_nsg.side_effect = lambda *a, **k: events.append('nsg-stage') or {'azureWrites': 0}
        original_save = lifecycle._save_deployment_state
        def save(*args):
            result = original_save(*args)
            events.append('saved-state')
            return result
        def complete(*args, **kwargs):
            self.assertEqual(json.loads((self.paths.state / 'latest.json').read_text(encoding="utf-8"))['outputs'], outputs)
            events.append('nsg-complete')
            if completion_failure:
                raise lifecycle.FixedNsgError('NSG tuple readback failed')
            return {'status': 'completed', 'azureWrites': 0}
        self.complete_nsg.side_effect = complete
        def hostnames(*args, custom_properties_out=None):
            if custom_properties_out is not None:
                custom_properties_out['apim-' + self.config['resourceStem'] + '-abc123'] = {
                    'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls11': 'False'}
            return {}
        def bootstrap(*args, mutation_recorder):
            self.assertTrue(callable(mutation_recorder))
            mutation_recorder('fixture-bootstrap', {'status': 'planned'})
            events.append('l7-bootstrap')
            return {'enabled': True, 'l7IngressEnabled': True, 'runtimeVerified': False}
        with mock.patch.object(lifecycle, 'run', return_value={'resources': []}), \
             mock.patch.object(lifecycle, 'ArmTransport', return_value=transport), \
             mock.patch.object(lifecycle, 'discover_existing_network_security_groups', return_value=existing_names), \
             mock.patch.object(lifecycle, 'discover_existing_route_table_routes', return_value={}), \
             mock.patch.object(lifecycle, 'discover_existing_apim_hostnames', side_effect=hostnames), \
             mock.patch.object(lifecycle, 'discover_existing_apim_egress_fqdns', return_value=['api.example.invalid']), \
             mock.patch.object(lifecycle, 'discover_existing_portal_app', return_value=True), \
             mock.patch.object(lifecycle, 'load_secret_inputs', return_value={'sshPublicKey': 'PRIVATE_SSH_FIXTURE'}) as secrets, \
             mock.patch.object(lifecycle, 'prepare_grpc_secret', return_value={'l7IngressSecret': 'PRIVATE_L7_FIXTURE'}) as ingress_secrets, \
             mock.patch.object(lifecycle, '_save_deployment_state', side_effect=save), \
             mock.patch.object(lifecycle, 'configure_grpc_endpoint', side_effect=bootstrap) as ingress:
            if completion_failure:
                with self.assertRaisesRegex(lifecycle.DeploymentError, 'tuple readback failed'):
                    lifecycle.deploy_phase(self.config, 'core', secrets=provided_secrets)
            else:
                self.assertEqual(lifecycle.deploy_phase(self.config, 'core', secrets=provided_secrets), outputs)
        if provided_secrets is None:
            secrets.assert_called_once_with(self.config)
            ingress_secrets.assert_called_once()
        else:
            secrets.assert_not_called()
            ingress_secrets.assert_not_called()
        if completion_failure:
            ingress.assert_not_called()
        self.assertEqual([call['method'] for call in transport.calls], ['PUT', 'GET'])
        return events, transport.calls[0]['body']['properties']['parameters']

    def test_direct_core_saves_outputs_then_verifies_complete_set_then_bootstraps_ingress(self):
        events, parameters = self.direct_core()
        self.assertEqual(events, ['portal-gate', 'nsg-stage', 'saved-state', 'nsg-complete', 'l7-bootstrap'])
        self.assertEqual(parameters['sshPublicKey']['value'], 'PRIVATE_SSH_FIXTURE')
        self.assertEqual(parameters['l7IngressSecret']['value'], 'PRIVATE_L7_FIXTURE')
        self.assertIn('api.example.invalid', parameters['configuration']['value']['generatedApimServiceFqdns'])
        self.assertNotIn('apimAllowedFqdns', parameters['configuration']['value']['network']['firewall'])
        self.assertEqual(parameters['existingApimCustomProperties']['value'], {
            'apim-' + self.config['resourceStem'] + '-abc123': {
                'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls11': 'False'}})
        self.assertEqual(len(parameters['existingNetworkSecurityGroupNames']['value']), 5)
        self.assertTrue(parameters['existingPortalApp']['value'])
        self.assertNotIn('PRIVATE_', json.dumps(parameters['configuration']['value']))
        self.assertEqual(json.loads((self.paths.state / 'ingress-bootstrap.json').read_text(encoding="utf-8")),
                         {'enabled': True, 'l7IngressEnabled': True, 'runtimeVerified': False})

    def test_direct_core_can_use_explicit_secrets_without_reading_or_recreating_credentials(self):
        _, parameters = self.direct_core(provided_secrets={'l7IngressSecret': 'PRIVATE_PREPARED_FIXTURE'})
        self.assertEqual(parameters['l7IngressSecret']['value'], 'PRIVATE_PREPARED_FIXTURE')

    def test_direct_core_failed_completion_preserves_actual_deployment_state(self):
        events, _ = self.direct_core(completion_failure=True)
        self.assertEqual(events, ['portal-gate', 'nsg-stage', 'saved-state', 'nsg-complete'])
        self.assertEqual(json.loads((self.paths.state / 'latest.json').read_text(encoding="utf-8"))['outputs'], self.outputs())
        self.assertFalse((self.paths.state / 'ingress-bootstrap.json').exists())

    def test_upgrade_journal_preserves_repeated_events_and_separate_attempts(self):
        first = lifecycle.upgrade_journal(self.config, 'portal-http80')
        first('gateway-before', {'value': 'first-snapshot'})
        first('gateway-before', {'value': 'second-snapshot'})
        second = lifecycle.upgrade_journal(self.config, 'portal-http80')
        second('gateway-before', {'value': 'next-attempt'})
        directories = list((self.paths.state / 'portal-http80').iterdir())
        self.assertEqual(len(directories), 2)
        snapshots = list((self.paths.state / 'portal-http80').glob('*/*.json'))
        self.assertEqual(len(snapshots), 3)
        self.assertEqual({json.loads(path.read_text(encoding="utf-8"))['value'] for path in snapshots},
                         {'first-snapshot', 'second-snapshot', 'next-attempt'})
        self.assertTrue(all(path.resolve().is_relative_to(self.root) for path in snapshots))

    def test_protected_snapshot_syncs_file_before_replacement_and_directory_after(self):
        events, path = [], self.paths.state / 'durable-snapshot.json'
        original_sync, original_replace = lifecycle.os.fsync, lifecycle.os.replace
        def sync(descriptor):
            events.append('sync')
            return original_sync(descriptor)
        def replace(*args):
            events.append('replace')
            return original_replace(*args)
        with mock.patch.object(lifecycle.os, 'fsync', side_effect=sync), \
             mock.patch.object(lifecycle.os, 'replace', side_effect=replace):
            lifecycle.protected_json(path, {'value': 'durable-fixture'})
        self.assertEqual(events, ['sync', 'replace'] if os.name == 'nt' else ['sync', 'replace', 'sync'])

    def test_apim_discovery_retains_protocol_settings_and_excludes_certificate_material(self):
        name = self.outputs()['names']['apim']
        scope = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/"
                 f"{self.config['resourceGroupNames']['svc']}/providers/Microsoft.ApiManagement/service/{name}")
        domain = {'type': 'Proxy', 'hostName': name + '.azure-api.net', 'certificateSource': 'BuiltIn',
                  'encodedCertificate': None, 'certificatePassword': None}
        settings = {'Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2': 'true',
                    'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls11': 'false'}
        body = {'id': scope, 'name': name, 'type': 'Microsoft.ApiManagement/service', 'location': self.config['location'],
                'tags': {'product': self.config['productName'], 'environment': self.config['environmentName'],
                         'azd-env-name': self.config['environmentName']},
                'properties': {'provisioningState': 'Succeeded', 'hostnameConfigurations': [domain], 'customProperties': settings}}
        transport = ScriptedTransport([{'statusCode': 200, 'body': {'value': [{'name': name}]}},
                                       {'statusCode': 200, 'body': body}])
        retained = {}
        domains = lifecycle.discover_existing_apim_hostnames(self.config, transport, custom_properties_out=retained)
        self.assertEqual(retained, {name: settings})
        self.assertEqual(domains, {name: [{k: v for k, v in domain.items() if v is not None}]})
        self.assertFalse(transport.mutations())

    def test_command_failure_suppresses_private_stdout_and_stderr(self):
        result = mock.Mock(returncode=1, stdout="PRIVATE_STDOUT_FIXTURE", stderr="PRIVATE_STDERR_FIXTURE")
        with mock.patch.object(lifecycle.subprocess, "run", return_value=result):
            with self.assertRaises(lifecycle.DeploymentError) as error:
                self.real_run(["az", "mock-command"])
        self.assertNotIn("PRIVATE_STDOUT_FIXTURE", str(error.exception))
        self.assertNotIn("PRIVATE_STDERR_FIXTURE", str(error.exception))

    def test_predown_retains_environment_scoped_model_deletion_receipt(self):
        state_path = self.paths.state / "latest.json"
        lifecycle.protected_json(state_path, lifecycle.state_from_outputs(self.config, self.outputs()))
        result = {"phase": "predown", "status": "ready-for-azd-down", "modelDeployments": {
            "modelDeletionVerified": True, "quotaReleaseVerified": False, "quotaStatus": "not-read", "deployments": []}}
        with mock.patch.object(lifecycle, "ROOT", self.root), \
             mock.patch.object(lifecycle, "load_configuration", return_value=self.config), \
             mock.patch.object(lifecycle, "ArmTransport"), mock.patch.object(cleanup, "pre_down", return_value=result), \
             mock.patch("builtins.print"):
            self.assertEqual(lifecycle.main(["predown", "--config", str(self.paths.params)]), 0)
        receipt = json.loads((self.root / ".local/test/state/cleanup-predown.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["resourceStem"], self.config["resourceStem"])
        self.assertEqual(receipt["result"], result)
        self.assertIn("observedAtUtc", receipt)
        generated = json.loads(self.paths.parameters.read_text(encoding="utf-8"))["parameters"]
        self.assertEqual(generated["dnsPluginArgumentsJson"]["value"], "")
        self.assertEqual(generated["credentialProviderSecrets"]["value"], {})
        self.assertEqual(generated["l7IngressSecret"]["value"], "")
        self.arm_environment.assert_called_once_with([
            "azd", "env", "set", "EGAKUMI_ARM_PARAMETERS_FILE",
            ".local/test/generated/main.parameters.json", "--environment", "test"])


if __name__ == "__main__":
    unittest.main()
