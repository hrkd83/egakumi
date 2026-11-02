"""音声試験用loopback relayの認証・所有者・停止処理を検証する。

偽の上流とローカルHTTP/WebSocket接続を使い、CSRF・接続元・要求上限・音声sessionの扱いを確認する。マイクや実音声サービスは使わない。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import ipaddress
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest

from aiohttp import ClientSession, CookieJar, WSMsgType, web
from aiohttp.test_utils import TestServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import realtime_browser as browser


class RealtimeBrowserTests(unittest.IsolatedAsyncioTestCase):
    """音声試験用loopback relayの認証・所有者・停止処理を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.token = "PRIVATE_GATEWAY_TOKEN"
        self.ticket = "PRIVATE_OWNERSHIP.TICKET"
        self.answer = "v=0\r\ns=PRIVATE_ANSWER\r\n"
        token = self.root / "token"
        token.write_text(self.token); token.chmod(0o600)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "loopback-fixture")])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
        cert_path, key_path = self.root / "fixture.crt", self.root / "fixture.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption())); key_path.chmod(0o600)
        server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); server_ssl.load_cert_chain(cert_path, key_path)
        self.client_ssl = ssl.create_default_context(cafile=str(cert_path))
        self.calls = []
        self.sink_calls = 0
        self.create_status = 201
        self.observer_sockets = set()
        async def create(request):
            self.calls.append({"operation": "create", "headers": dict(request.headers), "body": await request.json()})
            if self.create_status != 201:
                return web.Response(status=self.create_status, text="PRIVATE_ERROR_BODY")
            return web.Response(status=201, text=self.answer, content_type="application/sdp",
                headers={"X-Core-Call-Id": "call-fixture", "X-Core-Call-Ticket": self.ticket,
                         "apim-request-id": "11111111-1111-4111-8111-111111111111",
                         "Location": "https://foreign.invalid/MUST_NOT_FOLLOW"})
        async def hangup(request):
            self.calls.append({"operation": "hangup", "headers": dict(request.headers), "path": request.path})
            for socket in list(self.observer_sockets):
                await socket.close()
            return web.Response(status=204)
        async def observer(request):
            if request.query.get("call_id") != "call-fixture" or request.headers.get("X-Core-Call-Ticket") != self.ticket:
                raise web.HTTPForbidden()
            self.calls.append({"operation": "observer", "headers": dict(request.headers), "query": dict(request.query)})
            socket = web.WebSocketResponse(protocols=["realtime"])
            await socket.prepare(request)
            self.observer_sockets.add(socket)
            await socket.send_json({"type": "session.created", "session": {"id": "PRIVATE_SESSION"}})
            event = {"type": "response.done", "response": {"id": "PRIVATE_RESPONSE", "status": "completed", "usage": {
                "input_tokens": 3, "output_tokens": 4, "total_tokens": 7}, "output": "PRIVATE_TRANSCRIPT"}}
            await socket.send_json(event)
            await socket.send_json(event)  # Duplicate observation must not double-count.
            async for message in socket:
                self.calls.append({"operation": "unexpected-observer-command"})
            self.observer_sockets.discard(socket)
            return socket
        async def voice(request):
            self.calls.append({"operation": "voice", "headers": dict(request.headers)})
            socket = web.WebSocketResponse(protocols=["realtime"])
            await socket.prepare(request)
            offer = await socket.receive_json(timeout=5)
            self.calls.append({"operation": "offer", "body": offer})
            await socket.send_json({"type": "session.created", "client_secret": "PRIVATE_EPHEMERAL", "text": "PRIVATE_TRANSCRIPT"})
            await socket.send_json({"type": "rtc.call.sdp.created", "sdp_answer": self.answer,
                                    "rtc_call_id": "PRIVATE_SERVER_SESSION_ID"})
            await socket.send_json({"type": "response.done", "response": {"usage": {"input_tokens": 3,
                "output_tokens": 4, "PRIVATE_USAGE_KEY": 42}, "text": "PRIVATE_TRANSCRIPT"}})
            async for message in socket:
                if message.type == WSMsgType.TEXT:
                    self.calls.append({"operation": "control", "body": json.loads(message.data)})
                    await socket.send_json({"type": "response.done", "response": {"status": "cancelled", "text": "PRIVATE_CANCELLED_TEXT"}})
            return socket
        async def sink(request):
            self.sink_calls += 1
            return web.Response(text="redirect was incorrectly followed")
        async def redirect(request):
            return web.Response(status=307, headers={"Location": "/sink"})
        stub = web.Application()
        stub.add_routes([web.post("/calls", create), web.post("/calls/{call}/hangup", hangup),
                         web.get("/voice", voice), web.get("/observer", observer), web.get("/redirect-voice", redirect),
                         web.post("/redirect-create", redirect), web.route("*", "/sink", sink)])
        self.upstream = TestServer(stub, host="127.0.0.1", scheme="https")
        await self.upstream.start_server(ssl=server_ssl)
        self.settings = browser.Settings(key_file=str(token), create_url=str(self.upstream.make_url("/calls")),
            voice_live_url=str(self.upstream.make_url("/voice")).replace("https:", "wss:"))
        self.app = browser.create_app(self.settings, upstream_ssl=self.client_ssl)
        self.server = TestServer(self.app, host="127.0.0.1")
        await self.server.start_server()
        self.relay = self.app[browser.RELAY_KEY]
        self.relay.origin = str(self.server.make_url("/")).rstrip("/")
        self.client = ClientSession(cookie_jar=CookieJar(unsafe=True))
        async with self.client.get(self.server.make_url("/")) as response:
            self.assertEqual(response.status, 200)
            self.html = await response.text()
        async with self.client.get(self.server.make_url("/bootstrap")) as response:
            self.bootstrap = await response.json()
        self.headers = {"Origin": self.relay.origin, "X-Core-CSRF": self.bootstrap["csrf"]}

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        await self.upstream.close()
        self.directory.cleanup()

    async def post(self, path, value=None, headers=None):
        return await self.client.post(self.server.make_url(path), json={} if value is None else value,
                                      headers=self.headers if headers is None else headers)

    async def test_real_tls_create_owner_control_and_body_free_evidence(self):
        async with await self.post("/api/realtime", {"sdp": "v=0\r\ns=PRIVATE_OFFER\r\n"}) as response:
            self.assertEqual(response.status, 200)
            result = await response.json()
            self.assertEqual(set(result), {"id", "sdp"})
            self.assertNotIn(self.ticket, json.dumps(result))
        self.assertEqual(self.calls[0]["headers"]["api-key"], self.token)
        self.assertEqual(self.calls[0]["body"], {"sdp": "v=0\r\ns=PRIVATE_OFFER\r\n"})
        self.assertNotIn("X-Core-Call-Ticket", self.calls[0]["headers"])
        async with await self.post("/api/realtime/unknown/hangup") as response:
            self.assertEqual(response.status, 404)
        async with await self.post("/api/realtime/" + result["id"] + "/hangup") as response:
            self.assertEqual(await response.json(), {"hangupConfirmed": True})
        hangup = self.calls[-1]
        self.assertEqual(hangup["headers"]["X-Core-Call-Ticket"], self.ticket)
        self.assertEqual(hangup["path"], "/calls/call-fixture/hangup")
        async with await self.post("/api/observe", {"sdp": "PRIVATE_OFFER", "text": "PRIVATE_TRANSCRIPT",
            "bytesReceived": 123, "protocol": "udp", "remoteAddress": "192.0.2.1", "remotePort": 3478,
            "totalAudioEnergy": 0.125, "totalSamplesDuration": 1.2, "audioContextState": "running",
            "dataChannelState": "open", "sourcePlaybackStarted": True,
            "usage": {"input_tokens": 8, "secret": self.token}}) as response:
            self.assertEqual(response.status, 200)
        async with await self.post("/api/evidence") as response:
            exported = await response.text()
            evidence = json.loads(exported)
        self.assertFalse(evidence["trustedUsageVerified"])
        self.assertFalse(evidence["runtimeVerified"])
        self.assertEqual(evidence["activeOwnedCalls"], 0)
        self.assertEqual(evidence["clientObservations"]["bytesReceived"], 123)
        self.assertEqual(evidence["clientObservations"]["totalAudioEnergy"], 0.125)
        self.assertEqual(evidence["clientObservations"]["totalSamplesDuration"], 1.2)
        self.assertEqual(evidence["clientObservations"]["audioContextState"], "running")
        self.assertEqual(evidence["clientObservations"]["dataChannelState"], "open")
        self.assertIs(evidence["clientObservations"]["sourcePlaybackStarted"], True)
        self.assertEqual(evidence["serverCorrelations"], [{"apim-request-id": "11111111-1111-4111-8111-111111111111"}])
        for private in (self.token, self.ticket, "PRIVATE_OFFER", "PRIVATE_TRANSCRIPT", "PRIVATE_ANSWER", "192.0.2.1"):
            self.assertNotIn(private, exported)
            self.assertNotIn(private, self.html)

    async def test_wrong_origin_host_csrf_and_browser_destination_are_rejected(self):
        variants = ({"Origin": "https://evil.invalid", "X-Core-CSRF": self.relay.csrf},
                    {"Origin": self.relay.origin}, {"X-Core-CSRF": self.relay.csrf},
                    {**self.headers, "Host": "evil.invalid"})
        for headers in variants:
            async with await self.post("/api/realtime", {"sdp": "v=0"}, headers=headers) as response:
                self.assertEqual(response.status, 403)
        for value in ({"sdp": "v=0", "url": "https://evil.invalid"}, {"sdp": "bad"}):
            async with await self.post("/api/realtime", value) as response:
                self.assertEqual(response.status, 400)
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(Exception, "403"):
            await self.client.ws_connect(self.server.make_url("/api/voice-live"), origin="https://evil.invalid",
                                         protocols=["core-realtime", self.relay.csrf])
        with self.assertRaisesRegex(Exception, "403"):
            await self.client.ws_connect(self.server.make_url("/api/voice-live"), origin=self.relay.origin,
                                         protocols=["core-realtime", "wrong-csrf"])
        self.assertEqual(self.calls, [])

    async def test_audio_observations_reject_unknown_states_and_non_numeric_energy(self):
        """端末の任意文字列を音声状態・エネルギーの証跡として保存しない。"""
        async with await self.post("/api/observe", {"totalAudioEnergy": "PRIVATE-energy", "totalSamplesDuration": -1,
            "audioContextState": "PRIVATE-context", "dataChannelState": "PRIVATE-channel", "sourcePlaybackStarted": 1}) as response:
            self.assertEqual(response.status, 200)
        async with await self.post("/api/evidence") as response:
            evidence = await response.json()
        self.assertEqual(evidence["clientObservations"], {"usage": {}})
        self.assertFalse(evidence["trustedUsageVerified"])
        self.assertNotIn("PRIVATE", json.dumps(evidence))

    async def test_real_wss_voice_live_sdp_fixed_configuration_and_sanitized_events(self):
        async with self.client.ws_connect(self.server.make_url("/api/voice-live"), origin=self.relay.origin,
                protocols=["core-realtime", self.relay.csrf]) as socket:
            await socket.send_json({"sdp": "v=0\r\ns=PRIVATE_OFFER\r\n"})
            first = await socket.receive_json(timeout=5)
            second = await socket.receive_json(timeout=5)
            third = await socket.receive_json(timeout=5)
            self.assertEqual(first, {"type": "session.created", "usage": {}, "failed": False})
            self.assertEqual(second, {"type": "rtc.call.sdp.created", "sdp_answer": self.answer})
            self.assertEqual(third["usage"], {"input_tokens": 3, "output_tokens": 4})
            self.assertEqual(self.calls[0]["headers"]["api-key"], self.token)
            offer = next(call["body"] for call in self.calls if call["operation"] == "offer")
            self.assertEqual(offer["type"], "rtc.call.sdp.create")
            self.assertEqual(offer["session"], self.settings.voice_session)
            await socket.send_json({"action": "cancel"})
            for _ in range(20):
                if any(call["operation"] == "control" for call in self.calls):
                    break
                await asyncio.sleep(0.01)
            self.assertIn({"operation": "control", "body": {"type": "response.cancel"}}, self.calls)
            cancelled = await socket.receive_json(timeout=5)
            self.assertEqual(cancelled, {"type": "response.done", "usage": {}, "failed": False, "responseStatus": "cancelled"})
            self.assertEqual(self.relay.evidence["controlCancelRequests"], 1)
            self.assertEqual(self.relay.evidence["serverCancelledResponses"], 1)
        async with await self.post("/api/evidence") as response:
            exported = await response.text()
        for private in (self.token, self.ticket, "PRIVATE_EPHEMERAL", "PRIVATE_TRANSCRIPT", "PRIVATE_OFFER", "PRIVATE_SERVER_SESSION_ID", "PRIVATE_ANSWER", "PRIVATE_CANCELLED_TEXT"):
            self.assertNotIn(private, exported)

    async def test_owner_bound_server_observer_keeps_usage_separate_and_deduplicates(self):
        self.settings.sideband_url = str(self.upstream.make_url("/observer")).replace("https:", "wss:")
        self.relay.evidence["sideband"]["configured"] = True
        async with await self.post("/api/realtime", {"sdp": "v=0\r\ns=PRIVATE_OFFER"}) as response:
            self.assertEqual(response.status, 200)
            result = await response.json()
        for _ in range(20):
            if self.relay.evidence["sideband"]["usageEvents"]:
                break
            await asyncio.sleep(0.01)
        await self.post("/api/observe", {"usage": {"total_tokens": 999}})
        async with await self.post("/api/evidence") as response:
            evidence = await response.json()
        self.assertEqual(evidence["serverObservedUsage"], {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})
        self.assertEqual(evidence["serverObservedResponseStatuses"], {"completed": 1})
        self.assertEqual(evidence["clientObservations"]["usage"], {"total_tokens": 999})
        self.assertEqual(evidence["sideband"]["usageEvents"], 1)
        self.assertFalse(evidence["sideband"]["billableUsageReconciled"])
        self.assertFalse(evidence["trustedUsageVerified"])
        call = next(item for item in self.calls if item["operation"] == "observer")
        self.assertEqual(call["query"], {"call_id": "call-fixture"})
        self.assertEqual(call["headers"]["api-key"], self.token)
        self.assertEqual(call["headers"]["X-Core-Call-Ticket"], self.ticket)
        self.assertNotIn("unexpected-observer-command", [item["operation"] for item in self.calls])
        for private in (self.ticket, self.token, "PRIVATE_SESSION", "PRIVATE_RESPONSE", "PRIVATE_TRANSCRIPT"):
            self.assertNotIn(private, json.dumps(evidence))
            self.assertNotIn(private, json.dumps(result))
        async with await self.post("/api/realtime/" + result["id"] + "/hangup") as response:
            self.assertEqual(await response.json(), {"hangupConfirmed": True})
        self.assertEqual(self.relay.observers, {})
        self.assertEqual(self.relay.evidence["sideband"]["closed"], 1)

    async def test_observer_redirect_never_follows_and_owned_call_is_hung_up(self):
        self.settings.sideband_url = str(self.upstream.make_url("/redirect-voice")).replace("https:", "wss:")
        async with await self.post("/api/realtime", {"sdp": "v=0"}) as response:
            self.assertEqual(response.status, 502)
            self.assertNotIn(self.ticket, await response.text())
        self.assertEqual(self.sink_calls, 0)
        self.assertEqual([call["operation"] for call in self.calls], ["create", "hangup"])
        self.assertEqual(self.relay.calls, {})

    async def test_relay_exit_hangs_up_owned_session_and_closes_server_observer(self):
        self.settings.sideband_url = str(self.upstream.make_url("/observer")).replace("https:", "wss:")
        async with await self.post("/api/realtime", {"sdp": "v=0"}) as response:
            self.assertEqual(response.status, 200)
            await response.json()
        await self.server.close()
        self.assertEqual(self.relay.calls, {})
        self.assertEqual(self.relay.observers, {})
        self.assertEqual(self.relay.evidence["hangupConfirmed"], 1)
        self.assertTrue(any(call["operation"] == "hangup" for call in self.calls))

    async def test_redirects_are_not_followed_for_http_or_wss(self):
        self.settings.create_url = str(self.upstream.make_url("/redirect-create"))
        async with await self.post("/api/realtime", {"sdp": "v=0"}) as response:
            self.assertEqual(response.status, 502)
        self.settings.voice_live_url = str(self.upstream.make_url("/redirect-voice")).replace("https:", "wss:")
        with self.assertRaisesRegex(Exception, "502"):
            await self.client.ws_connect(self.server.make_url("/api/voice-live"), origin=self.relay.origin,
                                         protocols=["core-realtime", self.relay.csrf])
        self.assertEqual(self.sink_calls, 0)

    async def test_data_channel_counts_reject_unknown_names_types_and_oversized_values(self):
        """診断イベント名と有限な整数件数だけを受け取り、本文等の任意値を落とす。"""
        counts = {"input_audio_buffer.speech_started": 1, "input_audio_buffer.speech_stopped": 2,
                  "input_audio_buffer.committed": 0, "response.created": 1_000_000,
                  "response.done": 1_000_001, "error": True, "rtc.call.error": 1.5,
                  "conversation.item.created": -1, "input_audio_buffer.cleared": "PRIVATE",
                  "conversation.item.input_audio_transcription.completed": {"text": "PRIVATE"},
                  "PRIVATE_UNKNOWN_EVENT": 1}
        async with await self.post("/api/observe", {"dataChannelEvents": counts, "text": "PRIVATE"}) as response:
            self.assertEqual(response.status, 200)
        saved = self.relay.evidence["clientObservations"]
        self.assertEqual(saved["dataChannelEvents"], {"input_audio_buffer.speech_started": 1,
            "input_audio_buffer.speech_stopped": 2, "input_audio_buffer.committed": 0, "response.created": 1_000_000})
        self.assertNotIn("PRIVATE", json.dumps(saved))
        for invalid in ([], "PRIVATE", None, 3):
            async with await self.post("/api/observe", {"dataChannelEvents": invalid}) as response:
                self.assertEqual(response.status, 200)
            self.assertNotIn("dataChannelEvents", self.relay.evidence["clientObservations"])

    async def test_terminal_status_counts_are_allowlisted_and_separate_from_client_claims(self):
        """server observerと画面申告を分け、任意status・本文・IDを保存しない。"""
        for terminal in ("completed", "cancelled", "failed", "incomplete", "PRIVATE", None, [], {}):
            self.relay.record({"type": "response.done", "response": {"status": terminal,
                "id": "PRIVATE", "text": "PRIVATE", "error": {"message": "PRIVATE"}}}, additive=True)
        self.assertEqual(self.relay.evidence["serverObservedResponseStatuses"],
                         {name: 1 for name in browser.RESPONSE_DONE_STATUSES})
        self.assertEqual(self.relay.evidence["voiceLiveControlResponseStatuses"], {})
        counts = {"completed": 2, "cancelled": 0, "failed": True, "incomplete": 1_000_001,
                  "PRIVATE_UNKNOWN_STATUS": 1, "status": {"text": "PRIVATE"}}
        async with await self.post("/api/observe", {"responseStatuses": counts}) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.relay.evidence["clientObservations"]["responseStatuses"], {"completed": 2, "cancelled": 0})
        for invalid in ([], "PRIVATE", None, 3):
            async with await self.post("/api/observe", {"responseStatuses": invalid}) as response:
                self.assertEqual(response.status, 200)
            self.assertNotIn("responseStatuses", self.relay.evidence["clientObservations"])
        self.assertNotIn("PRIVATE", json.dumps(self.relay.evidence))

    async def test_default_tls_verification_rejects_untrusted_upstream(self):
        self.relay.upstream_ssl = True
        loop = asyncio.get_running_loop()
        original = loop.get_exception_handler()
        def expected_tls_rejection(event_loop, context):
            if isinstance(context.get("exception"), ConnectionResetError):
                return
            if original:
                original(event_loop, context)
            else:
                event_loop.default_exception_handler(context)
        loop.set_exception_handler(expected_tls_rejection)
        try:
            async with await self.post("/api/realtime", {"sdp": "v=0"}) as response:
                self.assertEqual(response.status, 502)
                self.assertNotIn(self.token, await response.text())
            await asyncio.sleep(0.01)
        finally:
            loop.set_exception_handler(original)
        self.assertEqual(self.calls, [])

    async def test_cli_urls_do_not_allow_non_tls_credentials_or_arbitrary_ports(self):
        self.assertEqual(browser.gateway_url("https://gateway.example.com/calls?api-version=v1", "https"),
                         "https://gateway.example.com/calls?api-version=v1")
        for value in ("http://gateway.example.com/calls", "https://secret@gateway.example.com/calls",
                      "https://gateway.example.com:8443/calls", "https://gateway.example.com/calls#fragment"):
            with self.assertRaises(ValueError):
                browser.gateway_url(value, "https")
        with self.assertRaises(ValueError):
            browser.create_app(self.settings, upstream_ssl=False)


class BrowserControlTests(unittest.TestCase):
    """画面の実際の JavaScript を偽 DOM で実行し、製品別の取消を確認する。"""
    def test_public_webrtc_output_budget_is_explicit_without_changing_session_vad(self):
        """WebRTC例の枠だけ512にし、server VADの動作は変えない。"""
        api = json.loads((Path(__file__).resolve().parents[1] / "apps/gateway/apim/apis/openai-realtime-control-v1.json").read_text())
        self.assertEqual(api["authorization"]["operationConstraints"]["realtime-create"]["initialOutputTokens"], 512)
        self.assertNotIn("create_response", browser.Settings().voice_session["turn_detection"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser JavaScript checks")
    def test_terminal_status_observations_keep_fixed_counts_without_payloads(self):
        """実UIで正常・打切り・取消を区別し、不明statusと本文を保存しない。"""
        script = r"""
const fs=require('fs'),vm=require('vm'),nodes={};
const context=vm.createContext({document:{getElementById(id){return nodes[id]??={};}},
 window:{addEventListener(){}},fetch:async()=>{throw Error('offline');}});
const html=fs.readFileSync(process.argv[1],'utf8').split('<script>')[1].split('</script>')[0];vm.runInContext(html,context);
vm.runInContext(`for(const status of RESPONSE_DONE_STATUS_NAMES){observeEvent({type:'response.done',response:{status,id:'PRIVATE',error:{message:'PRIVATE'}}},true);}
 for(const status of ['PRIVATE',null,[],{},42])observeEvent({type:'response.done',response:{status}},true);
 observeEvent({type:'response.done',responseStatus:'cancelled'});
 responseStatuses.completed=1000000;observeEvent({type:'response.done',response:{status:'completed'}},true);`,context);
console.log(vm.runInContext('JSON.stringify({names:RESPONSE_DONE_STATUS_NAMES,counts:responseStatuses})',context));
"""
        result = subprocess.run([shutil.which("node"), "-e", script, str(browser.HTML)],
                                capture_output=True, text=True, check=True, timeout=15)
        measured = json.loads(result.stdout)
        self.assertEqual(set(measured["names"]), browser.RESPONSE_DONE_STATUSES)
        self.assertEqual(measured["counts"], {"completed": 1_000_000, "cancelled": 2, "failed": 1, "incomplete": 1})
        self.assertNotIn("PRIVATE", result.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser JavaScript checks")
    def test_data_channel_event_counts_keep_names_only_and_match_relay_allowlist(self):
        """実UIで未知イベント・本文・制御WSSを除外し、DCだけの件数を集める。"""
        script = r"""
const fs=require('fs'),vm=require('vm'),nodes={};
const context=vm.createContext({document:{getElementById(id){return nodes[id]??={};}},
 window:{addEventListener(){}},fetch:async()=>{throw Error('offline');}});
const html=fs.readFileSync(process.argv[1],'utf8').split('<script>')[1].split('</script>')[0];vm.runInContext(html,context);
vm.runInContext(`for(const type of DATA_CHANNEL_EVENT_NAMES){observeEvent({type,id:'PRIVATE',text:'PRIVATE',error:{message:'PRIVATE'}},true);}
 observeEvent({type:'input_audio_buffer.speech_started'},true);
 observeEvent({type:'response.created'});observeEvent({type:'PRIVATE_UNKNOWN_EVENT'},true);
 observeEvent('not-json',true);
 dataChannelEvents['response.done']=1000000;observeEvent({type:'response.done'},true);`,context);
console.log(vm.runInContext('JSON.stringify({names:DATA_CHANNEL_EVENT_NAMES,counts:dataChannelEvents})',context));
"""
        result = subprocess.run([shutil.which("node"), "-e", script, str(browser.HTML)],
                                capture_output=True, text=True, check=True, timeout=15)
        measured = json.loads(result.stdout)
        self.assertEqual(set(measured["names"]), browser.DATA_CHANNEL_EVENT_NAMES)
        self.assertEqual(set(measured["counts"]), browser.DATA_CHANNEL_EVENT_NAMES)
        self.assertEqual(measured["counts"]["input_audio_buffer.speech_started"], 2)
        self.assertEqual(measured["counts"]["response.created"], 1)
        self.assertEqual(measured["counts"]["response.done"], 1_000_000)
        self.assertTrue(all(type(value) is int for value in measured["counts"].values()))
        self.assertNotIn("PRIVATE", result.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser JavaScript checks")
    def test_statistics_keep_available_audio_energy_and_state_without_media_identifiers(self):
        """実UIの統計収集で、未提供値を推測せず音声本文やtrack IDを落とす。"""
        script = r"""
const fs=require('fs'),vm=require('vm'),nodes={},saved=[];
const context=vm.createContext({document:{getElementById(id){return nodes[id]??={};}},
 window:{addEventListener(){}},fetch:async()=>{throw Error('offline');},save:value=>saved.push(value)});
const html=fs.readFileSync(process.argv[1],'utf8').split('<script>')[1].split('</script>')[0];vm.runInContext(html,context);
(async()=>{
 for(const provided of [true,false]){
  context.provided=provided;
  await vm.runInContext(`pc={iceConnectionState:'connected',getStats:async()=>new Map(provided?[
   ['audio',{type:'media-source',kind:'audio',trackIdentifier:'PRIVATE-track',totalAudioEnergy:0.125,totalSamplesDuration:1.2}],
   ['video',{type:'media-source',kind:'video',totalAudioEnergy:999}],
   ['out',{type:'outbound-rtp',packetsSent:59}]]:[])};
   context={state:'running'};dataChannel={readyState:'open'};sourcePlaybackStarted=true;post=async(path,value)=>save(value);
   sampleStats()`,context);
 }
 console.log(JSON.stringify(saved));
})().catch(()=>process.exit(1));
"""
        result = subprocess.run([shutil.which("node"), "-e", script, str(browser.HTML)],
                                capture_output=True, text=True, check=True, timeout=15)
        measured, absent = json.loads(result.stdout)
        self.assertEqual(measured["totalAudioEnergy"], 0.125)
        self.assertEqual(measured["totalSamplesDuration"], 1.2)
        self.assertEqual(measured["packetsSent"], 59)
        self.assertEqual(measured["audioContextState"], "running")
        self.assertEqual(measured["dataChannelState"], "open")
        self.assertIs(measured["sourcePlaybackStarted"], True)
        self.assertNotIn("totalAudioEnergy", absent)
        self.assertNotIn("totalSamplesDuration", absent)
        self.assertNotIn("PRIVATE", result.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser JavaScript checks")
    def test_short_wav_waits_for_connected_and_failures_clean_up(self):
        """実UIの接続処理を動かし、ICE/DTLS待ちの短音声欠落と残存通話を防ぐ。"""
        script = r"""
const fs=require('fs'),vm=require('vm');
const html=fs.readFileSync(process.argv[1],'utf8').split('<script>')[1].split('</script>')[0];
async function scenario(kind){
 const nodes={},timers=new Map(),counts={starts:0,tracksStopped:0,audioClosed:0,hangups:0};let next=1,peer,channel;
 class Channel{
  constructor(){channel=this;this.readyState=kind.startsWith('channel-')?'connecting':'open';this.listeners=new Map();}
  addEventListener(name,handler){if(!this.listeners.has(name))this.listeners.set(name,new Set());this.listeners.get(name).add(handler);}
  removeEventListener(name,handler){this.listeners.get(name)?.delete(handler);}
  advance(state){this.readyState=state;for(const fn of [...(this.listeners.get(state==='open'?'open':'close')||[])])fn();}
  fail(){for(const fn of [...(this.listeners.get('error')||[])])fn();}
 }
 class Peer{
  constructor(){peer=this;this.connectionState='connecting';this.listeners=new Set();}
  addTrack(){} createDataChannel(){return new Channel();}
  async setRemoteDescription(){}
  addEventListener(name,handler){if(name==='connectionstatechange')this.listeners.add(handler);}
  removeEventListener(name,handler){this.listeners.delete(handler);}
  advance(state){this.connectionState=state;for(const handler of [...this.listeners])handler();}
  close(){this.advance('closed');}
 }
 const env=vm.createContext({document:{getElementById(id){return nodes[id]??={};}},
  window:{addEventListener(){}},RTCPeerConnection:Peer,WebSocket:{OPEN:1},
  setTimeout(fn,ms){const id=next++;timers.set(id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);},
  setInterval(fn,ms){const id=next++;timers.set(id,{fn,ms});return id;},clearInterval(id){timers.delete(id);},
  fetch:async()=>{throw Error('offline');},counts});
 vm.runInContext(html,env);
 vm.runInContext(`cfg={maxSeconds:60};$('kind').value='realtime';sampleStats=async()=>{};
  offer=async()=>'v=0';post=async(path)=>{if(path.endsWith('/hangup')){counts.hangups++;return {hangupConfirmed:true};}return {id:'owned-local',sdp:'v=0'};};
  input=async()=>{sourceNode={start(){counts.starts++;}};context={async close(){counts.audioClosed++;}};
   return {getTracks(){return [{stop(){counts.tracksStopped++;}}];}};};`,env);
 const pending=vm.runInContext('connect()',env);
 await new Promise(setImmediate);
 const before={starts:counts.starts,waiting:peer.listeners.size===1};
 let afterPeerReady=null;
 if(kind.startsWith('channel-')){
  peer.advance('connected');await new Promise(setImmediate);
  afterPeerReady={starts:counts.starts,state:channel.readyState,waiting:peer.listeners.size===1};
  if(kind==='channel-open')channel.advance('open');
  else if(kind==='channel-closed')channel.advance('closed');
  else if(kind==='channel-error')channel.fail();
  else if(kind==='channel-peer-failed')peer.advance('failed');
  else if(kind==='channel-timeout'){const timer=[...timers.values()].find(value=>value.ms===15000);if(!timer)throw Error('No bounded channel wait');timer.fn();}
 }else if(kind==='connected')peer.advance('connected');
 else if(kind==='connected-then-closed'){peer.advance('connected');peer.advance('closed');}
 else if(kind==='timeout'){
  const timer=[...timers.values()].find(value=>value.ms===15000);if(!timer)throw Error('No bounded wait');timer.fn();
 }else peer.advance(kind);
 await pending;
 const after={starts:counts.starts,listeners:peer.listeners.size,
  channelListeners:[...channel.listeners.values()].reduce((sum,items)=>sum+items.size,0),started:vm.runInContext('sourcePlaybackStarted',env)};
 if(kind==='connected'||kind==='channel-open')await vm.runInContext('disconnect()',env);
 return {kind,before,after,afterPeerReady,counts,activeTimers:timers.size,
  cleaned:vm.runInContext('pc===null&&stream===null&&sourceNode===null&&callId===null&&!busy',env)};
}
(async()=>{const values=[];for(const kind of ['connected','failed','closed','timeout','connected-then-closed',
 'channel-open','channel-closed','channel-error','channel-timeout','channel-peer-failed'])values.push(await scenario(kind));
 console.log(JSON.stringify(values));})().catch(()=>process.exit(1));
"""
        result = subprocess.run([shutil.which("node"), "-e", script, str(browser.HTML)],
                                capture_output=True, text=True, check=True, timeout=15)
        values = json.loads(result.stdout)
        self.assertEqual(len(values), 10)
        for value in values:
            with self.subTest(state=value["kind"]):
                self.assertEqual(value["before"], {"starts": 0, "waiting": True})
                played = value["kind"] in ("connected", "channel-open")
                self.assertEqual(value["after"]["starts"], 1 if played else 0)
                self.assertEqual(value["after"]["started"], played)
                self.assertEqual(value["after"]["listeners"], 0)
                self.assertEqual(value["after"]["channelListeners"], 0)
                if value["kind"].startswith("channel-"):
                    self.assertEqual(value["afterPeerReady"], {"starts": 0, "state": "connecting", "waiting": True})
                self.assertEqual(value["counts"]["hangups"], 1)
                self.assertEqual(value["counts"]["tracksStopped"], 1)
                self.assertEqual(value["counts"]["audioClosed"], 1)
                self.assertEqual(value["activeTimers"], 0)
                self.assertTrue(value["cleaned"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser JavaScript checks")
    def test_cancel_button_sends_only_fixed_protocol_events(self):
        script = r"""
const fs=require('fs'),vm=require('vm');
const nodes={}; const calls=[];
const context=vm.createContext({document:{getElementById(id){return nodes[id]??={};}},
 window:{addEventListener(){}},WebSocket:{OPEN:1},fetch:async()=>{throw Error('offline');}});
const html=fs.readFileSync(process.argv[1],'utf8');
vm.runInContext(html.split('<script>')[1].split('</script>')[0],context);
(async()=>{
 context.record=value=>calls.push(value);
 await vm.runInContext(`sampleStats=async()=>{};signal={readyState:1,send:record};cancelResponse()`,context);
 await vm.runInContext(`signal=null;dataChannel={readyState:'open',send:record};cancelResponse()`,context);
 vm.runInContext(`observeEvent({type:'response.done',responseStatus:'cancelled'});`,context);
 console.log(JSON.stringify({calls:calls.map(JSON.parse),cancelRequests:vm.runInContext('cancelRequests',context),
 cancelledResponses:vm.runInContext('cancelledResponses',context),button:typeof nodes.cancel.onclick}));
})().catch(()=>process.exit(1));
"""
        result = subprocess.run([shutil.which("node"), "-e", script, str(browser.HTML)], capture_output=True, text=True, check=True)
        value = json.loads(result.stdout)
        self.assertEqual(value, {"calls": [{"action": "cancel"}, {"type": "response.cancel"}],
                                "cancelRequests": 2, "cancelledResponses": 1, "button": "function"})


if __name__ == "__main__":
    unittest.main()
