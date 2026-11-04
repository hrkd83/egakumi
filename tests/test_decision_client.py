"""実サービスへ接続せず、Decision の型検査と1要求だけの安全な試験を確認する。"""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/integration_client.py").is_file())
SCRIPTS = CORE / "scripts"
sys.path.insert(0, str(SCRIPTS))
STAGED_CLIENT = Path(__file__).with_name("decision_client.py")
spec = importlib.util.spec_from_file_location("decision_client_staged", STAGED_CLIENT if STAGED_CLIENT.is_file() else SCRIPTS / "decision_client.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
import httpx2


def response():
    """本文の機密を模した未知項目を含む、計算上整合する合成応答を返す。"""
    return {"model": "microsoft-decision-1", "private": "PRIVATE-MODEL-BODY",
            "answers": {"refund": {"type": "noul", "noul": 0.9},
                        "team": {"type": "choice", "choice": "billing", "probabilities": {"billing": 0.9, "engineering": 0.05, "support": 0.05}},
                        "severity": {"type": "score", "score": 1.1, "probabilities": {"0": 0.1, "1": 0.7, "2": 0.2}}},
            "usage": {"input_tokens": 120, "output_tokens": 0, "private": "PRIVATE-USAGE"}}


class DecisionTests(unittest.TestCase):
    def test_fixed_request_combines_three_types_and_omits_generation_parameters(self):
        body = probe.body_for("normal", probe.MODEL)
        self.assertEqual({item["type"] for item in body["questions"].values()}, {"noul", "choice", "score"})
        self.assertEqual(set(body), {"model", "state", "questions"})
        self.assertLess(len(probe.encode(body)), 1024)
        self.assertEqual(len(probe.encode(probe.body_for("oversized", probe.MODEL))), 8193)

    def test_real_schema_and_model_case_are_checked_without_response_text(self):
        result = probe.inspect_decision(response())
        self.assertTrue(result["shapeValid"] and result["expectedChoiceObserved"])
        self.assertEqual(result["usage"], {"input_tokens": 120, "output_tokens": 0})
        self.assertNotIn("PRIVATE", json.dumps(result))
        value = response(); value["model"] = "another-model"
        self.assertFalse(probe.inspect_decision(value)["shapeValid"])

    def test_malformed_probabilities_names_types_and_weighted_score_fail(self):
        for change in (
            lambda v: v["answers"]["refund"].update(noul=True),
            lambda v: v["answers"]["refund"].update(noul=float("nan")),
            lambda v: v["answers"]["team"]["probabilities"].update(billing=1.0),
            lambda v: v["answers"]["team"].update(choice="engineering"),
            lambda v: v["answers"]["team"].update(confidence=1.5),
            lambda v: v["answers"]["severity"].update(score=2.0),
            lambda v: v["answers"].update(extra={"type": "noul", "noul": 1}),
            lambda v: v["answers"].update(refund=[]),
        ):
            value = response(); change(value)
            self.assertFalse(probe.inspect_decision(value)["shapeValid"])

    def test_plan_does_not_read_token_or_send_requests(self):
        with patch.object(sys, "argv", ["decision", "--url", "https://gateway.invalid/decision", "--model", probe.MODEL,
                                        "--token-file", "/not/read"]), patch.object(probe, "auth_headers", side_effect=AssertionError("No token read")), patch("builtins.print"):
            self.assertEqual(probe.main(), 0)

    def test_evidence_writer_rejects_existing_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "evidence.json"
            probe.write_new(target, {"succeeded": False})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError): probe.write_new(target, {})
            link = Path(directory) / "link"; link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symbolic links"): probe.write_new(link, {})

    def test_unwritable_or_existing_output_is_detected_before_token_read(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "evidence.json"; target.write_text("keep")
            with patch.object(sys, "argv", ["decision", "--url", "https://gateway.invalid/decision", "--model", probe.MODEL,
                                            "--execute", "--token-file", "/not/read", "--output", str(target)]), patch.object(probe, "auth_headers", side_effect=AssertionError("No token read")):
                with self.assertRaises(ValueError): probe.main()
            self.assertEqual(target.read_text(), "keep")


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_has_one_call_no_secret_and_audit_stays_pending(self):
        def handler(request):
            self.assertEqual(request.headers["authorization"], "Bearer PRIVATE-TOKEN")
            self.assertEqual(len(json.loads(request.content)["questions"]), 3)
            return httpx2.Response(200, json=response(), headers={"apim-request-id": "11111111-1111-4111-8111-111111111111"})
        result = await probe.probe("https://gateway.invalid/decision", "decision-1", "normal", {"Authorization": "Bearer PRIVATE-TOKEN"}, transport=httpx2.MockTransport(handler))
        self.assertTrue(result["succeeded"])
        self.assertEqual(result["httpAttempts"], 1)
        self.assertEqual(result["auditVerification"], "pending")
        self.assertEqual(result["backendInvocationVerification"], "pending")
        self.assertNotIn("PRIVATE", json.dumps(result))

    async def test_deny_cases_require_correct_status_correlation_and_size_code(self):
        for case, status, code in (("anonymous", 401, None), ("unregistered", 403, None), ("disallowed-model", 403, None),
                                   ("missing-model", 403, None), ("oversized", 413, "REQUEST_TOO_LARGE"), ("invalid-question", 422, None)):
            with self.subTest(case=case):
                def handler(request):
                    self.assertEqual("authorization" in request.headers, case != "anonymous")
                    return httpx2.Response(status, json={"error": {"code": code, "message": "PRIVATE-ERROR"}},
                                           headers={"apim-request-id": "11111111-1111-4111-8111-111111111111"})
                result = await probe.probe("https://gateway.invalid/decision", probe.MODEL, case, {"Authorization": "Bearer PRIVATE-TOKEN"}, transport=httpx2.MockTransport(handler))
                self.assertTrue(result["responseChecksPassed"])
                self.assertNotIn("PRIVATE", json.dumps(result))
        result = await probe.probe("https://gateway.invalid/decision", probe.MODEL, "invalid-question", {},
                                   transport=httpx2.MockTransport(lambda request: httpx2.Response(403)))
        self.assertFalse(result["responseChecksPassed"])

    async def test_redirect_or_429_does_not_retry_or_print_upstream_body(self):
        for status in (302, 429):
            count = 0
            def handler(request):
                nonlocal count; count += 1
                return httpx2.Response(status, json={"error": {"message": "PRIVATE-BODY"}}, headers={"location": "https://other.invalid"})
            result = await probe.probe("https://gateway.invalid/decision", probe.MODEL, "normal", {}, transport=httpx2.MockTransport(handler))
            self.assertEqual(count, 1)
            self.assertFalse(result["succeeded"])
            self.assertNotIn("PRIVATE", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
