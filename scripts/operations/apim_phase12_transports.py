#!/usr/bin/env python3
"""Bounded authenticated WebSocket and generated MCP acceptance after phase 1/2 gates."""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["AZURE_LOGGING_ENABLE_LOG_FILE"] = "false"

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "apps/api-gateway/deploy"))

from operations import apim_phase12_acceptance as rest
from deployment.helpers.client_tls import verified_tls_context
from standard_apis import PUBLIC_BINDINGS, resolve_publication_configuration

WS_APIS = (
    "openai-realtime-ws-v1", "openai-realtime-observer-v1",
    "azure-voice-live-ws-v1", "azure-voice-live-webrtc-v1",
    "openai-live-ws-v1", "openai-transcription-ws-v1",
)
FRAME_LIMIT = 65536
EVENT_LIMIT = 8
TIMEOUT_SECONDS = 45
QUERY_NAMES = frozenset(("api-version", "model", "language", "voice", "intent"))
EVENT_NAMES = frozenset(("session.created", "session.updated", "session.started", "session.closed",
                         "error", "session.error", "rtc.call.error"))
ERROR_CLASSES = frozenset(("ValueError", "TypeError", "KeyError", "JSONDecodeError", "TimeoutError",
    "OSError", "ConnectionError", "ConnectionClosed", "ConnectionClosedError", "ConnectionClosedOK",
    "InvalidStatus", "InvalidHandshake", "InvalidHeader", "InvalidMessage", "SecurityError",
    "SSLError", "SSLCertVerificationError", "ExceptionGroup", "McpError", "ConnectError",
    "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout", "RemoteProtocolError",
    "LocalProtocolError", "HTTPStatusError", "EndOfStream", "BrokenResourceError", "ClosedResourceError"))


@dataclass(frozen=True)
class Case:
    api_id: str
    url: str
    key_header: str
    family: str
    model: str | None = None
    start_event: dict | None = None
    exclusion: str | None = None


def public_origin(configuration, state):
    origin = state["outputs"]["endpoints"]["public"].rstrip("/")
    parsed = urlsplit(rest.probe.endpoint(origin))
    if (origin != "https://" + configuration["gateway"]["publicHostName"]
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("The public Gateway origin differs")
    return origin


def path_for(api):
    path = api["path"]
    if (not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", path)
            or path.startswith("/") or path.endswith("/") or "//" in path
            or any(part in (".", "..") for part in path.split("/"))):
        raise ValueError("The declared public path is not canonical")
    return "/" + path


def websocket_plan(configuration, state):
    """Use declared provider query and session models; never invent a call ticket or SDP."""
    origin = public_origin(configuration, state)
    definitions = {api["name"]: api for api in configuration["apiDefinitions"]}
    if (len(definitions) != len(configuration["apiDefinitions"])
            or {name for name, api in definitions.items() if api.get("apiType") == "websocket"} != set(WS_APIS)):
        raise ValueError("The six declared WebSocket APIs differ")
    deployments = {item["name"] for item in configuration["services"]["modelDeployments"]}
    cases = []
    for name in WS_APIS:
        api = definitions[name]
        if (api.get("backendService") != "external" or not api.get("backendUrl", "").startswith("wss://")
                or len(api.get("operations", [])) != 1
                or any(api["operations"][0].get(key) != value for key, value in
                       (("name", "invoke"), ("method", "GET"), ("urlTemplate", "/")))):
            raise ValueError("The WebSocket operation differs")
        key_header = api["subscriptionKeyHeader"]
        if key_header != configuration["gateway"]["authentication"]["subscriptionKeyHeader"]:
            raise ValueError("The credential header differs")
        settings = api["websocket"]
        query = dict(settings.get("queryParameters", {}))
        if any(key not in QUERY_NAMES or not isinstance(value, str)
               or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) for key, value in query.items()):
            raise ValueError("The fixed provider query is not public and bounded")
        family, model, start, exclusion = "realtime", None, None, None
        if name == "openai-realtime-observer-v1":
            if set(settings) != {"realtimeObserver"}:
                raise ValueError("Observer query must use its own ticket contract")
            family, exclusion = "observer", "requires-existing-owner-bound-call-ticket"
        elif name == "azure-voice-live-webrtc-v1":
            family, model = "voice-live-calls", query.get("model")
            exclusion = "requires-browser-generated-audio-only-sdp"
            if query != {"api-version": PUBLIC_BINDINGS["VOICE_LIVE_CALLS_API_VERSION"],
                         "model": PUBLIC_BINDINGS["VOICE_LIVE_CALLS_MODEL"]}:
                raise ValueError("The Voice Live calls declaration differs")
        elif name == "azure-voice-live-ws-v1":
            family, model = "voice-live", query.get("model")
            if query != {"api-version": PUBLIC_BINDINGS["VOICE_LIVE_API_VERSION"],
                         "model": PUBLIC_BINDINGS["VOICE_LIVE_MODEL"]}:
                raise ValueError("The Voice Live declaration differs")
        elif name in ("openai-live-ws-v1", "openai-transcription-ws-v1"):
            session = settings["session"]
            models = session["models"]
            if not models or not set(models) <= deployments:
                raise ValueError("Session models are not declared deployments")
            family, model = session["kind"], models[0]
            if name == "openai-live-ws-v1" and family == "live" and query == {}:
                # Same start/close contract and declared default voice as the Portal Live client.
                start = {"type": "session.start", "session": {"model": model,
                         "instructions": "No tools or external operations. Transport acceptance only.",
                         "audio": {"output": {"voice": "marin"}}, "delegation": {"type": "client"}}}
            elif name == "openai-transcription-ws-v1" and family == "transcription" and query == {"intent": "transcription"}:
                start = {"type": "session.update", "session": {"type": "transcription", "audio": {"input": {
                    "format": {"type": "audio/pcm", "rate": 24000}, "turn_detection": None,
                    "transcription": {"model": model, "language": "ja", "delay": "medium"}}}}}
            else:
                raise ValueError("The declared session protocol differs")
        else:
            if name != "openai-realtime-ws-v1":
                raise ValueError("The realtime route differs")
            model = settings.get("defaultModel")
            if not model or model not in settings.get("allowedModels", []) or model not in deployments or "model" in query:
                raise ValueError("The realtime model is not declared")
            query["model"] = model
        url = "wss://" + urlsplit(origin).netloc + path_for(api) + "/"
        if query:
            url += "?" + urlencode(sorted(query.items()))
        cases.append(Case(name, url, key_header, family, model, start, exclusion))
    return cases


def mcp_plan(configuration, state):
    origin = public_origin(configuration, state)
    api = next(item for item in configuration["apiDefinitions"] if item["name"] == "pii-v1")
    mcp = api.get("mcp", {})
    if (mcp.get("name") != "pii-v1-mcp" or mcp.get("operationNames") != ["pii_redact_text"]
            or not any(op.get("name") == "pii_redact_text" and op.get("method") == "POST" for op in api["operations"])):
        raise ValueError("The generated PII MCP declaration differs")
    if api["subscriptionKeyHeader"] != configuration["gateway"]["authentication"]["subscriptionKeyHeader"]:
        raise ValueError("The MCP credential header differs")
    return [Case(mcp["name"], origin + path_for(mcp) + "/mcp", api["subscriptionKeyHeader"], "mcp")]


def fixed_connector():
    from websockets.asyncio.client import connect

    class FixedTargetConnect(connect):
        def process_redirect(self, error):
            return error
    return FixedTargetConnect


def validate_socket_target(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "wss" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("A fixed TLS Gateway target is required")
    from urllib.parse import parse_qsl
    values = parse_qsl(parsed.query, keep_blank_values=True)
    if (len({key for key, _ in values}) != len(values)
            or any(key not in QUERY_NAMES or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) for key, value in values)):
        raise ValueError("Credentials, dynamic targets and duplicate query values are forbidden")


def failure_class(value):
    return value if isinstance(value, str) and value in ERROR_CLASSES else "OtherError"


def safe_correlations(headers):
    return rest.probe.correlations({name: value for name in rest.probe.CORRELATION_HEADERS
        if isinstance(value := headers.get(name), str)})


def require_deadline(timeout):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("A bounded transport deadline is required")


def event_summary(raw, case, result):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > FRAME_LIMIT:
        raise ValueError("The WebSocket frame is not bounded JSON text")
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise ValueError("The service event is not an object")
    kind = value["type"]
    safe = kind if kind in EVENT_NAMES else "other"
    result["eventCounts"][safe] = result["eventCounts"].get(safe, 0) + 1
    if kind in ("error", "session.error", "rtc.call.error"):
        raise ValueError("The service rejected the transport session")
    if result["firstEventType"] is None:
        result["firstEventType"] = safe
        initial = {"live": {"session.started"}, "transcription": {"session.created", "session.updated"}}.get(
            case.family, {"session.created"})
        if kind not in initial:
            raise ValueError("The first event does not match the declared session protocol")
    expected = {"live": "session.started", "transcription": "session.updated"}.get(case.family, "session.created")
    session = value.get("session")
    if kind in ("session.created", "session.updated", "session.started") and not isinstance(session, dict):
        raise ValueError("The session acknowledgement is missing")
    observed_models = []
    if isinstance(session, dict):
        paths = [("model",)]
        if case.family == "transcription":
            paths += [("audio", "input", "transcription", "model"), ("input_audio_transcription", "model")]
        for path in paths:
            item = session
            for index, key in enumerate(path):
                if item is None:
                    break
                if not isinstance(item, dict):
                    raise ValueError("The session model declaration is malformed")
                if key not in item:
                    break
                item = item[key]
                if index == len(path) - 1:
                    observed_models.append(item)
    for model in observed_models:
        result["modelReturned"] = True
        result["modelObservationCount"] = result.get("modelObservationCount", 0) + 1
        result["modelMatched"] = isinstance(model, str) and model == case.model
        if not result["modelMatched"]:
            raise ValueError("The service acknowledged another model")
    if kind == expected:
        result["ackModelReturned"] = bool(observed_models)
        result["sessionAcknowledged"] = True
    return kind


async def websocket_probe(case, headers, *, connector=None, timeout=TIMEOUT_SECONDS):
    result = {"kind": "websocket", "startedAt": datetime.now(timezone.utc).isoformat(),
              "requestId": str(uuid.uuid4()), "connectAttempts": 0, "handshakeStatus": None,
              "eventCounts": {}, "firstEventType": None, "sessionAcknowledged": False,
              "modelReturned": False, "modelMatched": False, "modelObservationCount": 0,
              "ackModelReturned": False, "normalClose": False, "passed": False}
    started = time.monotonic()
    if case.exclusion:
        return {**result, "executed": False, "exclusion": case.exclusion}
    socket = None
    try:
        validate_socket_target(case.url)
        require_deadline(timeout)
        async with asyncio.timeout(timeout):
            factory = connector or fixed_connector()
            result["connectAttempts"] = 1
            async with factory(case.url, additional_headers={**headers, "x-ms-client-request-id": result["requestId"]},
                               ssl=verified_tls_context(), proxy=None, open_timeout=min(15, timeout),
                               close_timeout=5, max_size=FRAME_LIMIT, max_queue=4, ping_interval=None) as socket:
                response = socket.response
                result["handshakeStatus"] = response.status_code
                result["correlationIds"] = safe_correlations(response.headers)
                if response.status_code != 101:
                    raise ValueError("The WebSocket handshake was not accepted")
                if case.start_event is not None:
                    await socket.send(json.dumps(case.start_event, separators=(",", ":")))
                for _ in range(EVENT_LIMIT):
                    event_summary(await socket.recv(), case, result)
                    if result["sessionAcknowledged"]:
                        break
                if not result["sessionAcknowledged"]:
                    raise ValueError("The expected session event was not received")
                if case.family == "live":
                    await socket.send('{"type":"session.close"}')
                    for _ in range(EVENT_LIMIT):
                        if event_summary(await socket.recv(), case, result) == "session.closed":
                            result["liveSessionClosed"] = True
                            break
                    if result.get("liveSessionClosed") is not True:
                        raise ValueError("Live session closure was not acknowledged")
                await socket.close(code=1000, reason="")
                await socket.wait_closed()
                result["closeCode"] = socket.close_code
                result["normalClose"] = socket.close_code == 1000
                result["passed"] = result["normalClose"]
    except Exception as error:
        result.update(failureClass=failure_class(type(error).__name__), passed=False)
        response = getattr(error, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
            if type(status) is int and 100 <= status <= 599:
                result["handshakeStatus"] = status
                result["correlationIds"] = safe_correlations(response.headers)
    finally:
        result["elapsedMs"] = round((time.monotonic() - started) * 1000, 2)
    return result


async def mcp_probe(case, headers, *, runner=None, timeout=TIMEOUT_SECONDS):
    """Reuse the official SDK client, but persist only controlled protocol summaries."""
    started = time.monotonic()
    base = {"kind": "mcp", "startedAt": datetime.now(timezone.utc).isoformat(), "requestId": str(uuid.uuid4()),
            "gatewayOAuthVerified": False, "authenticationMode": "manual-credential"}
    try:
        require_deadline(timeout)
        parsed = urlsplit(rest.probe.endpoint(case.url))
        if parsed.query or parsed.port not in (None, 443) or case.family != "mcp":
            raise ValueError("A fixed TLS MCP target is required")
        async with asyncio.timeout(timeout):
            raw = await (runner or rest.probe.mcp_probe)(case.url, rest.probe.synthetic_body("pii", None, 0),
                {**headers, "x-ms-client-request-id": base["requestId"]}, timeout=timeout, strict_tool_contract=True)
        required = ("initialized", "toolsCapabilityAdvertised", "toolsListReceived", "toolPresent",
                    "toolInputFieldsValid", "toolContractCheckedBeforeCall", "contractShapeValid", "normalTerminal", "succeeded")
        result = base | {key: raw[key] for key in (*required, "toolsListMorePages") if type(raw.get(key)) is bool}
        for key, maximum in (("status", 599), ("toolsAdvertisedCount", 10000), ("toolPostCount", 10000)):
            if type(value := raw.get(key)) is int and 0 <= value <= maximum:
                result[key] = value
        if raw.get("failureClass"):
            result["failureClass"] = failure_class(raw["failureClass"])
        result["failureLeaves"] = [failure_class(value) for value in raw.get("failureLeaves", [])[:16]]
        result["rpcErrorCodes"] = [value for value in raw.get("rpcErrorCodes", [])[:16]
                                   if type(value) is int and -(2**31) <= value < 2**31]
        result["httpSteps"] = []
        enums = {"role": {"mcp", "metadata", "token"}, "httpMethod": {"GET", "POST", "DELETE"},
                 "rpcMethod": {"initialize", "notifications/initialized", "tools/list", "tools/call",
                               "notifications/cancelled", "receive", "other"},
                 "contentType": {"application/json", "text/event-stream", "other"}}
        steps = raw.get("httpSteps", [])
        for step in steps[:64]:
            safe = {key: step[key] for key, allowed in enums.items()
                    if isinstance(step.get(key), str) and step[key] in allowed}
            if type(step.get("status")) is int and 100 <= step["status"] <= 599:
                safe["status"] = step["status"]
            safe["correlationIds"] = safe_correlations(step.get("correlationIds", {}))
            result["httpSteps"].append(safe)
        descriptors = raw.get("toolDescriptors", [])
        schema_verified = any(all(item.get(key) is True for key in
            ("expectedNameEquals", "inputSchemaObject", "textPropertyPresent", "languagePropertyPresent",
             "textPropertyString", "languagePropertyString", "requiredFieldsMatch"))
            and type(item.get("inputPropertyCount")) is int and item["inputPropertyCount"] == 2 for item in descriptors[:16])
        result["toolSchemaVerified"] = schema_verified
        tool_steps = [step for step in result["httpSteps"] if step.get("rpcMethod") == "tools/call"]
        result["passed"] = (all(result.get(key) is True for key in required)
                            and type(raw.get("toolPostCount")) is int and raw["toolPostCount"] == 1
                            and result.get("toolsListMorePages") is False and not raw.get("failureClass")
                            and schema_verified and len(steps) <= 64 and len(tool_steps) == 1
                            and all(tool_steps[0].get(key) == value for key, value in
                                    (("role", "mcp"), ("httpMethod", "POST"), ("status", 200))))
        if result["passed"]:
            # 固定PII結果のREST操作IDだけを再検査し、外側HTTPの相関IDと分けます。
            returned_id = raw.get("piiResultRequestId")
            if isinstance(returned_id, str) and re.fullmatch(
                    r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", returned_id):
                parsed_id = uuid.UUID(returned_id)
                if parsed_id.int != 0:
                    result["piiResultRequestId"] = str(parsed_id)
        result.update(sdkProtocolVerified=result["passed"], elapsedMs=round((time.monotonic() - started) * 1000, 2))
        return result
    except Exception as error:
        return base | {"passed": False, "failureClass": failure_class(type(error).__name__), "sdkProtocolVerified": False,
                       "elapsedMs": round((time.monotonic() - started) * 1000, 2)}


def credential_headers(context, mode, configuration):
    if mode not in ("key", "bearer"):
        raise ValueError("Select one declared authentication mode")
    receipt = rest.private_json(context, context.workspace.state / "phase12-acceptance-credentials.json")
    if (Path(receipt["configFile"]).resolve() != context.config_path.resolve()
            or Path(receipt["runtimeRoot"]).resolve() != context.workspace.repository):
        raise ValueError("The credential record belongs to another runtime")
    path = context.output_path(Path(receipt["keyFile" if mode == "key" else "bearerFile"]))
    if not path.is_relative_to(context.workspace.secrets):
        raise ValueError("Only the recorded selected secret file is accepted")
    if mode == "key":
        return rest.probe.auth_headers(key_file=path, key_header=configuration["gateway"]["authentication"]["subscriptionKeyHeader"])
    headers = rest.probe.auth_headers(bearer_file=path)
    rest.bearer_eligible(headers, configuration)
    return headers


def execute(context, kind, mode, *, case_id=None, live=False):
    if kind not in ("websocket", "mcp") or mode not in ("key", "bearer"):
        raise ValueError("Select a declared transport and authentication mode")
    with context.locked():
        configuration = resolve_publication_configuration(rest.load_configuration_source(context.config_path), context.state)
        current = rest.snapshot(context)
        gate = rest.private_json(context, context.workspace.state / "phase12-two-stage-migration.json")
        waf = rest.private_json(context, context.workspace.state / "phase12-waf-result.json")
        rest.require_gates(gate, waf, current, source=ROOT, runtime=context.workspace.repository,
                           config=context.config_path, module_hash=hashlib.sha256(rest.api_publication.MODULE.read_bytes()).hexdigest())
        checkpoint = rest.private_json(context, context.workspace.state / "gateway-api-source.json")
        plan = rest.api_publication.prepare_plan(configuration, context.state)
        expected = rest.api_publication.source_checkpoint(plan, checkpoint["source"], checkpoint["globalPolicy"],
            entra_authentication=checkpoint["entraAuthentication"], browser_source=checkpoint.get("browserSource"),
            generation_id=checkpoint.get("sourceGenerationId"))
        if checkpoint != expected:
            raise ValueError("The current source checkpoint differs")
        cases = websocket_plan(configuration, context.state) if kind == "websocket" else mcp_plan(configuration, context.state)
        if case_id is not None:
            cases = [case for case in cases if case.api_id == case_id]
            if not cases:
                raise ValueError("The selected case is not declared")
        if not live:
            print(json.dumps({"status": "transport-plan-only", "caseCount": len(cases), "networkRequests": 0,
                              "excludedCount": sum(bool(case.exclusion) for case in cases)}), flush=True)
            return 0
        headers = credential_headers(context, mode, configuration) if any(not case.exclusion for case in cases) else {}
        run = context.output_path(context.workspace.root / "evidence" / ("apim-phase12-transports-" + uuid.uuid4().hex))
        results = []
        for case in cases:
            if rest.snapshot(context) != current:
                raise ValueError("The protected inputs changed before the next case")
            result = asyncio.run(websocket_probe(case, headers) if kind == "websocket" else mcp_probe(case, headers))
            if rest.snapshot(context) != current:
                raise ValueError("The protected inputs changed during transport acceptance")
            record = {**result, "apiId": case.api_id, "operationId": "invoke" if kind == "websocket" else "pii_redact_text",
                      "mode": mode, "auditVerified": False, "phase12Completed": False}
            context.save_private(run / (case.api_id + ".json"), record)
            results.append(record)
            print(json.dumps({"status": "transport-case-passed" if record["passed"] else "transport-case-not-verified",
                              "case": case.api_id, "mode": mode}), flush=True)
        passed = all(record["passed"] for record in results)
        if rest.snapshot(context) != current:
            raise ValueError("The protected inputs changed before finalizing the result")
        context.save_private(run / "transport-result.json", {"passed": passed, "kind": kind, "mode": mode,
            "cases": results, "inputCoreAndCheckpointUnchanged": True,
            "auditVerified": False, "phase12Completed": False})
        return 0 if passed else 1


def main(argv=None):
    try:
        parser = rest.SafeParser(description=__doc__)
        parser.add_argument("kind", choices=("websocket", "mcp"))
        parser.add_argument("mode", choices=("key", "bearer"))
        parser.add_argument("--case")
        parser.add_argument("--execute", action="store_true")
        args = parser.parse_args(argv)
        return execute(rest.prepared_context(), args.kind, args.mode, case_id=args.case, live=args.execute)
    except Exception:
        print('{"status":"transport-preflight-or-run-stopped","phase12Completed":false}', flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
