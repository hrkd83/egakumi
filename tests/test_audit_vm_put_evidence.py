"""Synthetic PUT evidence, durable intent, and no-replay controls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from operations import apim_phase12_audit_vm as audit
import test_phase12_audit_vm as f


class AuditVmPutEvidenceTests(unittest.TestCase):
    execute = f.AuditVmTransportTests.execute
    mutations = f.AuditVmTransportTests.mutations

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic-put-evidence-")
        self.addCleanup(self.temporary.cleanup)
        self.ctx = f.FakeContext(self.temporary.name)
        self.clock = f.Clock()
        proof = patch.object(audit, "DELETE_IF_MATCH_VERIFIED", False)
        proof.start()
        self.addCleanup(proof.stop)
        trace = patch.object(audit.traceback, "format_exception",
                             side_effect=lambda cls, error, tb, **kwargs:
                                 ["<synthetic-transport-" + cls.__name__ + ">\n"])
        trace.start()
        self.addCleanup(trace.stop)

    def read(self, name):
        return json.loads((Path(self.temporary.name) / name).read_text(encoding="utf-8"))

    def reject_or_unknown(self, response):
        original = self.ctx.request
        def request(method, resource_id, api_version, body=None, headers=None):
            if method == "PUT":
                self.ctx.calls.append((method, resource_id))
                self.ctx.request_headers.append((method, headers))
                return deepcopy(response)
            return original(method, resource_id, api_version, body, headers)
        return patch.object(self.ctx, "request", side_effect=request)

    def test_actual_400_is_saved_before_rejection_and_same_id_never_replays(self):
        response = {"statusCode": 400, "headers": {"X-Synthetic-Receipt": "synthetic-value"},
                    "body": {"error": {"code": "SyntheticBadRequest", "message": "Synthetic detail"}}}
        original = self.ctx.save_private
        observed = []
        def save(path, value, overwrite=False):
            result = original(path, value, overwrite)
            if Path(path).name == "query-put-response.json":
                observed.append(self.read("query-command.json")["putClassification"])
            return result
        with self.reject_or_unknown(response), patch.object(self.ctx, "save_private", side_effect=save):
            with self.assertRaises(audit.AuditVmError) as caught:
                self.execute()
            with self.assertRaises(audit.AuditVmError):
                self.execute()
        self.assertNotIn("Synthetic detail", str(caught.exception))
        self.assertEqual(observed, ["submission-requested"])
        self.assertEqual(self.read("query-put-response.json"), response)
        receipt = self.read("query-command.json")
        self.assertEqual(receipt["putHttpStatus"], 400)
        self.assertEqual(receipt["putClassification"], "http-not-accepted")
        self.assertIs(receipt["putResponseKnown"], True)
        self.assertIs(receipt["putEvidenceSaved"], True)
        self.assertEqual(receipt["putEvidenceFile"], "query-put-response.json")
        self.assertIs(receipt["putAzureAsyncOperationHeaderPresent"], False)
        self.assertIs(receipt["putLocationHeaderPresent"], False)
        self.assertNotIn("headers", receipt)
        self.assertEqual(len(self.mutations("PUT")), 1)
        self.assertEqual(self.mutations("DELETE"), [])
        self.assertEqual(self.ctx.expanded_count, 0)

    def test_accepted_statuses_and_async_receipts_are_saved_before_polling(self):
        for status in (200, 201, 202):
            with self.subTest(status=status), tempfile.TemporaryDirectory(prefix="synthetic-accepted-") as root:
                self.temporary = SimpleNamespace(name=root)
                self.ctx = f.FakeContext(root)
                self.clock = f.Clock()
                original_request = self.ctx.request
                original_expanded = self.ctx.expanded_request
                reply = {}
                def request(method, resource_id, api_version, body=None, headers=None):
                    result = original_request(method, resource_id, api_version, body, headers)
                    if method == "PUT":
                        result["statusCode"] = status
                        result["headers"] = {
                            "AZURE-ASYNCOPERATION": "https://example.invalid/synthetic-operation",
                            "lOcAtIoN": "https://example.invalid/synthetic-location",
                            "X-Synthetic-Raw": "synthetic-header"}
                        reply.update(deepcopy(result))
                    return result
                def expanded(method, path):
                    persisted = json.loads((Path(root) / "query-put-response.json").read_text())
                    receipt = json.loads((Path(root) / "query-command.json").read_text())
                    self.assertEqual(persisted, reply)
                    self.assertEqual(receipt["putClassification"], "http-accepted")
                    self.assertEqual(receipt["putHttpStatus"], status)
                    self.assertIs(receipt["putEvidenceSaved"], True)
                    self.assertIs(receipt["putAzureAsyncOperationHeaderPresent"], True)
                    self.assertIs(receipt["putLocationHeaderPresent"], True)
                    return original_expanded(method, path)
                self.ctx.transport.request = expanded
                with patch.object(self.ctx, "request", side_effect=request):
                    _, _, removed = self.execute()
                self.assertFalse(removed)  # Conditional DELETE remains unverified.
                self.assertEqual(len(self.mutations("PUT")), 1)
                self.assertEqual(self.mutations("DELETE"), [])
                self.assertTrue(all(resource.startswith(f.VM_ID + "/runCommands/") for _, resource in self.ctx.calls))
                self.assertEqual(json.loads((Path(root) / "query-put-response.json").read_text()), reply)
                final = json.loads((Path(root) / "query-command.json").read_text())
                self.assertEqual(final["putClassification"], "http-accepted")
                self.assertNotIn("headers", final)

    def test_transport_exception_saves_outer_and_suppressed_cause_without_http_claims(self):
        original = self.ctx.request
        def request(method, resource_id, api_version, body=None, headers=None):
            if method == "PUT":
                self.ctx.calls.append((method, resource_id))
                outer = audit.OperationError("Synthetic context boundary")
                outer.__context__ = OSError("Synthetic connection closed")
                outer.__suppress_context__ = True
                raise outer
            return original(method, resource_id, api_version, body, headers)
        with patch.object(self.ctx, "request", side_effect=request):
            with self.assertRaises(audit.AuditVmError) as caught:
                self.execute()
            with self.assertRaises(audit.AuditVmError):
                self.execute()
        self.assertNotIn("Synthetic connection closed", str(caught.exception))
        evidence = self.read("query-put-response.json")
        self.assertEqual(evidence["transportException"], {
            "class": "OperationError", "detail": "Synthetic context boundary",
            "traceback": "<synthetic-transport-OperationError>\n"})
        self.assertEqual(evidence["exceptionChain"], [{
            "class": "OSError", "detail": "Synthetic connection closed",
            "traceback": "<synthetic-transport-OSError>\n"}])
        self.assertNotIn("statusCode", evidence)
        self.assertNotIn("headers", evidence)
        receipt = self.read("query-command.json")
        self.assertIs(receipt["putResponseKnown"], False)
        self.assertIs(receipt["putEvidenceSaved"], True)
        self.assertEqual(receipt["putClassification"], "transport-exception")
        self.assertNotIn("putHttpStatus", receipt)
        self.assertNotIn("putAzureAsyncOperationHeaderPresent", receipt)
        self.assertNotIn("putLocationHeaderPresent", receipt)
        self.assertEqual(receipt["status"], "submission-requested")
        self.assertEqual(len(self.mutations("PUT")), 1)
        self.assertEqual(self.ctx.expanded_count, 0)
        self.assertEqual(self.mutations("DELETE"), [])

    def test_evidence_or_receipt_save_failure_stops_with_explicit_uncertainty(self):
        for mode in ("response", "receipt", "both"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix="synthetic-save-failure-") as root:
                self.temporary = SimpleNamespace(name=root)
                self.ctx = f.FakeContext(root)
                self.clock = f.Clock()
                original_save = self.ctx.save_private
                def save(path, value, overwrite=False):
                    name = Path(path).name
                    if ((mode in ("response", "both") and name == "query-put-response.json")
                            or (mode in ("receipt", "both") and name == "query-command.json" and overwrite)):
                        raise OSError("Synthetic protected write failure")
                    return original_save(path, value, overwrite)
                with patch.object(self.ctx, "save_private", side_effect=save):
                    with self.assertRaises(audit.AuditVmError) as caught:
                        self.execute()
                    with self.assertRaises(audit.AuditVmError):
                        self.execute()
                self.assertNotIn("Synthetic protected write failure", str(caught.exception))
                evidence_saved = mode == "receipt"
                receipt_saved = mode == "response"
                self.assertIs(caught.exception.putEvidenceSaved, evidence_saved)
                self.assertIs(caught.exception.putReceiptSaved, receipt_saved)
                self.assertEqual((Path(root) / "query-put-response.json").exists(), evidence_saved)
                receipt = json.loads((Path(root) / "query-command.json").read_text())
                self.assertEqual(receipt["status"], "submission-requested")
                self.assertIs(receipt["putEvidenceSaved"], False)
                if receipt_saved:
                    self.assertEqual(receipt["putClassification"], "evidence-save-failed")
                    self.assertIs(receipt["putResponseKnown"], True)
                    self.assertEqual(receipt["putHttpStatus"], 201)
                self.assertFalse(receipt["temporaryCommandRemoved"])
                self.assertEqual(len(self.mutations("PUT")), 1)
                self.assertEqual(self.ctx.expanded_count, 0)
                self.assertEqual(self.mutations("DELETE"), [])

    def test_malformed_responses_never_create_http_claims_and_orphan_evidence_blocks_put(self):
        for response in ({"statusCode": True}, {"statusCode": "202"}, {"statusCode": 999},
                         {"body": {}}, None, ["synthetic-response"]):
            with self.subTest(response=response), tempfile.TemporaryDirectory(prefix="synthetic-invalid-put-") as root:
                self.temporary = SimpleNamespace(name=root)
                self.ctx = f.FakeContext(root)
                self.clock = f.Clock()
                with self.reject_or_unknown(response):
                    with self.assertRaises(audit.AuditVmError):
                        self.execute()
                evidence = json.loads((Path(root) / "query-put-response.json").read_text())
                receipt = json.loads((Path(root) / "query-command.json").read_text())
                self.assertEqual(evidence, response)
                self.assertIs(receipt["putResponseKnown"], False)
                self.assertIs(receipt["putEvidenceSaved"], True)
                self.assertEqual(receipt["putClassification"], "http-response-invalid")
                self.assertNotIn("putHttpStatus", receipt)
                self.assertNotIn("putAzureAsyncOperationHeaderPresent", receipt)
                self.assertEqual(len(self.mutations("PUT")), 1)
                self.assertEqual(self.ctx.expanded_count, 0)
        with tempfile.TemporaryDirectory(prefix="synthetic-orphan-") as root:
            self.temporary = SimpleNamespace(name=root)
            self.ctx = f.FakeContext(root)
            self.ctx.save_private(Path(root) / "query-put-response.json", {"statusCode": 400})
            with self.assertRaises(audit.AuditVmError):
                self.execute()
            self.assertEqual(self.mutations("PUT"), [])
            self.assertEqual(self.ctx.calls, [])


if __name__ == "__main__":
    unittest.main()
