"""gRPCの証明書設定・HTTP/2・入口と鍵の管理を検証する。

偽のARM応答とBicep定義を使い、対象APIMだけの更新、既存HTTPS経路の維持、再実行・撤去時の取扱いを調べる。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import stat
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import grpc_lifecycle as grpc
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


class GrpcBootstrapTests(unittest.TestCase):
    """gRPCの証明書設定・HTTP/2・入口と鍵の管理を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
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
        self.secret_uri = f"https://{self.vault}.vault.azure.net/secrets/gateway-tls"
        self.outputs = {"ingressSecrets": {"enabled": True, "l7IngressEnabled": True, "apimId": self.apim_id,
            "publicHostName": "fixture.example.com", "certificateSecretUri": self.secret_uri,
            "namedValueId": self.apim_id + "/namedValues/" + grpc.SECRET_NAME},
            "cleanup": {"serviceResourceIds": {"apim": self.apim_id, "keyVault": self.vault_id}}}
        self.desired = {"type": "Proxy", "hostName": "fixture.example.com", "keyVaultId": self.secret_uri,
            "defaultSslBinding": True, "negotiateClientCertificate": False, "certificateSource": "KeyVault"}
        self.journal = []
        self.other = {"type": "Proxy", "hostName": self.apim_id.rsplit("/", 1)[-1] + ".azure-api.net", "defaultSslBinding": True, "negotiateClientCertificate": False, "certificateSource": "BuiltIn"}

    def resource(self, domains=None, **properties):
        return {"etag": '"fixture-etag"', "id": self.apim_id, "name": self.apim_id.rsplit("/", 1)[-1],
            "type": "Microsoft.ApiManagement/service", "properties": {"virtualNetworkType": "Internal",
            "gatewayUrl": "https://" + self.other["hostName"],
            "provisioningState": "Succeeded", "hostnameConfigurations": domains if domains is not None else [self.other],
            "customProperties": {grpc.HTTP2: "false", "KeepExistingSetting": "false"}, **properties}}

    def configure_endpoint(self, *args, **kwargs):
        kwargs.setdefault("mutation_recorder", lambda event, value: self.journal.append((event, copy.deepcopy(value))))
        return grpc.configure_endpoint(*args, **kwargs)

    def named(self, name=grpc.SECRET_NAME, status="Success"):
        return {"properties": {"secret": True, "keyVault": {
            "secretIdentifier": f"https://{self.vault}.vault.azure.net/secrets/{name}",
            "lastStatus": {"code": status}}}}

    def test_retired_grpc_inputs_are_rejected_before_writes_or_transport(self):
        for value in ({}, {"enabled": True}, {"enabled": False}, {"publicPort": 8443}):
            self.config["gateway"]["grpc"] = value
            transport = Transport([])
            for action in (lambda: grpc.prepare_secret(self.config, self.root, lifecycle.protected_json), lambda: grpc.configure_endpoint(self.config, {}, transport), lambda: grpc.configure_named_values(self.config, {}, transport)):
                with self.assertRaises(ValueError): action()
            self.assertEqual(transport.calls, [])
            self.assertFalse(list(self.root.iterdir()))

    def test_omitted_grpc_still_prepares_the_permanent_ingress_secret(self):
        self.config["gateway"].pop("grpc", None)
        self.assertTrue(grpc.enabled(self.config))
        self.assertEqual(set(grpc.prepare_secret(self.config, self.root, lifecycle.protected_json)), {"l7IngressSecret"})

    def test_apim_name_matches_owned_service_case_insensitively(self):
        """所有するサービス名だけを認め、別名・パス・クエリーは拒否する。"""
        self.assertTrue(grpc.apim_name_matches(self.config, "APIM-TEST-FIXTURE-ABC123"))
        for candidate in ("apim-unrelated-service", "apim-test-fixture", "apim-test-fixture-def456",
                          "apim-test-fixture-abc123/apis/other",
                          "apim-test-fixture-abc123?x=1", None):
            with self.subTest(candidate=candidate):
                self.assertFalse(grpc.apim_name_matches(self.config, candidate))

    def test_grpc_checks_exact_scope_and_cleanup_id(self):
        """同じ標準名でも、別サブスク・別RG・撤去記録不一致は変更しない。"""
        self.assertEqual(grpc._own_apim(self.config, self.outputs)[0], self.apim_id)
        for segment, replacement in (("rg-test-fixture-svc", "rg-other-svc"),
                (self.config["subscriptionId"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")):
            candidate = copy.deepcopy(self.outputs)
            foreign = self.apim_id.replace(segment, replacement)
            candidate["ingressSecrets"]["apimId"] = foreign
            candidate["cleanup"]["serviceResourceIds"]["apim"] = foreign
            with self.subTest(scope=segment), self.assertRaises(ValueError):
                grpc._own_apim(self.config, candidate)
        candidate = copy.deepcopy(self.outputs)
        candidate["cleanup"]["serviceResourceIds"]["apim"] = self.apim_id + "-old"
        with self.assertRaises(ValueError):
            grpc._own_apim(self.config, candidate)

    def test_stale_output_contract_is_rejected_before_transport(self):
        for key,value in (("enabled", False), ("l7IngressEnabled", False), ("publicHostName", "other.example.com")):
            candidate = copy.deepcopy(self.outputs)
            candidate["ingressSecrets"][key] = value
            transport = Transport([])
            with self.assertRaises(ValueError): grpc.configure_endpoint(self.config, candidate, transport)
            self.assertEqual(transport.calls, [])

    def test_legacy_versionless_certificate_reference_accepts_only_one_optional_final_slash(self):
        for uri in (self.secret_uri, self.secret_uri + "/"):
            feature = {**self.outputs["ingressSecrets"], "certificateSecretUri": uri}
            self.assertEqual(grpc._legacy_owned_domain_uri(self.config, feature, self.outputs), self.secret_uri)
        for uri in (self.secret_uri + "//", self.secret_uri + "/version", self.secret_uri + "?query=1",
                    self.secret_uri + "-other"):
            feature = {**self.outputs["ingressSecrets"], "certificateSecretUri": uri}
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                grpc._legacy_owned_domain_uri(self.config, feature, self.outputs)

    def test_grpc_rejects_coherent_foreign_service_identity(self):
        """出力と撤去記録が一致しても、別サービスのIDは採用しない。"""
        candidate = copy.deepcopy(self.outputs)
        for entry in (candidate["ingressSecrets"], candidate["cleanup"]["serviceResourceIds"]):
            key = "apimId" if "apimId" in entry else "apim"
            entry[key] = self.apim_id.rsplit("/", 1)[0] + "/apim-unrelated-service"
        with self.assertRaises(ValueError):
            grpc._own_apim(self.config, candidate)

    def test_first_up_protected_random_and_reup_reuses_value(self):
        first = grpc.prepare_secret(self.config, self.root, lifecycle.protected_json)
        second = grpc.prepare_secret(self.config, self.root, lifecycle.protected_json)
        self.assertEqual(first, second)
        self.assertRegex(first["l7IngressSecret"], r"^[a-f0-9]{128}$")
        path = grpc.secret_path(self.config, self.root)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text())["azureTenantId"], self.config["azureTenantId"])

    def test_realtime_key_is_independent_and_reused_when_reselected(self):
        self.config["apiDefinitions"] = [{"authorization": {"operationConstraints": {"create": {"kind": "realtimeCreate"}}}}]
        both = grpc.prepare_secret(self.config, self.root, lifecycle.protected_json)
        self.assertNotEqual(both["l7IngressSecret"], both["realtimeTicketKey"])
        self.config["apiDefinitions"] = []
        ingress = grpc.prepare_secret(self.config, self.root, lifecycle.protected_json)
        self.assertEqual(ingress, {"l7IngressSecret": both["l7IngressSecret"]})
        self.config["gateway"]["realtimeControlEnabled"] = True
        self.assertEqual(grpc.prepare_secret(self.config, self.root, lifecycle.protected_json), both)
        grpc.remove_secret(self.config, self.root)
        self.assertFalse(list((lifecycle.local_paths(self.config, self.root).secrets).glob("*.json")))

    def test_foundation_can_prepare_ticket_before_api_publication(self):
        self.config["gateway"]["realtimeControlEnabled"] = True
        self.config["apiDefinitions"] = []
        self.assertTrue(grpc.realtime_enabled(self.config))
        credentials = grpc.prepare_secret(self.config, self.root, lifecycle.protected_json)
        self.assertEqual(set(credentials), {"l7IngressSecret", "realtimeTicketKey"})
        self.assertRegex(credentials["realtimeTicketKey"], r"^[a-f0-9]{128}$")

    def test_document_jobs_prepare_and_refresh_owner_key_without_realtime(self):
        """文書専用構成でも、公開前の署名鍵と所有するNamedValueを用意する。"""
        self.config["infrastructureApiDefinitions"] = [{"authorization": {"operationConstraints": {
            "analyze_document": {"kind": "documentAnalysis"}}}}]
        self.assertFalse(grpc.realtime_enabled(self.config))
        self.assertTrue(grpc.owner_ticket_enabled(self.config))
        def save_fixture(path, value):
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")
            path.chmod(0o600)

        credentials = grpc.prepare_secret(self.config, self.root, save_fixture)
        self.assertEqual(set(credentials), {"l7IngressSecret", "realtimeTicketKey"})
        transport = Transport([ok(self.named()), ok(self.named()),
                               ok(self.named(grpc.TICKET_NAME)), ok(self.named(grpc.TICKET_NAME))])
        result = grpc.configure_named_values(self.config, self.outputs, transport)
        self.assertTrue(result["ownerTicketEnabled"])
        self.assertTrue(result["l7IngressEnabled"])
        self.assertFalse(result["realtimeEnabled"])
        self.assertEqual([call["method"] for call in transport.calls], ["POST", "GET", "POST", "GET"])

    def test_empty_public_array_prepares_standard_document_signing_before_metadata_exists(self):
        """Coreが公開APIをまだ展開していなくても、標準文書APIの鍵は必要になる。"""
        self.config.pop("infrastructureApiDefinitions")
        self.assertEqual(self.config["apiDefinitions"], [])
        self.assertNotIn("infrastructureApiDefinitions", self.config)
        self.assertFalse(grpc.realtime_enabled(self.config))
        self.assertTrue(grpc.owner_ticket_enabled(self.config))
        writes = []
        value = grpc.prepare_secret(self.config, self.root, lambda path, payload: writes.append((path, payload)))
        self.assertEqual(set(value), {"l7IngressSecret", "realtimeTicketKey"})
        self.assertEqual([path for path, _ in writes], [grpc.secret_path(self.config, self.root),
                         grpc.secret_path(self.config, self.root, "realtime-ticket")])

    def test_wrong_environment_weak_file_and_symlink_fail_without_replacing(self):
        path = grpc.secret_path(self.config, self.root)
        lifecycle.private_parent(path, self.root)
        lifecycle.protected_json(path, {"value": "a" * 128, "subscriptionId": "other"})
        with self.assertRaisesRegex(ValueError, "different environment"):
            grpc.prepare_secret(self.config, self.root, lifecycle.protected_json)
        path.unlink()
        if os.name != "nt":
            grpc.prepare_secret(self.config, self.root, lifecycle.protected_json)
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "private regular"):
                grpc.prepare_secret(self.config, self.root, lifecycle.protected_json)
            path.unlink()
        target = self.root / "unrelated.json"
        target.write_text("do not change")
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            grpc.prepare_secret(self.config, self.root, lifecycle.protected_json)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            grpc.remove_secret(self.config, self.root)
        self.assertEqual(target.read_text(), "do not change")

    def test_state_directory_symlink_never_unlinks_foreign_credential(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / grpc.secret_path(self.config, self.root).name
            external.write_text("unrelated fixture")
            directory = lifecycle.local_paths(self.config, self.root).secrets
            lifecycle.private_parent(directory, self.root)
            directory.symlink_to(Path(outside), target_is_directory=True)
            for action in (lambda: grpc.prepare_secret(self.config, self.root, lifecycle.protected_json),
                           lambda: grpc.remove_secret(self.config, self.root)):
                with self.assertRaisesRegex(ValueError, "symlinks"):
                    action()
            self.assertEqual(external.read_text(), "unrelated fixture")

    def test_retirement_preserves_other_domains_settings_and_records_readback(self):
        foreign = {**self.desired, "hostName": "maintenance.example.com", "defaultSslBinding": False}
        transport = Transport([ok(self.resource([self.other, foreign, self.desired])), ok(code=202), ok(self.resource([self.other, foreign]))])
        result = self.configure_endpoint(self.config, self.outputs, transport, sleep=lambda _: None)
        patch = transport.calls[1]
        self.assertEqual(patch["method"], "PATCH")
        self.assertEqual(patch["body"]["properties"]["hostnameConfigurations"], [self.other, foreign])
        self.assertEqual(patch["body"]["properties"]["customProperties"], {grpc.HTTP2: "false", "KeepExistingSetting": "false"})
        self.assertEqual(patch["headers"], {"If-Match": '"fixture-etag"'})
        self.assertEqual([event for event, _ in self.journal], ["planned", "accepted", "verified"])
        self.assertTrue(result["legacyCustomDomainRetired"])
        self.assertTrue(result["mutationApplied"])
        self.assertFalse(result["runtimeVerified"])
        self.assertEqual(result["apimGatewayHost"], self.other["hostName"])
        self.assertEqual(transport.responses, [])

    def test_fresh_and_reup_never_create_domain_or_touch_named_values(self):
        self.outputs["ingressSecrets"].pop("certificateSecretUri")
        self.outputs["cleanup"]["serviceResourceIds"].pop("keyVault")
        transport = Transport([ok(self.resource()), ok(self.resource())])
        result = self.configure_endpoint(self.config, self.outputs, transport)
        self.assertEqual([call["method"] for call in transport.calls], ["GET", "GET"])
        self.assertEqual(self.journal, [])
        self.assertFalse(result["mutationApplied"])
        self.assertTrue(result["legacyCustomDomainRetired"])
        self.assertFalse(any("namedValues" in call["path"] for call in transport.calls))
        self.assertEqual(transport.responses, [])

    def test_gateway_deployment_refreshes_only_owned_named_value_without_changing_domain(self):
        transport = Transport([ok(self.named()), ok(self.named())])
        result = grpc.configure_named_values(self.config, self.outputs, transport)
        self.assertTrue(result["l7IngressEnabled"])
        self.assertEqual([call["method"] for call in transport.calls], ["POST", "GET"])
        self.assertTrue(transport.calls[0]["path"].endswith("/namedValues/core-l7-ingress-secret/refreshSecret?api-version=2024-05-01"))
        self.assertNotIn("listSecrets", json.dumps(transport.calls))
        self.assertEqual(transport.responses, [])

    def test_mutation_requires_protected_journal_and_nonwildcard_etag(self):
        initial = self.resource([self.other, self.desired])
        transport = Transport([ok(initial)])
        with self.assertRaisesRegex(ValueError, "protected per-attempt journal"):
            grpc.configure_endpoint(self.config, self.outputs, transport)
        self.assertEqual([call["method"] for call in transport.calls], ["GET"])
        for etag in (None, "", "*", '"*"', 'W/"*"', '""', 'W/""', " * ",
                     "bad\nheader", '"first","second"', '"bad\x7fheader"', "a" * 513):
            candidate = copy.deepcopy(initial)
            candidate["etag"] = etag
            transport = Transport([ok(candidate)])
            with self.subTest(etag=etag), self.assertRaisesRegex(ValueError, "ETag"):
                self.configure_endpoint(self.config, self.outputs, transport)
            self.assertEqual([call["method"] for call in transport.calls], ["GET"])
        transport = Transport([ok(initial)])
        with self.assertRaisesRegex(OSError, "fixture journal failure"):
            grpc.configure_endpoint(self.config, self.outputs, transport,
                mutation_recorder=lambda event, value: (_ for _ in ()).throw(OSError("fixture journal failure")))
        self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_removing_only_custom_proxy_restores_native_default_tls_binding(self):
        native = {**self.other, "defaultSslBinding": False}
        transport = Transport([ok(self.resource([native, self.desired])), ok(code=202), ok(self.resource())])
        self.configure_endpoint(self.config, self.outputs, transport)
        patch = next(call for call in transport.calls if call["method"] == "PATCH")
        self.assertEqual(patch["body"]["properties"]["hostnameConfigurations"], [self.other])

    def test_retirement_uses_http_etag_when_json_version_is_unquoted_base64(self):
        initial = self.resource([self.other, self.desired])
        initial['etag'] = 'AAAAAQ=='
        transport = Transport([ok(initial, headers={'ETag': '"AAAAAQ=="'}), ok(code=202), ok(self.resource())])
        self.configure_endpoint(self.config, self.outputs, transport)
        patch = next(call for call in transport.calls if call['method'] == 'PATCH')
        self.assertEqual(patch['headers'], {'If-Match': '"AAAAAQ=="'})
        transport = Transport([ok(initial, headers={'ETag': '*'})])
        with self.assertRaisesRegex(ValueError, 'ETag'):
            self.configure_endpoint(self.config, self.outputs, transport)
        self.assertEqual([call['method'] for call in transport.calls], ['GET'])

    def test_native_endpoint_and_response_identity_are_required_before_mutation(self):
        for field, value in (("id", self.apim_id + "-other"), ("name", "other"), ("type", "other"),
                             ("gatewayUrl", "https://other.example.invalid"), ("virtualNetworkType", "External")):
            initial = self.resource([self.other, self.desired])
            target = initial["properties"] if field in ("gatewayUrl", "virtualNetworkType") else initial
            target[field] = value
            transport = Transport([ok(initial)])
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.configure_endpoint(self.config, self.outputs, transport)
            self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_nonowned_domain_drift_and_patch_conflict_never_report_success(self):
        foreign = {**self.desired, "hostName": "maintenance.example.com", "defaultSslBinding": False}
        initial = self.resource([self.other, foreign, self.desired])
        transport = Transport([ok(initial), ok(code=412)])
        with self.assertRaises(ValueError):
            self.configure_endpoint(self.config, self.outputs, transport)
        self.assertEqual([call["method"] for call in transport.calls], ["GET", "PATCH"])
        final = self.resource([self.other, {**foreign, "negotiateClientCertificate": True}])
        transport = Transport([ok(initial), ok(code=202), ok(final)])
        with self.assertRaisesRegex(ValueError, "still pending"):
            self.configure_endpoint(self.config, self.outputs, transport, timeout_seconds=0)
        self.assertEqual([call["method"] for call in transport.calls], ["GET", "PATCH", "GET"])

    def test_resume_waits_for_accepted_retirement_without_repatch(self):
        transport = Transport([ok(self.resource([self.other, self.desired], provisioningState="Updating")),
            ok(self.resource(provisioningState="Activating")), ok(self.resource()), ok(self.resource())])
        slept = []
        result = self.configure_endpoint(self.config, self.outputs, transport, sleep=slept.append)
        self.assertEqual(slept, [10, 10])
        self.assertEqual([call["method"] for call in transport.calls], ["GET"] * 4)
        self.assertFalse(result["mutationApplied"])
        self.assertEqual(transport.responses, [])

    def test_completion_waits_for_retirement_readback_before_success(self):
        transport = Transport([ok(self.resource([self.other, self.desired])), ok(),
            ok(self.resource([self.other, self.desired], provisioningState="Updating")), ok(self.resource())])
        slept = []
        self.configure_endpoint(self.config, self.outputs, transport, sleep=slept.append)
        self.assertEqual(slept, [10])
        patches = [call for call in transport.calls if call["method"] == "PATCH"]
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0]["body"]["properties"]["hostnameConfigurations"], [self.other])
        self.assertEqual(transport.responses, [])

    def test_pending_timeout_and_failed_or_unknown_service_never_request_patch(self):
        """期限切れ・失敗・削除中・未知状態で再 PATCH や Secret 更新へ進まない。"""
        for state in ("Updating", "Failed", "Deleting", "Unknown", ""):
            with self.subTest(state=state):
                transport = Transport([ok(self.resource(provisioningState=state))])
                with self.assertRaises(ValueError) as error:
                    grpc.configure_endpoint(self.config, self.outputs, transport, sleep=lambda _: None, timeout_seconds=0)
                self.assertEqual([call["method"] for call in transport.calls], ["GET"])
                self.assertNotIn("fixture-etag", str(error.exception))

    def test_final_readback_timeout_does_not_repeat_accepted_patch(self):
        transport = Transport([ok(self.resource([self.other, self.desired])), ok(), ok(self.resource(provisioningState="Updating"))])
        with self.assertRaisesRegex(ValueError, "still pending"):
            self.configure_endpoint(self.config, self.outputs, transport, sleep=lambda _: None, timeout_seconds=0)
        self.assertEqual([call["method"] for call in transport.calls], ["GET", "PATCH", "GET"])
        self.assertEqual([event for event, _ in self.journal], ["planned", "accepted"])

    def test_native_endpoint_http2_is_disabled_without_creating_domain(self):
        initial = self.resource(customProperties={grpc.HTTP2: "true", "KeepExistingSetting": "false"})
        transport = Transport([ok(initial), ok(code=202), ok(self.resource())])
        self.configure_endpoint(self.config, self.outputs, transport)
        patch = next(call for call in transport.calls if call["method"] == "PATCH")
        self.assertEqual(patch["body"]["properties"]["customProperties"], {grpc.HTTP2: "false", "KeepExistingSetting": "false"})
        self.assertEqual(patch["body"]["properties"]["hostnameConfigurations"], [self.other])

    def test_owned_legacy_user_assigned_certificate_binding_is_removed(self):
        old_domain = {**self.desired, "identityClientId": self.client_id}
        transport = Transport([ok(self.resource([self.other, old_domain])), ok(), ok(self.resource())])
        self.configure_endpoint(self.config, self.outputs, transport)
        domains = transport.calls[1]["body"]["properties"]["hostnameConfigurations"]
        self.assertEqual(domains, [self.other])

    def test_null_system_identity_readback_is_accepted_but_user_assigned_named_value_is_rejected(self):
        """API が null/省略で表す SAMI は同一視し、成功済み UAMI は許可しない。"""
        for identity in (None, self.client_id):
            named = self.named()
            named["properties"]["keyVault"]["identityClientId"] = identity
            transport = Transport([ok(named), ok(named)])
            with self.subTest(identity=identity):
                if identity is None:
                    grpc.configure_named_values(self.config, self.outputs, transport)
                else:
                    with self.assertRaisesRegex(ValueError, "refresh was not confirmed"):
                        grpc.configure_named_values(self.config, self.outputs, transport)

    def test_realtime_refreshes_both_keys_without_custom_domain_or_http2_patch(self):
        self.config["apiDefinitions"] = [{"authorization": {"operationConstraints": {"stop": {"kind": "realtimeHangup"}}}}]
        transport = Transport([ok(self.named()), ok(self.named()),
                               ok(self.named(grpc.TICKET_NAME)), ok(self.named(grpc.TICKET_NAME))])
        result = grpc.configure_named_values(self.config, self.outputs, transport)
        self.assertTrue(result["l7IngressEnabled"])
        self.assertTrue(result["realtimeEnabled"])
        self.assertEqual([call["method"] for call in transport.calls], ["POST", "GET", "POST", "GET"])
        self.assertIn("/namedValues/core-l7-ingress-secret/", transport.calls[0]["path"])
        self.assertIn("/namedValues/core-realtime-ticket-key/", transport.calls[2]["path"])

    def test_foreign_outputs_and_unowned_certificate_are_not_removed(self):
        for change in ("service", "vault", "cert", "version", "host", "native"):
            with self.subTest(change=change):
                outputs = copy.deepcopy(self.outputs)
                if change == "service": outputs["ingressSecrets"]["apimId"] += "-foreign"
                elif change == "vault": outputs["cleanup"]["serviceResourceIds"]["keyVault"] = self.vault_id.replace("rg-test-fixture-svc", "rg-other-svc")
                elif change == "cert": outputs["ingressSecrets"]["certificateSecretUri"] = "https://other.vault.azure.net/secrets/gateway-tls"
                elif change == "version": outputs["ingressSecrets"]["certificateSecretUri"] += "/version"
                elif change == "native": outputs["ingressSecrets"]["apimGatewayHost"] = "other.example.invalid"
                else: outputs["ingressSecrets"]["publicHostName"] = "other.example.com"
                transport = Transport([ok(self.resource([self.other, self.desired]))])
                with self.assertRaises(ValueError): self.configure_endpoint(self.config, outputs, transport)
                self.assertTrue(all(call["method"] == "GET" for call in transport.calls))
        for uri in ("https://other.vault.azure.net/secrets/other", self.secret_uri + "/version", None):
            foreign = {**self.desired, "keyVaultId": uri}
            transport = Transport([ok(self.resource([self.other, foreign]))])
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                self.configure_endpoint(self.config, self.outputs, transport)
            self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_async_redirect_other_host_or_service_never_followed(self):
        for destination in ("https://evil.example.com/operation", "https://management.azure.com" + self.apim_id.replace("apim-test-fixture-abc123", "apim-other-fixture") + "/operationresults/x"):
            transport = Transport([])
            with self.assertRaisesRegex(ValueError, "outside this service"):
                grpc._poll_operation(ok(code=202, headers={"Location": destination}), self.apim_id, transport,
                    sleep=lambda _: None, deadline=time.monotonic() + 1)
            self.assertEqual(transport.calls, [])

    def test_patch_ignores_completion_links_and_waits_for_owned_retirement(self):
        target = "https://evil.example.com/PRIVATE_FIXTURE"
        initial = self.resource([self.other, self.desired])
        transport = Transport([ok(initial), ok(code=202, headers={"Location": target, "Azure-AsyncOperation": target}),
            ok(initial), ok(self.resource(provisioningState="Updating")), ok(self.resource())])
        slept = []
        self.configure_endpoint(self.config, self.outputs, transport, sleep=slept.append)
        self.assertEqual([call["path"] for call in transport.calls[2:5]], [self.apim_id + "?api-version=" + grpc.API] * 3)
        self.assertEqual(slept, [10, 10])
        self.assertEqual([call["method"] for call in transport.calls].count("PATCH"), 1)
        self.assertNotIn(target, [call["path"] for call in transport.calls])
        self.assertEqual(transport.responses, [])

    def test_stale_succeeded_until_deadline_is_not_success_and_never_repatched(self):
        initial = self.resource([self.other, self.desired])
        for state in ("Succeeded", "Failed", "Deleting"):
            transport = Transport([ok(initial), ok(code=202), ok(self.resource([self.other, self.desired], provisioningState=state))])
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.configure_endpoint(self.config, self.outputs, transport, sleep=lambda _: None, timeout_seconds=0)
            self.assertEqual([call["method"] for call in transport.calls], ["GET", "PATCH", "GET"])

    def test_refresh_accepts_tenant_operation_and_only_its_own_location_fallback(self):
        """Named Value の二つの正式形式を保ち、別 Secret の読込先は拒否する。"""
        named_path = self.apim_id + "/namedValues/" + grpc.SECRET_NAME + "/refreshSecret"
        tenant = "https://management.azure.com" + self.apim_id + "/tenant/operationResults/fixture==?api-version=" + grpc.API
        fallback = "https://management.azure.com" + named_path + "?api-version=" + grpc.API + "&asyncId=fixture&asyncCode=201"
        azure_async = "https://management.azure.com" + named_path + "?api-version=" + grpc.API + "&azure-asyncId=a1b2c3d4e5f6a1b2c3d4e5f6"
        for target, header in ((tenant, "Azure-AsyncOperation"), (fallback, "Location"),
                               (fallback.replace("asyncCode=201", "asyncCode=200"), "Location"),
                               (azure_async, "Azure-AsyncOperation")):
            with self.subTest(header=header, target=target):
                transport = Transport([ok(code=202, headers={header: target}), ok(), ok(self.named())])
                grpc._refresh_secret(self.apim_id, grpc.SECRET_NAME, self.vault, transport,
                    sleep=lambda _: None, deadline=time.monotonic() + 10, location=self.config["location"])
                self.assertEqual(transport.calls[1]["path"], target)
                self.assertEqual(transport.responses, [])
        for target in (fallback.replace(grpc.SECRET_NAME, grpc.TICKET_NAME),
                       azure_async.replace(grpc.SECRET_NAME, grpc.TICKET_NAME),
                       fallback + "&sig=PRIVATE_FIXTURE", azure_async + "&asyncId=fixture",
                       fallback.replace("asyncCode=201", "asyncCode=202"), azure_async + "&access_token=PRIVATE_FIXTURE"):
            transport = Transport([ok(code=202, headers={"Azure-AsyncOperation": target})])
            with self.subTest(rejected=target), self.assertRaisesRegex(ValueError, "outside this service") as error:
                grpc._refresh_secret(self.apim_id, grpc.SECRET_NAME, self.vault, transport,
                    sleep=lambda _: None, deadline=time.monotonic() + 10, location=self.config["location"])
            self.assertNotIn("PRIVATE_FIXTURE", str(error.exception))
            self.assertEqual([call["method"] for call in transport.calls], ["POST"])

    def test_completion_rejects_foreign_scope_arbitrary_arm_paths_and_credentials_before_get(self):
        """完了 URL でも所有範囲・正式パス・固定クエリーを満たさなければ一度も送信しない。"""
        regional = ("https://management.azure.com/subscriptions/" + self.config["subscriptionId"]
                    + "/providers/Microsoft.ApiManagement/locations/japaneast/operationResults/fixture?api-version=" + grpc.API)
        service = "https://management.azure.com" + self.apim_id
        invalid = [regional.replace(self.config["subscriptionId"], self.config["azureTenantId"]),
            regional.replace("japaneast", "westus2"), regional.replace("operationResults", "unrelated"),
            regional.replace("fixture?", "../?"), regional.replace("fixture?", "%2e%2e?"),
            regional.replace("fixture?", "nested%2Ffixture?"), regional.replace("fixture?", "fixture/extra?"),
            regional.replace("https:", "http:"), regional.replace("management.azure.com", "management.azure.com.evil.example"),
            regional.replace("management.azure.com", "user:PRIVATE_FIXTURE@management.azure.com"),
            regional.replace("management.azure.com", "management.azure.com:444"), regional + "#PRIVATE_FIXTURE",
            regional + "&access_token=PRIVATE_FIXTURE", regional + "&api-version=" + grpc.API,
            regional.replace(grpc.API, "2022-08-01"), regional + "&asyncResponse=PRIVATE_FIXTURE",
            regional.replace("fixture", "fixture\n"), service + "/namedValues/unrelated?api-version=" + grpc.API,
            service + "/operationresults/fixture?api-version=" + grpc.API + "&sig=PRIVATE_FIXTURE"]
        for target in invalid:
            with self.subTest(target=target):
                transport = Transport([])
                with self.assertRaisesRegex(ValueError, "outside this service") as error:
                    grpc._poll_operation(ok(code=202, headers={"Location": target}), self.apim_id, transport,
                        sleep=lambda _: None, deadline=time.monotonic() + 10, location=self.config["location"])
                self.assertNotIn("PRIVATE_FIXTURE", str(error.exception))
                self.assertEqual(transport.calls, [])

    def test_service_completion_flag_retained_and_regional_requires_configured_region(self):
        """公式 Service Update の opaque ID と asyncResponse を許すが、地域を URL から推定しない。"""
        service = "https://management.azure.com" + self.apim_id + "/operationresults/fixture==?api-version=" + grpc.API + "&asyncResponse"
        transport = Transport([ok({"status": "Succeeded"})])
        grpc._poll_operation(ok(code=202, headers={"Azure-AsyncOperation": service}), self.apim_id, transport,
            sleep=lambda _: None, deadline=time.monotonic() + 10)
        self.assertEqual(transport.calls[0]["path"], service)
        regional = ("https://management.azure.com/subscriptions/" + self.config["subscriptionId"]
                    + "/providers/Microsoft.ApiManagement/locations/japaneast/operationResults/fixture?api-version=" + grpc.API)
        transport = Transport([])
        with self.assertRaisesRegex(ValueError, "outside this service"):
            grpc._poll_operation(ok(code=202, headers={"Location": regional}), self.apim_id, transport,
                sleep=lambda _: None, deadline=time.monotonic() + 10)
        self.assertEqual(transport.calls, [])

    def test_refresh_failed_or_unconfirmed_does_not_report_configured(self):
        for response in (ok(code=403, body={"error": {"message": "PRIVATE_FIXTURE"}}), ok(self.named(status="AccessDenied"))):
            transport = Transport([response, ok(self.named(status="AccessDenied"))])
            with self.assertRaises(ValueError) as error:
                grpc.configure_named_values(self.config, self.outputs, transport)
            self.assertNotIn("PRIVATE_FIXTURE", str(error.exception))

    def test_pending_custom_domain_removes_arm_secret_inputs_but_retains_cleanup_state(self):
        config = {**self.config, "location": "japaneast", "foundryLocation": "eastus2"}
        outputs = {**self.outputs, "resourceGroups": config["resourceGroupNames"]}
        workspace = lifecycle.local_paths(config, self.root)
        parameter_file, state_file = workspace.parameters, workspace.state / "latest.json"
        lifecycle.protected_json(parameter_file, {"l7IngressSecret": "PRIVATE_FIXTURE"})
        response = {"properties": {"outputs": {key: {"value": value} for key, value in outputs.items()}}}
        with mock.patch.object(lifecycle, "run", side_effect=[{"CORE_DEPLOYMENT_NAME": "owned"}, response]), \
             mock.patch.object(lifecycle, "ROOT", self.root), \
             mock.patch.object(lifecycle, "ArmTransport"), \
             mock.patch.object(lifecycle, "complete_core_network_upgrade"), \
             mock.patch.object(lifecycle, "configure_grpc_endpoint", side_effect=ValueError("Still pending")):
            with self.assertRaisesRegex(ValueError, "Still pending"):
                lifecycle.postprovision(config)
        self.assertFalse(parameter_file.exists())
        state = state_file.read_text()
        self.assertIn(self.apim_id, state)
        self.assertNotIn("PRIVATE_FIXTURE", state)


class GrpcInfrastructureSourceTests(unittest.TestCase):
    """gRPCの証明書設定・HTTP/2・入口と鍵の管理を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def test_grpc_assets_are_absent_and_https_maintenance_rules_remain(self):
        core = Path(__file__).resolve().parents[1]
        network = (core / "infra/modules/network-bindings.bicep").read_text()
        for name in ("allow-appgw-grpc-ingress", "allow-apim-grpc-egress", "grpcEnabled", "grpcBackendPort"):
            self.assertNotIn(name, network)
        self.assertIn("allow-mgmt-vm-https-ingress", network)
        self.assertIn("allow-apim-https-egress", network)
        self.assertIn("destinationPorts: ['80']", network)

    def test_secure_inputs_remain_private_and_normal_http_ingress_is_permanent(self):
        core = Path(__file__).resolve().parents[1]
        source = (core / "infra/main.bicep").read_text()
        self.assertIn("@secure()\nparam l7IngressSecret", source)
        self.assertIn("@secure()\nparam realtimeTicketKey", source)
        self.assertNotIn("output grpc ", source)
        self.assertIn("l7IngressEnabled: true", source)
        services = (core / "infra/modules/services.bicep").read_text()
        self.assertIn("'Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2': 'false'", services)
        gateway = (core / "infra/modules/application-gateway.bicep").read_text()
        self.assertIn("headerName: 'X-Core-L7-Ingress', headerValue: l7IngressSecret", gateway)
        self.assertNotIn("listener-grpc-tcp", gateway)
        self.assertIn("enableHttp2: true", gateway)

    def test_vm_secret_functions_do_not_rotate_on_reapply_or_certificate_renewal(self):
        core = Path(__file__).resolve().parents[1]
        runtime = os.environ.get("POWERSHELL_RUNTIME") or shutil.which("pwsh")
        if not runtime:
            candidates = sorted((core / ".tools/powershell").glob("*/pwsh"))
            runtime = str(candidates[-1]) if candidates else None
        if not runtime:
            self.skipTest("PowerShell runtime unavailable")
        # Import only functions through the parser AST. The script entry point,
        # IMDS, certificate issuer and Azure endpoints can never run here.
        script = r'''
param([string]$Source)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if ($errors.Count) {throw 'Guest script parse errors'}
foreach ($fn in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
 . ([scriptblock]::Create($fn.Extent.Text))
}
$script:Stored=@{}; $script:Writes=0; $script:Reads=0
function Get-VaultSecret([string]$Name) {$script:Reads++; return $script:Stored[$Name]}
function Set-VaultSecret([string]$Name,[string]$Value) {
 $script:Writes++; $script:Stored[$Name]=[pscustomobject]@{value=$Value;attributes=[pscustomobject]@{enabled=$true}}
 return $script:Stored[$Name]
}
$script:Config=[pscustomobject]@{l7IngressEnabled=$true;realtimeTicketEnabled=$true}
$Action='Install'; $L7IngressSecret=('a'*128); $RealtimeTicketKey=('b'*128)
Set-L7IngressProof; Set-RealtimeTicketKey
if ($script:Writes -ne 2 -or $script:Stored['core-l7-ingress-secret'].value -ne $L7IngressSecret -or
    $script:Stored['core-realtime-ticket-key'].value -ne $RealtimeTicketKey) {throw 'Separate credentials not stored'}
Set-L7IngressProof; Set-RealtimeTicketKey
if ($script:Writes -ne 2) {throw 'Reapply rotated a credential'}
$beforeReads=$script:Reads; $beforeWrites=$script:Writes
$Action='Renew'; $L7IngressSecret='PRIVATE_FIXTURE_VALUE'; $RealtimeTicketKey='PRIVATE_FIXTURE_VALUE'
Set-L7IngressProof; Set-RealtimeTicketKey
if ($script:Reads -ne $beforeReads -or $script:Writes -ne $beforeWrites) {throw 'Renewal accessed input credentials'}
$Action='Install'; $script:Config.l7IngressEnabled=$false; $script:Config.realtimeTicketEnabled=$false
Set-L7IngressProof; Set-RealtimeTicketKey; Ensure-ApimCertificateChain
if ($script:Reads -ne $beforeReads -or $script:Writes -ne $beforeWrites) {throw 'Disabled features accessed credentials'}
$script:Config.l7IngressEnabled=$true; $script:Config.realtimeTicketEnabled=$true
foreach ($fn in @('Set-L7IngressProof','Set-RealtimeTicketKey')) {
 $failed=$false; try {& $fn} catch {$failed=$true; if ($_.Exception.Message -match 'PRIVATE_FIXTURE_VALUE') {throw 'Private input was echoed'}}
 if (-not $failed) {throw 'Invalid input accepted'}
}
if ($script:Reads -ne $beforeReads -or $script:Writes -ne $beforeWrites) {throw 'Invalid input reached Key Vault'}
'Guest ingress credential fixtures PASS; no Azure, DNS or CA requests.'
'''
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "guest-fixture.ps1"
            fixture.write_text(script, encoding="utf-8")
            result = subprocess.run([runtime, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(fixture),
                                     "-Source", str(core / "guest/certificate.ps1")],
                                    capture_output=True, text=True, timeout=30, check=False,
                                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fixtures PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
