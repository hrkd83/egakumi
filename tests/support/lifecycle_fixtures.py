"""Isolated lifecycle configuration and scripted ARM responses shared by tests."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from deployment import lifecycle
from deployment.helpers import cleanup
from deployment.helpers import prepare_secrets as secret_preparation
from deployment.helpers import private_permissions
from deployment.helpers.configuration import normalize_configuration
from deployment.helpers.paths import local_paths
from tests.support.configuration_fixtures import connection_fixture, fixture, ssh_fixture_key


class ScriptedTransport:
    """事前に並べたARM応答を順番に返す。想定外の要求は検証失敗として扱う。"""
    def __init__(self, responses, *, expected_get_paths=None):
        self.responses = copy.deepcopy(responses)
        self.calls = []
        self.expected_get_paths = expected_get_paths

    def request(self, method, path, body=None, headers=None):
        self.calls.append({"method": method, "path": path, "body": body, "headers": headers})
        if not self.responses:
            raise AssertionError("Unexpected mock ARM request")
        if self.expected_get_paths is not None:
            expected = self.expected_get_paths[len(self.calls) - 1]
            if method != "GET" or path != expected:
                raise AssertionError(f"Expected mock GET {expected}, got {method} {path}")
        return self.responses.pop(0)

    def mutations(self):
        return [call for call in self.calls if call["method"] != "GET"]

def response_policy(state="Unlocked", *, days=2557, append=True, etag='"version-1"'):
    """配備されたpolicyに相当する合成応答を作り、順序の検証に使う。"""
    return {
        "statusCode": 200,
        "headers": {} if etag is None else {"ETag": etag},
        "body": {"properties": {
            "state": state,
            "immutabilityPeriodSinceCreationInDays": days,
            "allowProtectedAppendWrites": append,
        }},
    }

class LifecycleFixture:
    """lifecycleの検証に必要な一時フォルダー、合成設定と依存の差し替えを共通化する。"""
    def setUp(self):
        core = Path(__file__).resolve().parents[2]
        raw = fixture()
        raw["publishApis"] = True  # These fixtures exercise the complete azd up lifecycle.
        self.config = normalize_configuration(raw, base_directory=core / "infra/config")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.paths = local_paths(self.config, self.root)
        # 正規化済み設定にも元repoの絶対パスが残るため、ROOTだけの差替では足りない。
        # 新しい鍵準備hookを誤って実行しても、実ユーザー鍵へ到達させない。
        self.config["management"]["sshPublicKeyFile"] = str(self.paths.secrets / "management-ssh.pub")
        # 対話準備hookに将来分岐が増えても、実ユーザーの秘密ファイルを
        # 読み書きさせない。すべての秘密参照を同じ一時フォルダーへ閉じ込める。
        secret_fields = {"pluginArgumentsFile", "clientSecretFile"}
        paths_seen = {}

        def isolate_secret_paths(value):
            if isinstance(value, list):
                for item in value:
                    isolate_secret_paths(item)
            elif isinstance(value, dict):
                for key, item in value.items():
                    if key in secret_fields and isinstance(item, str):
                        paths_seen.setdefault(item, str(self.paths.secrets / (str(len(paths_seen)) + "-" + Path(item).name)))
                        value[key] = paths_seen[item]
                    else:
                        isolate_secret_paths(item)

        isolate_secret_paths(self.config)
        # ROOTを差し替え、全可変パスを同じtemp内の環境別領域へ隔離する。
        paths = mock.patch.object(lifecycle, "ROOT", self.root)
        paths.start()
        self.addCleanup(paths.stop)
        original_write = lifecycle.protected_json

        def isolated_write(path, value):
            """将来書込先が増えても、temp 外へ書く前に単体試験を失敗させる。"""
            self.assertTrue(Path(path).resolve().is_relative_to(self.root.resolve()),
                            "Lifecycle fixture must never write outside its temporary root")
            return original_write(path, value)

        writes = mock.patch.object(lifecycle, "protected_json", side_effect=isolated_write)
        writes.start()
        self.addCleanup(writes.stop)
        # An accidentally unmocked CLI call must fail locally, never execute.
        process_guard = mock.patch.object(lifecycle.subprocess, "run", side_effect=AssertionError("No CLI process is permitted in lifecycle tests"))
        process_guard.start()
        self.addCleanup(process_guard.stop)
        if os.name == "nt":
            acl = mock.patch.object(private_permissions, "_windows_owner_acl")
            acl.start()
            self.addCleanup(acl.stop)
        # Serialの所有・世代・DACLは専用結合試験で検査する。他のhook試験には
        # VM/RGのHTTP要求やnative ACL processを追加せず、入口の呼出しだけを残す。
        for name, value in (("_prepare_management_serial_password", ""),
                            ("_confirm_management_serial_target", None),
                            ("_bind_management_serial_password", None),
                            ("require_serial_storage_permissions", None),
                            ("protect_serial_storage", None)):
            patch = mock.patch.object(lifecycle, name, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)
        # 通常hook試験でも生成JSONの保護保存を通す。azd環境へのパス登録だけを
        # process境界で受け、未知のCLI要求は実行せず失敗させる。
        def environment_only(arguments, **kwargs):
            expected = ["azd", "env", "set", "EGAKUMI_ARM_PARAMETERS_FILE",
                        ".local/" + self.config["environmentName"] + "/generated/main.parameters.json",
                        "--environment", self.config["environmentName"]]
            self.assertEqual(arguments, expected, "Unexpected lifecycle CLI process")
            return {}

        self.real_run = lifecycle.run
        run = mock.patch.object(lifecycle, "run", side_effect=environment_only)
        self.arm_environment = run.start()
        self.addCleanup(run.stop)
        bridge = mock.patch.object(lifecycle, "check_parameter_bridge")
        self.parameter_bridge = bridge.start()
        self.addCleanup(bridge.stop)
        # 診断discoveryのHTTPは専用試験で実装を通す。他のhook試験には純粋な正本だけを渡す。
        diagnostic = mock.patch.object(lifecycle, "prepare_diagnostic_settings", side_effect=lambda config, transport, phase, preserve_collection=False: {
            key: item["properties"] for key, item in lifecycle.canonical_settings(
                lifecycle.build_bicep_configuration(config), phase, preserve_collection).items()})
        self.diagnostics = diagnostic.start()
        self.addCleanup(diagnostic.stop)
        # 復元のHTTP分岐は専用テストで検査し、他のhook試験では追加Azure要求をさせない。
        api_center = mock.patch.object(lifecycle, "ensure_api_center_available",
                                       return_value={"status": "available-for-create"})
        self.api_center = api_center.start()
        self.addCleanup(api_center.stop)
        # 取り込み変換のHTTP/readbackは専用テストが担当し、他のhook試験へARM要求を追加しません。
        privacy_read = mock.patch.object(lifecycle, "read_workspace_privacy_link", return_value="")
        privacy_read.start()
        self.addCleanup(privacy_read.stop)
        privacy_link = mock.patch.object(lifecycle, "ensure_workspace_link", return_value="/subscriptions/fixture/dataCollectionRules/dcr-fixture-workspace")
        privacy_link.start()
        self.addCleanup(privacy_link.stop)
        # NSGの規則と所有境界は専用試験で検査します。
        # hook試験では実wrapperを通し、Coreの前後に照合する順序を検査します。
        host_time = mock.patch.object(lifecycle, "require_management_host_time_core_ready", return_value={})
        self.host_time_gate = host_time.start()
        self.addCleanup(host_time.stop)
        ca_loader = mock.patch.object(lifecycle, "load_firewall_tls_inspection", return_value={})
        ca_loader.start()
        self.addCleanup(ca_loader.stop)
        portal_gate = mock.patch.object(lifecycle, "require_appgw_backend_tls_ready",
                                       return_value={"newGateway": True, "httpMigrationRequired": False})
        self.backend_tls_gate = portal_gate.start()
        self.appgw_tls_gate = self.backend_tls_gate
        self.addCleanup(portal_gate.stop)
        prepare_nsg = mock.patch.object(lifecycle, "verify_fixed_nsg_before_deployment")
        self.prepare_nsg = prepare_nsg.start()
        self.addCleanup(prepare_nsg.stop)
        complete_nsg = mock.patch.object(lifecycle, "verify_fixed_nsg_after_deployment")
        self.complete_nsg = complete_nsg.start()
        self.addCleanup(complete_nsg.stop)
        # APIMの固定名確認は別の専用試験でARM応答を再現する。hook試験では
        # 実Azureを呼ばず、有料段階より前に呼び出される順序を検査する。
        apim_name = mock.patch.object(lifecycle, "verify_apim_name",
            return_value={"status": "available-for-create", "name": "apim-" + self.config["resourceStem"] + "-abc123"})
        self.apim_name = apim_name.start()
        self.addCleanup(apim_name.stop)
        # 既存hookの単体試験はDNS選択を無操作にします。専用の結合ケース以外が
        # 誤って実ユーザーのparamsを書き換えないよう、公開設定の再保存も隔離します。
        selector = mock.patch.object(lifecycle, "select_dns_provider", side_effect=lambda source, config: config, create=True)
        self.dns_selection = selector.start()
        self.addCleanup(selector.stop)
        dns = mock.patch.object(lifecycle, "configure_public_dns", return_value={"status": "configured"})
        self.public_dns = dns.start()
        self.addCleanup(dns.stop)
        for module, name, settings in (
            ("deployment.helpers.certificate_jobs", "complete_initial_certificates", {
                "return_value": {"imageDigest": "synthetic-digest", "tls": {}, "portal": {}}}),
            ("deployment.helpers.portal_auth_certificate", "store_initial_certificate", {}),
            ("deployment.lifecycle", "deploy_public_ingress", {
                "side_effect": lambda config, outputs, *args: {"publicUrl": outputs["endpoints"]["public"]}}),
            ("deployment.lifecycle", "observe_initial_certificate", {}),
        ):
            dependency = mock.patch(f"{module}.{name}", **settings)
            dependency.start()
            self.addCleanup(dependency.stop)
        env = mock.patch.dict(os.environ, {"AZURE_ENV_NAME": self.config["environmentName"], "PYTHONDONTWRITEBYTECODE": "1"}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def preprovision_transport(self, *, firewall_snapshots=()):
        """初回の所有資源探索をARMの実際の読取先ごとに再現する。"""
        config = self.config
        stem = config["resourceStem"]
        subscription = f"/subscriptions/{config['subscriptionId']}"
        groups = config["resourceGroupNames"]
        services = f"{subscription}/resourceGroups/{groups['svc']}/providers"
        network = f"{subscription}/resourceGroups/{groups['nw']}/providers/Microsoft.Network"
        missing = {"statusCode": 404, "headers": {}, "body": None}
        requests = [
            (f"{services}/Microsoft.ContainerRegistry/registries/acr{stem.replace('-', '')}?api-version=2025-11-01", missing),
            (f"{services}/Microsoft.KeyVault/vaults/kv-{stem}/secrets/core-l7-ingress-secret?api-version=2024-11-01", missing),
        ]
        if config["gateway"].get("realtimeControlEnabled") is True:
            requests.append(
                (f"{services}/Microsoft.KeyVault/vaults/kv-{stem}/secrets/core-realtime-ticket-key?api-version=2024-11-01", missing))
        requests.append((f"{subscription}/providers/Microsoft.Resources/deployments/{stem}-monitoring?api-version=2022-09-01", missing))

        nsg_suffixes = ["agw", "apim", "pep", "vm", "portal", "certificate", "broker"]
        route_suffixes = ["agw", "apim", "vm", "portal", "certificate", "broker"]
        requests.extend((f"{network}/networkSecurityGroups/nsg-{stem}-{suffix}?api-version=2025-01-01", missing)
                        for suffix in nsg_suffixes)
        requests.extend((f"{network}/routeTables/rt-{stem}-{suffix}?api-version=2025-01-01", missing)
                        for suffix in route_suffixes)
        requests.extend([
            (f"{services}/Microsoft.ApiManagement/service?api-version=2024-05-01", missing),
            (f"{services}/Microsoft.App/containerApps/ca-{stem}-portal?api-version=2025-01-01", missing),
        ])
        if firewall_snapshots:
            requests.extend((snapshot["body"]["id"] + "?api-version=2025-01-01", snapshot)
                            for snapshot in firewall_snapshots)
        else:
            requests.append((f"{network}/firewallPolicies/afwp-{stem}?api-version=2025-01-01", missing))
        paths, responses = zip(*requests)
        return ScriptedTransport(list(responses), expected_get_paths=paths)

    def core_completion_response(self, outputs):
        """生成入力とネイティブCore出力を同じ合成環境へ束縛する。"""
        outputs.setdefault("endpoints", {"public": "https://fixture.example.com"})
        outputs["gateway"] = {"publicUrl": outputs["endpoints"]["public"]}
        outputs["gatewayPlan"] = {}
        outputs["ingressMonitoringPlan"] = {}
        stem = self.config["resourceStem"]
        service_group = self.config["resourceGroupNames"]["svc"]
        certificate_environment = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/{service_group}"
                                   f"/providers/Microsoft.App/managedEnvironments/cae-{stem}-certificate")
        bootstrap = {"jobName": "synthetic-job", "environmentId": certificate_environment}
        outputs["certificateBootstrap"] = {**bootstrap, "imageDigest": "synthetic-digest"}
        prepared = {
            **outputs, "gateway": {},
            "certificateBootstrap": bootstrap,
            "ingressSecrets": {**outputs["ingressSecrets"], "enabled": False},
        }
        lifecycle.protected_json(self.paths.parameters, {"parameters": {
            "firewallTlsInspection": {"value": {}},
            "l7IngressSecret": {"value": "synthetic-input"},
        }})
        return {"properties": {"provisioningState": "Succeeded",
                "outputs": {key: {"value": value} for key, value in prepared.items()}}}

    def outputs(self):
        sub = self.config["subscriptionId"]
        svc = self.config["resourceGroupNames"]["svc"]
        prefix = f"/subscriptions/{sub}/resourceGroups/{svc}/providers/"
        return {
            "names": {"apiCenter": "apic-" + self.config["resourceStem"], "apim": "apim-" + self.config["resourceStem"] + "-abc123"},
            "resourceGroups": self.config["resourceGroupNames"],
            "monitoring": {"workspaceId": f"/subscriptions/{sub}/resourceGroups/{self.config['resourceGroupNames']['monitor']}/providers/Microsoft.OperationalInsights/workspaces/law-test-egakumi"},
            "auditStorage": {"immutabilityMode": "Disabled"},
            "ingressSecrets": {"enabled": True, "l7IngressEnabled": True,
                               "publicHostName": self.config["gateway"]["publicHostName"]},
            "cleanup": {
                "serviceResourceIds": {
                    "keyVault": prefix + "Microsoft.KeyVault/vaults/kv-test-egakumi",
                    "certificateVault": prefix + "Microsoft.KeyVault/vaults/kvtls-test-egakumi",
                    "foundry": prefix + "Microsoft.CognitiveServices/accounts/aif-test-egakumi",
                    "apim": prefix + "Microsoft.ApiManagement/service/apim-test-egakumi-abc123",
                },
            },
        }
