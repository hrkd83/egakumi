"""Isolated lifecycle configuration and scripted ARM responses shared by tests."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment import lifecycle
from deployment.helpers import cleanup
from deployment.helpers import prepare_secrets as secret_preparation
from deployment.helpers.configuration import normalize_configuration
from deployment.helpers.paths import local_paths
from configuration_fixtures import connection_fixture, fixture, ssh_fixture_key


class ScriptedTransport:
    """事前に並べたARM応答を順番に返す。想定外の要求は検証失敗として扱う。"""
    def __init__(self, responses):
        self.responses = copy.deepcopy(responses)
        self.calls = []

    def request(self, method, path, body=None, headers=None):
        self.calls.append({"method": method, "path": path, "body": body, "headers": headers})
        if not self.responses:
            raise AssertionError("Unexpected mock ARM request")
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
        core = Path(__file__).resolve().parents[1]
        raw = fixture()
        raw["publishApis"] = True  # These fixtures exercise the complete azd up lifecycle.
        self.config = normalize_configuration(raw, base_directory=core / "config")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.paths = local_paths(self.config, self.root)
        # 正規化済み設定にも元repoの絶対パスが残るため、ROOTだけの差替では足りない。
        # 新しい鍵準備hookを誤って実行しても、実ユーザー鍵へ到達させない。
        self.config["management"]["sshPublicKeyFile"] = str(self.paths.secrets / "management-ssh.pub")
        # 対話準備hookに将来分岐が増えても、実ユーザーの秘密ファイルを
        # 読み書きさせない。すべての秘密参照を同じ一時フォルダーへ閉じ込める。
        secret_fields = {"pluginArgumentsFile", "preparedPfxFile", "preparedPfxPasswordFile", "clientSecretFile"}
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
        env = mock.patch.dict(os.environ, {"AZURE_ENV_NAME": self.config["environmentName"], "PYTHONDONTWRITEBYTECODE": "1"}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def outputs(self):
        sub = self.config["subscriptionId"]
        svc = self.config["resourceGroupNames"]["svc"]
        prefix = f"/subscriptions/{sub}/resourceGroups/{svc}/providers/"
        return {
            "names": {"apiCenter": "apic-" + self.config["resourceStem"], "apim": "apim-" + self.config["resourceStem"] + "-abc123"},
            "resourceGroups": self.config["resourceGroupNames"],
            "monitoring": {"workspaceId": f"/subscriptions/{sub}/resourceGroups/{self.config['resourceGroupNames']['monitor']}/providers/Microsoft.OperationalInsights/workspaces/law-test-egakumi"},
            "auditStorage": {"immutabilityMode": "Disabled"},
            "cleanup": {
                "serviceResourceIds": {
                    "keyVault": prefix + "Microsoft.KeyVault/vaults/kv-test-egakumi",
                    "foundry": prefix + "Microsoft.CognitiveServices/accounts/aif-test-egakumi",
                    "apim": prefix + "Microsoft.ApiManagement/service/apim-test-egakumi-abc123",
                },
            },
        }
