"""APIMのHTTPS・HTTP/2と入口の鍵の管理を検証する。

偽のARM応答とBicep定義を使い、対象APIMだけの更新、既存HTTPS経路の維持、再実行・撤去時の取扱いを調べる。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import ingress
from deployment import lifecycle


class Transport:
    """ネットワークの代わりに決めた応答を返し、対象ID・更新順・失敗時の停止を調べる。"""
    def __init__(self, responses):
        self.responses = copy.deepcopy(responses)
        self.calls = []

    def request(self, method, path, body=None, headers=None):
        self.calls.append({"method": method, "path": path, "body": body, "headers": headers})
        if not self.responses:
            raise AssertionError("Unexpected mock ARM call")
        return self.responses.pop(0)


def ok(body=None, code=200, headers=None):
    """偽transportから返す成功応答を作る。実ARM接続の代わりに使う。"""
    return {"statusCode": code, "body": body or {}, "headers": headers or {}}


class IngressBootstrapTests(unittest.TestCase):
    """APIMのHTTPS・HTTP/2と入口の鍵の管理を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.enterContext(mock.patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "1"}))
        self.config = {"subscriptionId": "11111111-1111-4111-8111-111111111111",
            "azureTenantId": "22222222-2222-4222-8222-222222222222", "resourceStem": "test-fixture",
            "location": "japaneast", "environmentName": "test",
            "services": {"apimInstanceId": "abc123"},
            "resourceGroupNames": {"svc": "rg-test-fixture-svc"},
            "gateway": {"publicHostName": "fixture.example.com"},
            "certificate": {"certificateName": "gateway-tls"}, "apiDefinitions": [],
            "infrastructureApiDefinitions": []}
        self.client_id = "33333333-3333-4333-8333-333333333333"
        scope = f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/rg-test-fixture-svc/providers/"
        self.apim_id = scope + "Microsoft.ApiManagement/service/apim-test-fixture-abc123"
        self.vault_id = scope + "Microsoft.KeyVault/vaults/kv-test-fixture"
        self.vault = "kv-test-fixture"
        self.outputs = {"ingressSecrets": {"enabled": True, "l7IngressEnabled": True, "apimId": self.apim_id,
            "publicHostName": "fixture.example.com", "apimGatewayHost": self.apim_id.rsplit("/", 1)[-1] + ".azure-api.net"},
            "cleanup": {"serviceResourceIds": {"apim": self.apim_id, "keyVault": self.vault_id}}}
        self.other = {"type": "Proxy", "hostName": self.apim_id.rsplit("/", 1)[-1] + ".azure-api.net", "defaultSslBinding": True, "negotiateClientCertificate": False, "certificateSource": "BuiltIn"}

    def resource(self, domains=None, **properties):
        return {"etag": '"fixture-etag"', "id": self.apim_id, "name": self.apim_id.rsplit("/", 1)[-1],
            "type": "Microsoft.ApiManagement/service", "properties": {"virtualNetworkType": "Internal",
            "gatewayUrl": "https://" + self.other["hostName"],
            "provisioningState": "Succeeded", "hostnameConfigurations": domains if domains is not None else [self.other],
            "customProperties": {ingress.HTTP2: "false", "KeepExistingSetting": "false"}, **properties}}

    def named(self, name=ingress.SECRET_NAME, status="Success"):
        return {"properties": {"secret": True, "keyVault": {
            "secretIdentifier": f"https://{self.vault}.vault.azure.net/secrets/{name}",
            "lastStatus": {"code": status}}}}

    def test_prepares_the_permanent_ingress_secret(self):
        self.assertEqual(set(ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)), {"l7IngressSecret"})

    def test_initial_ingress_credentials_register_once_without_reading_secret_values(self):
        self.config["gateway"]["realtimeControlEnabled"] = True
        source = ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)
        initial = Transport([ok(code=404), ok(code=404)])
        plan = ingress.plan_secret_registration(self.config, self.root, initial, source)
        self.assertEqual(set(plan), {"l7IngressSecret", "realtimeTicketKey"})
        self.assertEqual(len(set(plan.values())), 2)
        names = [ingress.SECRET_NAME, ingress.TICKET_NAME]
        for call, name in zip(initial.calls, names, strict=True):
            self.assertEqual(call["method"], "GET")
            self.assertEqual(call["path"], self.vault_id + f"/secrets/{name}?api-version=2024-11-01")
            self.assertIsNone(call["body"])
        self.assertTrue(set(plan.values()).isdisjoint(source.values()))

        responses = [
            ok({"id": call["path"].split("?")[0], "type": "Microsoft.KeyVault/vaults/secrets",
                "tags": {"egakumi-ingress-generation": plan[parameter]},
                "properties": {"attributes": {"enabled": True}}})
            for call, parameter in zip(initial.calls, ("l7IngressSecret", "realtimeTicketKey"), strict=True)
        ]
        retry = Transport(responses)
        self.assertEqual(ingress.prepare_secret(self.config, self.root, lifecycle.protected_json), source)
        self.assertEqual(ingress.plan_secret_registration(self.config, self.root, retry, source), {})
        self.assertEqual(retry.calls, initial.calls)

        wrong_assignment = copy.deepcopy(responses)
        wrong_assignment[0]["body"]["tags"] = {}
        with self.assertRaisesRegex(ValueError, "restore the protected source"):
            ingress.plan_secret_registration(self.config, self.root, Transport(wrong_assignment), source)

    def test_existing_registration_blocks_replacement_if_owned_source_was_lost(self):
        ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)
        ingress.secret_path(self.config, self.root).unlink()
        replacement = ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)
        owned_path = f"{self.vault_id}/secrets/{ingress.SECRET_NAME}"
        transport = Transport([ok({"id": owned_path, "type": "Microsoft.KeyVault/vaults/secrets",
                                  "tags": {"egakumi-ingress-generation": "0" * 32},
                                  "properties": {"attributes": {"enabled": True}}})])
        with self.assertRaisesRegex(ValueError, "restore the protected source"):
            ingress.plan_secret_registration(self.config, self.root, transport, replacement)
        self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_apim_name_matches_owned_service_case_insensitively(self):
        """所有するサービス名だけを認め、別名・パス・クエリーは拒否する。"""
        self.assertTrue(ingress.apim_name_matches(self.config, "APIM-TEST-FIXTURE-ABC123"))
        for candidate in ("apim-unrelated-service", "apim-test-fixture", "apim-test-fixture-def456",
                          "apim-test-fixture-abc123/apis/other",
                          "apim-test-fixture-abc123?x=1", None):
            with self.subTest(candidate=candidate):
                self.assertFalse(ingress.apim_name_matches(self.config, candidate))

    def test_ingress_checks_exact_scope_and_cleanup_id(self):
        """同じ標準名でも、別サブスク・別RG・撤去記録不一致は変更しない。"""
        self.assertEqual(ingress._own_apim(self.config, self.outputs)[0], self.apim_id)
        for segment, replacement in (("rg-test-fixture-svc", "rg-other-svc"),
                (self.config["subscriptionId"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")):
            candidate = copy.deepcopy(self.outputs)
            foreign = self.apim_id.replace(segment, replacement)
            candidate["ingressSecrets"]["apimId"] = foreign
            candidate["cleanup"]["serviceResourceIds"]["apim"] = foreign
            with self.subTest(scope=segment), self.assertRaises(ValueError):
                ingress._own_apim(self.config, candidate)
        candidate = copy.deepcopy(self.outputs)
        candidate["cleanup"]["serviceResourceIds"]["apim"] = self.apim_id + "-old"
        with self.assertRaises(ValueError):
            ingress._own_apim(self.config, candidate)

    def test_stale_output_contract_is_rejected_before_transport(self):
        for key,value in (("enabled", False), ("l7IngressEnabled", False), ("publicHostName", "other.example.com")):
            candidate = copy.deepcopy(self.outputs)
            candidate["ingressSecrets"][key] = value
            transport = Transport([])
            with self.assertRaises(ValueError): ingress.verify_endpoint(self.config, candidate, transport)
            self.assertEqual(transport.calls, [])

    def test_ingress_rejects_coherent_foreign_service_identity(self):
        """出力と撤去記録が一致しても、別サービスのIDは採用しない。"""
        candidate = copy.deepcopy(self.outputs)
        for entry in (candidate["ingressSecrets"], candidate["cleanup"]["serviceResourceIds"]):
            key = "apimId" if "apimId" in entry else "apim"
            entry[key] = self.apim_id.rsplit("/", 1)[0] + "/apim-unrelated-service"
        with self.assertRaises(ValueError):
            ingress._own_apim(self.config, candidate)

    def test_first_up_protected_random_and_reup_reuses_value(self):
        first = ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)
        second = ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)
        self.assertEqual(first, second)
        self.assertRegex(first["l7IngressSecret"], r"^[a-f0-9]{128}$")
        path = ingress.secret_path(self.config, self.root)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text())["azureTenantId"], self.config["azureTenantId"])

    def test_realtime_key_is_independent_and_reused_when_reselected(self):
        self.config["apiDefinitions"] = [{"authorization": {"operationConstraints": {"create": {"kind": "realtimeCreate"}}}}]
        both = ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)
        self.assertNotEqual(both["l7IngressSecret"], both["realtimeTicketKey"])
        self.config["apiDefinitions"] = []
        ingress_only = ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)
        self.assertEqual(ingress_only, {"l7IngressSecret": both["l7IngressSecret"]})
        self.config["gateway"]["realtimeControlEnabled"] = True
        self.assertEqual(ingress.prepare_secret(self.config, self.root, lifecycle.protected_json), both)
        ingress.remove_secret(self.config, self.root)
        self.assertFalse(list((lifecycle.local_paths(self.config, self.root).secrets).glob("*.json")))

    def test_foundation_can_prepare_ticket_before_api_publication(self):
        self.config["gateway"]["realtimeControlEnabled"] = True
        self.config["apiDefinitions"] = []
        self.assertTrue(ingress.realtime_enabled(self.config))
        credentials = ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)
        self.assertEqual(set(credentials), {"l7IngressSecret", "realtimeTicketKey"})
        self.assertRegex(credentials["realtimeTicketKey"], r"^[a-f0-9]{128}$")

    def test_document_jobs_prepare_and_refresh_owner_key_without_realtime(self):
        """文書専用構成でも、公開前の署名鍵と所有するNamedValueを用意する。"""
        self.config["infrastructureApiDefinitions"] = [{"authorization": {"operationConstraints": {
            "analyze_document": {"kind": "documentAnalysis"}}}}]
        self.assertFalse(ingress.realtime_enabled(self.config))
        self.assertTrue(ingress.owner_ticket_enabled(self.config))
        def save_fixture(path, value):
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")
            path.chmod(0o600)

        credentials = ingress.prepare_secret(self.config, self.root, save_fixture)
        self.assertEqual(set(credentials), {"l7IngressSecret", "realtimeTicketKey"})
        transport = Transport([ok(self.named()), ok(self.named()),
                               ok(self.named(ingress.TICKET_NAME)), ok(self.named(ingress.TICKET_NAME))])
        result = ingress.configure_named_values(self.config, self.outputs, transport)
        self.assertTrue(result["ownerTicketEnabled"])
        self.assertTrue(result["l7IngressEnabled"])
        self.assertFalse(result["realtimeEnabled"])
        self.assertEqual([call["method"] for call in transport.calls], ["POST", "GET", "POST", "GET"])

    def test_empty_public_array_prepares_standard_document_signing_before_metadata_exists(self):
        """Coreが公開APIをまだ展開していなくても、標準文書APIの鍵は必要になる。"""
        self.config.pop("infrastructureApiDefinitions")
        self.assertEqual(self.config["apiDefinitions"], [])
        self.assertNotIn("infrastructureApiDefinitions", self.config)
        self.assertFalse(ingress.realtime_enabled(self.config))
        self.assertTrue(ingress.owner_ticket_enabled(self.config))
        writes = []
        value = ingress.prepare_secret(self.config, self.root, lambda path, payload: writes.append((path, payload)))
        self.assertEqual(set(value), {"l7IngressSecret", "realtimeTicketKey"})
        self.assertEqual([path for path, _ in writes], [ingress.secret_path(self.config, self.root),
                         ingress.secret_path(self.config, self.root, "realtime-ticket")])

    def test_wrong_environment_weak_file_and_symlink_fail_without_replacing(self):
        path = ingress.secret_path(self.config, self.root)
        lifecycle.private_parent(path, self.root)
        lifecycle.protected_json(path, {"value": "a" * 128, "subscriptionId": "other"})
        with self.assertRaisesRegex(ValueError, "different environment"):
            ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)
        path.unlink()
        if os.name != "nt":
            ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "private regular"):
                ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)
            path.unlink()
        target = self.root / "unrelated.json"
        target.write_text("do not change")
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            ingress.prepare_secret(self.config, self.root, lifecycle.protected_json)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            ingress.remove_secret(self.config, self.root)
        self.assertEqual(target.read_text(), "do not change")

    def test_state_directory_symlink_never_unlinks_foreign_credential(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / ingress.secret_path(self.config, self.root).name
            external.write_text("unrelated fixture")
            directory = lifecycle.local_paths(self.config, self.root).secrets
            lifecycle.private_parent(directory, self.root)
            directory.symlink_to(Path(outside), target_is_directory=True)
            for action in (lambda: ingress.prepare_secret(self.config, self.root, lifecycle.protected_json),
                           lambda: ingress.remove_secret(self.config, self.root)):
                with self.assertRaisesRegex(ValueError, "symlinks"):
                    action()
            self.assertEqual(external.read_text(), "unrelated fixture")

    def test_core_confirms_https_without_changing_domains_or_named_values(self):
        transport = Transport([ok(self.resource())])
        result = ingress.verify_endpoint(self.config, self.outputs, transport)
        self.assertEqual([call["method"] for call in transport.calls], ["GET"])
        self.assertEqual(result["apimHttp2"], "false")
        self.assertFalse(result["runtimeVerified"])
        self.assertFalse(any("namedValues" in call["path"] for call in transport.calls))

    def test_core_waits_for_current_http2_setting_without_writes(self):
        transport = Transport([ok(self.resource(provisioningState="Updating")),
                               ok(self.resource(customProperties={ingress.HTTP2: "true"})),
                               ok(self.resource())])
        slept = []
        result = ingress.verify_endpoint(self.config, self.outputs, transport, sleep=slept.append)
        self.assertEqual(result["apimGatewayHost"], self.other["hostName"])
        self.assertEqual(slept, [10, 10])
        self.assertEqual([call["method"] for call in transport.calls], ["GET", "GET", "GET"])

    def test_gateway_deployment_refreshes_only_owned_named_value_without_changing_domain(self):
        transport = Transport([ok(self.named()), ok(self.named())])
        result = ingress.configure_named_values(self.config, self.outputs, transport)
        self.assertTrue(result["l7IngressEnabled"])
        self.assertEqual([call["method"] for call in transport.calls], ["POST", "GET"])
        self.assertTrue(transport.calls[0]["path"].endswith("/namedValues/core-l7-ingress-secret/refreshSecret?api-version=2024-05-01"))
        self.assertNotIn("listSecrets", json.dumps(transport.calls))
        self.assertEqual(transport.responses, [])

    def test_native_endpoint_and_response_identity_are_required(self):
        for field, value in (("id", self.apim_id + "-other"), ("name", "other"), ("type", "other"),
                             ("gatewayUrl", "https://other.example.invalid"), ("virtualNetworkType", "External")):
            initial = self.resource()
            target = initial["properties"] if field in ("gatewayUrl", "virtualNetworkType") else initial
            target[field] = value
            transport = Transport([ok(initial)])
            with self.subTest(field=field), self.assertRaises(ValueError):
                ingress.verify_endpoint(self.config, self.outputs, transport)
            self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_pending_timeout_and_failed_or_unknown_service_are_not_success(self):
        """期限切れ・失敗・削除中・未知状態を設定成功にしない。"""
        for state in ("Updating", "Failed", "Deleting", "Unknown", ""):
            with self.subTest(state=state):
                transport = Transport([ok(self.resource(provisioningState=state))])
                with self.assertRaises(ValueError) as error:
                    ingress.verify_endpoint(self.config, self.outputs, transport, sleep=lambda _: None, timeout_seconds=0)
                self.assertEqual([call["method"] for call in transport.calls], ["GET"])
                self.assertNotIn("fixture-etag", str(error.exception))

    def test_null_system_identity_readback_is_accepted_but_user_assigned_named_value_is_rejected(self):
        """API が null/省略で表す SAMI は同一視し、成功済み UAMI は許可しない。"""
        for identity in (None, self.client_id):
            named = self.named()
            named["properties"]["keyVault"]["identityClientId"] = identity
            transport = Transport([ok(named), ok(named)])
            with self.subTest(identity=identity):
                if identity is None:
                    ingress.configure_named_values(self.config, self.outputs, transport)
                else:
                    with self.assertRaisesRegex(ValueError, "refresh was not confirmed"):
                        ingress.configure_named_values(self.config, self.outputs, transport)

    def test_realtime_refreshes_both_keys_without_custom_domain_or_http2_patch(self):
        self.config["apiDefinitions"] = [{"authorization": {"operationConstraints": {"stop": {"kind": "realtimeHangup"}}}}]
        transport = Transport([ok(self.named()), ok(self.named()),
                               ok(self.named(ingress.TICKET_NAME)), ok(self.named(ingress.TICKET_NAME))])
        result = ingress.configure_named_values(self.config, self.outputs, transport)
        self.assertTrue(result["l7IngressEnabled"])
        self.assertTrue(result["realtimeEnabled"])
        self.assertEqual([call["method"] for call in transport.calls], ["POST", "GET", "POST", "GET"])
        self.assertIn("/namedValues/core-l7-ingress-secret/", transport.calls[0]["path"])
        self.assertIn("/namedValues/core-realtime-ticket-key/", transport.calls[2]["path"])

    def test_refresh_accepts_tenant_operation_and_only_its_own_location_fallback(self):
        """Named Value の二つの正式形式を保ち、別 Secret の読込先は拒否する。"""
        named_path = self.apim_id + "/namedValues/" + ingress.SECRET_NAME + "/refreshSecret"
        tenant = "https://management.azure.com" + self.apim_id + "/tenant/operationResults/fixture==?api-version=" + ingress.API
        fallback = "https://management.azure.com" + named_path + "?api-version=" + ingress.API + "&asyncId=fixture&asyncCode=201"
        azure_async = "https://management.azure.com" + named_path + "?api-version=" + ingress.API + "&azure-asyncId=a1b2c3d4e5f6a1b2c3d4e5f6"
        for target, header in ((tenant, "Azure-AsyncOperation"), (fallback, "Location"),
                               (fallback.replace("asyncCode=201", "asyncCode=200"), "Location"),
                               (azure_async, "Azure-AsyncOperation")):
            with self.subTest(header=header, target=target):
                transport = Transport([ok(code=202, headers={header: target}), ok(), ok(self.named())])
                ingress._refresh_secret(self.apim_id, ingress.SECRET_NAME, self.vault, transport,
                    sleep=lambda _: None, deadline=time.monotonic() + 10, location=self.config["location"])
                self.assertEqual(transport.calls[1]["path"], target)
                self.assertEqual(transport.responses, [])
        for target in (fallback.replace(ingress.SECRET_NAME, ingress.TICKET_NAME),
                       azure_async.replace(ingress.SECRET_NAME, ingress.TICKET_NAME),
                       fallback + "&sig=PRIVATE_FIXTURE", azure_async + "&asyncId=fixture",
                       fallback.replace("asyncCode=201", "asyncCode=202"), azure_async + "&access_token=PRIVATE_FIXTURE"):
            transport = Transport([ok(code=202, headers={"Azure-AsyncOperation": target})])
            with self.subTest(rejected=target), self.assertRaisesRegex(ValueError, "outside this service") as error:
                ingress._refresh_secret(self.apim_id, ingress.SECRET_NAME, self.vault, transport,
                    sleep=lambda _: None, deadline=time.monotonic() + 10, location=self.config["location"])
            self.assertNotIn("PRIVATE_FIXTURE", str(error.exception))
            self.assertEqual([call["method"] for call in transport.calls], ["POST"])

    def test_refresh_uses_validated_location_when_preferred_monitor_is_unsupported(self):
        named_path = self.apim_id + "/namedValues/" + ingress.SECRET_NAME + "/refreshSecret"
        unsupported = ("https://management.azure.com" + self.apim_id
                       + "/unsupportedMonitor/fixture?api-version=" + ingress.API
                       + "&c=synthetic&h=synthetic&s=synthetic&t=synthetic")
        for code in ("200", "201"):
            location = ("https://management.azure.com" + named_path
                        + "?api-version=" + ingress.API + "&asyncId=fixture&asyncCode=" + code)
            transport = Transport([ok(code=202, headers={"Azure-AsyncOperation": unsupported,
                "Location": location}), ok(code=202), ok(self.named()), ok(self.named())])
            with self.subTest(code=code):
                ingress._refresh_secret(self.apim_id, ingress.SECRET_NAME, self.vault, transport,
                    sleep=lambda _: None, deadline=time.monotonic() + 10)
            self.assertEqual([call["method"] for call in transport.calls], ["POST", "GET", "GET", "GET"])
            self.assertEqual([call["path"] for call in transport.calls[1:3]], [location, location])
            self.assertNotIn(unsupported, [call["path"] for call in transport.calls])
            self.assertTrue(transport.calls[-1]["path"].endswith("/namedValues/" + ingress.SECRET_NAME + "?api-version=" + ingress.API))
            self.assertEqual(transport.responses, [])

    def test_completion_prefers_valid_monitor_without_reading_the_other_candidate(self):
        monitor = "https://management.azure.com" + self.apim_id + "/tenant/operationResults/fixture?api-version=" + ingress.API
        for location in (monitor.replace("fixture?", "second?"), "https://other.example.invalid/PRIVATE_FIXTURE"):
            transport = Transport([ok({"status": "Succeeded"})])
            with self.subTest(location=location):
                ingress._poll_operation(ok(code=202, headers={"Azure-AsyncOperation": monitor, "Location": location}),
                    self.apim_id, transport, sleep=lambda _: None, deadline=time.monotonic() + 10)
            self.assertEqual([call["path"] for call in transport.calls], [monitor])

    def test_refresh_fallback_does_not_accept_foreign_named_value_or_extra_query(self):
        own = ("https://management.azure.com" + self.apim_id + "/namedValues/" + ingress.SECRET_NAME
               + "/refreshSecret?api-version=" + ingress.API + "&asyncId=fixture&asyncCode=200")
        unsupported = "https://other.example.invalid/PRIVATE_FIXTURE"
        for location in (own.replace(ingress.SECRET_NAME, ingress.TICKET_NAME), own + "&sig=PRIVATE_FIXTURE",
                         own.replace(ingress.API, "2022-08-01"), unsupported):
            transport = Transport([ok(code=202, headers={"Azure-AsyncOperation": unsupported, "Location": location})])
            with self.subTest(location=location), self.assertRaisesRegex(ValueError, "outside this service") as error:
                ingress._refresh_secret(self.apim_id, ingress.SECRET_NAME, self.vault, transport,
                    sleep=lambda _: None, deadline=time.monotonic() + 10)
            self.assertEqual([call["method"] for call in transport.calls], ["POST"])
            self.assertNotIn("PRIVATE_FIXTURE", str(error.exception))

    def test_selected_completion_failure_does_not_switch_candidate_or_repeat_post(self):
        own = ("https://management.azure.com" + self.apim_id + "/namedValues/" + ingress.SECRET_NAME
               + "/refreshSecret?api-version=" + ingress.API + "&asyncId=fixture&asyncCode=200")
        monitor = "https://management.azure.com" + self.apim_id + "/tenant/operationResults/fixture?api-version=" + ingress.API
        transport = Transport([ok(code=202, headers={"Azure-AsyncOperation": monitor, "Location": own}),
                               ok(code=403, body={"error": {"message": "PRIVATE_FIXTURE"}})])
        with self.assertRaises(ValueError) as error:
            ingress._refresh_secret(self.apim_id, ingress.SECRET_NAME, self.vault, transport,
                sleep=lambda _: None, deadline=time.monotonic() + 10)
        self.assertEqual([call["method"] for call in transport.calls], ["POST", "GET"])
        self.assertEqual(transport.calls[-1]["path"], monitor)
        self.assertNotIn("PRIVATE_FIXTURE", str(error.exception))

    def test_refresh_fallback_respects_expired_deadline_without_get(self):
        own = ("https://management.azure.com" + self.apim_id + "/namedValues/" + ingress.SECRET_NAME
               + "/refreshSecret?api-version=" + ingress.API + "&asyncId=fixture&asyncCode=200")
        transport = Transport([ok(code=202, headers={"Azure-AsyncOperation": "https://other.example.invalid/fixture", "Location": own})])
        with self.assertRaisesRegex(ValueError, "still pending"):
            ingress._refresh_secret(self.apim_id, ingress.SECRET_NAME, self.vault, transport,
                sleep=lambda _: None, deadline=time.monotonic() - 1)
        self.assertEqual([call["method"] for call in transport.calls], ["POST"])

    def test_completion_rejects_foreign_scope_arbitrary_arm_paths_and_credentials_before_get(self):
        """完了 URL でも所有範囲・正式パス・固定クエリーを満たさなければ一度も送信しない。"""
        regional = ("https://management.azure.com/subscriptions/" + self.config["subscriptionId"]
                    + "/providers/Microsoft.ApiManagement/locations/japaneast/operationResults/fixture?api-version=" + ingress.API)
        service = "https://management.azure.com" + self.apim_id
        invalid = [regional.replace(self.config["subscriptionId"], self.config["azureTenantId"]),
            regional.replace("japaneast", "westus2"), regional.replace("operationResults", "unrelated"),
            regional.replace("fixture?", "../?"), regional.replace("fixture?", "%2e%2e?"),
            regional.replace("fixture?", "nested%2Ffixture?"), regional.replace("fixture?", "fixture/extra?"),
            regional.replace("https:", "http:"), regional.replace("management.azure.com", "management.azure.com.evil.example"),
            regional.replace("management.azure.com", "user:PRIVATE_FIXTURE@management.azure.com"),
            regional.replace("management.azure.com", "management.azure.com:444"), regional + "#PRIVATE_FIXTURE",
            regional + "&access_token=PRIVATE_FIXTURE", regional + "&api-version=" + ingress.API,
            regional.replace(ingress.API, "2022-08-01"), regional + "&asyncResponse=PRIVATE_FIXTURE",
            regional.replace("fixture", "fixture\n"), service + "/namedValues/unrelated?api-version=" + ingress.API,
            service + "/operationresults/fixture?api-version=" + ingress.API + "&sig=PRIVATE_FIXTURE"]
        for target in invalid:
            with self.subTest(target=target):
                transport = Transport([])
                with self.assertRaisesRegex(ValueError, "outside this service") as error:
                    ingress._poll_operation(ok(code=202, headers={"Location": target}), self.apim_id, transport,
                        sleep=lambda _: None, deadline=time.monotonic() + 10, location=self.config["location"])
                self.assertNotIn("PRIVATE_FIXTURE", str(error.exception))
                self.assertEqual(transport.calls, [])

    def test_service_completion_flag_retained_and_regional_requires_configured_region(self):
        """公式 Service Update の opaque ID と asyncResponse を許すが、地域を URL から推定しない。"""
        service = "https://management.azure.com" + self.apim_id + "/operationresults/fixture==?api-version=" + ingress.API + "&asyncResponse"
        transport = Transport([ok({"status": "Succeeded"})])
        ingress._poll_operation(ok(code=202, headers={"Azure-AsyncOperation": service}), self.apim_id, transport,
            sleep=lambda _: None, deadline=time.monotonic() + 10)
        self.assertEqual(transport.calls[0]["path"], service)
        regional = ("https://management.azure.com/subscriptions/" + self.config["subscriptionId"]
                    + "/providers/Microsoft.ApiManagement/locations/japaneast/operationResults/fixture?api-version=" + ingress.API)
        transport = Transport([])
        with self.assertRaisesRegex(ValueError, "outside this service"):
            ingress._poll_operation(ok(code=202, headers={"Location": regional}), self.apim_id, transport,
                sleep=lambda _: None, deadline=time.monotonic() + 10)
        self.assertEqual(transport.calls, [])

    def test_refresh_failed_or_unconfirmed_does_not_report_configured(self):
        for response in (ok(code=403, body={"error": {"message": "PRIVATE_FIXTURE"}}), ok(self.named(status="AccessDenied"))):
            transport = Transport([response, ok(self.named(status="AccessDenied"))])
            with self.assertRaises(ValueError) as error:
                ingress.configure_named_values(self.config, self.outputs, transport)
            self.assertNotIn("PRIVATE_FIXTURE", str(error.exception))


class IngressInfrastructureSourceTests(unittest.TestCase):
    """APIMのHTTPS・HTTP/2と入口の鍵の管理を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def test_secure_inputs_remain_private_and_normal_http_ingress_is_permanent(self):
        core = Path(__file__).resolve().parents[1]
        source = (core / "infra/main.bicep").read_text()
        self.assertIn("@secure()\nparam l7IngressSecret", source)
        self.assertIn("@secure()\nparam realtimeTicketKey", source)
        self.assertNotIn("output grpc ", source)
        self.assertIn("l7IngressEnabled: useIngressSecrets", source)
        services = (core / "infra/modules/services/services.bicep").read_text()
        self.assertIn("'Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2': 'false'", services)
        gateway = (core / "infra/modules/ingress/application-gateway.bicep").read_text()
        self.assertIn("headerName: 'X-Core-L7-Ingress', headerValue: l7IngressSecret", gateway)
        self.assertIn("enableHttp2: true", gateway)



if __name__ == "__main__":
    unittest.main()
