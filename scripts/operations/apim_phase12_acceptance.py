#!/usr/bin/env python3
"""Run bounded REST acceptance after verified phase 1/2 publication and WAF."""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["AZURE_LOGGING_ENABLE_LOG_FILE"] = "false"

import argparse
import base64
import copy
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
from pathlib import Path
import queue
import socket
import threading
import time
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "apps/gateway/deploy"))

import api_publication
import integration_client as probe
from deployment.helpers import credential_login
from deployment.helpers.apim_authentication import entra_authentication_settings
from deployment.helpers.configuration import load_configuration_source
from deployment.helpers.paths import _no_links
from deployment.helpers.private_permissions import check_private_permissions
from deployment.helpers.client_tls import verified_tls_context
from operations.common import OperationContext
from standard_apis import resolve_publication_configuration

TIMEOUT_SECONDS = 120
BODY_LIMIT = 4 * 1024 * 1024
LINE_LIMIT = 1024 * 1024
TOKEN_LIMIT = 512
NONCE = "phase12-synthetic-nonce"
DATE_METADATA = {
    "probe_iso": "2000-01-02T03:04:05Z",
    "probe_iso_fraction": "2000-01-02T03:04:05.000Z",
    "probe_control": "prefix 2000-01-02T03:04:05.000Z",
}


def private_json(context, path):
    path = context.output_path(path)
    check_private_permissions(path)
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot(context):
    paths = {"input": context.config_path, "coreState": context.state_path,
             "checkpoint": context.workspace.state / "gateway-api-source.json"}
    for path in paths.values():
        check_private_permissions(path)
    return {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()}


def require_gates(gate, waf, current, *, source, runtime, config, module_hash):
    """Reject pending, stale or differently selected receipts before HTTP calls."""
    selected = {"sourceRoot": source, "runtimeRoot": runtime, "configFile": config}
    if any(Path(gate[key]).resolve() != Path(value).resolve() for key, value in selected.items()):
        raise ValueError("Publication target differs")
    a, b = gate["stageA"], gate["stageB"]
    if a.get("status") != "verified" or a.get("managementReadbackVerified") is not True:
        raise ValueError("Stage A is incomplete")
    required = ("module500Restored", "apiPublicationCompleted", "azure500DefinitionReadbackVerified",
                "currentCheckpointMatched")
    if b.get("status") != "verified" or any(b.get(key) is not True for key in required):
        raise ValueError("Stage B is incomplete")
    if b.get("endSnapshot") != current or gate.get("publicFinalModuleSha256") != module_hash:
        raise ValueError("Publication source or protected state changed")
    if any(waf.get(key) is not True for key in
           ("scopeGatewayOnly", "managementReadbackVerified", "inputCoreAndCheckpointUnchanged")):
        raise ValueError("Gateway WAF is not verified")


def prepared_context():
    values = {key: Path(os.environ[key]).expanduser() for key in
              ("GW_PHASE12_SOURCE_ROOT", "GW_PHASE12_RUNTIME_ROOT", "GW_PHASE12_CONFIG_FILE")}
    for path in values.values():
        if not path.is_absolute():
            raise ValueError("Explicit absolute paths are required")
        _no_links(path, Path(path.anchor))
    if values["GW_PHASE12_SOURCE_ROOT"].resolve() != ROOT:
        raise ValueError("The executing source differs")
    return OperationContext.load(values["GW_PHASE12_CONFIG_FILE"],
                                 runtime_root=values["GW_PHASE12_RUNTIME_ROOT"])


def http_exchange(url, method, body, headers, *, kind=None, stream=False, timeout=TIMEOUT_SECONDS):
    """One direct HTTPS request; returned JSON is transient and never journaled."""
    parsed = urlsplit(probe.endpoint(url))
    wire = (body if isinstance(body, bytes) else
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) if body is not None else None
    if wire is not None and len(wire) > BODY_LIMIT:
        raise ValueError("Probe request exceeds its limit")
    started, completed, cancelled = time.monotonic(), queue.Queue(maxsize=1), threading.Event()
    state = {"socket": None, "requestStarted": False}
    base = {"status": None, "normalTerminal": False, "httpAttempts": 0,
            "startedAt": datetime.now(timezone.utc).isoformat(), "requestId": str(uuid.uuid4())}
    request_headers = {**headers, "x-ms-client-request-id": base["requestId"],
                       "Accept": "text/event-stream" if stream else "application/json"}
    if wire is not None:
        request_headers["Content-Type"] = "application/json"

    def remaining():
        budget = timeout - (time.monotonic() - started)
        if cancelled.is_set() or budget <= 0:
            raise TimeoutError("HTTP probe deadline reached")
        return budget

    def perform():
        result, value, response, connection = dict(base), None, None, None
        try:
            connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                                                    context=verified_tls_context(), timeout=remaining())
            connection.connect()
            state["socket"] = connection.sock
            connection.sock.settimeout(remaining())
            state["requestStarted"] = True
            result["httpAttempts"] = 1
            connection.request(method, (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                               body=wire, headers=request_headers)
            response = connection.getresponse()
            result.update(status=response.status, correlationIds=probe.correlations(response.headers),
                          noStore="no-store" in [part.strip().lower() for part in response.getheader("Cache-Control", "").split(",")],
                          bearerChallenge=response.getheader("WWW-Authenticate", "").strip().lower() == "bearer")
            if stream and response.status == 200 and response.getheader("Content-Type", "").lower().startswith("text/event-stream"):
                parser, size = probe.StreamSummary(kind, started), 0
                while True:
                    remaining()
                    line = response.readline(LINE_LIMIT + 1)
                    if not line:
                        break
                    size += len(line)
                    if len(line) > LINE_LIMIT or size > BODY_LIMIT:
                        raise ValueError("SSE probe exceeds its limits")
                    parser.line(line.decode("utf-8").rstrip("\r\n"))
                if getattr(response, "length", None) not in (None, 0):
                    raise http.client.IncompleteRead(b"", response.length)
                result.update(parser.result())
            else:
                chunks, size = [], 0
                while True:
                    remaining()
                    chunk = response.read1(min(65536, BODY_LIMIT + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > BODY_LIMIT:
                        raise ValueError("JSON probe exceeds its limit")
                if getattr(response, "length", None) not in (None, 0):
                    raise http.client.IncompleteRead(b"", response.length)
                raw = b"".join(chunks)
                result["bodyEmpty"] = not raw
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    value = None
                result.update(probe.field_summary(value))
                result["usage"] = probe.usage_numbers(value.get("usage")) if isinstance(value, dict) else {}
                result["contractShapeValid"] = probe.json_shape(kind, value) if kind else False
                result["normalTerminal"] = not stream and 200 <= response.status < 300 and result["contractShapeValid"]
                result["storeObservation"] = ("false" if value.get("store") is False else "other") if isinstance(value, dict) and "store" in value else "not-returned"
            remaining()
        except Exception as error:
            result.update(failureClass=type(error).__name__, normalTerminal=False)
        finally:
            try:
                if response is not None:
                    response.close()
                if connection is not None:
                    connection.close()
            except Exception as error:
                result.update(failureClass=type(error).__name__, normalTerminal=False)
            result["elapsedMs"] = round((time.monotonic() - started) * 1000, 2)
            if not cancelled.is_set():
                completed.put_nowait((result, value))

    # A daemon also bounds DNS/connect and a peer that trickles an unterminated
    # SSE line. A late connect observes cancellation before sending any request.
    threading.Thread(target=perform, daemon=True, name="phase12-rest-probe").start()
    try:
        return completed.get(timeout=timeout)
    except queue.Empty:
        cancelled.set()
        active_socket = state["socket"]
        if active_socket is not None:
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
                active_socket.close()
            except OSError:
                pass
        return {**base, "httpAttempts": int(state["requestStarted"]), "failureClass": "TimeoutError",
                "elapsedMs": round((time.monotonic() - started) * 1000, 2)}, None


def continuation(original_input, response, *, transport_result=None):
    """Preserve every returned Item and supply only the declared local function."""
    if transport_result is not None and (transport_result.get("status") != 200
            or transport_result.get("failureClass") or transport_result.get("storeObservation") == "other"):
        raise ValueError("The first function request was not fully received")
    if not isinstance(response, dict) or response.get("error") is not None or response.get("status") != "completed":
        raise ValueError("Function response is incomplete")
    output = response.get("output")
    if not isinstance(output, list):
        raise ValueError("Function output Items are missing")
    calls = [item for item in output if isinstance(item, dict) and item.get("type") == "function_call"]
    if len(calls) != 1:
        raise ValueError("Expected one declared function call")
    call = calls[0]
    arguments = json.loads(call.get("arguments", ""))
    if (call.get("name") != "echo_nonce" or not isinstance(call.get("call_id"), str) or not call["call_id"]
            or not isinstance(arguments, dict) or arguments != {"nonce": NONCE}):
        raise ValueError("Function call differs from the synthetic contract")
    return copy.deepcopy(original_input) + copy.deepcopy(output) + [
        {"type": "function_call_output", "call_id": call["call_id"],
         "output": json.dumps(arguments, separators=(",", ":"))}]


def response_text_contains(value, expected):
    output = value.get("output") if isinstance(value, dict) else None
    if not isinstance(output, list):
        return False
    for item in output:
        content = item.get("content") if isinstance(item, dict) else None
        if isinstance(content, list) and any(
                isinstance(part, dict) and part.get("type") == "output_text"
                and isinstance(part.get("text"), str) and expected in part["text"] for part in content):
            return True
    return False


def selected_routes(configuration, state, *, include_models=True):
    origin = state["outputs"]["endpoints"]["public"].rstrip("/")
    if origin != "https://" + configuration["gateway"]["publicHostName"]:
        raise ValueError("The public Gateway endpoint differs")
    definitions = {api["name"]: api for api in configuration["apiDefinitions"]}
    if not include_models:
        return origin, definitions, None, None, None, {}
    api = definitions["openai-inference-v1"]
    operations = {op["name"]: op for op in api["operations"]}
    constraints = api["authorization"]["operationConstraints"]
    names = [model["name"] for model in configuration["services"]["modelDeployments"]]
    common = set(constraints["chat-completions"]["allowedModels"]) & set(constraints["responses-create"]["allowedModels"])
    model = next(name for name in names if name in common)
    aliases = constraints["responses-create"].get("modelAliases", {})
    alias = next(key for key, target in aliases.items() if target in names)
    urls = {}
    for kind, name in (("chat", "chat-completions"), ("responses", "responses-create")):
        operation = operations[name]
        backend = operation.get("backendOverride", {}).get("backendService", api["backendService"])
        expected_path, expected_kind = (("/chat/completions", "bodyModel") if kind == "chat" else
                                        ("/responses", "statelessResponses"))
        if (operation["method"] != "POST" or operation["urlTemplate"] != expected_path
                or backend != "foundryOpenAi" or constraints[name].get("kind") != expected_kind):
            raise ValueError("The inference operation uses another contract")
        urls[kind] = origin + "/" + api["path"].strip("/") + operation["urlTemplate"]
    return origin, definitions, api["subscriptionKeyHeader"], model, alias, urls


def bearer_eligible(headers, configuration):
    """Local audience checks are eligibility only; APIM verifies the signature."""
    token = headers["Authorization"].removeprefix("Bearer ")
    payload = token.split(".")
    if len(payload) != 3:
        raise ValueError("A Gateway user token is required")
    claims = json.loads(base64.urlsafe_b64decode(payload[1] + "=" * (-len(payload[1]) % 4)))
    expected = entra_authentication_settings(configuration)
    if (claims.get("tid") != expected["tenantId"] or claims.get("aud") != expected["audience"]
            or claims.get("ver") != "2.0" or expected["requiredScope"] not in claims.get("scp", "").split()
            or not uuid.UUID(claims.get("oid", "")).int or type(claims.get("exp")) not in (int, float)
            or not math.isfinite(claims["exp"])
            or claims["exp"] <= time.time() + TIMEOUT_SECONDS):
        raise ValueError("Token is not locally eligible for this Gateway")


def execute(context, mode):
    with context.locked():
        configuration = resolve_publication_configuration(load_configuration_source(context.config_path), context.state)
        current = snapshot(context)
        gate = private_json(context, context.workspace.state / "phase12-two-stage-migration.json")
        waf = private_json(context, context.workspace.state / "phase12-waf-result.json")
        require_gates(gate, waf, current, source=ROOT, runtime=context.workspace.repository,
                      config=context.config_path,
                      module_hash=hashlib.sha256(api_publication.MODULE.read_bytes()).hexdigest())
        checkpoint = private_json(context, context.workspace.state / "gateway-api-source.json")
        plan = api_publication.prepare_plan(configuration, context.state)
        expected = api_publication.source_checkpoint(plan, checkpoint["source"], checkpoint["globalPolicy"],
            entra_authentication=checkpoint["entraAuthentication"], browser_source=checkpoint.get("browserSource"),
            generation_id=checkpoint.get("sourceGenerationId"))
        if checkpoint != expected:
            raise ValueError("The current checkpoint differs from the selected source")
        origin, definitions, key_header, model, alias, urls = selected_routes(configuration, context.state,
                                                                              include_models=mode != "invalid-bearer")
        if mode == "invalid-bearer":
            headers = {"Authorization": "Bearer synthetic-invalid-token"}
        else:
            credentials = private_json(context, context.workspace.state / "phase12-acceptance-credentials.json")
            if (Path(credentials["configFile"]).resolve() != context.config_path.resolve()
                    or Path(credentials["runtimeRoot"]).resolve() != context.workspace.repository):
                raise ValueError("Credential record belongs to another selected runtime")
            path = context.output_path(Path(credentials["keyFile" if mode == "key" else "bearerFile"]))
            headers = probe.auth_headers(key_file=path, key_header=key_header) if mode == "key" else probe.auth_headers(bearer_file=path)
            if mode == "bearer":
                bearer_eligible(headers, configuration)
        run = context.output_path(context.workspace.root / "evidence" / ("apim-phase12-acceptance-" + mode + "-" + uuid.uuid4().hex))
        results = []

        def save_case(name, result, passed):
            if snapshot(context) != current:
                raise ValueError("Protected inputs changed during REST acceptance")
            result = {"mode": mode, "case": name, "passed": bool(passed), "sdkMcpVerified": False,
                      "auditVerified": False, **result}
            context.save_private(run / (name + ".json"), result)
            results.append(result)
            print(json.dumps({"status": "case-passed" if passed else "case-not-verified", "mode": mode, "case": name,
                              "httpStatus": result.get("status")}), flush=True)

        if mode == "invalid-bearer":
            tests = []
            for api_name, operation_name in (("document-analysis-v1", "analyze_document"),
                                             ("document-analysis-v1", "get_document_operation"), ("pii-v1", "pii_redact_text")):
                api = definitions[api_name]
                operation = next(op for op in api["operations"] if op["name"] == operation_name)
                path = operation["urlTemplate"].replace("{operationId}", str(uuid.UUID(int=1)))
                tests.append((operation_name, origin + "/" + api["path"].strip("/") + path, operation["method"]))
            if not configuration["gateway"]["authentication"].get("credentialLogin"):
                raise ValueError("CM protected operations are not configured")
            tests.extend(("cm-" + name, origin + "/" + credential_login.API_PATH + "/" + credential_login.OPERATION_PATHS[name], method)
                         for name, method in credential_login.OPERATIONS.items())
            for name, url, method in tests:
                result, _ = http_exchange(url, method, {} if method == "POST" else None, headers)
                save_case(name, result, result.get("status") == 401 and not result.get("failureClass") and result.get("bodyEmpty") is True
                          and result.get("bearerChallenge") is True and result.get("noStore") is True
                          and bool(result.get("correlationIds", {}).get("apim-request-id")))
        else:
            for kind in ("chat", "responses"):
                for stream in (False, True):
                    body = probe.synthetic_body(kind, model, TOKEN_LIMIT, stream)
                    body["store"] = True
                    result, _ = http_exchange(urls[kind], "POST", body, headers, kind=kind, stream=stream)
                    passed = (result.get("status") == 200 and result.get("normalTerminal") is True
                              and result.get("storeObservation") != "other")
                    if stream:
                        passed = passed and result.get("outputObserved") is True
                    save_case(kind + ("-sse" if stream else "-json"), result, passed)
            for name, target in (("metadata-deployment", model), ("metadata-alias", alias)):
                body = probe.synthetic_body("responses", target, TOKEN_LIMIT)
                body.update(store=True, metadata=copy.deepcopy(DATE_METADATA))
                result, value = http_exchange(urls["responses"], "POST", body, headers, kind="responses")
                preserved = isinstance(value, dict) and value.get("metadata") == DATE_METADATA
                result["metadataExact"] = preserved
                save_case(name, result, result.get("status") == 200 and result.get("normalTerminal") is True
                          and result.get("storeObservation") != "other" and preserved)
            original = [{"role": "user", "content": "Call echo_nonce with nonce " + NONCE + ", then repeat the returned nonce."}]
            tools = [{"type": "function", "name": "echo_nonce", "description": "Echo the supplied synthetic nonce.", "strict": True,
                      "parameters": {"type": "object", "properties": {"nonce": {"type": "string"}}, "required": ["nonce"], "additionalProperties": False}}]
            body = {"model": model, "input": original, "tools": tools, "tool_choice": {"type": "function", "name": "echo_nonce"},
                    "parallel_tool_calls": False, "include": ["reasoning.encrypted_content"], "store": True, "max_output_tokens": TOKEN_LIMIT}
            first, value = http_exchange(urls["responses"], "POST", body, headers, kind="responses")
            context.save_private(run / "function-first-response.json", first)
            try:
                next_input = continuation(original, value, transport_result=first)
                body.update(input=next_input, tool_choice="none")
                second, final_value = http_exchange(urls["responses"], "POST", body, headers, kind="responses")
                passed = (second.get("status") == 200 and second.get("normalTerminal") is True
                          and second.get("storeObservation") != "other" and response_text_contains(final_value, NONCE))
                result = {**second, "steps": [first, second], "allReturnedItemsRetained": True, "functionResultMatched": passed}
            except Exception as error:
                passed, result = False, {**first, "failureClass": type(error).__name__, "allReturnedItemsRetained": False}
            save_case("function-roundtrip", result, passed)
            for name, field, value in (("ci-tools-denied", "tools", [{"type": "code_interpreter"}]),
                                       ("ci-input-denied", "input", [{"type": "code_interpreter_call"}])):
                body = probe.synthetic_body("responses", model, TOKEN_LIMIT)
                body.update(store=True)
                body[field] = value
                result, _ = http_exchange(urls["responses"], "POST", body, headers)
                save_case(name, result, result.get("status") == 403 and not result.get("failureClass") and result.get("noStore") is True
                          and bool(result.get("correlationIds", {}).get("apim-request-id")))
        passed = all(result["passed"] for result in results)
        context.save_private(run / "acceptance-result.json", {"mode": mode, "passed": passed, "cases": results,
            "inputCoreAndCheckpointUnchanged": snapshot(context) == current, "phase12Completed": False,
            "sdkMcpVerified": False, "auditVerified": False})
        print(json.dumps({"status": "rest-acceptance-verified" if passed else "rest-acceptance-incomplete",
                          "mode": mode, "phase12Completed": False}), flush=True)
        return 0 if passed else 1


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("Select key, bearer or invalid-bearer mode")


def main(argv=None):
    try:
        parser = SafeParser(description=__doc__)
        parser.add_argument("mode", choices=("key", "bearer", "invalid-bearer"))
        arguments = parser.parse_args(argv)
        return execute(prepared_context(), arguments.mode)
    except Exception:
        print('{"status":"rest-acceptance-preflight-or-run-stopped","phase12Completed":false}', flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
