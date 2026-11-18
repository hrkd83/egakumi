"""KV参照の自動更新情報だけを除き、構成と他資源の競合検出を維持する。"""
import copy
import unittest

from test_entra_publication import APIM, response
import entra_publication as publication


VALUE_ID = APIM + "/namedValues/synthetic-reference"
VALUE_PATH = VALUE_ID + "?api-version=" + publication.APIM_API
COLLECTION_PATH = APIM + "/namedValues?api-version=" + publication.APIM_API


def named_value():
    value = response(VALUE_ID, {
        "displayName": "synthetic-reference", "secret": True, "tags": [],
        "keyVault": {
            "secretIdentifier": "https://vault.synthetic.invalid/secrets/synthetic-secret",
            "identityClientId": None,
            "lastStatus": {"code": "Success", "message": "Reference resolved",
                           "timeStampUtc": "2026-01-01T00:00:00.0000000Z"},
        },
    })
    value["body"].update(type="Microsoft.ApiManagement/service/namedValues", name="synthetic-reference")
    return value


def refreshed(value):
    value = copy.deepcopy(value)
    value["headers"]["ETag"] = '"synthetic-refresh"'
    value["body"]["etag"] = '"synthetic-refresh"'
    value["body"]["properties"]["keyVault"]["lastStatus"]["timeStampUtc"] = "2026-01-02T01:02:03+00:00"
    return value


class NamedValueRefreshSignatureTests(unittest.TestCase):
    def signature(self, value, path=VALUE_PATH):
        return publication._signature(value, resource_id=path)

    def test_refresh_time_and_its_etag_do_not_change_the_meaning_or_conditional_etag(self):
        before = named_value()
        original = copy.deepcopy(before)
        after = refreshed(before)
        self.assertEqual(self.signature(before), self.signature(after))
        self.assertEqual(before, original)
        self.assertEqual(publication._etag(before), '"fixture-1"')
        self.assertEqual(publication._etag(after), '"synthetic-refresh"')

    def test_every_configuration_or_status_change_is_still_detected(self):
        changes = {
            "secret": lambda body: body["properties"].update(secret=False),
            "display-name": lambda body: body["properties"].update(displayName="changed"),
            "tags": lambda body: body["properties"].update(tags=["changed"]),
            "value": lambda body: body["properties"].update(value="unexpected-inline-value"),
            "resource-type": lambda body: body.update(type="Microsoft.ApiManagement/service/apis"),
            "resource-id": lambda body: body.update(id=VALUE_ID + "-other"),
            "resource-name": lambda body: body.update(name="other"),
            "secret-reference": lambda body: body["properties"]["keyVault"].update(
                secretIdentifier="https://vault.synthetic.invalid/secrets/another-secret"),
            "identity": lambda body: body["properties"]["keyVault"].update(
                identityClientId="22222222-2222-4222-8222-222222222222"),
            "status-code": lambda body: body["properties"]["keyVault"]["lastStatus"].update(code="Failed"),
            "status-message": lambda body: body["properties"]["keyVault"]["lastStatus"].update(message="Changed"),
            "extra-vault-setting": lambda body: body["properties"]["keyVault"].update(unexpected=True),
            "extra-status": lambda body: body["properties"]["keyVault"]["lastStatus"].update(unexpected=True),
            "missing-time": lambda body: body["properties"]["keyVault"]["lastStatus"].pop("timeStampUtc"),
            "invalid-time": lambda body: body["properties"]["keyVault"]["lastStatus"].update(timeStampUtc="invalid"),
            "null-time": lambda body: body["properties"]["keyVault"]["lastStatus"].update(timeStampUtc=None),
        }
        for label, change in changes.items():
            with self.subTest(change=label):
                before = named_value()
                after = refreshed(before)
                change(after["body"])
                self.assertNotEqual(self.signature(before), self.signature(after))

    def test_malformed_named_values_do_not_receive_the_etag_exception(self):
        changes = (
            lambda body: body["properties"].update(secret="true"),
            lambda body: body["properties"].update(keyVault=[]),
            lambda body: body["properties"]["keyVault"].update(secretIdentifier="not-a-vault-url"),
            lambda body: body["properties"]["keyVault"].update(identityClientId={}),
            lambda body: body["properties"]["keyVault"].update(lastStatus=[]),
            lambda body: body["properties"]["keyVault"]["lastStatus"].update(code=200),
            lambda body: body["properties"]["keyVault"]["lastStatus"].update(message=[]),
            lambda body: body["properties"]["keyVault"]["lastStatus"].update(timeStampUtc="2026-02-30T00:00:00Z"),
            lambda body: body.update(id="/not-an-arm-resource"),
            lambda body: body.pop("type"),
        )
        for index, change in enumerate(changes):
            with self.subTest(malformed=index):
                before = named_value()
                change(before["body"])
                after = copy.deepcopy(before)
                after["headers"]["ETag"] = '"changed"'
                after["body"]["etag"] = '"changed"'
                self.assertNotEqual(self.signature(before), self.signature(after))

    def test_plain_named_values_policies_apis_and_failed_responses_keep_etags(self):
        plain = named_value()
        plain["body"]["properties"].pop("keyVault")
        plain["body"]["properties"].update(secret=False, value="synthetic-setting")
        cases = [(plain, VALUE_PATH), (named_value(), VALUE_PATH)]
        cases[1][0]["statusCode"] = 404
        for kind, name in (("apis", "synthetic-api"), ("policies", "policy")):
            value = named_value()
            rid = APIM + "/" + kind + "/" + name
            value["body"].update(id=rid, name=name, type="Microsoft.ApiManagement/service/" + kind)
            cases.append((value, rid))
        for before, path in cases:
            with self.subTest(resource=path, status=before["statusCode"]):
                after = copy.deepcopy(before)
                after["headers"]["ETag"] = '"changed"'
                self.assertNotEqual(self.signature(before, path), self.signature(after, path))

    def test_named_value_collection_normalizes_only_its_own_valid_vault_entries(self):
        before = {"statusCode": 200, "body": {"value": [named_value()["body"]], "count": 1}, "headers": {}}
        after = copy.deepcopy(before)
        after["body"]["value"][0] = refreshed(named_value())["body"]
        self.assertEqual(self.signature(before, COLLECTION_PATH), self.signature(after, COLLECTION_PATH))
        self.assertNotEqual(self.signature(before, APIM + "/apis"), self.signature(after, APIM + "/apis"))
        self.assertNotEqual(publication._signature(before), publication._signature(after))
        foreign = COLLECTION_PATH.replace("/service/fixture/", "/service/another/")
        self.assertNotEqual(self.signature(before, foreign), self.signature(after, foreign))
        for change in (
            lambda value: value["body"]["value"][0]["properties"]["keyVault"]["lastStatus"].update(code="Failed"),
            lambda value: value["body"]["value"].append(copy.deepcopy(value["body"]["value"][0])),
            lambda value: value["body"].update(count=2),
            lambda value: value["headers"].update(ETag='"changed-collection"'),
        ):
            changed = copy.deepcopy(after)
            change(changed)
            self.assertNotEqual(self.signature(before, COLLECTION_PATH), self.signature(changed, COLLECTION_PATH))

    def test_collection_preserves_non_vault_and_malformed_entry_etags(self):
        for malformed in (False, True):
            with self.subTest(malformed=malformed):
                entry = named_value()["body"]
                if malformed:
                    entry["properties"]["keyVault"]["lastStatus"]["timeStampUtc"] = None
                else:
                    entry["properties"].pop("keyVault")
                    entry["properties"].update(secret=False, value="synthetic-setting")
                before = {"statusCode": 200, "body": {"value": [entry], "count": 1}, "headers": {}}
                after = copy.deepcopy(before)
                after["body"]["value"][0]["etag"] = '"changed"'
                self.assertNotEqual(self.signature(before, COLLECTION_PATH), self.signature(after, COLLECTION_PATH))

    def test_verify_snapshot_scopes_refresh_comparison_to_the_actual_get_path(self):
        class Observed:
            def __init__(self, value):
                self.value = value

            def request(self, method, path, body=None, headers=None):
                self.requested = method, path, body, headers
                return copy.deepcopy(self.value)

        for path, before, after in (
            (VALUE_PATH, named_value(), refreshed(named_value())),
            (COLLECTION_PATH,
             {"statusCode": 200, "body": {"value": [named_value()["body"]]}, "headers": {}},
             {"statusCode": 200, "body": {"value": [refreshed(named_value())["body"]]}, "headers": {}}),
        ):
            transport = Observed(after)
            publication.verify_snapshot({"resource": (path, {"Accept": "application/json"}, before)}, transport)
            self.assertEqual(transport.requested, ("GET", path, None, {"Accept": "application/json"}))


if __name__ == "__main__":
    unittest.main()
