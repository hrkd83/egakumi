"""基本API試験clientのHTTP・SSE・PII結果判定を検証する。

合成応答とローカルtransportを使い、要求上限・応答終端・安全な要約を確認する。外部モデルへの推論要求は送らない。
"""

import asyncio
import importlib.util
import json
import logging
from pathlib import Path
import socket
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, build_opener, urlopen

CORE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("integration_client", CORE / "scripts/integration_client.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
try:
    import httpx2
    import mcp
except ImportError:
    httpx2 = None


def pii_result():
    """許可された項目だけを持つPII応答例を作り、clientの判定を調べる。"""
    return {"redactedText": "連絡先は ******** です。", "language": "ja", "detectedCount": 1,
            "modelVersion": "2026-05-01", "warningCodes": [], "requestId": "11111111-1111-4111-8111-111111111111"}


class SummaryTests(unittest.TestCase):
    """基本API試験clientのHTTP・SSE・PII結果判定を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def test_metadata_never_contains_unknown_fields_or_body(self):
        secret = "RAW-PRIVATE-PROMPT-ACCESS-TOKEN"
        summary = probe.field_summary({secret: secret, "usage": {"input_tokens": 2}, "redactedText": secret})
        summary["usage"] = probe.usage_numbers({"input_tokens": 2, "output_tokens": secret, secret: 999,
                                               "output_tokens_details": {"reasoning_tokens": 1, secret: 3}})
        self.assertNotIn(secret, json.dumps(summary))
        self.assertEqual(summary["unknownFieldCount"], 1)
        self.assertEqual(summary["usage"], {"input_tokens": 2, "output_tokens_details.reasoning_tokens": 1})

    def test_credentials_require_owner_only_file_and_do_not_accept_whitespace(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "token"
            path.write_text("secret-token")
            path.chmod(0o644)
            with self.assertRaises(ValueError): probe.auth_headers(path)
            path.chmod(0o600)
            self.assertEqual(probe.auth_headers(path)["Authorization"], "Bearer secret-token")
            path.write_text("secret\ninjection")
            with self.assertRaises(ValueError): probe.auth_headers(path)
            path.unlink()
            path.symlink_to(Path(temp) / "other")
            with self.assertRaises((ValueError, FileNotFoundError)): probe.auth_headers(path)

    def test_chat_partial_and_missing_finish_are_not_success(self):
        stream = probe.StreamSummary("chat", time.monotonic())
        stream.event(json.dumps({"choices": [{"delta": {"content": "private reply"}}]}))
        self.assertFalse(stream.result()["normalTerminal"])
        stream.event("[DONE]")
        self.assertFalse(stream.result()["normalTerminal"])
        stream.event(json.dumps({"choices": [{"finish_reason": "stop"}], "usage": {"total_tokens": 3}}))
        self.assertTrue(stream.result()["normalTerminal"])
        self.assertNotIn("private reply", json.dumps(stream.result()))

    def test_responses_done_is_not_completed_and_incomplete_is_failure(self):
        stream = probe.StreamSummary("responses", time.monotonic())
        stream.event("[DONE]")
        self.assertFalse(stream.result()["normalTerminal"])
        stream.event(json.dumps({"type": "response.completed", "response": {"status": "completed", "usage": {"input_tokens": 2}}}))
        self.assertTrue(stream.result()["normalTerminal"])
        stream.event(json.dumps({"type": "response.incomplete"}))
        self.assertFalse(stream.result()["normalTerminal"])

    def test_model_empty_output_is_not_a_valid_normal_json_result(self):
        self.assertFalse(probe.json_shape("responses", {"status": "completed", "output": []}))
        self.assertFalse(probe.json_shape("chat", {"choices": [{"message": {"content": ""}}]}))

    def test_responses_nullable_error_and_real_text_are_checked_separately(self):
        """正常時の error:null と、実エラー・未完了・本文なしを取り違えない。"""
        answer = {"status": "completed", "error": None,
                  "output": [{"type": "message", "content": [
                      {"type": "output_text", "text": "PRIVATE-ANSWER"}]}]}
        self.assertTrue(probe.json_shape("responses", answer))
        for error in ({"code": "server_error"}, {}, False, ""):
            self.assertFalse(probe.json_shape("responses", {**answer, "error": error}))
        self.assertFalse(probe.json_shape("responses", {**answer, "status": "incomplete"}))
        for output in ([{"type": "reasoning"}], [{"content": [{"type": "output_text", "text": "  "}]}]):
            self.assertFalse(probe.json_shape("responses", {**answer, "output": output}))

    def test_chat_json_and_sse_require_stop_for_this_tool_free_probe(self):
        """本文や DONE だけで、枠不足・filter・tool 呼出しを正常完了にしない。"""
        for reason in ("stop", "length", "content_filter", "tool_calls", None):
            choice = {"message": {"content": "PRIVATE-REPLY"}}
            terminal = {}
            if reason is not None:
                choice["finish_reason"] = reason
                terminal["finish_reason"] = reason
            expected = reason == "stop"
            with self.subTest(reason=reason):
                self.assertEqual(probe.json_shape("chat", {"choices": [choice]}), expected)
                stream = probe.StreamSummary("chat", time.monotonic())
                stream.event(json.dumps({"choices": [{"delta": {"content": "PRIVATE-REPLY"}}]}))
                stream.event(json.dumps({"choices": [terminal]}))
                stream.event("[DONE]")
                self.assertEqual(stream.result()["normalTerminal"], expected)
                self.assertNotIn("PRIVATE-REPLY", json.dumps(stream.result()))
        stream = probe.StreamSummary("chat", time.monotonic())
        stream.event(json.dumps({"choices": [{"finish_reason": "stop"}, {"finish_reason": "length"}]}))
        stream.event("[DONE]")
        self.assertFalse(stream.result()["normalTerminal"])

    def test_oauth_endpoints_are_bound_to_an_explicit_tenant(self):
        tenant = "11111111-1111-4111-8111-111111111111"
        base = f"https://login.microsoftonline.com/{tenant}"
        config = {"client_id": tenant, "issuer": base + "/v2.0", "scope": "https://gateway.example.com/Gateway.Invoke",
                  "authorization_endpoint": base + "/oauth2/v2.0/authorize", "token_endpoint": base + "/oauth2/v2.0/token"}
        probe.validate_oauth_config(config)
        for field in ("authorization_endpoint", "token_endpoint"):
            with self.assertRaises(ValueError):
                probe.validate_oauth_config({**config, field: "https://unapproved.example/token"})


@unittest.skipUnless(httpx2, "Use .tools/integration-venv for the actual SDK fixture tests")
class HttpTests(unittest.IsolatedAsyncioTestCase):
    """基本API試験clientのHTTP・SSE・PII結果判定を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    async def asyncSetUp(self):
        logging.disable(logging.CRITICAL)

    async def test_chat_http200_json_and_sse_still_require_normal_stop(self):
        """実 parser を通し、200・本文ありでも不完全終端を合格にしない。"""
        for streaming in (False, True):
            for reason in ("stop", "length", "content_filter", "tool_calls", None):
                calls = []
                def handler(request):
                    calls.append(request)
                    terminal = {} if reason is None else {"finish_reason": reason}
                    if streaming:
                        events = [{"choices": [{"delta": {"content": "PRIVATE-REPLY"}}]}, {"choices": [terminal]}]
                        body = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
                        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})
                    return httpx2.Response(200, json={"choices": [{**terminal, "message": {"content": "PRIVATE-REPLY"}}]})
                result = await probe.rest_probe("https://gateway.example/chat", "chat", {}, {}, stream=streaming,
                                                transport=httpx2.MockTransport(handler))
                with self.subTest(streaming=streaming, reason=reason):
                    self.assertEqual(result["status"], 200)
                    self.assertEqual(result["succeeded"], reason == "stop")
                    self.assertEqual(result["normalTerminal"], reason == "stop")
                    self.assertEqual(len(calls), 1)
                    self.assertNotIn("PRIVATE-REPLY", json.dumps(result))

    async def test_rest_json_error_not_retried_or_logged(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx2.Response(429, json={"error": {"message": "RAW-TOKEN-SECRET"}}, headers={"x-request-id": "11111111-1111-4111-8111-111111111111"})
        result = await probe.rest_probe("https://gateway.example/models/responses", "responses", {}, {}, transport=httpx2.MockTransport(handler))
        self.assertEqual(result["status"], 429)
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["succeeded"])
        self.assertNotIn("RAW-TOKEN-SECRET", json.dumps(result))

    async def test_connection_expected_subject_is_compared_without_recording_profile_or_id(self):
        """Graph の実本人を照合し、型だけが正しい別人の200応答を合格にしない。"""
        subject = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        for actual, matched in ((subject.upper(), True),
                                ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", False),
                                ("invalid-profile-id", False)):
            requests = []
            def handler(request):
                requests.append(request)
                return httpx2.Response(200, json={"id": actual, "displayName": "PRIVATE-PROFILE-NAME"})
            result = await probe.rest_probe("https://gateway.example/tools/graph/v1/me", "connection", None, {},
                                            expected_subject_id=subject, transport=httpx2.MockTransport(handler))
            with self.subTest(matched=matched, actual_format_valid=actual != "invalid-profile-id"):
                self.assertEqual(result["subjectMatched"], matched)
                self.assertEqual(result["succeeded"], matched)
                self.assertEqual(result["normalTerminal"], matched)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].method, "GET")
                for private_value in (actual, subject, "PRIVATE-PROFILE-NAME"):
                    self.assertNotIn(private_value, json.dumps(result))

    async def test_connection_subject_check_is_optional_and_http_errors_never_match(self):
        """既存呼出しとの互換を保ち、エラー本文中の id を本人確認に使わない。"""
        subject = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        compatible = await probe.rest_probe("https://gateway.example/me", "connection", None, {},
            transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={"id": "legacy-profile-id"})))
        self.assertTrue(compatible["succeeded"])
        self.assertNotIn("subjectMatched", compatible)
        denied = await probe.rest_probe("https://gateway.example/me", "connection", None, {},
            expected_subject_id=subject,
            transport=httpx2.MockTransport(lambda request: httpx2.Response(403, json={"id": subject})))
        self.assertFalse(denied["subjectMatched"])
        self.assertFalse(denied["succeeded"])
        self.assertNotIn(subject, json.dumps(denied))

    async def test_invalid_subject_options_are_rejected_before_any_request(self):
        """不正 GUID と別 API への誤指定は、認証情報を送る前に拒否する。"""
        calls = []
        def handler(request):
            calls.append(request)
            return httpx2.Response(200, json={})
        for kind, subject in (("connection", "not-a-guid"),
                              ("connection", "00000000-0000-0000-0000-000000000000"),
                              ("pii", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                await probe.rest_probe("https://gateway.example/me", kind, None, {}, expected_subject_id=subject,
                                        transport=httpx2.MockTransport(handler))
        self.assertEqual(calls, [])

    async def test_real_sse_parser_requires_terminal_and_records_usage(self):
        payload = 'data: {"type":"response.output_text.delta","delta":"RAW-PRIVATE-REPLY"}\n\ndata: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        def handler(request):
            return httpx2.Response(200, content=payload, headers={"content-type": "text/event-stream"})
        result = await probe.rest_probe("https://gateway.example/models/responses", "responses", {}, {}, stream=True, transport=httpx2.MockTransport(handler))
        self.assertTrue(result["succeeded"])
        self.assertEqual(result["eventCount"], 2)
        self.assertEqual(result["usage"]["output_tokens"], 1)
        self.assertNotIn("RAW-PRIVATE-REPLY", json.dumps(result))
        partial = await probe.rest_probe("https://gateway.example/models/responses", "responses", {}, {}, stream=True,
                                         stop_after_events=1, transport=httpx2.MockTransport(handler))
        self.assertFalse(partial["succeeded"])
        self.assertTrue(partial["interruptedByClient"])

    async def test_pii_shape_does_not_accept_unredacted_extra_fields(self):
        value = {**pii_result(), "entities": [{"text": "RAW-PRIVATE-CONTACT"}]}
        result = await probe.rest_probe("https://gateway.example/tools/pii/v1/redact", "pii", {}, {},
                                        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=value)))
        self.assertFalse(result["succeeded"])
        self.assertTrue(result["unexpectedPiiFields"])
        self.assertNotIn("RAW-PRIVATE-CONTACT", json.dumps(result))

    async def mcp_fixture(self, oauth=True, bad_state=False, token_file=None, prm_patch=None, authority_patch=None,
                          tools_result=None, list_error=False):
        from mcp.client.auth import AuthorizationCodeResult
        url = "https://gateway.example/tools/pii-v1-mcp/mcp"
        shared_resource = "https://gateway.example"
        authority = "https://login.microsoftonline.com/11111111-1111-4111-8111-111111111111"
        issuer = authority + "/v2.0"
        config = {"client_id": "22222222-2222-4222-8222-222222222222", "redirect_uri": "http://localhost:8400/callback",
                  "scope": "https://gateway.example.com/Gateway.Invoke", "issuer": issuer,
                  "authorization_endpoint": authority + "/oauth2/v2.0/authorize",
                  "token_endpoint": authority + "/oauth2/v2.0/token"}
        records, authorization = [], {}

        class Login:
            """基本API試験clientのHTTP・SSE・PII結果判定を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
            async def redirect(self, authorization_url):
                authorization.update(parse_qs(urlsplit(authorization_url).query))
            async def callback(self):
                return AuthorizationCodeResult(code="MOCK-CODE-PRIVATE", state="bad" if bad_state else authorization["state"][0], iss=issuer)

        def handler(request):
            records.append((request.method, str(request.url)))
            location = str(request.url)
            if request.method == "GET" and "oauth-protected-resource" in location:
                if location != shared_resource + "/.well-known/oauth-protected-resource":
                    return httpx2.Response(404)
                return httpx2.Response(200, json={"resource": shared_resource, "authorization_servers": [issuer],
                    "scopes_supported": [config["scope"]], **(prm_patch or {})})
            if request.method == "GET" and ("oauth-authorization-server" in location or "openid-configuration" in location):
                return httpx2.Response(200, json={"issuer": issuer, "authorization_endpoint": config["authorization_endpoint"],
                    "token_endpoint": config["token_endpoint"], "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"],
                    "grant_types_supported": ["authorization_code", "refresh_token"], "token_endpoint_auth_methods_supported": ["none"],
                    "scopes_supported": [config["scope"]], **(authority_patch or {})})
            if location == config["token_endpoint"]:
                token_body = parse_qs(request.content.decode())
                self.assertIn("code_verifier", token_body)
                self.assertEqual(token_body["resource"], [shared_resource])
                return httpx2.Response(200, json={"access_token": "MOCK-ACCESS-TOKEN-PRIVATE", "token_type": "Bearer", "expires_in": 3600, "scope": config["scope"]})
            if request.method == "POST" and location == url:
                if "authorization" not in request.headers:
                    return httpx2.Response(401, headers={"WWW-Authenticate": 'Bearer scope="https://gateway.example.com/Gateway.Invoke"'})
                message = json.loads(request.content)
                if message["method"] == "notifications/initialized": return httpx2.Response(202)
                if message["method"] == "initialize":
                    result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture", "version": "1"}}
                elif message["method"] == "tools/list":
                    if list_error:
                        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": message["id"],
                            "error": {"code": -32603, "message": "PRIVATE-PROVIDER-ERROR-BODY"}})
                    result = tools_result if tools_result is not None else {"tools": [{"name": "pii_redact_text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "language": {"type": "string"}}, "required": ["text", "language"]}}]}
                elif message["method"] == "tools/call":
                    result = {"content": [{"type": "text", "text": json.dumps(pii_result())}], "structuredContent": pii_result(), "isError": False}
                else: raise AssertionError("Unexpected fixture method")
                return httpx2.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})
            if request.method == "GET" and location == url: return httpx2.Response(405)
            raise AssertionError("Unexpected fixture endpoint")

        result = await probe.mcp_probe(url, {"text": "PRIVATE-SYNTHETIC-INPUT", "language": "ja"},
            {} if oauth else {"Authorization": "Bearer MANUAL-TOKEN-PRIVATE"}, oauth_config=config if oauth else None,
            login=Login() if oauth else None, token_file=token_file, timeout=5, transport=httpx2.MockTransport(handler))
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertFalse(any("register" in location for _, location in records))
        return result, authorization

    async def test_actual_mcp_sdk_discovers_metadata_runs_pkce_and_calls_tool(self):
        result, authorization = await self.mcp_fixture()
        self.assertTrue(result["succeeded"], result)
        self.assertTrue(result["oauthFlowVerified"])
        self.assertEqual(result["toolPostCount"], 1)
        self.assertEqual(authorization["code_challenge_method"], ["S256"])
        self.assertEqual(authorization["client_id"], ["22222222-2222-4222-8222-222222222222"])
        self.assertEqual(authorization["resource"], ["https://gateway.example"])

    async def test_mcp_manual_bearer_is_not_sdk_authentication_pass(self):
        result, _ = await self.mcp_fixture(oauth=False)
        self.assertTrue(result["succeeded"], result)
        self.assertFalse(result["oauthFlowVerified"])
        self.assertEqual(result["authenticationMode"], "manual-credential")

    async def test_mcp_diagnostics_distinguish_empty_list_name_and_schema_without_calling(self):
        """HTTP200だけで合格にせず、未広告/別名/包装schemaを本文なしで分ける。"""
        schema = {"type": "object", "properties": {"text": {"type": "string"}, "language": {"type": "string"}}, "required": ["text", "language"]}
        scenarios = [
            ("empty", {"tools": []}, 0),
            ("name", {"tools": [{"name": "PRIVATE-prefix-pii_redact_text", "inputSchema": schema}]}, 1),
            ("schema", {"tools": [{"name": "pii_redact_text", "inputSchema": {"type": "object", "properties": {"PRIVATE-wrapper": {"type": "object"}}}}]}, 1),
        ]
        for label, tools, count in scenarios:
            result, _ = await self.mcp_fixture(tools_result=tools)
            with self.subTest(label=label):
                self.assertFalse(result["succeeded"])
                self.assertEqual(result["mcpPhase"], "checking-tool-schema")
                self.assertTrue(result["toolsListReceived"])
                self.assertEqual(result["toolsAdvertisedCount"], count)
                self.assertEqual(result["toolPostCount"], 0)
                self.assertIn("ValueError", result["failureLeaves"])
                self.assertTrue(result["oauthTokenExchangeSucceeded"])
                self.assertFalse(result["oauthFlowVerified"])
                self.assertEqual([step["rpcMethod"] for step in result["httpSteps"] if step["role"] == "mcp" and step["httpMethod"] == "POST"],
                                 ["initialize", "initialize", "notifications/initialized", "tools/list"])
                self.assertNotIn("PRIVATE", json.dumps(result))
                if label == "name":
                    self.assertFalse(result["toolDescriptors"][0]["expectedNameEquals"])
                    self.assertTrue(result["toolDescriptors"][0]["expectedNameSuffix"])
                if label == "schema":
                    self.assertTrue(result["toolPresent"])
                    self.assertFalse(result["toolInputFieldsValid"])

    async def test_mcp_diagnostics_keep_only_rpc_code_and_leaf_class_for_list_errors(self):
        """SDKの例外に含まれるprovider messageや認証値を保存しない。"""
        result, _ = await self.mcp_fixture(list_error=True)
        self.assertFalse(result["succeeded"])
        self.assertEqual(result["mcpPhase"], "listing-tools")
        self.assertFalse(result["toolsListReceived"])
        self.assertEqual(result["toolPostCount"], 0)
        self.assertIn("MCPError", result["failureLeaves"])
        self.assertEqual(result["rpcErrorCodes"], [-32603])
        self.assertNotIn("PRIVATE", json.dumps(result))

    async def test_mcp_oauth_rejects_another_resource_authority_or_scope_before_login(self):
        """公開resource・tenant・scopeをSDKの発見結果で勝手に変更しない。"""
        other = "https://login.microsoftonline.com/33333333-3333-4333-8333-333333333333/v2.0"
        cases = [("resource", {"prm_patch": {"resource": "https://gateway.example/tools/another/mcp"}}),
                 ("slash", {"prm_patch": {"resource": "https://gateway.example/"}}),
                 ("authority", {"prm_patch": {"authorization_servers": [other]}}),
                 ("scope", {"prm_patch": {"scopes_supported": ["https://graph.microsoft.com/User.Read"]}}),
                 ("token", {"authority_patch": {"token_endpoint": "https://unapproved.example/token"}})]
        for name, kwargs in cases:
            with self.subTest(name=name):
                result, authorization = await self.mcp_fixture(**kwargs)
                self.assertFalse(result["succeeded"])
                self.assertFalse(result["oauthFlowVerified"])
                self.assertEqual(result["toolPostCount"], 0)
                self.assertFalse(authorization)

    async def test_actual_mcp_sdk_rejects_oauth_state_mismatch(self):
        result, _ = await self.mcp_fixture(bad_state=True)
        self.assertFalse(result["succeeded"])
        self.assertFalse(result["oauthFlowVerified"])
        self.assertEqual(result["toolPostCount"], 0)

    async def test_sdk_token_store_is_separate_owner_only_and_cache_is_not_initial_login(self):
        with tempfile.TemporaryDirectory() as temp:
            token_file = Path(temp) / "sdk-tokens.json"
            fresh, _ = await self.mcp_fixture(token_file=token_file)
            self.assertTrue(fresh["oauthFlowVerified"])
            self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)
            self.assertIn("tokens", json.loads(probe.private_text(token_file)))
            cached, authorization = await self.mcp_fixture(token_file=token_file)
            self.assertTrue(cached["succeeded"])
            self.assertFalse(cached["oauthFlowVerified"])
            self.assertFalse(authorization)

    async def test_registered_root_loopback_callback_returns_state_and_never_logs_code(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        with tempfile.TemporaryDirectory() as temp:
            url_file = Path(temp) / "login.url"
            config = {"redirect_uri": f"http://localhost:{port}", "authorization_endpoint": "https://identity.example/authorize"}
            login = probe.LoopbackLogin(config, url_file, 10)
            try:
                await login.redirect("https://identity.example/authorize?state=SAFE-STATE")
                pending = asyncio.create_task(login.callback())
                def send_callback():
                    # テスト用のローカル受信口へ、専用の HTTP クライアントで接続する。
                    local = build_opener(ProxyHandler({}))
                    with local.open(f"http://127.0.0.1:{port}/?code=PRIVATE-CODE&state=SAFE-STATE", timeout=10) as response:
                        return response.status, response.read().decode()
                status, body = await asyncio.to_thread(send_callback)
                callback = await pending
                self.assertEqual(status, 200)
                self.assertEqual(callback.state, "SAFE-STATE")
                self.assertEqual(callback.code, "PRIVATE-CODE")
                self.assertNotIn("PRIVATE-CODE", body)
                self.assertFalse(url_file.exists())
                self.assertEqual(login.server.server_address[0], "127.0.0.1")
            finally:
                login.close()


if __name__ == "__main__":
    unittest.main()
