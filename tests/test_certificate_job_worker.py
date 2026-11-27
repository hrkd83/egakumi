"""Offline tests for the certificate job process and its durable publication gate."""
from importlib import util
from pathlib import Path
import json
import shutil
import subprocess
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = util.spec_from_file_location("certificate_job_worker", ROOT / "apps/certificate-jobs/job.py")
worker = util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class CertificateJobWorkerTests(unittest.TestCase):
    def setUp(self):
        self.env = {"CERTIFICATE_MODE": "portal", "CERTIFICATE_ACTION": "retire",
                    "CERTIFICATE_ATTEMPT_ID": "a" * 32, "CERTIFICATE_CONFIG_JSON": "{}"}
        self.retired = {"retired": True, "keyId": "synthetic-key-reference"}
        self.storage = MagicMock()
        self.storage.read_json.return_value = None
        self.lease = MagicMock()
        self.receipt_path = "receipts/" + self.env["CERTIFICATE_ATTEMPT_ID"] + ".json"

    def run_job(self):
        with patch.object(worker, "Storage", return_value=self.storage), \
                patch.object(worker, "Lease", return_value=self.lease), \
                patch.object(worker, "run_worker", return_value=self.retired) as run_worker:
            self.run_worker = run_worker
            return worker.run(self.env)

    def test_receipt_is_bound_to_attempt_mode_action_and_verified_lease(self):
        self.assertEqual(self.run_job(), "succeeded")
        self.run_worker.assert_called_once_with(self.lease.__enter__.return_value, "portal", "retire")
        self.lease.__enter__.return_value.verify.assert_called_once()
        self.storage.write_json.assert_called_once_with(self.receipt_path, {
            "attemptId": self.env["CERTIFICATE_ATTEMPT_ID"], "mode": "portal", "action": "retire",
            "result": self.retired,
        }, new=True)

    def test_receipt_is_not_written_when_lease_was_lost(self):
        self.lease.__enter__.return_value.verify.side_effect = worker.JobError("lease-lost")
        with self.assertRaisesRegex(worker.JobError, "lease-lost"):
            self.run_job()
        self.storage.write_json.assert_not_called()

    def test_same_attempt_reuses_receipt_and_conflicting_receipt_stops(self):
        self.storage.read_json.return_value = {
            "attemptId": self.env["CERTIFICATE_ATTEMPT_ID"], "mode": "portal", "action": "retire", "result": self.retired,
        }
        self.assertEqual(self.run_job(), "already-succeeded")
        self.run_worker.assert_not_called()
        self.storage.read_json.return_value["action"] = "prepare"
        with self.assertRaisesRegex(worker.JobError, "unrelated-existing-receipt"):
            self.run_job()
        self.run_worker.assert_not_called()

    def test_scheduled_non_divisor_renewal_respects_durable_check_interval(self):
        self.env = {"CERTIFICATE_MODE": "tls", "CERTIFICATE_ACTION": "renew",
                    "CERTIFICATE_CONFIG_JSON": json.dumps({"renewIntervalHours": 5})}
        self.storage.read_json.side_effect = [None, {"checkedAt": 100}]
        with patch.object(worker.time, "time", return_value=110):
            self.assertEqual(self.run_job(), "not-due")
        self.run_worker.assert_not_called()
        self.storage.write_json.assert_not_called()

    def test_finite_lease_is_verified_and_released_with_exact_identifier(self):
        lease = worker.Lease(self.storage, "locks/tls.lock")
        self.storage.blob.return_value = ({"x-ms-lease-id": lease.identifier}, b"")
        with patch.object(worker.threading, "Thread") as heartbeat:
            with lease:
                lease.verify()
        heartbeat.return_value.start.assert_called_once()
        self.assertTrue(lease.stop.is_set())
        self.storage.write.assert_called_once_with("locks/tls.lock", b"", new=True)
        acquire, renew, release = self.storage.blob.call_args_list
        self.assertEqual(acquire.args[2], {
            "x-ms-lease-action": "acquire", "x-ms-lease-duration": "60",
            "x-ms-proposed-lease-id": lease.identifier,
        })
        self.assertEqual(renew.args[2], {"x-ms-lease-action": "renew", "x-ms-lease-id": lease.identifier})
        self.assertEqual(release.args[2], {"x-ms-lease-action": "release", "x-ms-lease-id": lease.identifier})

    def test_heartbeat_failure_fences_later_receipt_publication(self):
        lease = worker.Lease(self.storage, "locks/tls.lock")
        with patch.object(lease.stop, "wait", return_value=False), \
                patch.object(lease, "change", side_effect=worker.HttpFailure(412)):
            lease.renew()
            self.assertTrue(lease.lost.is_set())
            with self.assertRaisesRegex(worker.JobError, "lease-lost"):
                lease.verify()

    def test_token_endpoint_is_local_and_redirect_free(self):
        self.assertEqual(worker.local_identity_url("http://localhost:10000/msi/token"),
                         "http://localhost:10000/msi/token")
        with self.assertRaises((worker.JobError, ValueError)):
            worker.local_identity_url("http://example.com/identity")
        with self.assertRaises(worker.JobError):
            worker.local_identity_url("http://localhost/token?resource=other")

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required to exercise ACME order logic")
    def test_acme_order_intent_precedes_creation_and_uncertain_order_blocks_new_request(self):
        process = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
             str(ROOT / "tests/certificate-job-order-offline.ps1")],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)


if __name__ == "__main__":
    unittest.main()
