"""合成ARMで独立readbackの並行化と、全照合後に戻る境界を検証する。"""
import copy
import threading
import unittest

from test_entra_publication import APIM, response
import entra_publication as publication


class SnapshotTransport:
    """最初のreadbackを保留し、pending状態と要求内容を観測する。"""
    def __init__(self, snapshot, *, concurrent=False, drift=None, failure=False):
        self.supports_concurrent_reads = concurrent
        self.responses = {entry[0]: copy.deepcopy(entry[2]) for entry in snapshot.values()}
        self.drift, self.failure = drift, failure
        self.lock = threading.Lock()
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls, self.completed = [], []
        self.active, self.maximum = 0, 0

    def request(self, method, path, body=None, headers=None):
        with self.lock:
            self.calls.append((method, path, copy.deepcopy(body), copy.deepcopy(headers)))
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            if self.active >= (8 if self.supports_concurrent_reads is True else 1):
                self.started.set()
        try:
            if not self.release.wait(5):
                raise TimeoutError("Synthetic readback was not released")
            if self.failure and path == next(iter(self.responses)):
                raise RuntimeError("Synthetic transport failed")
            observed = copy.deepcopy(self.responses[path])
            if self.drift is not None and path == next(iter(self.responses)):
                self.drift(observed)
            return observed
        finally:
            with self.lock:
                self.completed.append(path)
                self.active -= 1


def snapshot_fixture():
    snapshot = {}
    for index in range(24):
        resource_id = APIM + "/namedValues/synthetic-" + str(index)
        path = resource_id + "?api-version=" + publication.APIM_API
        headers = {"Accept": "application/json", "X-Synthetic-Read": str(index)}
        expected = response(resource_id, {"value": str(index)}, etag='"synthetic-' + str(index) + '"')
        snapshot[resource_id] = (path, headers, expected)
    # Mutable collection representations are also checked without normalization.
    key = next(iter(snapshot))
    snapshot[key][2]["body"]["value"] = [{"id": APIM + "/apis/synthetic-a"}]
    return snapshot


class SnapshotConcurrencyTests(unittest.TestCase):
    def run_held_readback(self, *, concurrent=True, drift=None, failure=False):
        snapshot = snapshot_fixture()
        transport = SnapshotTransport(snapshot, concurrent=concurrent, drift=drift, failure=failure)
        result, finished = {}, threading.Event()

        def caller():
            try:
                publication.verify_snapshot(snapshot, transport)
                result["success"] = True
            except Exception as error:
                result["error"] = error
            finally:
                result["activeAtReturn"] = transport.active
                result["completedAtReturn"] = len(transport.completed)
                finished.set()

        thread = threading.Thread(target=caller, daemon=True)
        thread.start()
        try:
            self.assertTrue(transport.started.wait(5), "Readback did not reach its first batch")
            self.assertFalse(finished.is_set(), "Readback returned while requests were pending")
            self.assertEqual(transport.active, 8 if concurrent is True else 1)
            self.assertTrue(all(method == "GET" and body is None for method, _, body, _ in transport.calls))
        finally:
            transport.release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive(), "Readback did not join all submitted checks")
        self.assertTrue(finished.is_set())
        self.assertEqual(result["activeAtReturn"], 0)
        self.assertEqual(result["completedAtReturn"], len(snapshot))
        self.assertEqual(sorted(transport.completed), sorted(entry[0] for entry in snapshot.values()))
        self.assertEqual(sorted((path, headers) for _, path, _, headers in transport.calls),
                         sorted((path, headers) for path, headers, _ in snapshot.values()))
        self.assertTrue(all(method == "GET" and body is None for method, _, body, _ in transport.calls))
        return result, transport

    def test_explicit_concurrent_support_checks_every_exact_request_with_at_most_eight_workers(self):
        result, transport = self.run_held_readback()
        self.assertTrue(result["success"])
        self.assertEqual(transport.maximum, 8)

    def test_absent_or_non_boolean_support_keeps_sequential_reads(self):
        for support in (False, None, "true", 1):
            with self.subTest(support=support):
                result, transport = self.run_held_readback(concurrent=support)
                self.assertTrue(result["success"])
                self.assertEqual(transport.maximum, 1)

    def test_parallel_drift_rejection_joins_all_other_checks_before_propagating(self):
        mutations = {
            "etag": lambda item: item["body"].update(etag='"changed"'),
            "properties": lambda item: item["body"]["properties"].update(value="changed"),
            "collection": lambda item: item["body"].update(value=[{"id": APIM + "/apis/synthetic-b"}]),
            "status": lambda item: item.update(statusCode=404),
        }
        for label, mutation in mutations.items():
            with self.subTest(drift=label):
                result, transport = self.run_held_readback(drift=mutation)
                self.assertIsInstance(result["error"], ValueError)
                self.assertIn("drifted", str(result["error"]))
                self.assertLessEqual(transport.maximum, 8)

    def test_parallel_transport_exception_joins_other_reads_before_propagating(self):
        result, transport = self.run_held_readback(failure=True)
        self.assertIsInstance(result["error"], RuntimeError)
        self.assertLessEqual(transport.maximum, 8)


if __name__ == "__main__":
    unittest.main()
