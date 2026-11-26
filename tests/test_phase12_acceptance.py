"""合成HTTPだけで、反映ゲート・返却Item・保存前の応答要約を確認する。"""
import copy
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import threading
import time
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("phase12_acceptance", ROOT / "scripts/operations/apim_phase12_acceptance.py")
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


def headers(content_type="application/json", **extra):
    result = Message()
    result["Content-Type"] = content_type
    for key, value in extra.items():
        result[key.replace("_", "-")] = value
    return result


class FakeSocket:
    def __init__(self):
        self.stopped = threading.Event()

    def settimeout(self, seconds):
        self.timeout = seconds

    def shutdown(self, how):
        self.stopped.set()

    def close(self):
        self.stopped.set()


class FakeResponse:
    def __init__(self, payload=b"", status=200, response_headers=None):
        self.status, self.headers = status, response_headers or headers()
        self.content = io.BytesIO(payload)

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, limit):
        return self.content.read(limit)

    def readline(self, limit):
        return self.content.readline(limit)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, response):
        self.sock, self.response = FakeSocket(), response
        self.requests, self.finished = [], threading.Event()

    def connect(self):
        pass

    def request(self, method, target, **kwargs):
        self.requests.append((method, target, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.finished.set()


class PublicationGateTests(unittest.TestCase):
    def fixture(self):
        paths = {"source": ROOT, "runtime": ROOT / "synthetic-runtime", "config": ROOT / "synthetic-runtime/.local/test/params.local.json"}
        current = {"input": "synthetic-input", "coreState": "synthetic-core", "checkpoint": "synthetic-checkpoint"}
        gate = {"sourceRoot": str(paths["source"]), "runtimeRoot": str(paths["runtime"]), "configFile": str(paths["config"]),
                "publicFinalModuleSha256": "synthetic-module", "stageA": {"status": "verified", "managementReadbackVerified": True},
                "stageB": {"status": "verified", "module500Restored": True, "apiPublicationCompleted": True,
                           "azure500DefinitionReadbackVerified": True, "currentCheckpointMatched": True, "endSnapshot": current}}
        waf = {"scopeGatewayOnly": True, "managementReadbackVerified": True, "inputCoreAndCheckpointUnchanged": True}
        return paths, current, gate, waf

    def test_pending_second_stage_stale_checkpoint_or_non_gateway_waf_cannot_open_gate(self):
        paths, current, original, original_waf = self.fixture()
        mutations = [
            lambda g, w: g["stageB"].update(status="pending"),
            lambda g, w: g["stageB"].update(azure500DefinitionReadbackVerified=False),
            lambda g, w: g["stageA"].update(managementReadbackVerified=False),
            lambda g, w: g["stageB"].update(endSnapshot={**current, "checkpoint": "stale"}),
            lambda g, w: w.update(scopeGatewayOnly=False),
            lambda g, w: g.update(runtimeRoot=str(ROOT / "another-synthetic-runtime")),
            lambda g, w: g.update(publicFinalModuleSha256="changed-module"),
        ]
        for change in mutations:
            gate, waf = copy.deepcopy(original), copy.deepcopy(original_waf)
            change(gate, waf)
            with self.assertRaises(ValueError):
                acceptance.require_gates(gate, waf, current, **paths, module_hash="synthetic-module")
        acceptance.require_gates(original, original_waf, current, **paths, module_hash="synthetic-module")

    def test_invalid_bearer_routes_do_not_depend_on_a_model_or_inference_api(self):
        configuration = {"gateway": {"publicHostName": "gateway.example.com"},
                         "apiDefinitions": [{"name": "pii-v1"}]}
        state = {"outputs": {"endpoints": {"public": "https://gateway.example.com/"}}}
        origin, definitions, key, model, alias, urls = acceptance.selected_routes(configuration, state, include_models=False)
        self.assertEqual(origin, "https://gateway.example.com")
        self.assertEqual(set(definitions), {"pii-v1"})
        self.assertIsNone(model)
        self.assertEqual(urls, {})


class FunctionContinuationTests(unittest.TestCase):
    def reply(self):
        return {"status": "completed", "error": None, "output": [
            {"id": "synthetic-reasoning-id", "type": "reasoning", "encrypted_content": "synthetic-encrypted-value", "summary": [],
             "future_field": {"nested": ["keep", {"date": acceptance.DATE_METADATA["probe_iso_fraction"]}]}},
            {"id": "synthetic-call-item-id", "type": "function_call", "name": "echo_nonce", "call_id": "synthetic-call-id",
             "arguments": json.dumps({"nonce": acceptance.NONCE}), "status": "completed"}]}

    def test_continuation_keeps_complete_items_without_stripping_ids_dates_or_unknown_fields(self):
        original = [{"role": "user", "content": "Synthetic question."}]
        response = self.reply()
        before = copy.deepcopy(response)
        result = acceptance.continuation(original, response)
        self.assertEqual(result[:1], original)
        self.assertEqual(result[1:-1], response["output"])
        self.assertEqual(result[-1]["call_id"], response["output"][1]["call_id"])
        self.assertEqual(json.loads(result[-1]["output"]), {"nonce": acceptance.NONCE})
        result[1]["summary"].append("changed only in the new request")
        self.assertEqual(response, before)

    def test_undeclared_function_changed_nonce_or_incomplete_response_is_refused(self):
        for change in (lambda r: r.update(status="incomplete"),
                       lambda r: r["output"][1].update(name="unapproved_function"),
                       lambda r: r["output"][1].update(arguments='{"nonce":"different"}'),
                       lambda r: r["output"][1].update(call_id=None)):
            response = self.reply()
            change(response)
            with self.assertRaises(ValueError):
                acceptance.continuation([], response)

    def test_malformed_text_shapes_never_count_as_the_nonce_answer(self):
        for value in (None, {"output": None}, {"output": [None]}, {"output": [{"content": None}]},
                      {"output": [{"content": [{"type": "output_text", "text": None}]}]}):
            self.assertFalse(acceptance.response_text_contains(value, acceptance.NONCE))

    def test_completed_function_json_cannot_continue_after_a_transport_failure(self):
        with self.assertRaises(ValueError):
            acceptance.continuation([], self.reply(), transport_result={"status": 200, "failureClass": "TimeoutError"})


class DirectHTTPSProbeTests(unittest.TestCase):
    def exchange(self, connection, **kwargs):
        with mock.patch.object(acceptance.http.client, "HTTPSConnection", return_value=connection) as construct, \
                mock.patch.object(acceptance, "verified_tls_context", return_value=object()) as tls:
            result, value = acceptance.http_exchange("https://gateway.example.com/models/responses?synthetic=value", "POST",
                {"metadata": acceptance.DATE_METADATA}, {"api-key": "<SYNTHETIC_APIM_KEY>"}, **kwargs)
        tls.assert_called_once_with()
        self.assertIs(construct.call_args.kwargs["context"], tls.return_value)
        return result, value, construct

    def test_redirect_is_not_followed_and_wire_strings_are_not_normalized(self):
        connection = FakeConnection(FakeResponse(status=302, response_headers=headers(Location="https://other.example.com/")))
        result, value, construct = self.exchange(connection)
        self.assertEqual(len(connection.requests), 1)
        self.assertEqual(connection.requests[0][1], "/models/responses?synthetic=value")
        self.assertEqual(json.loads(connection.requests[0][2]["body"])["metadata"], acceptance.DATE_METADATA)
        self.assertEqual(construct.call_args.args[0], "gateway.example.com")
        self.assertEqual(result["status"], 302)
        self.assertFalse(result["normalTerminal"])
        self.assertNotIn("<SYNTHETIC_APIM_KEY>", json.dumps(result))

    def test_json_keeps_generated_text_in_memory_and_out_of_summary(self):
        text = "SYNTHETIC-GENERATED-CONTENT"
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": text}}],
                   "usage": {"prompt_tokens": 3, "completion_tokens": 1}, "store": False}
        connection = FakeConnection(FakeResponse(json.dumps(payload).encode()))
        result, value, _ = self.exchange(connection, kind="chat")
        self.assertTrue(result["normalTerminal"])
        self.assertEqual(value, payload)
        self.assertEqual(result["storeObservation"], "false")
        self.assertNotIn(text, json.dumps(result))
        self.assertTrue(connection.finished.is_set())

    def test_sse_response_uses_existing_terminal_parser_without_storing_content(self):
        text = "SYNTHETIC-STREAM-CONTENT"
        payload = ('data: ' + json.dumps({"choices": [{"delta": {"content": text}}]}) + '\n\n'
                   'data: ' + json.dumps({"choices": [{"finish_reason": "stop"}]}) + '\n\n'
                   'data: [DONE]\n\n').encode()
        connection = FakeConnection(FakeResponse(payload, response_headers=headers("text/event-stream")))
        result, value, _ = self.exchange(connection, kind="chat", stream=True)
        self.assertTrue(result["normalTerminal"])
        self.assertTrue(result["outputObserved"])
        self.assertIsNone(value)
        self.assertNotIn(text, json.dumps(result))

    def test_response_size_limit_fails_without_persisting_partial_body(self):
        connection = FakeConnection(FakeResponse(b"SYNTHETIC-OVERSIZED-BODY" * 20))
        with mock.patch.object(acceptance, "BODY_LIMIT", 200):
            result, value, _ = self.exchange(connection)
        self.assertEqual(result["failureClass"], "ValueError")
        self.assertFalse(result["normalTerminal"])
        self.assertIsNone(value)
        self.assertNotIn("SYNTHETIC-OVERSIZED-BODY", json.dumps(result))

    def test_unauthenticated_contract_headers_and_empty_body_are_observed(self):
        request_id = str(uuid.UUID(int=7))
        connection = FakeConnection(FakeResponse(status=401, response_headers=headers(
            WWW_Authenticate="Bearer", Cache_Control="no-store", apim_request_id=request_id)))
        result, value, _ = self.exchange(connection)
        self.assertTrue(result["bodyEmpty"])
        self.assertTrue(result["bearerChallenge"])
        self.assertTrue(result["noStore"])
        self.assertEqual(result["correlationIds"]["apim-request-id"], request_id)
        self.assertFalse(result["normalTerminal"])

    def test_missing_verified_tls_stops_before_creating_a_connection(self):
        with mock.patch.object(acceptance.http.client, "HTTPSConnection") as construct, \
                mock.patch.object(acceptance, "verified_tls_context", side_effect=RuntimeError("SYNTHETIC-TLS-ERROR")):
            result, value = acceptance.http_exchange("https://gateway.example.com/responses", "POST", {},
                {"Authorization": "Bearer <SYNTHETIC_TOKEN>"})
        construct.assert_not_called()
        self.assertEqual(result["httpAttempts"], 0)
        self.assertFalse(result["normalTerminal"])
        self.assertIsNone(value)
        self.assertNotIn("SYNTHETIC-TLS-ERROR", json.dumps(result))

    def test_valid_json_with_truncated_declared_length_is_not_complete(self):
        body = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "SYNTHETIC-PARTIAL-REPLY"}}]}).encode()
        wire = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body) + 10).encode() + b"\r\n\r\n" + body)

        class MemorySocket:
            def makefile(self, mode):
                return io.BytesIO(wire)

        response = acceptance.http.client.HTTPResponse(MemorySocket())
        response.begin()
        connection = FakeConnection(response)
        result, value, _ = self.exchange(connection, kind="chat")
        self.assertEqual(result["failureClass"], "IncompleteRead")
        self.assertFalse(result["normalTerminal"])
        self.assertIsNone(value)
        self.assertNotIn("SYNTHETIC-PARTIAL-REPLY", json.dumps(result))

    def test_trickled_unterminated_line_is_bounded_and_socket_is_stopped(self):
        connection = FakeConnection(FakeResponse(response_headers=headers("text/event-stream")))

        def blocked_read(limit):
            connection.sock.stopped.wait(1)
            raise OSError("synthetic-untrusted-peer-error")

        connection.response.readline = blocked_read
        started = time.monotonic()
        result, value, _ = self.exchange(connection, kind="responses", stream=True, timeout=0.1)
        self.assertLess(time.monotonic() - started, 0.7)
        self.assertEqual(result["failureClass"], "TimeoutError")
        self.assertTrue(connection.sock.stopped.is_set())
        self.assertTrue(connection.finished.wait(1))
        self.assertEqual(len(connection.requests), 1)
        self.assertIsNone(value)
        self.assertNotIn("synthetic-untrusted-peer-error", json.dumps(result))

    def test_late_connect_after_timeout_cannot_send_the_model_request(self):
        connection = FakeConnection(FakeResponse())
        release = threading.Event()
        connection.connect = lambda: release.wait(1)
        result, value, _ = self.exchange(connection, timeout=0.1)
        self.assertEqual(result["failureClass"], "TimeoutError")
        self.assertEqual(result["httpAttempts"], 0)
        release.set()
        self.assertTrue(connection.finished.wait(1))
        self.assertEqual(connection.requests, [])


if __name__ == "__main__":
    unittest.main()
