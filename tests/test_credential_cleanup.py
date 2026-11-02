"""モデル→APIM→RGの撤去順と、保護対象を残す動作を検証する。

偽のARM応答で削除・一覧の全ページ・404・WORM保持・Key Vault保護・完全削除の対象IDを再現する。成功を装わず、処理を止める必要があるケースも確認する。
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import cleanup
from deployment.helpers.configuration import load_configuration


from cleanup_fixtures import (
    CleanupFixture, FakeTransport, ApiCenterListTransport, ModelTransport,
    CredentialTransport, ProviderGetInconsistencyTransport, InventoryTransport, ManagementTransport,
)


class CredentialCleanupTests(CleanupFixture, unittest.TestCase):
    def test_api_center_404_permission_and_provider_errors_do_not_block_other_purges(self):
        collection = self.svc_rg + "/providers/Microsoft.ApiCenter/deletedServices"
        for status in (404, 403, 502):
            with self.subTest(status=status):
                transport = FakeTransport(self.tombstones())
                transport.forced["GET", collection] = status
                result = self.post(transport)
                self.assertEqual(result["status"], "complete-with-unverified-api-center-retention")
                self.assertEqual(result["apiCenterRetention"]["status"], "unverified")
                self.assertFalse(result["apiCenterRetention"]["listReadVerified"])
                if status == 404:
                    self.assertEqual(result["apiCenterRetention"]["httpStatus"], 404)
                self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))
                self.assertEqual(len(transport.mutations()), 3)
                self.assertEqual(transport.calls[-1]["id"], collection)
                self.assertTrue(all(item["status"] == "absent" for item in result["softDeletedServices"]))

    def test_model_then_cm_policies_connections_provider_then_apim_deletion_and_receipt(self):
        """モデル→接続権限→本人接続→providerの不在確認後にAPIMを削除する。"""
        transport = self.credential_transport(models=2)
        apim_id = self.state["serviceResourceIds"]["apim"]
        provider_id = apim_id + "/authorizationProviders/approved-oauth"
        connection_id, policy_ids = self.credential_children(transport, provider_id)[0]
        policy_id = policy_ids[0]
        # 接続の非同期受付は、その接続の個別GET404まで確認する。
        # providerは公式仕様どおり同期DELETE200→GET404を返す。
        transport.delayed_reads[connection_id] = 1
        result = self.pre(transport, sleep=lambda _: None)
        self.assertNotIn(provider_id, transport.resources)
        self.assertNotIn(connection_id, transport.resources)
        self.assertNotIn(policy_id, transport.resources)
        cm_delete = next(i for i, call in enumerate(transport.calls) if call["method"] == "DELETE" and call["id"] == provider_id)
        apim_delete = next(i for i, call in enumerate(transport.calls) if call["method"] == "DELETE" and call["id"] == apim_id)
        last_models = max(i for i, call in enumerate(transport.calls) if call["id"] == transport.collection_id)
        self.assertLess(last_models, cm_delete)
        self.assertLess(cm_delete, apim_delete)
        self.assertEqual([call["id"] for call in transport.mutations()][2:],
                         [policy_id, connection_id, provider_id, apim_id])
        self.assertEqual(transport.calls[cm_delete]["headers"], {"If-Match": '"provider-version-1"'})
        self.assertEqual(sum(call["method"] == "GET" and call["id"] == provider_id for call in transport.calls), 3)
        cm = result["apiManagement"]["credentialProviders"][0]
        self.assertEqual((cm["resourceId"], cm["status"], cm["httpStatus"], cm["deletionVerified"]),
                         (provider_id, "absent", 404, True))
        self.assertTrue(cm["childrenDeletionVerified"])
        self.assertFalse(cm["internalAccountReleaseVerified"])
        self.assertEqual(cm["authorizations"][0]["resourceId"], connection_id)
        self.assertEqual(cm["authorizations"][0]["accessPolicies"][0]["resourceId"], policy_id)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))
        for resource_id in (policy_id, connection_id, provider_id):
            deletion = next(i for i, call in enumerate(transport.calls) if call["method"] == "DELETE" and call["id"] == resource_id)
            self.assertEqual(transport.calls[deletion]["headers"].keys(), {"If-Match"})
            self.assertTrue(any(call["method"] == "GET" and call["id"] == resource_id
                                for call in transport.calls[deletion + 1:apim_delete]))

    def test_cm_live_access_policy_type_slash_preserves_checked_deletion_order(self):
        """実 ARM の末尾 / を一覧・GET・再GETで再現し、子からの回収を検証する。"""
        transport = self.credential_transport()
        apim_id = self.state["serviceResourceIds"]["apim"]
        provider_id = apim_id + "/authorizationProviders/approved-oauth"
        connection_id, policy_ids = self.credential_children(transport, provider_id)[0]
        policy_id = policy_ids[0]
        transport.resources[policy_id]["type"] += "/"
        transport.resources[policy_id]["properties"]["appIds"] = []
        # 実サービスの ETag 未提供時も、同じ ID と公開識別項目を再確認する。
        transport.provider_headers[policy_id] = {}
        result = self.pre(transport)
        self.assertEqual([call["id"] for call in transport.mutations()],
                         [policy_id, connection_id, provider_id, apim_id])
        policy = result["apiManagement"]["credentialProviders"][0]["authorizations"][0]["accessPolicies"][0]
        self.assertTrue(policy["deletionVerified"])
        self.assertTrue(policy["identityRevalidated"])
        self.assertFalse(policy["versionPreconditionApplied"])
        self.assertEqual(policy["httpStatus"], 404)

    def test_cm_live_type_slash_never_allows_a_different_policy_identity(self):
        """表記ゆれを許しても、別 ID・名前・種類を持つ資源は削除しない。"""
        for mutation in ("id", "name", "type", "double-slash", "trailing-space"):
            with self.subTest(mutation=mutation):
                transport = self.credential_transport()
                provider_id = next(iter(transport.provider_headers))
                _, policy_ids = self.credential_children(transport, provider_id)[0]
                policy_id = policy_ids[0]
                child = transport.resources[policy_id]
                child["type"] += "/"
                if mutation == "id":
                    child["id"] = policy_id.replace("/approved-oauth/", "/other-oauth/")
                elif mutation == "name":
                    child["name"] += "-other"
                elif mutation == "type":
                    child["type"] = "Microsoft.KeyVault/vaults/"
                elif mutation == "double-slash":
                    child["type"] += "/"
                else:
                    child["type"] += " "
                self.assert_code("credential-child-owner-mismatch", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())

    def test_cm_authorization_type_does_not_inherit_access_policy_slash_exception(self):
        """既知の access policy 表記だけを許し、本人接続の種類には広げない。"""
        transport = self.credential_transport()
        provider_id = next(iter(transport.provider_headers))
        authorization_id, _ = self.credential_children(transport, provider_id)[0]
        transport.resources[authorization_id]["type"] += "/"
        self.assert_code("credential-child-owner-mismatch", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())

    def test_absent_provider_and_absent_parent_resume_without_false_cm_verification(self):
        transport = self.credential_transport()
        apim_id = self.state["serviceResourceIds"]["apim"]
        provider_id = apim_id + "/authorizationProviders/approved-oauth"
        transport.resources.pop(provider_id)
        result = self.pre(transport)
        self.assertEqual(result["apiManagement"]["credentialProviders"], [{"resourceId": provider_id,
            "status": "absent", "httpStatus": 404, "deletionVerified": True, "deleteRequested": False}])
        self.assertEqual([call["id"] for call in transport.mutations()], [apim_id])
        transport.calls.clear()
        again = self.pre(transport)
        self.assertEqual(again["apiManagement"]["credentialProviders"], [{"resourceId": provider_id,
            "status": "parent-absent", "deletionVerified": False, "parentDeletionVerified": True, "deleteRequested": False}])
        self.assertFalse(transport.mutations())
        self.assertFalse(any(call["id"] == provider_id for call in transport.calls))

    def test_provider_wrong_identity_type_name_or_etag_stops_before_cm_or_apim_delete(self):
        for field, suffix in (("id", "-foreign"), ("name", "-foreign"), ("type", "-foreign"), ("id", "/"), ("name", "/")):
            transport = self.credential_transport()
            provider_id = next(iter(transport.provider_headers))
            transport.resources[provider_id][field] += suffix
            with self.subTest(field=field, suffix=suffix):
                self.assert_code("credential-provider-owner-mismatch", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())
        for etag in (None, "", "*", '"*"', 'W/"*"', "unquoted", '"injected\nheader"', 123):
            transport = self.credential_transport()
            provider_id = next(iter(transport.provider_headers))
            transport.provider_headers[provider_id] = {"ETag": etag}
            with self.subTest(etag=etag):
                self.assert_code("credential-provider-etag-missing", lambda: self.pre(transport))
                self.assertFalse(transport.mutations())

    def test_missing_provider_etag_revalidates_exact_identity_and_confirms_404_before_apim(self):
        transport, provider_id = self.missing_etag_transport(models=2)
        result = self.pre(transport)
        deletion = next(i for i, c in enumerate(transport.calls) if c["method"] == "DELETE" and c["id"] == provider_id)
        apim_id = self.state["serviceResourceIds"]["apim"]
        apim_deletion = next(i for i, c in enumerate(transport.calls) if c["method"] == "DELETE" and c["id"] == apim_id)
        model_absence = max(i for i, c in enumerate(transport.calls) if c["id"] == transport.collection_id)
        self.assertEqual(transport.calls[deletion]["headers"], {"If-Match": "*"})
        self.assertEqual((transport.calls[deletion - 1]["method"], transport.calls[deletion - 1]["id"]), ("GET", provider_id))
        self.assertLess(model_absence, deletion)
        self.assertTrue(any(c["method"] == "GET" and c["id"] == provider_id for c in transport.calls[deletion + 1:apim_deletion]))
        self.assertNotIn(provider_id, transport.resources)
        cm = result["apiManagement"]["credentialProviders"][0]
        self.assertEqual((cm["identityRevalidated"], cm["versionPreconditionApplied"], cm["ifMatchMode"]),
                         (True, False, "documented-wildcard-missing-etag"))
        self.assertTrue(cm["deletionVerified"])
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))

    def test_missing_provider_etag_prefers_a_valid_etag_returned_by_fresh_get(self):
        transport, provider_id = self.missing_etag_transport()
        transport.provider_fresh_results[provider_id] = {"statusCode": 200,
            "body": copy.deepcopy(transport.resources[provider_id]), "headers": {"ETag": '"fresh-version"'}}
        result = self.pre(transport)
        deletion = next(c for c in transport.mutations() if c["id"] == provider_id)
        self.assertEqual(deletion["headers"], {"If-Match": '"fresh-version"'})
        cm = result["apiManagement"]["credentialProviders"][0]
        self.assertEqual((cm["versionPreconditionApplied"], cm["ifMatchMode"]), (True, "etag-after-refresh"))

    def test_missing_provider_etag_refuses_changed_or_unreadable_fresh_metadata(self):
        for mutation in ("id", "name", "type", "identityProvider", "clientId", "tenantId", "scopes", "redirectUrl", "missing-field", "malformed-field", "malformed-structure", "403"):
            transport, provider_id = self.missing_etag_transport()
            fresh = {"statusCode": 200, "body": copy.deepcopy(transport.resources[provider_id]), "headers": {}}
            props = fresh["body"]["properties"]
            expected = "credential-provider-metadata-changed"
            if mutation in ("id", "name", "type"):
                fresh["body"][mutation] += "-foreign"
                expected = "credential-provider-owner-mismatch"
            elif mutation == "identityProvider": props["identityProvider"] = "oauth2"
            elif mutation in ("clientId", "tenantId", "scopes"):
                props["oauth2"]["grantTypes"]["authorizationCode"][mutation] = "changed-public-field"
            elif mutation == "redirectUrl": props["oauth2"]["redirectUrl"] = "https://different.example/callback"
            elif mutation == "missing-field": props.pop("identityProvider")
            elif mutation == "malformed-field":
                props["identityProvider"] = []
                expected = "credential-provider-metadata-invalid"
            elif mutation == "malformed-structure":
                props["oauth2"] = []
                expected = "credential-provider-metadata-invalid"
            elif mutation == "403":
                fresh = {"statusCode": 403, "body": {"error": {"message": "PRIVATE_SENTINEL"}}, "headers": {}}
                expected = "azure-request-failed"
            transport.provider_fresh_results[provider_id] = fresh
            with self.subTest(mutation=mutation):
                self.assert_code(expected, lambda: self.pre(transport))
                self.assertFalse(transport.mutations())

    def test_missing_provider_etag_fresh_404_is_idempotent_without_provider_delete(self):
        transport, provider_id = self.missing_etag_transport()
        transport.provider_fresh_results[provider_id] = {"statusCode": 404, "body": {}, "headers": {}}
        result = self.pre(transport)
        self.assertEqual([c["id"] for c in transport.mutations()], [self.state["serviceResourceIds"]["apim"]])
        cm = result["apiManagement"]["credentialProviders"][0]
        self.assertEqual((cm["resourceId"], cm["status"], cm["deletionVerified"], cm["deleteRequested"]),
                         (provider_id, "absent", True, False))

    def test_provider_duplicate_or_fresh_invalid_etags_never_fall_back_to_wildcard(self):
        for invalid_headers in ({"ETag": '"one"', "etag": '"two"'}, {"ETag": ""}, {"ETag": None}, {"ETag": "*"}):
            for fresh_only in (False, True):
                transport, provider_id = self.missing_etag_transport()
                if fresh_only:
                    transport.provider_fresh_results[provider_id] = {"statusCode": 200,
                        "body": copy.deepcopy(transport.resources[provider_id]), "headers": invalid_headers}
                else:
                    transport.provider_headers[provider_id] = invalid_headers
                with self.subTest(headers=invalid_headers, fresh=fresh_only):
                    self.assert_code("credential-provider-etag-missing", lambda: self.pre(transport))
                    self.assertFalse(transport.mutations())

    def test_missing_provider_etag_does_not_compare_or_report_private_fields(self):
        transport, provider_id = self.missing_etag_transport()
        fresh = copy.deepcopy(transport.resources[provider_id])
        fresh["properties"]["oauth2"]["grantTypes"]["authorizationCode"]["clientSecret"] = "DIFFERENT_PRIVATE_SENTINEL"
        transport.provider_fresh_results[provider_id] = {"statusCode": 200, "body": fresh, "headers": {}}
        result = self.pre(transport)
        self.assertTrue(result["apiManagement"]["credentialProviders"][0]["deletionVerified"])
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))

    def test_provider_read_and_delete_failures_never_delete_apim_or_log_error_body(self):
        for method, status in (("GET", 403), ("GET", 429), ("GET", 500), ("DELETE", 403), ("DELETE", 409), ("DELETE", 412)):
            transport = self.credential_transport()
            provider_id = next(iter(transport.provider_headers))
            transport.forced[method, provider_id] = status
            with self.subTest(method=method, status=status):
                self.assert_code("azure-request-failed", lambda: self.pre(transport))
                self.assertIn(self.state["serviceResourceIds"]["apim"], transport.resources)
                self.assertFalse(any(call["method"] == "DELETE" and call["id"] == self.state["serviceResourceIds"]["apim"]
                                     for call in transport.calls))

    def test_all_configured_providers_are_validated_before_first_provider_delete(self):
        transport = self.credential_transport(names=("first-provider", "second-provider"))
        second = list(transport.provider_headers)[1]
        transport.provider_headers[second] = {"ETag": ""}
        self.assert_code("credential-provider-etag-missing", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())
        self.assertTrue(all(provider_id in transport.resources for provider_id in transport.provider_headers))

    def test_provider_pending_delete_keeps_parent_and_stops_native_handoff(self):
        transport = self.credential_transport()
        provider_id = next(iter(transport.provider_headers))
        transport.keep_after_delete.add(provider_id)
        self.assert_code("cleanup-pending", lambda: self.pre(transport, sleep=lambda _: None))
        self.assertIn(self.state["serviceResourceIds"]["apim"], transport.resources)
        self.assertEqual([call["id"] for call in transport.mutations()], [provider_id])

    def test_sync_provider_delete_list_absence_defers_confirmation_to_exact_parent_404(self):
        """list0＋旧GET200はprovider404にせず、所有親404を別の証明として残す。"""
        for status in (200, 204):
            with self.subTest(status=status):
                transport, provider_id = self.provider_get_inconsistency_transport(status=status)
                result = self.pre(transport)
                apim_id = self.state["serviceResourceIds"]["apim"]
                provider = result["apiManagement"]["credentialProviders"][0]
                self.assertFalse(provider["deletionVerified"])
                self.assertEqual(provider["individualGetStatus"], 200)
                self.assertEqual(provider["httpStatus"], 200)
                self.assertEqual(provider["deleteHttpStatus"], status)
                self.assertTrue(provider["listAbsenceVerified"])
                self.assertTrue(provider["identityRevalidatedAfterDelete"])
                self.assertTrue(provider["parentDeletionRequired"])
                self.assertTrue(provider["parentDeletionVerified"])
                self.assertFalse(provider["internalAccountReleaseVerified"])
                self.assertTrue(result["apiManagement"]["apimDeletionVerified"])
                self.assertTrue(provider["authorizations"][0]["deletionVerified"])
                self.assertTrue(provider["authorizations"][0]["accessPolicies"][0]["deletionVerified"])
                # 個別GETが404になるようにmockを都合良く書き換えていない。
                self.assertIn(provider_id, transport.deleted_provider_gets)
                self.assertNotIn(provider_id, transport.resources)
                self.assertNotIn(apim_id, transport.resources)
                listing = apim_id + "/authorizationProviders"
                listed_index = next(i for i, c in enumerate(transport.calls) if c["id"] == listing)
                parent_delete = next(i for i, c in enumerate(transport.calls)
                                     if c["id"] == apim_id and c["method"] == "DELETE")
                self.assertLess(listed_index, parent_delete)
                after_parent_delete = transport.calls[parent_delete + 1:]
                self.assertTrue(any(c["id"] == apim_id and c["method"] == "GET" for c in after_parent_delete))
                self.assertFalse(any(c["id"] == provider_id for c in after_parent_delete))
                self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))

    def test_provider_list_absence_is_only_intermediate_until_parent_deleted(self):
        transport, provider_id = self.provider_get_inconsistency_transport(children=False)
        result = cleanup._delete_credential_providers([provider_id], transport,
            max_checks=2, wait_seconds=0, sleep=lambda _: None)[0]
        self.assertFalse(result["deletionVerified"])
        self.assertEqual(result["individualGetStatus"], 200)
        self.assertTrue(result["listAbsenceVerified"])
        self.assertTrue(result["parentDeletionRequired"])
        self.assertNotIn("parentDeletionVerified", result)
        self.assertIn(self.state["serviceResourceIds"]["apim"], transport.resources)
        self.assertEqual([c["id"] for c in transport.mutations()], [provider_id])

    def test_provider_fallback_follows_all_valid_pages_without_deleting_other_providers(self):
        transport, provider_id = self.provider_get_inconsistency_transport(children=False)
        collection = provider_id.rsplit("/", 1)[0]
        unrelated = copy.deepcopy(transport.resources[provider_id])
        unrelated.update(id=collection + "/another-provider", name="another-provider")
        transport.resources[unrelated["id"]] = unrelated
        transport.cm_collection_pages[collection] = [
            {"statusCode": 200, "body": {"value": [unrelated], "nextLink":
                "https://management.azure.com" + collection + "?api-version=2024-05-01&$skiptoken=1"}, "headers": {}},
            {"statusCode": 200, "body": {"value": [], "nextLink": ""}, "headers": {}},
        ]
        result = self.pre(transport)
        self.assertTrue(result["apiManagement"]["credentialProviders"][0]["parentDeletionVerified"])
        self.assertEqual(sum(c["id"] == collection for c in transport.calls), 2)
        self.assertNotIn(unrelated["id"], [c["id"] for c in transport.mutations()])
        self.assertFalse(any(c["id"] == unrelated["id"] for c in transport.calls))

    def test_provider_target_in_later_collection_page_stops_parent_delete(self):
        transport, provider_id = self.provider_get_inconsistency_transport(children=False)
        collection = provider_id.rsplit("/", 1)[0]
        target = copy.deepcopy(transport.resources[provider_id])
        transport.cm_collection_pages[collection] = [
            {"statusCode": 200, "body": {"value": [], "nextLink": collection +
                "?api-version=2024-05-01&$skiptoken=1"}, "headers": {}},
            {"statusCode": 200, "body": {"value": [target], "nextLink": ""}, "headers": {}},
        ]
        self.assert_code("cleanup-pending", lambda: self.pre(transport))
        self.assertEqual([c["id"] for c in transport.mutations()], [provider_id])

    def test_provider_list_accepts_azure_camel_case_skip_token_and_checks_later_page(self):
        transport, provider_id = self.provider_get_inconsistency_transport(children=False)
        collection = provider_id.rsplit("/", 1)[0]
        unrelated = copy.deepcopy(transport.resources[provider_id])
        unrelated.update(id=collection + "/another-provider", name="another-provider")
        target = copy.deepcopy(transport.resources[provider_id])
        transport.cm_collection_pages[collection] = [
            {"statusCode": 200, "body": {"value": [unrelated], "nextLink":
                "https://management.azure.com" + collection + "?api-version=2024-05-01&$skipToken=opaque"}, "headers": {}},
            {"statusCode": 200, "body": {"value": [target], "nextLink": ""}, "headers": {}},
        ]
        self.assert_code("cleanup-pending", lambda: self.pre(transport))
        self.assertEqual(sum(c["id"] == collection for c in transport.calls), 2)
        self.assertEqual([c["id"] for c in transport.mutations()], [provider_id])

    def test_provider_list_unknown_or_malformed_never_becomes_absence(self):
        for scenario in ("404", "403", "202", "missing-value", "bad-value", "bad-next", "late-403"):
            with self.subTest(scenario=scenario):
                transport, provider_id = self.provider_get_inconsistency_transport(children=False)
                collection = provider_id.rsplit("/", 1)[0]
                body = {"value": []}
                status = int(scenario) if scenario.isdigit() else 200
                if scenario == "missing-value": body = {}
                if scenario == "bad-value": body["value"] = {}
                if scenario == "bad-next": body["nextLink"] = " "
                if scenario == "late-403": body["nextLink"] = collection + "?api-version=2024-05-01&$skiptoken=1"
                transport.cm_collection_pages[collection] = [{"statusCode": status, "body": body, "headers": {}}]
                if scenario == "late-403":
                    transport.cm_collection_pages[collection].append({"statusCode": 403, "body": {}, "headers": {}})
                expected = "azure-request-failed" if scenario in ("403", "late-403") else "read-incomplete" if scenario == "202" else "credential-child-list-unverified"
                self.assert_code(expected, lambda: self.pre(transport))
                self.assertEqual([c["id"] for c in transport.mutations()], [provider_id])

    def test_provider_list_scope_type_duplicate_and_pagination_guards_are_preserved(self):
        for scenario in ("foreign-id", "name", "type", "duplicate", "nested", "encoded", "host", "version", "duplicate-version", "cycle", "fragment", "credentials", "filter", "duplicate-skiptoken"):
            with self.subTest(scenario=scenario):
                transport, provider_id = self.provider_get_inconsistency_transport(children=False)
                collection = provider_id.rsplit("/", 1)[0]
                item = copy.deepcopy(transport.resources[provider_id])
                item.update(id=collection + "/other-provider", name="other-provider")
                body = {"value": [item]}
                expected = "credential-child-owner-mismatch"
                if scenario == "foreign-id": item["id"] = item["id"].replace("rg-test-egakumi-svc", "rg-other")
                elif scenario == "name": item["name"] += "-mismatch"; expected = "credential-provider-owner-mismatch"
                elif scenario == "type": item["type"] = "Microsoft.KeyVault/vaults"; expected = "credential-provider-owner-mismatch"
                elif scenario == "duplicate": body["value"].append(copy.deepcopy(item))
                elif scenario == "nested": item["id"] += "/extra"
                elif scenario == "encoded": item["id"] += "%2fextra"
                else:
                    next_link = "https://management.azure.com" + collection + "?api-version=2024-05-01&$skiptoken=1"
                    if scenario == "host": next_link = next_link.replace("management.azure.com", "outside.invalid")
                    elif scenario == "version": next_link = next_link.replace("2024-05-01", "2022-08-01")
                    elif scenario == "duplicate-version": next_link += "&api-version=2024-05-01"
                    elif scenario == "cycle": next_link = collection + "?api-version=2024-05-01"
                    elif scenario == "fragment": next_link += "#part"
                    elif scenario == "credentials": next_link = next_link.replace("https://", "https://private:credential@")
                    elif scenario == "filter": next_link += "&$filter=name%20eq%20%27unrelated%27"
                    elif scenario == "duplicate-skiptoken": next_link += "&$skiptoken=2"
                    body["nextLink"] = next_link; expected = "credential-child-list-unverified"
                transport.cm_collection_pages[collection] = [{"statusCode": 200, "body": body, "headers": {}}]
                self.assert_code(expected, lambda: self.pre(transport))
                self.assertEqual([c["id"] for c in transport.mutations()], [provider_id])
                self.assertFalse(any("outside.invalid" in c["path"] or "private:credential" in c["path"] for c in transport.calls))

    def test_provider_get_changed_after_synchronous_delete_stops_before_list_or_parent(self):
        for field in ("id", "name", "type", "identityProvider", "clientId", "missing-public-field"):
            with self.subTest(field=field):
                transport, provider_id = self.provider_get_inconsistency_transport(children=False)
                def change(resource):
                    if field in ("id", "name", "type"): resource[field] += "-different"
                    elif field == "identityProvider": resource["properties"][field] = "other"
                    elif field == "missing-public-field": resource["properties"].pop("identityProvider")
                    else: resource["properties"]["oauth2"]["grantTypes"]["authorizationCode"][field] = "other"
                transport.after_delete_mutation = change
                expected = "credential-provider-owner-mismatch" if field in ("id", "name", "type") else "credential-provider-metadata-changed"
                self.assert_code(expected, lambda: self.pre(transport))
                self.assertEqual([c["id"] for c in transport.mutations()], [provider_id])
                self.assertFalse(any(c["id"] == provider_id.rsplit("/", 1)[0] for c in transport.calls))

    def test_provider_delete_202_has_no_list_fallback_or_parent_delete(self):
        transport, provider_id = self.provider_get_inconsistency_transport(status=202, children=False)
        self.assert_code("credential-provider-delete-unverified", lambda: self.pre(transport))
        self.assertEqual([c["id"] for c in transport.mutations()], [provider_id])
        self.assertFalse(any(c["id"] == provider_id.rsplit("/", 1)[0] for c in transport.calls))

    def test_provider_list_absence_does_not_hide_parent_failure_or_timeout(self):
        for failure in ("500", "pending"):
            with self.subTest(failure=failure):
                transport, provider_id = self.provider_get_inconsistency_transport(children=False)
                apim_id = self.state["serviceResourceIds"]["apim"]
                if failure == "500": transport.forced["DELETE", apim_id] = 500
                else: transport.keep_after_delete.add(apim_id)
                expected = "azure-request-failed" if failure == "500" else "cleanup-pending"
                self.assert_code(expected, lambda: self.pre(transport))
                self.assertIn(apim_id, transport.resources)
                self.assertIn(provider_id, transport.deleted_provider_gets)

    def test_already_deleting_parent_waits_without_child_mutation_or_claim_of_cm_cleanup(self):
        transport = self.credential_transport()
        apim_id = self.state["serviceResourceIds"]["apim"]
        transport.resources[apim_id]["properties"] = {"provisioningState": "Deleting"}
        transport.delayed_reads[apim_id] = 1
        transport.pending_delete.add(apim_id)
        result = self.pre(transport)
        self.assertTrue(result["apiManagement"]["apimDeletionVerified"])
        cm = result["apiManagement"]["credentialProviders"][0]
        self.assertEqual(cm["status"], "not-verified-parent-deleting")
        self.assertFalse(cm["deletionVerified"])
        self.assertTrue(cm["parentDeletionVerified"])
        self.assertFalse(transport.mutations())
        self.assertFalse(any("/authorizationProviders/" in call["id"] for call in transport.calls))

    def test_only_configured_providers_on_exact_owned_apim_are_targeted(self):
        transport = self.credential_transport()
        provider_id = next(iter(transport.provider_headers))
        foreign_provider = provider_id.replace("apim-test-egakumi", "apim-other-test-abc123")
        unconfigured = provider_id.rsplit("/", 1)[0] + "/not-configured"
        transport.resources.update({foreign_provider: {"id": foreign_provider}, unconfigured: {"id": unconfigured}})
        self.pre(transport)
        self.assertFalse(any(call["id"] in (foreign_provider, unconfigured) for call in transport.calls))
        transport = self.credential_transport()
        transport.resources[self.state["serviceResourceIds"]["apim"]]["tags"]["product"] = "other"
        self.assert_code("apim-owner-mismatch", lambda: self.pre(transport))
        self.assertFalse(any("/authorizationProviders/" in call["id"] for call in transport.calls))
        self.assertFalse(transport.mutations())

    def test_cm_paged_children_are_all_deleted_individually_before_parent(self):
        """本人接続と権限の複数ページを読み、カスケードに頼らず全IDを回収する。"""
        transport = self.credential_transport()
        provider_id = next(iter(transport.provider_headers))
        children = self.credential_children(transport, provider_id, names=("owner-1", "owner-2"), policies=2)
        result = self.pre(transport)
        expected = []
        for authorization_id, policy_ids in children:
            expected.extend([*policy_ids, authorization_id])
        expected.extend([provider_id, self.state["serviceResourceIds"]["apim"]])
        self.assertEqual([call["id"] for call in transport.mutations()], expected)
        self.assertTrue(any("$skiptoken=1" in call["path"] for call in transport.calls))
        self.assertEqual(len(result["apiManagement"]["credentialProviders"][0]["authorizations"]), 2)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))

    def test_cm_final_next_link_null_or_empty_string_ends_paged_inventories(self):
        """公式一覧のnextLink空文字を、接続・権限の両方で正常な最終ページとして扱う。"""
        for kind in ("authorizations", "accessPolicies"):
            for terminal in (None, ""):
                transport = self.credential_transport()
                provider_id = next(iter(transport.provider_headers))
                children = self.credential_children(transport, provider_id, names=("owner-1", "owner-2"), policies=2)
                collection = provider_id + "/authorizations" if kind == "authorizations" else children[0][0] + "/accessPolicies"
                child_ids = [entry[0] for entry in children] if kind == "authorizations" else children[0][1]
                transport.cm_collection_pages[collection] = [
                    {"statusCode": 200, "body": {"value": [copy.deepcopy(transport.resources[child_ids[0]])],
                        "nextLink": "https://management.azure.com" + collection + "?api-version=2024-05-01&$skiptoken=1"}, "headers": {}},
                    {"statusCode": 200, "body": {"value": [copy.deepcopy(transport.resources[child_ids[1]])],
                        "nextLink": terminal}, "headers": {}},
                ]
                with self.subTest(kind=kind, terminal=terminal):
                    result = self.pre(transport)
                    self.assertTrue(result["apiManagement"]["apimDeletionVerified"])
                    self.assertFalse(transport.cm_collection_pages[collection])
                    self.assertTrue(all(child_id not in transport.resources for child_id in child_ids))

    def test_cm_next_link_whitespace_non_strings_and_invalid_urls_are_not_terminators(self):
        """偽値を空文字へ変換せず、型不正・空白・不正URLは削除前に停止する。"""
        for kind in ("authorizations", "accessPolicies"):
            for next_link in (False, 0, [], {}, " ", "\t", "\n", "not a URL"):
                transport = self.credential_transport()
                provider_id = next(iter(transport.provider_headers))
                authorization_id, _ = self.credential_children(transport, provider_id)[0]
                collection = provider_id + "/authorizations" if kind == "authorizations" else authorization_id + "/accessPolicies"
                transport.cm_collection_pages[collection] = [
                    {"statusCode": 200, "body": {"value": [], "nextLink": next_link}, "headers": {}},
                ]
                with self.subTest(kind=kind, next_link=next_link):
                    self.assert_code("credential-child-list-unverified", lambda: self.pre(transport))
                    self.assertFalse(transport.mutations())

    def test_cm_child_inventory_rejects_foreign_duplicate_or_malformed_items_before_delete(self):
        for kind in ("authorizations", "accessPolicies"):
            for mutation in ("foreign-provider", "foreign-group", "name", "type", "duplicate", "nested", "encoded", "not-object"):
                transport = self.credential_transport()
                provider_id = next(iter(transport.provider_headers))
                authorization_id, policies = self.credential_children(transport, provider_id)[0]
                child_id = authorization_id if kind == "authorizations" else policies[0]
                collection = provider_id + "/authorizations" if kind == "authorizations" else authorization_id + "/accessPolicies"
                child = copy.deepcopy(transport.resources[child_id])
                if mutation == "foreign-provider": child["id"] = child["id"].replace("/approved-oauth/", "/foreign-oauth/")
                elif mutation == "foreign-group": child["id"] = child["id"].replace("rg-test-egakumi-svc", "rg-foreign")
                elif mutation == "name": child["name"] += "-foreign"
                elif mutation == "type": child["type"] = "Microsoft.KeyVault/vaults"
                elif mutation == "nested": child["id"] += "/extra"
                elif mutation == "encoded": child["id"] += "%2fextra"
                elif mutation == "not-object": child = "PRIVATE_SENTINEL"
                transport.cm_collection_extras[collection] = [child]
                with self.subTest(kind=kind, mutation=mutation):
                    self.assert_code("credential-child-owner-mismatch", lambda: self.pre(transport))
                    self.assertFalse(transport.mutations())
                    if isinstance(child, dict) and child.get("id") != child_id:
                        self.assertFalse(any(call["id"] == child["id"] for call in transport.calls))

    def test_cm_child_next_links_are_confined_to_same_arm_collection_and_version(self):
        for kind in ("authorizations", "accessPolicies"):
            for variant in ("host", "provider", "version", "duplicate-version", "fragment", "credentials", "scheme", "port", "cycle", "control-character"):
                transport = self.credential_transport()
                provider_id = next(iter(transport.provider_headers))
                children = self.credential_children(transport, provider_id, names=("owner-1", "owner-2"), policies=2)
                collection = provider_id + "/authorizations" if kind == "authorizations" else children[0][0] + "/accessPolicies"
                normal = "https://management.azure.com" + collection + "?api-version=2024-05-01&$skiptoken=1"
                link = {
                    "host": normal.replace("management.azure.com", "outside.example"),
                    "provider": normal.replace("/approved-oauth/", "/foreign-oauth/"),
                    "version": normal.replace("2024-05-01", "2023-05-01-preview"),
                    "duplicate-version": normal + "&api-version=2024-05-01",
                    "fragment": normal + "#invalid",
                    "credentials": normal.replace("https://", "https://private:credential@"),
                    "scheme": normal.replace("https://", "http://"),
                    "port": normal.replace("management.azure.com", "management.azure.com:444"),
                    "cycle": "https://management.azure.com" + collection + "?api-version=2024-05-01",
                    "control-character": normal.replace("management.azure.com", "management.azure.\ncom"),
                }[variant]
                transport.cm_next_links[collection] = link
                with self.subTest(kind=kind, variant=variant):
                    self.assert_code("credential-child-list-unverified", lambda: self.pre(transport))
                    self.assertFalse(transport.mutations())
                    self.assertFalse(any("outside.example" in call["path"] or "private:credential" in call["path"] for call in transport.calls))

    def test_cm_child_list_404_bad_shape_and_late_page_errors_do_not_remove_parent(self):
        for kind in ("authorizations", "accessPolicies"):
            for scenario in ("404", "403", "value-missing", "value-invalid", "next-invalid", "later-403"):
                transport = self.credential_transport()
                provider_id = next(iter(transport.provider_headers))
                authorization_id, _ = self.credential_children(transport, provider_id)[0]
                collection = provider_id + "/authorizations" if kind == "authorizations" else authorization_id + "/accessPolicies"
                if scenario in ("404", "403"):
                    transport.forced["GET", collection] = int(scenario)
                else:
                    body = {"value": []}
                    if scenario == "value-missing": body = {}
                    elif scenario == "value-invalid": body["value"] = {}
                    elif scenario == "next-invalid": body["nextLink"] = " "
                    elif scenario == "later-403": body["nextLink"] = collection + "?api-version=2024-05-01&$skiptoken=1"
                    transport.cm_collection_pages[collection] = [{"statusCode": 200, "body": body, "headers": {}}]
                    if scenario == "later-403":
                        transport.cm_collection_pages[collection].append({"statusCode": 403, "body": {"error": {"message": "PRIVATE_SENTINEL"}}, "headers": {}})
                expected = "azure-request-failed" if scenario in ("403", "later-403") else "credential-child-list-unverified"
                with self.subTest(kind=kind, scenario=scenario):
                    self.assert_code(expected, lambda: self.pre(transport))
                    self.assertFalse(transport.mutations())

    def test_cm_child_individual_reads_and_etags_are_validated_before_any_delete(self):
        for kind in ("authorizations", "accessPolicies"):
            for mutation in ("404", "403", "id", "type", "name", "etag"):
                transport = self.credential_transport()
                provider_id = next(iter(transport.provider_headers))
                authorization_id, policies = self.credential_children(transport, provider_id)[0]
                child_id = authorization_id if kind == "authorizations" else policies[0]
                if mutation in ("404", "403"):
                    transport.forced["GET", child_id] = int(mutation)
                elif mutation == "etag":
                    transport.provider_headers[child_id] = {"ETag": "unquoted"}
                else:
                    # 一覧は元の正しい識別情報を返し、個別GETだけの不一致を再現する。
                    collection = child_id.rsplit("/", 1)[0]
                    original = copy.deepcopy(transport.resources[child_id])
                    transport.cm_collection_pages[collection] = [{"statusCode": 200, "body": {"value": [original]}, "headers": {}}]
                    transport.resources[child_id][mutation] += "-foreign"
                expected = ("credential-child-read-unverified" if mutation == "404" else
                            "azure-request-failed" if mutation == "403" else
                            "credential-provider-etag-missing" if mutation == "etag" else "credential-child-owner-mismatch")
                with self.subTest(kind=kind, mutation=mutation):
                    self.assert_code(expected, lambda: self.pre(transport))
                    self.assertFalse(transport.mutations())

    def test_all_configured_cm_child_trees_are_checked_before_first_delete(self):
        transport = self.credential_transport(names=("first-provider", "second-provider"))
        first, second = list(transport.provider_headers)
        self.credential_children(transport, first)
        _, policies = self.credential_children(transport, second)[0]
        transport.provider_headers[policies[0]] = {"ETag": "invalid"}
        self.assert_code("credential-provider-etag-missing", lambda: self.pre(transport))
        self.assertFalse(transport.mutations())

    def test_cm_parent_identity_change_after_child_deletion_stops_cleanup(self):
        for kind in ("provider", "authorization"):
            transport = self.credential_transport()
            provider_id = next(iter(transport.provider_headers))
            authorization_id, _ = self.credential_children(transport, provider_id)[0]
            target = provider_id if kind == "provider" else authorization_id
            key = "identityProvider" if kind == "provider" else "authorizationType"
            transport.resources[target]["properties"][key] = "original-public-field"
            fresh = copy.deepcopy(transport.resources[target])
            fresh["properties"][key] = "changed-public-field"
            transport.provider_fresh_results[target] = {"statusCode": 200, "body": fresh, "headers": {"ETag": '"fresh"'}}
            expected = "credential-provider-metadata-changed" if kind == "provider" else "credential-child-metadata-changed"
            with self.subTest(kind=kind):
                self.assert_code(expected, lambda: self.pre(transport))
                self.assertIn(provider_id, transport.resources)
                self.assertFalse(any(call["method"] == "DELETE" and call["id"] in
                    (target, self.state["serviceResourceIds"]["apim"]) for call in transport.calls))

    def test_cm_child_delete_failures_and_pending_absence_preserve_parent(self):
        for kind in ("authorizations", "accessPolicies"):
            for status in (403, 409, 412, "pending"):
                transport = self.credential_transport()
                provider_id = next(iter(transport.provider_headers))
                authorization_id, policies = self.credential_children(transport, provider_id)[0]
                child_id = authorization_id if kind == "authorizations" else policies[0]
                if status == "pending":
                    transport.keep_after_delete.add(child_id)
                else:
                    transport.forced["DELETE", child_id] = status
                expected = "cleanup-pending" if status == "pending" else "azure-request-failed"
                with self.subTest(kind=kind, status=status):
                    self.assert_code(expected, lambda: self.pre(transport, sleep=lambda _: None))
                    self.assertIn(provider_id, transport.resources)
                    self.assertIn(self.state["serviceResourceIds"]["apim"], transport.resources)
                    self.assertFalse(any(call["method"] == "DELETE" and call["id"] in
                        (provider_id, self.state["serviceResourceIds"]["apim"]) for call in transport.calls))

    def test_cm_parents_refresh_etags_after_explicit_child_deletion(self):
        transport = self.credential_transport()
        provider_id = next(iter(transport.provider_headers))
        authorization_id, _ = self.credential_children(transport, provider_id)[0]
        transport.etag_after_child_delete = {provider_id: {"ETag": '"provider-after-child"'},
                                            authorization_id: {"ETag": '"authorization-after-child"'}}
        result = self.pre(transport)
        for resource_id, etag in ((authorization_id, '"authorization-after-child"'), (provider_id, '"provider-after-child"')):
            deletion = next(call for call in transport.mutations() if call["id"] == resource_id)
            self.assertEqual(deletion["headers"], {"If-Match": etag})
        provider = result["apiManagement"]["credentialProviders"][0]
        self.assertTrue(provider["identityRevalidated"])
        self.assertTrue(provider["authorizations"][0]["identityRevalidated"])

    def test_cm_children_missing_etags_revalidate_before_documented_wildcard(self):
        transport = self.credential_transport()
        provider_id = next(iter(transport.provider_headers))
        authorization_id, policies = self.credential_children(transport, provider_id)[0]
        for child_id in [authorization_id, *policies]:
            transport.provider_headers[child_id] = {}
        result = self.pre(transport)
        for resource_id in [authorization_id, *policies]:
            deletion = next(i for i, call in enumerate(transport.calls) if call["method"] == "DELETE" and call["id"] == resource_id)
            self.assertEqual(transport.calls[deletion]["headers"], {"If-Match": "*"})
            self.assertEqual((transport.calls[deletion - 1]["method"], transport.calls[deletion - 1]["id"]), ("GET", resource_id))
        authorization = result["apiManagement"]["credentialProviders"][0]["authorizations"][0]
        self.assertFalse(authorization["versionPreconditionApplied"])
        self.assertFalse(authorization["accessPolicies"][0]["versionPreconditionApplied"])

    def test_new_cm_children_observed_after_cleanup_stop_parent_deletion(self):
        for kind in ("authorizations", "accessPolicies"):
            transport = self.credential_transport()
            provider_id = next(iter(transport.provider_headers))
            authorization_id, policies = self.credential_children(transport, provider_id)[0]
            collection = provider_id + "/authorizations" if kind == "authorizations" else authorization_id + "/accessPolicies"
            original_id = authorization_id if kind == "authorizations" else policies[0]
            original = copy.deepcopy(transport.resources[original_id])
            late = copy.deepcopy(original)
            late["id"] += "-late"
            late["name"] += "-late"
            transport.cm_collection_pages[collection] = [
                {"statusCode": 200, "body": {"value": [original]}, "headers": {}},
                {"statusCode": 200, "body": {"value": [late]}, "headers": {}},
            ]
            with self.subTest(kind=kind):
                self.assert_code("credential-children-remain", lambda: self.pre(transport))
                self.assertFalse(any(call["method"] == "DELETE" and call["id"] == provider_id for call in transport.calls))
                self.assertFalse(any(call["id"] == late["id"] for call in transport.calls))

    def test_cm_child_failure_can_resume_without_redeleting_completed_children(self):
        transport = self.credential_transport()
        provider_id = next(iter(transport.provider_headers))
        authorization_id, policies = self.credential_children(transport, provider_id, policies=2)[0]
        transport.forced["DELETE", policies[1]] = 409
        self.assert_code("azure-request-failed", lambda: self.pre(transport))
        self.assertNotIn(policies[0], transport.resources)
        transport.forced.clear()
        transport.calls.clear()
        result = self.pre(transport)
        self.assertTrue(result["apiManagement"]["apimDeletionVerified"])
        self.assertFalse(any(call["id"] == policies[0] for call in transport.calls))
        self.assertEqual([call["id"] for call in transport.mutations()],
                         [policies[1], authorization_id, provider_id, self.state["serviceResourceIds"]["apim"]])


if __name__ == "__main__":
    unittest.main()
