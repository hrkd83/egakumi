"""合成のtransportと保護contextで受入ゲート・完了判定・記録境界を確認する。"""
import asyncio
import copy
from contextlib import contextmanager, ExitStack
from email.message import Message
import hashlib
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from operations import apim_phase12_transports as transports

SYNTHETIC_KEY = "<SYNTHETIC_APIM_KEY>"
SYNTHETIC_BODY = "SYNTHETIC-UNTRUSTED-SERVICE-CONTENT"


def configuration():
    """公開のAPI宣言へ実環境と独立したorigin/backendを設定する。"""
    definitions = []
    models = set()
    for name in transports.WS_APIS:
        api = json.loads((ROOT / "apps/gateway/apim/apis" / (name + ".json")).read_text("utf-8"))
        api["backendUrl"] = "wss://backend.example.com/"
        query = api["websocket"].get("queryParameters", {})
        for key, value in query.items():
            if value.startswith("__"):
                query[key] = transports.PUBLIC_BINDINGS[value.strip("_")]
        models.update(api["websocket"].get("allowedModels", []))
        models.update(api["websocket"].get("session", {}).get("models", []))
        definitions.append(api)
    definitions.append({"name": "pii-v1", "path": "tools/pii/v1", "subscriptionKeyHeader": "api-key",
        "operations": [{"name": "pii_redact_text", "method": "POST"}],
        "mcp": {"name": "pii-v1-mcp", "path": "tools/pii/mcp/v1", "operationNames": ["pii_redact_text"]}})
    return {"gateway": {"publicHostName": "gateway.example.com", "authentication": {"subscriptionKeyHeader": "api-key"}},
        "services": {"modelDeployments": [{"name": name} for name in sorted(models)]}, "apiDefinitions": definitions}


def state():
    return {"outputs": {"endpoints": {"public": "https://gateway.example.com/"}}}


def by_id(config, name):
    return next(item for item in config["apiDefinitions"] if item["name"] == name)


def socket_case(family="realtime"):
    return next(case for case in transports.websocket_plan(configuration(), state()) if case.family == family)


def service_event(kind, model=None):
    value = {"type": kind, "session": {"id": SYNTHETIC_BODY, "instructions": SYNTHETIC_BODY}}
    if model is not None:
        value["session"]["model"] = model
    return json.dumps(value)


class FakeSocket:
    def __init__(self, events=(), status=101, close_code=1000):
        headers = Message()
        headers["apim-request-id"] = str(uuid.UUID(int=5))
        headers["Authorization"] = SYNTHETIC_KEY
        self.response = types.SimpleNamespace(status_code=status, headers=headers)
        self.events = list(events)
        self.sent = []
        self.close_code = close_code
        self.close_calls = []
        self.waited = False

    async def send(self, value):
        self.sent.append(json.loads(value))

    async def recv(self):
        if self.events:
            value = self.events.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        await asyncio.sleep(3600)

    async def close(self, **kwargs):
        self.close_calls.append(kwargs)

    async def wait_closed(self):
        self.waited = True


class FakeConnector:
    def __init__(self, socket, error=None):
        self.socket, self.error = socket, error
        self.calls = []
        self.exits = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self.socket

    async def __aexit__(self, kind, value, traceback):
        self.exits.append(kind)
        if not self.socket.close_calls:
            await self.socket.close(code=1000, reason="")
        await self.socket.wait_closed()


class PlanTests(unittest.TestCase):
    def test_exact_six_inventory_uses_declared_paths_and_model_location(self):
        config = configuration()
        cases = transports.websocket_plan(config, state())
        self.assertEqual({case.api_id for case in cases}, set(transports.WS_APIS))
        self.assertEqual(sum(case.exclusion is not None for case in cases), 2)
        for case in cases:
            api = by_id(config, case.api_id)
            parsed = urlsplit(case.url)
            self.assertEqual(parsed.netloc, "gateway.example.com")
            self.assertEqual(parsed.path, "/" + api["path"] + "/")
            query = parse_qs(parsed.query)
            if case.family in ("live", "transcription"):
                self.assertNotIn("model", query)
                self.assertIsNotNone(case.start_event)
            elif case.family == "voice-live":
                self.assertEqual(query["model"], [transports.PUBLIC_BINDINGS["VOICE_LIVE_MODEL"]])
            elif case.family == "realtime":
                self.assertEqual(query["model"], [api["websocket"]["defaultModel"]])

    def test_wrong_origin_inventory_operation_header_and_model_are_rejected(self):
        changes = (
            lambda c: c["gateway"].update(publicHostName="other.example.com"),
            lambda c: c["apiDefinitions"].pop(0),
            lambda c: c["apiDefinitions"].append(copy.deepcopy(c["apiDefinitions"][0])),
            lambda c: by_id(c, "openai-live-ws-v1")["operations"][0].update(method="POST"),
            lambda c: by_id(c, "openai-live-ws-v1").update(subscriptionKeyHeader="another-key"),
            lambda c: by_id(c, "openai-live-ws-v1")["websocket"]["session"].update(models=["undeclared-model"]),
            lambda c: by_id(c, "openai-live-ws-v1")["websocket"]["session"].update(kind="transcription"),
            lambda c: by_id(c, "openai-realtime-ws-v1")["websocket"].update(defaultModel="undeclared-model"),
            lambda c: by_id(c, "azure-voice-live-ws-v1")["websocket"]["queryParameters"].update(model="changed-model"),
        )
        for change in changes:
            config = configuration()
            change(config)
            with self.assertRaises(ValueError):
                transports.websocket_plan(config, state())

    def test_query_credentials_noncanonical_paths_and_query_model_on_live_are_rejected(self):
        for path in ("/leading", "trailing/", "a//b", "a/../b", "a/./b", "a?b=c"):
            with self.assertRaises(ValueError):
                transports.path_for({"path": path})
        for query in ({"access_token": SYNTHETIC_KEY}, {"model": "a b"}, {"model": "live-model"}):
            config = configuration()
            by_id(config, "openai-live-ws-v1")["websocket"]["queryParameters"] = query
            with self.assertRaises(ValueError):
                transports.websocket_plan(config, state())
        synthetic_userinfo = urlsplit("wss://gateway.example.com/")._replace(
            netloc="synthetic-user:synthetic-password@gateway.example.com").geturl()
        for url in ("ws://gateway.example.com/", synthetic_userinfo, "wss://gateway.example.com:444/",
                    "wss://gateway.example.com/#fragment", "wss://gateway.example.com/?model=a&model=b",
                    "wss://gateway.example.com/?api-key=value", "wss://gateway.example.com/?model="):
            with self.assertRaises(ValueError):
                transports.validate_socket_target(url)

    def test_generated_mcp_is_distinct_from_rest_and_rejects_another_tool(self):
        config = configuration()
        case, = transports.mcp_plan(config, state())
        self.assertEqual(case.url, "https://gateway.example.com/tools/pii/mcp/v1/mcp")
        by_id(config, "pii-v1")["mcp"]["operationNames"].append("another_tool")
        with self.assertRaises(ValueError):
            transports.mcp_plan(config, state())


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def probe(self, case=None, events=None, **kwargs):
        case = case or socket_case()
        socket = FakeSocket(events if events is not None else [service_event("session.created", case.model)],
                            status=kwargs.pop("status", 101), close_code=kwargs.pop("close_code", 1000))
        connector = FakeConnector(socket, kwargs.pop("error", None))
        with mock.patch.object(transports, "verified_tls_context", return_value=object()) as tls:
            result = await transports.websocket_probe(case, {"api-key": SYNTHETIC_KEY}, connector=connector, **kwargs)
        if connector.calls:
            tls.assert_called_once_with()
            self.assertIs(connector.calls[0][1]["ssl"], tls.return_value)
        else:
            tls.assert_not_called()
        return result, socket, connector

    async def test_missing_verified_tls_stops_before_the_socket_is_opened(self):
        connector = FakeConnector(FakeSocket())
        with mock.patch.object(transports, "verified_tls_context", side_effect=RuntimeError("SYNTHETIC-TLS-ERROR")):
            result = await transports.websocket_probe(socket_case(), {"api-key": SYNTHETIC_KEY}, connector=connector)
        self.assertEqual(connector.calls, [])
        self.assertFalse(result["passed"])
        self.assertIsNone(result["handshakeStatus"])
        self.assertNotIn("SYNTHETIC-TLS-ERROR", json.dumps(result))

    async def test_expected_ack_and_normal_close_are_required_without_persisting_content(self):
        result, socket, connector = await self.probe()
        self.assertTrue(result["passed"])
        self.assertTrue(result["modelMatched"])
        self.assertEqual(result["eventCounts"], {"session.created": 1})
        self.assertEqual(len(connector.calls), 1)
        options = connector.calls[0][1]
        self.assertIsNone(options["proxy"])
        self.assertIsNone(options["ping_interval"])
        self.assertEqual(options["max_size"], transports.FRAME_LIMIT)
        self.assertEqual(options["additional_headers"]["api-key"], SYNTHETIC_KEY)
        self.assertEqual(socket.close_calls, [{"code": 1000, "reason": ""}])
        self.assertTrue(socket.waited)
        self.assertNotIn(SYNTHETIC_BODY, json.dumps(result))
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(result))

    async def test_101_without_event_times_out_without_retry(self):
        result, socket, connector = await self.probe(events=[], timeout=0.02)
        self.assertFalse(result["passed"])
        self.assertEqual(result["handshakeStatus"], 101)
        self.assertEqual(result["failureClass"], "TimeoutError")
        self.assertEqual(len(connector.calls), 1)
        self.assertTrue(socket.waited)

    async def test_wrong_first_event_model_error_malformed_or_oversized_frame_cannot_pass(self):
        events = (service_event("response.completed"), service_event("session.created", "different-model"),
                  json.dumps({"type": "error", "message": SYNTHETIC_BODY}), "not-json",
                  json.dumps({"type": "session.created"}), b"binary-frame", " " * (transports.FRAME_LIMIT + 1))
        for event in events:
            result, _, connector = await self.probe(events=[event])
            self.assertFalse(result["passed"])
            self.assertEqual(len(connector.calls), 1)
            self.assertNotIn(SYNTHETIC_BODY, json.dumps(result))

    async def test_session_without_model_does_not_claim_model_routing(self):
        result, _, _ = await self.probe(events=[service_event("session.created")])
        self.assertTrue(result["passed"])
        self.assertFalse(result["modelReturned"])
        self.assertFalse(result["modelMatched"])
        self.assertFalse(result["ackModelReturned"])
        self.assertEqual(result["modelObservationCount"], 0)

    async def test_transcription_initial_or_nested_wrong_model_is_rejected_even_without_top_level_ack_model(self):
        case = socket_case("transcription")
        nested = lambda model: json.dumps({"type": "session.updated", "session": {
            "audio": {"input": {"transcription": {"model": model}}}}})
        scenarios = (
            [service_event("session.created", "synthetic-wrong-model"), service_event("session.updated")],
            [service_event("session.created"), nested("synthetic-wrong-model")],
            [service_event("session.created", case.model), nested("synthetic-wrong-model")],
            [service_event("session.created"), json.dumps({"type": "session.updated", "session": {
                "model": case.model, "input_audio_transcription": {"model": "synthetic-wrong-model"}}})],
        )
        for events in scenarios:
            result, _, connector = await self.probe(case, events)
            self.assertFalse(result["passed"])
            self.assertTrue(result["modelReturned"])
            self.assertFalse(result["modelMatched"])
            self.assertEqual(len(connector.calls), 1)
            self.assertNotIn("synthetic-wrong-model", json.dumps(result))
        result, _, _ = await self.probe(case, [service_event("session.created", case.model), nested(case.model)])
        self.assertTrue(result["passed"])
        self.assertTrue(result["modelMatched"])
        self.assertTrue(result["ackModelReturned"])
        self.assertEqual(result["modelObservationCount"], 2)
        result, _, _ = await self.probe(case, [service_event("session.created", case.model), service_event("session.updated")])
        self.assertTrue(result["passed"])
        self.assertTrue(result["modelReturned"])
        self.assertFalse(result["ackModelReturned"])
        result, _, _ = await self.probe(case, [json.dumps({"type": "session.created", "session": {
            "audio": None, "input_audio_transcription": None}}), nested(case.model)])
        self.assertTrue(result["passed"])
        self.assertTrue(result["ackModelReturned"])
        self.assertEqual(result["modelObservationCount"], 1)

    async def test_live_uses_start_then_close_and_transcription_updates_without_model_query(self):
        live = socket_case("live")
        result, socket, _ = await self.probe(live, [service_event("session.started", live.model), service_event("session.closed")])
        self.assertTrue(result["passed"])
        self.assertTrue(result["liveSessionClosed"])
        self.assertEqual(socket.sent, [live.start_event, {"type": "session.close"}])
        self.assertEqual(socket.sent[0]["session"]["delegation"], {"type": "client"})
        transcription = socket_case("transcription")
        result, socket, _ = await self.probe(transcription, [service_event("session.created"), service_event("session.updated")])
        self.assertTrue(result["passed"])
        self.assertEqual(socket.sent, [transcription.start_event])
        audio = socket.sent[0]["session"]["audio"]["input"]
        self.assertEqual(audio["transcription"]["model"], transcription.model)
        self.assertEqual(audio["format"], {"type": "audio/pcm", "rate": 24000})
        self.assertNotIn("model", parse_qs(urlsplit(transcription.url).query))

    async def test_event_limit_missing_live_closure_and_abnormal_close_fail(self):
        transcription = socket_case("transcription")
        result, _, _ = await self.probe(transcription, [service_event("session.created")] * transports.EVENT_LIMIT)
        self.assertFalse(result["passed"])
        live = socket_case("live")
        result, _, _ = await self.probe(live, [service_event("session.started", live.model)], timeout=0.02)
        self.assertFalse(result["passed"])
        result, _, _ = await self.probe(close_code=1001)
        self.assertFalse(result["passed"])
        self.assertFalse(result["normalClose"])

    async def test_redirect_error_preserves_only_status_and_one_attempt(self):
        error = ValueError(SYNTHETIC_BODY)
        error.response = FakeSocket(status=302).response
        result, _, connector = await self.probe(error=error)
        self.assertFalse(result["passed"])
        self.assertEqual(result["handshakeStatus"], 302)
        self.assertEqual(len(connector.calls), 1)
        self.assertNotIn(SYNTHETIC_BODY, json.dumps(result))
        module = types.ModuleType("websockets.asyncio.client")
        module.connect = type("SyntheticConnect", (), {})
        with mock.patch.dict(sys.modules, {"websockets.asyncio.client": module}):
            factory = transports.fixed_connector()
            self.assertIs(factory().process_redirect(error), error)

    async def test_exclusions_and_invalid_deadline_do_not_connect(self):
        for case in transports.websocket_plan(configuration(), state()):
            if case.exclusion:
                result, _, connector = await self.probe(case)
                self.assertFalse(result["passed"])
                self.assertFalse(result["executed"])
                self.assertEqual(connector.calls, [])
        for timeout in (0, -1, 121, float("nan"), float("inf"), True):
            result, _, connector = await self.probe(timeout=timeout)
            self.assertFalse(result["passed"])
            self.assertEqual(connector.calls, [])


def mcp_success():
    return {"status": 200, "initialized": True, "toolsCapabilityAdvertised": True, "toolsListReceived": True,
        "toolsAdvertisedCount": 1, "toolsListMorePages": False, "toolPresent": True, "toolInputFieldsValid": True,
        "toolPostCount": 1, "toolContractCheckedBeforeCall": True, "contractShapeValid": True, "normalTerminal": True, "succeeded": True,
        "toolDescriptors": [{"expectedNameEquals": True, "inputSchemaObject": True, "textPropertyPresent": True,
            "languagePropertyPresent": True, "textPropertyString": True, "languagePropertyString": True,
            "requiredFieldsMatch": True, "inputPropertyCount": 2}],
        "httpSteps": [{"role": "mcp", "httpMethod": "POST", "rpcMethod": method, "status": status,
            "contentType": "application/json", "correlationIds": {"apim-request-id": str(uuid.UUID(int=6))}}
            for method, status in (("initialize", 200), ("notifications/initialized", 202), ("tools/list", 200), ("tools/call", 200))]}


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def probe(self, raw=None, **kwargs):
        case, = transports.mcp_plan(configuration(), state())
        runner = mock.AsyncMock(return_value=raw if raw is not None else mcp_success())
        result = await transports.mcp_probe(case, {"Authorization": "Bearer <SYNTHETIC_TOKEN>"}, runner=runner, **kwargs)
        return result, runner

    async def test_one_sdk_probe_with_synthetic_tool_input_is_not_oauth_acceptance(self):
        result, runner = await self.probe()
        self.assertTrue(result["passed"])
        self.assertTrue(result["sdkProtocolVerified"])
        self.assertTrue(result["toolSchemaVerified"])
        self.assertTrue(result["toolContractCheckedBeforeCall"])
        self.assertFalse(result["gatewayOAuthVerified"])
        self.assertEqual(runner.await_count, 1)
        self.assertEqual(runner.call_args.args[1], transports.rest.probe.synthetic_body("pii", None, 0))
        self.assertEqual(runner.call_args.args[2]["x-ms-client-request-id"], result["requestId"])
        self.assertTrue(result["startedAt"].endswith("+00:00"))
        self.assertNotIn("oauth_config", runner.call_args.kwargs)
        self.assertIs(runner.call_args.kwargs["strict_tool_contract"], True)

    async def test_missing_initialization_schema_list_tool_http_success_or_completion_fails(self):
        changes = (
            lambda r: r.update(initialized=False), lambda r: r.update(toolsListReceived=False),
            lambda r: r.update(toolsCapabilityAdvertised=False), lambda r: r.update(toolsListMorePages=True),
            lambda r: r.update(toolPostCount=2), lambda r: r.update(toolPostCount=True),
            lambda r: r.update(contractShapeValid=False), lambda r: r.update(normalTerminal=False),
            lambda r: r.update(toolPresent=False), lambda r: r["toolDescriptors"][0].update(requiredFieldsMatch=False),
            lambda r: r["toolDescriptors"][0].update(textPropertyString=False, languagePropertyString=False),
            lambda r: r.update(toolContractCheckedBeforeCall=False), lambda r: r.pop("toolContractCheckedBeforeCall"),
            lambda r: r["httpSteps"][-1].update(status=401), lambda r: r["httpSteps"].pop(),
            lambda r: r["httpSteps"].append(copy.deepcopy(r["httpSteps"][-1])),
        )
        for change in changes:
            raw = mcp_success()
            change(raw)
            result, runner = await self.probe(raw)
            self.assertFalse(result["passed"])
            self.assertEqual(runner.await_count, 1)

    async def test_untrusted_body_headers_descriptors_and_summary_strings_are_never_saved(self):
        raw = mcp_success()
        raw.update(body=SYNTHETIC_BODY, url="https://other.example.com/", sessionId=SYNTHETIC_BODY,
            failureClass=SYNTHETIC_BODY, failureLeaves=[SYNTHETIC_BODY, "ValueError"],
            rpcErrorCodes=[SYNTHETIC_BODY, -(2**40), -32602], elapsedMs=SYNTHETIC_BODY)
        raw["httpSteps"][0].update(role=SYNTHETIC_BODY, contentType=SYNTHETIC_BODY, body=SYNTHETIC_BODY)
        raw["httpSteps"][0]["correlationIds"].update(Authorization=SYNTHETIC_KEY, **{"x-request-id": SYNTHETIC_BODY})
        raw["toolDescriptors"][0].update(name=SYNTHETIC_BODY, nameSha256=SYNTHETIC_BODY)
        result, _ = await self.probe(raw)
        self.assertFalse(result["passed"])
        self.assertEqual(result["failureClass"], "OtherError")
        self.assertEqual(result["failureLeaves"], ["OtherError", "ValueError"])
        self.assertEqual(result["rpcErrorCodes"], [-32602])
        self.assertNotIn(SYNTHETIC_BODY, json.dumps(result))
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(result))
        self.assertNotIn("other.example.com", json.dumps(result))

    async def test_runner_deadline_and_invalid_target_fail_without_replay(self):
        case, = transports.mcp_plan(configuration(), state())
        async def blocking(*args, **kwargs):
            await asyncio.sleep(3600)
        runner = mock.AsyncMock(side_effect=blocking)
        result = await transports.mcp_probe(case, {}, runner=runner, timeout=0.02)
        self.assertEqual(result["failureClass"], "TimeoutError")
        self.assertEqual(runner.await_count, 1)
        for url in ("https://gateway.example.com/mcp?token=value", "https://gateway.example.com:444/mcp", "http://gateway.example.com/mcp"):
            runner = mock.AsyncMock()
            result = await transports.mcp_probe(transports.Case("pii-v1-mcp", url, "api-key", "mcp"), {}, runner=runner)
            self.assertFalse(result["passed"])
            self.assertEqual(runner.await_count, 0)
        result, runner = await self.probe(timeout=float("nan"))
        self.assertFalse(result["passed"])
        self.assertEqual(runner.await_count, 0)


class FakeContext:
    def __init__(self):
        repository = ROOT / "synthetic-runtime"
        root = repository / ".local" / "synthetic"
        self.workspace = types.SimpleNamespace(repository=repository, root=root, state=root / "state", secrets=root / "secrets")
        self.config_path = root / "params.local.json"
        self.state = state()
        self.depth = 0
        self.saved = []

    @contextmanager
    def locked(self):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    def output_path(self, path):
        self_check = Path(path).is_relative_to(self.workspace.root)
        if not self_check:
            raise ValueError("Synthetic output outside the selected environment")
        return Path(path)

    def save_private(self, path, value):
        if self.depth != 1:
            raise AssertionError("Operation lock not held")
        self.saved.append((self.output_path(path), copy.deepcopy(value)))


class ExecutionGateTests(unittest.TestCase):
    def setUp(self):
        self.context = FakeContext()
        self.current = {"input": "synthetic-input", "coreState": "synthetic-core", "checkpoint": "synthetic-checkpoint"}
        self.gate = {"sourceRoot": str(transports.ROOT), "runtimeRoot": str(self.context.workspace.repository),
            "configFile": str(self.context.config_path),
            "publicFinalModuleSha256": hashlib.sha256(transports.rest.api_publication.MODULE.read_bytes()).hexdigest(),
            "stageA": {"status": "verified", "managementReadbackVerified": True},
            "stageB": {"status": "verified", "module500Restored": True, "apiPublicationCompleted": True,
                "azure500DefinitionReadbackVerified": True, "currentCheckpointMatched": True, "endSnapshot": self.current}}
        self.waf = {"scopeGatewayOnly": True, "managementReadbackVerified": True, "inputCoreAndCheckpointUnchanged": True}
        self.checkpoint = {"source": "synthetic-source", "globalPolicy": "synthetic-policy", "entraAuthentication": {}}

    @contextmanager
    def patches(self, snapshots=None, expected=None):
        def private(context, path):
            if context.depth != 1:
                raise AssertionError("Operation lock not held")
            return {"phase12-two-stage-migration.json": self.gate, "phase12-waf-result.json": self.waf,
                    "gateway-api-source.json": self.checkpoint}[Path(path).name]
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(transports, "resolve_publication_configuration", return_value=configuration()))
            stack.enter_context(mock.patch.object(transports.rest, "load_configuration_source", return_value={}))
            stack.enter_context(mock.patch.object(transports.rest, "private_json", side_effect=private))
            stack.enter_context(mock.patch.object(transports.rest, "snapshot", side_effect=snapshots, return_value=self.current))
            stack.enter_context(mock.patch.object(transports.rest.api_publication, "prepare_plan", return_value={}))
            stack.enter_context(mock.patch.object(transports.rest.api_publication, "source_checkpoint",
                return_value=self.checkpoint if expected is None else expected))
            credentials = stack.enter_context(mock.patch.object(transports, "credential_headers", return_value={"api-key": SYNTHETIC_KEY}))
            socket = stack.enter_context(mock.patch.object(transports, "websocket_probe", new=mock.AsyncMock(return_value={"passed": True})))
            mcp = stack.enter_context(mock.patch.object(transports, "mcp_probe", new=mock.AsyncMock(return_value={"passed": True})))
            stack.enter_context(mock.patch("sys.stdout", new=io.StringIO()))
            yield credentials, socket, mcp

    def test_plan_only_checks_gates_but_never_reads_credentials_calls_network_or_saves(self):
        for kind in ("websocket", "mcp"):
            with self.patches() as (credentials, socket, mcp):
                self.assertEqual(transports.execute(self.context, kind, "bearer"), 0)
                credentials.assert_not_called()
                socket.assert_not_awaited()
                mcp.assert_not_awaited()
                self.assertEqual(self.context.saved, [])

    def test_stale_stage_a_stage_b_waf_and_source_checkpoint_stop_before_credentials(self):
        changes = (lambda: self.gate["stageA"].update(status="pending"),
                   lambda: self.gate["stageB"].update(status="pending"),
                   lambda: self.gate["stageB"].update(azure500DefinitionReadbackVerified=False),
                   lambda: self.waf.update(scopeGatewayOnly=False),
                   lambda: self.gate["stageB"].update(endSnapshot={**self.current, "input": "changed"}))
        for change in changes:
            self.setUp()
            change()
            with self.patches() as (credentials, socket, mcp):
                with self.assertRaises(ValueError):
                    transports.execute(self.context, "websocket", "key", live=True)
                credentials.assert_not_called()
                socket.assert_not_awaited()
                mcp.assert_not_awaited()
        self.setUp()
        with self.patches(expected={**self.checkpoint, "globalPolicy": "changed"}) as (credentials, socket, mcp):
            with self.assertRaises(ValueError):
                transports.execute(self.context, "mcp", "key", live=True)
            credentials.assert_not_called()
            mcp.assert_not_awaited()

    def test_recorded_results_stay_private_and_do_not_complete_audit_or_phase12(self):
        with self.patches() as (credentials, socket, mcp):
            self.assertEqual(transports.execute(self.context, "mcp", "key", live=True), 0)
            credentials.assert_called_once()
            mcp.assert_awaited_once()
            socket.assert_not_awaited()
        self.assertEqual(len(self.context.saved), 2)
        for path, result in self.context.saved:
            self.assertTrue(path.is_relative_to(self.context.workspace.root / "evidence"))
            self.assertFalse(result["auditVerified"])
            self.assertFalse(result["phase12Completed"])
            self.assertNotIn(SYNTHETIC_KEY, json.dumps(result))
        self.assertTrue(self.context.saved[-1][1]["inputCoreAndCheckpointUnchanged"])

    def test_snapshot_drift_before_during_and_before_finalization_stops_progress(self):
        changed = {**self.current, "input": "changed"}
        for sequence, expected_calls in (([self.current, changed], 0),
                                         ([self.current, self.current, changed], 1),
                                         ([self.current, self.current, self.current, changed], 1)):
            self.context.saved.clear()
            with self.patches(snapshots=sequence) as (_, _, mcp):
                with self.assertRaises(ValueError):
                    transports.execute(self.context, "mcp", "bearer", live=True)
                self.assertEqual(mcp.await_count, expected_calls)
            self.assertFalse(any(path.name == "transport-result.json" for path, _ in self.context.saved))

    def test_unknown_cases_modes_or_transports_are_rejected_and_excluded_case_is_incomplete(self):
        for kind, mode, selected in (("http", "key", None), ("mcp", "unknown", None), ("websocket", "key", "unknown-api")):
            with self.patches() as (credentials, socket, mcp):
                with self.assertRaises(ValueError):
                    transports.execute(self.context, kind, mode, case_id=selected, live=True)
                credentials.assert_not_called()
                socket.assert_not_awaited()
                mcp.assert_not_awaited()
        with self.patches() as (credentials, socket, _):
            socket.return_value = {"passed": False, "executed": False, "exclusion": "requires-existing-owner-bound-call-ticket"}
            self.assertEqual(transports.execute(self.context, "websocket", "bearer", case_id="openai-realtime-observer-v1", live=True), 1)
            credentials.assert_not_called()
            self.assertFalse(self.context.saved[-1][1]["passed"])

    def test_credentials_must_match_selected_runtime_input_and_secret_directory(self):
        context = self.context
        receipt = {"runtimeRoot": str(context.workspace.repository), "configFile": str(context.config_path),
            "keyFile": str(context.workspace.secrets / "synthetic-key"), "bearerFile": str(context.workspace.secrets / "synthetic-bearer")}
        with mock.patch.object(transports.rest, "private_json", return_value=receipt), \
                mock.patch.object(transports.rest.probe, "auth_headers", return_value={"Authorization": "Bearer <SYNTHETIC_TOKEN>"}) as read, \
                mock.patch.object(transports.rest, "bearer_eligible") as eligible:
            transports.credential_headers(context, "bearer", configuration())
            eligible.assert_called_once()
            self.assertEqual(read.call_args.kwargs["bearer_file"], context.workspace.secrets / "synthetic-bearer")
            read.reset_mock()
            for field, value in (("runtimeRoot", str(ROOT / "another-synthetic-runtime")),
                                 ("configFile", str(context.workspace.root / "another-input.json")),
                                 ("keyFile", str(context.workspace.state / "synthetic-key"))):
                altered = {**receipt, field: value}
                with mock.patch.object(transports.rest, "private_json", return_value=altered), self.assertRaises(ValueError):
                    transports.credential_headers(context, "key", configuration())
            read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
