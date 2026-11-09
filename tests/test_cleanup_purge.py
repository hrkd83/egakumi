"""合成したKey Vaultの非同期purgeで、要求1回・所有範囲・最後の墓標404を検証する。"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment.helpers import cleanup
from cleanup_fixtures import CleanupFixture, FakeTransport


class Clock:
    """実際には待機せず、ポーリング間隔と期限を再現する。"""
    def __init__(self):
        """経過秒数と待機記録を初期化する。"""
        self.value = 0
        self.sleeps = []

    def now(self):
        """合成した単調増加時刻を返す。"""
        return self.value

    def sleep(self, seconds):
        """待機したことを記録し、合成時刻だけを進める。"""
        self.sleeps.append(seconds)
        self.value += seconds


class PurgeTransport(FakeTransport):
    """Key Vaultのpurge受付・操作結果・墓標だけを合成応答で返す。"""
    def __init__(self, tombstone, initial, operations=(), tombstones=(404,), *, clock=None):
        """対象の墓標と返却する応答列を保持する。"""
        super().__init__({tombstone["id"]: tombstone})
        self.tombstone = tombstone
        self.initial = initial
        self.operations = list(operations)
        self.tombstones = list(tombstones)
        self.started = False
        self.clock = clock
        self.read_times = []

    def request(self, method, path, *, body=None, headers=None):
        """要求順と時刻を記録し、Azureへ送らず合成応答を返す。"""
        resource_id = urlsplit(path).path
        operation = "/operationResults/" in resource_id or "/operations/" in resource_id
        if method == "POST" or method == "GET" and (operation or self.started and resource_id == self.tombstone["id"]):
            self.calls.append({"method": method, "id": resource_id, "path": path, "headers": headers, "body": body})
            if method == "POST":
                self.started = True
                return copy.deepcopy(self.initial)
            self.read_times.append(self.clock.now() if self.clock else 0)
            if operation:
                if not self.operations:
                    raise AssertionError("想定外の操作結果ポーリングです。")
                result = self.operations.pop(0)
                if isinstance(result, Exception):
                    raise result
                return copy.deepcopy(result)
            status = self.tombstones.pop(0) if len(self.tombstones) > 1 else self.tombstones[0]
            return {"statusCode": status, "body": self.tombstone if status == 200 else None, "headers": {}}
        return super().request(method, path, body=body, headers=headers)


def response(status=200, body=None, **headers):
    """合成HTTP応答を共通の形式にそろえる。"""
    return {"statusCode": status, "body": body, "headers": headers}


class PurgeTests(CleanupFixture, unittest.TestCase):
    """Key Vaultの完全削除を、失敗・期限切れも含めてローカルで確認する。"""
    def setUp(self):
        """所有状態に一致する架空のKey Vaultだけを用意する。"""
        super().setUp()
        self.state["serviceResourceIds"] = {"keyVault": self.state["serviceResourceIds"]["keyVault"]}
        self.tombstone = next(iter(self.tombstones().values()))
        prefix = "/".join(self.tombstone["id"].split("/")[:7])
        self.operation = "https://management.azure.com" + prefix + "/operationResults/synthetic-operation"
        self.operation += "?api-version=" + cleanup.API["keyVault"]
        self.clock = Clock()

    def run_purge(self, initial, operations=(), tombstones=(404,), **options):
        """正常終了がpurge一回と最後の墓標404を満たすか確認する。"""
        transport = PurgeTransport(self.tombstone, initial, operations, tombstones, clock=self.clock)
        result = self.post(transport, sleep=self.clock.sleep, monotonic=self.clock.now, **options)
        self.assertEqual(result["softDeletedServices"][0]["httpStatus"], 404)
        self.assertEqual(len(transport.mutations()), 1)
        return transport

    def test_location_waits_longer_than_old_minute_then_requires_final_404(self):
        """長い待機指示と操作完了後の墓標反映待ちを尊重する。"""
        target = self.operation + "&t=opaque&c=opaque&s=opaque&h=opaque"
        transport = self.run_purge(response(202, Location=target, **{"Retry-After": "120"}),
                                   [response(202, {"status": "Running"}, **{"Retry-After": "7"}),
                                    response(200, {"status": "Succeeded"})], [200, 404])
        self.assertEqual(self.clock.sleeps, [60, 60, 7, 5])
        self.assertEqual(transport.read_times, [120, 127, 127, 132])
        self.assertEqual([call["path"] for call in transport.calls if "https://" in call["path"]], [target, target])

    def test_azure_asyncoperation_has_priority_and_status_is_required(self):
        """優先ヘッダーを固定し、必須の非同期状態がなければ停止する。"""
        initial = response(202, **{"Azure-AsyncOperation": self.operation, "Location": "https://outside.invalid/SECRET_SENTINEL"})
        transport = self.run_purge(initial, [response(200, {"status": "Queued"}), response(200, {"status": "Succeeded"})])
        self.assertTrue(all("outside.invalid" not in call["path"] for call in transport.calls))
        transport = PurgeTransport(self.tombstone, initial, [response(200)])
        self.assert_code("invalid-purge-response", lambda: self.post(transport, sleep=self.clock.sleep, monotonic=self.clock.now))
        self.assertEqual(len(transport.mutations()), 1)

    def test_location_can_finish_without_body_but_not_replace_tombstone(self):
        """Locationの完了応答だけで墓標の消滅を代用しない。"""
        for status in (200, 204):
            with self.subTest(status=status):
                self.run_purge(response(202, Location=self.operation), [response(status)], [200, 404])
        transport = PurgeTransport(self.tombstone, response(202, Location=self.operation),
                                   [response(200, {"status": "Succeeded"})], [200])
        self.assert_code("cleanup-pending", lambda: self.post(transport, sleep=self.clock.sleep,
                         monotonic=self.clock.now, purge_timeout_seconds=12))
        self.assertEqual(len(transport.mutations()), 1)

    def test_expired_operation_404_still_checks_tombstone(self):
        """操作結果の失効後も、正確な墓標の404を別に要求する。"""
        transport = self.run_purge(response(202, Location=self.operation), [response(404)], [200, 404])
        self.assertEqual(len(transport.read_times), 3)

    def test_failed_or_canceled_operation_never_reports_absence(self):
        """操作失敗・取消を成功へ変換せず、外部エラー本文も表示しない。"""
        for status in ("Failed", "Canceled", "Cancelled"):
            with self.subTest(status=status):
                transport = PurgeTransport(self.tombstone, response(202, Location=self.operation),
                                           [response(200, {"status": status, "error": {"message": "SECRET_SENTINEL"}})])
                with self.assertRaises(cleanup.CleanupError) as raised:
                    self.post(transport, sleep=self.clock.sleep, monotonic=self.clock.now)
                self.assertEqual(raised.exception.code, "purge-operation-failed")
                self.assertNotIn("SECRET_SENTINEL", str(raised.exception))
                self.assertEqual(len(transport.mutations()), 1)
                self.assertEqual(len(transport.read_times), 1)

    def test_unscoped_or_ambiguous_urls_fail_without_following_or_repurging(self):
        """別範囲・資格情報・曖昧なURLを追跡せず、purgeも再送しない。"""
        unsafe = [self.operation.removeprefix("https://management.azure.com"),
                  self.operation.replace("https:", "http:"),
                  self.operation.replace("management.azure.com", "outside.invalid"),
                  self.operation.replace("management.azure.com", "user:@management.azure.com"),
                  self.operation.replace(self.configuration["subscriptionId"], "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                  self.operation.replace("Microsoft.KeyVault", "Microsoft.Compute"),
                  self.operation.replace(self.configuration["location"], "westus"),
                  self.operation.replace("/operationResults/", "/vaults/"),
                  self.operation.replace("synthetic-operation", "..%2Fother"),
                  self.operation + "&api-version=" + cleanup.API["keyVault"],
                  self.operation + "&access_token=SECRET_SENTINEL",
                  self.operation + "#SECRET_SENTINEL", self.operation + "\nSECRET_SENTINEL"]
        for target in unsafe:
            with self.subTest(target=target):
                transport = PurgeTransport(self.tombstone, response(202, Location=target))
                with self.assertRaises(cleanup.CleanupError) as raised:
                    self.post(transport, sleep=self.clock.sleep, monotonic=self.clock.now)
                self.assertEqual(raised.exception.code, "invalid-purge-operation")
                self.assertEqual(raised.exception.resource_id, self.tombstone["id"])
                self.assertNotIn("SECRET_SENTINEL", str(raised.exception))
                self.assertEqual(len(transport.mutations()), 1)
                self.assertFalse(transport.read_times)

    def test_duplicate_headers_and_invalid_retry_after_stop_safely(self):
        """重複ヘッダーや解釈できない待機指示を無視して進まない。"""
        for headers in ({"Location": self.operation, "location": self.operation},
                        {"Retry-After": "3", "retry-after": "3"},
                        {"Location": ""}, {"Retry-After": "invalid"},
                        {"Retry-After": "Wed, 01 Jan 2031 00:00:00 GMT"}):
            with self.subTest(headers=headers):
                transport = PurgeTransport(self.tombstone, response(202, **headers))
                with self.assertRaises(cleanup.CleanupError):
                    self.post(transport, sleep=self.clock.sleep, monotonic=self.clock.now)
                self.assertEqual(len(transport.mutations()), 1)
                self.assertFalse(transport.read_times)

    def test_retry_after_exceeding_budget_never_shortens_or_polls_early(self):
        """残り予算より長い待機指示を短縮して照会しない。"""
        for hint in ("901", "9" * 5000):
            with self.subTest(length=len(hint)):
                transport = PurgeTransport(self.tombstone, response(202, Location=self.operation, **{"Retry-After": hint}))
                self.assert_code("cleanup-pending", lambda: self.post(transport, sleep=self.clock.sleep, monotonic=self.clock.now))
                self.assertFalse(transport.read_times)
                self.assertEqual(self.clock.sleeps, [])
                self.assertEqual(len(transport.mutations()), 1)

    def test_poll_limit_is_finite_even_if_clock_does_not_advance(self):
        """単調増加時刻の代替が進まなくても、照会回数で終了する。"""
        transport = PurgeTransport(self.tombstone, response(202), tombstones=[200])
        self.assert_code("cleanup-pending", lambda: self.post(transport, sleep=lambda _: None, monotonic=lambda: 0))
        self.assertEqual(len(transport.read_times), 180)
        self.assertEqual(len(transport.mutations()), 1)

    def test_transport_failure_never_replays_purge_or_leaks_signed_url(self):
        """通信断後にpurgeを再送せず、例外へ署名付きURLを残さない。"""
        target = self.operation + "&h=SECRET_SENTINEL"
        transport = PurgeTransport(self.tombstone, response(202, Location=target), [OSError(target)])
        with self.assertRaises(cleanup.CleanupError) as raised:
            self.post(transport, sleep=self.clock.sleep, monotonic=self.clock.now)
        self.assertEqual(raised.exception.code, "transport-failed")
        self.assertNotIn("SECRET_SENTINEL", str(raised.exception))
        self.assertEqual(len(transport.mutations()), 1)

    def test_invalid_timeout_rejects_before_any_azure_request(self):
        """不正な期限では所有状態の読み取りも開始しない。"""
        for value in (True, 0, -1, 1801, float("nan"), float("inf")):
            with self.subTest(value=value):
                transport = FakeTransport()
                self.assert_code("invalid-options", lambda: self.post(transport, purge_timeout_seconds=value))
                self.assertFalse(transport.calls)


if __name__ == "__main__":
    unittest.main()
