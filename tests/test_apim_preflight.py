"""固定名APIMを、有料構築の前に安全に確認できることを検査する。

Azureの代わりに合成ARM応答を返す。所有している既存APIMは再利用でき、
他の所有者・削除履歴・名前競合・読取失敗では停止することを確認する。
この試験でAzure資源を作成・削除したり、実際の名前を予約したりしない。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
import unittest


CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
from deployment.helpers import apim_preflight

SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
NAME = "apim-test-egakumi-abc123"
RESOURCE_ID = (f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-test-egakumi-svc"
               f"/providers/Microsoft.ApiManagement/service/{NAME}")
VERSION = "2024-05-01"
SERVICE_PATH = RESOURCE_ID + "?api-version=" + VERSION
DELETED_PATH = (f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.ApiManagement/"
                f"locations/japaneast/deletedservices/{NAME}?api-version={VERSION}")
AVAILABILITY_PATH = (f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.ApiManagement/"
                     f"checkNameAvailability?api-version={VERSION}")


def configuration():
    """本物の個人IDを含まない、正規化済み設定の最小例を返す。"""
    return {"services": {"apimInstanceId": "abc123"}, "resourceStem": "test-egakumi", "subscriptionId": SUBSCRIPTION,
            "resourceGroupNames": {"svc": "rg-test-egakumi-svc"},
            "location": "japaneast", "productName": "egakumi", "environmentName": "test"}


def owned_service():
    """ID・配置先・所有タグ・完了状態が一致する既存APIMを表す。"""
    return {"id": RESOURCE_ID, "name": NAME, "type": "Microsoft.ApiManagement/service",
            "location": "Japan East", "tags": {"product": "egakumi", "environment": "test"},
            "properties": {"provisioningState": "Succeeded"}}


def response(status=200, body=None):
    """Transportが返すHTTP状態とJSON本文を組み立てる。"""
    return {"statusCode": status, "body": body}


class FakeTransport:
    """渡された応答だけを順に返し、要求の種類・宛先・本文を記録する。

    ネットワーク機能を持たないため、POSTの名前照会もAzureへ送信されない。
    想定外の追加要求は試験失敗とし、自動purgeや別名探索を見逃さない。
    """
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, *, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if not self.responses:
            raise AssertionError("Unexpected ARM request")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class ApimPreflightTests(unittest.TestCase):
    """同名での新規作成・再利用と、継続してはいけない条件を区別する。"""
    def verify(self, responses):
        config = configuration()
        before = copy.deepcopy(config)
        transport = FakeTransport(responses)
        result = apim_preflight.verify_apim_name(config, transport)
        self.assertEqual(config, before)
        self.assertEqual(result["name"], NAME)
        self.assertEqual(result["resourceId"], RESOURCE_ID)
        # 公開APIの確認だけでMicrosoft内部のCM予約まで保証しない。
        self.assertIs(result["credentialManagerInternalReservationVerified"], False)
        return result, transport

    def test_missing_or_invalid_instance_id_stops_before_any_arm_request(self):
        for value in (None, "", "ABC123", "abc12", "abc1234", "abc-12", 123456):
            with self.subTest(value=value):
                config = configuration()
                if value is None:
                    del config["services"]["apimInstanceId"]
                else:
                    config["services"]["apimInstanceId"] = value
                transport = FakeTransport([])
                with self.assertRaisesRegex(ValueError, "apimInstanceId"):
                    apim_preflight.verify_apim_name(config, transport)
                self.assertEqual(transport.calls, [])

    def test_new_name_checks_service_tombstone_then_read_only_name_query(self):
        """本体も削除履歴も無く、名前照会が成功した時だけ新規構築へ進める。"""
        result, transport = self.verify([
            response(404), response(404), response(body={"nameAvailable": True})])
        self.assertEqual(result["status"], "available-for-create")
        self.assertEqual(transport.calls, [
            ("GET", SERVICE_PATH, None), ("GET", DELETED_PATH, None),
            ("POST", AVAILABILITY_PATH, {"name": NAME})])

    def test_owned_completed_service_is_reused_without_name_query(self):
        """一致する既存APIMでは照会POSTや削除履歴の操作を追加しない。"""
        result, transport = self.verify([response(body=owned_service())])
        self.assertEqual(result["status"], "owned-existing")
        self.assertEqual(transport.calls, [("GET", SERVICE_PATH, None)])

    def test_arm_id_type_case_and_display_location_do_not_make_a_false_conflict(self):
        """ARMのID表記差と地域表示名の空白を、別資源の競合と取り違えない。"""
        service = owned_service()
        service["id"] = service["id"].upper()
        service["type"] = service["type"].upper()
        service["location"] = "JAPAN EAST"
        result, _ = self.verify([response(body=service)])
        self.assertEqual(result["status"], "owned-existing")

    def test_foreign_or_incomplete_owner_metadata_stops_before_any_more_calls(self):
        """名前だけが一致しても、配置先・種類・所有タグの不一致は再利用しない。"""
        mutations = {
            "subscription": lambda v: v.update(id=v["id"].replace(SUBSCRIPTION, "other-subscription")),
            "group": lambda v: v.update(id=v["id"].replace("rg-test-egakumi-svc", "rg-other-svc")),
            "id-name": lambda v: v.update(id=v["id"] + "-other"),
            "name": lambda v: v.update(name=NAME + "-other"),
            "type": lambda v: v.update(type="Microsoft.Compute/virtualMachines"),
            "location": lambda v: v.update(location="eastus2"),
            "location-missing": lambda v: v.pop("location"),
            "tags-missing": lambda v: v.pop("tags"),
            "tags-malformed": lambda v: v.update(tags=[]),
            "product": lambda v: v["tags"].update(product="other"),
            "environment": lambda v: v["tags"].update(environment="prod"),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                service = owned_service()
                mutate(service)
                transport = FakeTransport([response(body=service)])
                with self.assertRaises(apim_preflight.ApimPreflightError):
                    apim_preflight.verify_apim_name(configuration(), transport)
                self.assertEqual(transport.calls, [("GET", SERVICE_PATH, None)])

    def test_existing_non_completed_service_is_not_treated_as_reusable(self):
        """構築・更新・削除中や失敗状態を、存在するだけで再利用可能にしない。"""
        states = [None, [], {}, *({"provisioningState": value} for value in
                  (None, "Creating", "Updating", "Deleting", "Failed", "Unknown"))]
        for properties in states:
            with self.subTest(properties=properties):
                service = owned_service()
                service["properties"] = properties
                transport = FakeTransport([response(body=service)])
                with self.assertRaises(apim_preflight.ApimPreflightError):
                    apim_preflight.verify_apim_name(configuration(), transport)
                self.assertEqual(len(transport.calls), 1)

    def test_deleted_service_blocks_creation_without_automatic_purge_or_rename(self):
        """削除履歴が残る場合は停止し、purge・復元・別名作成を試さない。"""
        transport = FakeTransport([response(404), response(body={"id": "deleted-service"})])
        with self.assertRaises(apim_preflight.ApimPreflightError):
            apim_preflight.verify_apim_name(configuration(), transport)
        self.assertEqual(transport.calls, [("GET", SERVICE_PATH, None), ("GET", DELETED_PATH, None)])

    def test_name_available_must_be_the_json_boolean_true(self):
        """空欄・文字列true・数値1等を、名前の利用許可と誤判定しない。"""
        for value in (False, None, 1, 0, "true", "false", [], {}):
            with self.subTest(value=value):
                transport = FakeTransport([
                    response(404), response(404), response(body={"nameAvailable": value})])
                with self.assertRaises(apim_preflight.ApimPreflightError):
                    apim_preflight.verify_apim_name(configuration(), transport)
                self.assertEqual(len(transport.calls), 3)

    def test_failed_name_query_cannot_succeed_from_a_positive_body(self):
        """HTTP404の本文にtrueがあっても、成功した名前照会として扱わない。"""
        transport = FakeTransport([
            response(404), response(404), response(404, {"nameAvailable": True})])
        with self.assertRaises(apim_preflight.ApimPreflightError):
            apim_preflight.verify_apim_name(configuration(), transport)

    def test_unauthorized_throttled_or_unavailable_reads_stop_at_the_failed_stage(self):
        """権限不足・過負荷・Azure側の失敗を、存在しない資源と取り違えない。"""
        for stage in range(3):
            for status in (401, 403, 409, 429, 500, 503):
                with self.subTest(stage=stage, status=status):
                    transport = FakeTransport([*[response(404) for _ in range(stage)],
                        response(status, {"privateDetails": "SENTINEL-PRIVATE-RESPONSE"})])
                    with self.assertRaises(apim_preflight.ApimPreflightError) as caught:
                        apim_preflight.verify_apim_name(configuration(), transport)
                    self.assertNotIn("SENTINEL", str(caught.exception))
                    self.assertEqual(len(transport.calls), stage + 1)

    def test_malformed_transport_responses_never_advance_to_creation(self):
        """HTTP状態やJSON形状が壊れた応答を、読取成功に変換しない。"""
        invalid = (None, [], "SENTINEL-PRIVATE-RESPONSE", {}, {"statusCode": True},
                   {"statusCode": "200"}, response(body=[]), response(body="SENTINEL-PRIVATE-RESPONSE"))
        for stage in range(3):
            for value in invalid:
                with self.subTest(stage=stage, value=value):
                    transport = FakeTransport([*[response(404) for _ in range(stage)], value])
                    with self.assertRaises(apim_preflight.ApimPreflightError) as caught:
                        apim_preflight.verify_apim_name(configuration(), transport)
                    self.assertNotIn("SENTINEL", str(caught.exception))
                    self.assertEqual(len(transport.calls), stage + 1)

    def test_transport_exceptions_are_redacted_at_every_stage(self):
        """接続ライブラリーの生例外に含まれるURL・秘密値を上へ持ち出さない。"""
        for stage in range(3):
            with self.subTest(stage=stage):
                transport = FakeTransport([*[response(404) for _ in range(stage)],
                    RuntimeError("SENTINEL-PRIVATE-TOKEN")])
                with self.assertRaises(apim_preflight.ApimPreflightError) as caught:
                    apim_preflight.verify_apim_name(configuration(), transport)
                self.assertNotIn("SENTINEL", str(caught.exception))
                self.assertEqual(len(transport.calls), stage + 1)


if __name__ == "__main__":
    unittest.main()
