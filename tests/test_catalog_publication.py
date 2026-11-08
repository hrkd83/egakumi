"""gRPC取り込み後の説明反映を、通信せず所有確認・競合・再実行まで検証する。"""
import copy
from pathlib import Path
import sys
import unittest

from test_catalog_descriptions import public_contract

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers.catalog_descriptions import describe_api
from catalog_publication import finalize_descriptions
from deployment.helpers.apim_naming import apim_name


class MetadataTransport:
    """説明だけのPATCHと、その後の読取を再現する。外部接続は行わない。"""
    def __init__(self, parent, api):
        self.parent, self.api = copy.deepcopy(parent), copy.deepcopy(api)
        self.calls = []
        self.etag = '"fixture-api-etag"'
        self.patch_status = 200
        self.ignore_patch = False
        self.change_backend = False

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, copy.deepcopy(body), copy.deepcopy(headers)))
        if method == "GET":
            value = self.parent if path.split("?", 1)[0] == self.parent["id"] else self.api
            return {"statusCode": 200, "body": copy.deepcopy(value),
                    "headers": {} if self.etag is None else {"eTaG": self.etag}}
        if method == "PATCH":
            if self.patch_status != 200:
                return {"statusCode": self.patch_status, "body": {"error": {"message": "PRIVATE_ERROR_FIXTURE"}}}
            if not self.ignore_patch:
                self.api["properties"].update(body["properties"])
            if self.change_backend:
                self.api["properties"]["serviceUrl"] = "https://different-private.fixture"
            return {"statusCode": 200, "body": copy.deepcopy(self.api), "headers": {}}
        raise AssertionError("Unexpected method in metadata fixture")


class CatalogPublicationTests(unittest.TestCase):
    """同じ正規モジュールをnative up/API再公開から安全に呼べることを確認する。"""
    def setUp(self):
        self.config = public_contract()
        self.config["gateway"]["grpc"] = {"enabled": True, "publicHostName": "rpc.fixture.example", "publicPort": 9443}
        self.api = self.config["apiDefinitions"][0]
        self.api.update(apiType="grpc", path="", productId="tool-users",
                        grpc={"definition": 'syntax = "proto3"; package fixture.v1; service Probe { rpc Echo (X) returns (X); }'})
        name = apim_name(self.config)
        self.apim_id = (f"/subscriptions/{self.config['subscriptionId']}/resourceGroups/"
                        f"{self.config['resourceGroupNames']['svc']}/providers/Microsoft.ApiManagement/service/{name}")
        self.api_id = self.apim_id + "/apis/" + self.api["name"]
        self.outputs = {"names": {"apim": name}, "cleanup": {"serviceResourceIds": {"apim": self.apim_id}}}
        parent = {"id": self.apim_id, "tags": {"product": self.config["productName"], "environment": self.config["environmentName"]}}
        self.metadata = {"id": self.api_id, "properties": {"type": "grpc", "path": "", "displayName": self.api["displayName"],
            "description": "", "serviceUrl": "https://original-private.fixture", "protocols": ["https"],
            "subscriptionRequired": False, "apiRevision": "1", "provisioningState": "Succeeded"}}
        self.transport = MetadataTransport(parent, self.metadata)

    def patches(self):
        return [call for call in self.transport.calls if call[0] == "PATCH"]

    def test_missing_import_description_is_patched_once_and_next_run_is_read_only(self):
        before = copy.deepcopy(self.metadata["properties"])
        result = finalize_descriptions(self.config, self.outputs, self.transport)
        self.assertEqual(result, {"enabled": True, "checked": 1, "updated": 1, "apiIds": [self.api_id]})
        self.assertEqual(self.patches()[0][2], {"properties": {"description": describe_api(self.config, self.api)}})
        self.assertEqual(self.patches()[0][3], {"If-Match": '"fixture-api-etag"'})
        self.assertIn("https://rpc.fixture.example:9443/", self.transport.api["properties"]["description"])
        self.assertEqual({k: v for k, v in self.transport.api["properties"].items() if k != "description"},
                         {k: v for k, v in before.items() if k != "description"})
        self.transport.calls.clear()
        again = finalize_descriptions(self.config, self.outputs, self.transport)
        self.assertEqual(again["updated"], 0)
        self.assertEqual(self.patches(), [])

    def test_inactive_or_no_grpc_configuration_does_not_read_azure(self):
        for change in ("disabled", "no-grpc"):
            config = copy.deepcopy(self.config)
            if change == "disabled":
                config["publishApis"] = False
            else:
                config["apiDefinitions"] = []
            self.assertFalse(finalize_descriptions(config, {}, self.transport)["enabled"])
        self.assertEqual(self.transport.calls, [])

    def test_different_apim_or_cleanup_identity_is_rejected_before_reads(self):
        for change in ("name", "cleanup"):
            outputs = copy.deepcopy(self.outputs)
            if change == "name":
                outputs["names"]["apim"] += "-foreign"
            else:
                outputs["cleanup"]["serviceResourceIds"]["apim"] += "-foreign"
            with self.subTest(change=change), self.assertRaises(ValueError):
                finalize_descriptions(self.config, outputs, self.transport)
        self.assertEqual(self.transport.calls, [])

    def test_foreign_parent_or_api_contract_is_rejected_without_patch(self):
        for field in ("parent-owner", "api-id", "type", "path", "displayName"):
            transport = copy.deepcopy(self.transport)
            if field == "parent-owner":
                transport.parent["tags"]["product"] = "foreign"
            elif field == "api-id":
                transport.api["id"] += "-foreign"
            else:
                transport.api["properties"][field] = "different"
            with self.subTest(field=field), self.assertRaises(ValueError):
                finalize_descriptions(self.config, self.outputs, transport)
            self.assertFalse(any(call[0] == "PATCH" for call in transport.calls))

    def test_missing_or_wildcard_etag_is_not_used_for_update(self):
        for etag in (None, "", "*"):
            self.transport.etag = etag
            with self.subTest(etag=etag), self.assertRaises(ValueError):
                finalize_descriptions(self.config, self.outputs, self.transport)
        self.assertEqual(self.patches(), [])

    def test_conflict_is_not_retried_or_printed_with_private_error(self):
        self.transport.patch_status = 412
        with self.assertRaises(ValueError) as failure:
            finalize_descriptions(self.config, self.outputs, self.transport)
        self.assertEqual(len(self.patches()), 1)
        self.assertNotIn("PRIVATE_ERROR_FIXTURE", str(failure.exception))

    def test_ignored_patch_or_changed_backend_is_detected_by_full_readback(self):
        for field in ("ignore_patch", "change_backend"):
            transport = copy.deepcopy(self.transport)
            setattr(transport, field, True)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "readback differs"):
                finalize_descriptions(self.config, self.outputs, transport)
            self.assertEqual(sum(call[0] == "PATCH" for call in transport.calls), 1)

    def test_unsafe_later_target_is_rejected_before_any_description_update(self):
        second = copy.deepcopy(self.api)
        second["name"] = "../foreign-api"
        self.config["apiDefinitions"].append(second)
        with self.assertRaises(ValueError):
            finalize_descriptions(self.config, self.outputs, self.transport)
        self.assertEqual(self.patches(), [])


if __name__ == "__main__":
    unittest.main()
