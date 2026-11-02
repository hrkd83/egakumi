#!/usr/bin/env python3
"""ブラウザーによるWebRTC/Voice Live試験を、ローカル専用の中継で支える。

127.0.0.1だけで画面を提供し、利用者がConnectを押したときに実サービスへ接続する。
Gateway購読キーとAPIMの所有者チケットはサーバープロセス内に保持し、
ブラウザーには渡さない。SDP・音声・候補アドレス・本文は保存せず、
制御イベント・転送量・正常終了などの限定した証跡だけを出力する。
HTTPの認証/SDP交換とWebRTCのメディア通信は別経路のため、ブラウザー観測値だけで
課金量を確定しない。利用量はサーバー側の観測と別途照合する必要がある。
終了・切断・規定時間で、このプロセスが作った通話の停止を要求する。
importや画面起動だけでは上流を呼ばない。Connect後は実際の音声・推論料金が
発生し得る。Azure基盤やEntraアプリを作成する処理は含まない。
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import secrets
import ssl
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, ClientWSTimeout, WSMsgType, web

from integration_client import auth_headers, correlations, usage_numbers

MAX_SDP = 65536
CALL_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
KNOWN_EVENTS = frozenset(("rtc.call.sdp.created", "rtc.call.error", "session.created", "session.updated",
    "error", "response.created", "response.done", "input_audio_buffer.speech_started",
    "input_audio_buffer.speech_stopped", "conversation.item.input_audio_transcription.completed",
    "response.audio_transcript.done", "response.output_audio_transcript.done", "response.text.done",
    "response.output_item.done", "conversation.item.created"))
# WebRTC の VAD・応答は data channel に届くため、制御 WSS の集計と分けます。
# https://learn.microsoft.com/azure/ai-services/speech-service/voice-live-webrtc#event-routing
# ブラウザーの申告は信頼済み利用量ではなく、固定名の件数だけの診断情報です。
DATA_CHANNEL_EVENT_NAMES = frozenset(("input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped",
    "input_audio_buffer.committed", "input_audio_buffer.cleared", "response.created", "response.done", "error", "rtc.call.error",
    "conversation.item.created", "conversation.item.input_audio_transcription.completed",
    "conversation.item.input_audio_transcription.failed"))
RESPONSE_DONE_STATUSES = frozenset(("completed", "cancelled", "failed", "incomplete"))
HTML = Path(__file__).resolve().parents[1] / "tests/browser/realtime.html"


def gateway_url(value: str | None, scheme: str) -> str | None:
    """固定のHTTPS/WSS接続先を検査し、URL内の秘密や任意ポートを拒否する。"""
    if value is None:
        return None
    parsed = urlsplit(value)
    if (parsed.scheme != scheme or not parsed.hostname or parsed.username or parsed.password
        or parsed.fragment or parsed.port not in (None, 443) or not parsed.path.startswith("/")
        or any(char in value for char in ("\r", "\n", "\\"))
        or any(re.search(r"api[-_]?key|token|secret", key, re.I) for key, _ in parse_qsl(parsed.query))):
        raise ValueError("A fixed Gateway TLS URL on port 443 is required")
    return value


def validate_sdp(value) -> str:
    """先頭形式・サイズ・NULL文字を確認し、上限内のセッション記述だけを受け取る。"""
    if (not isinstance(value, str) or not value.startswith("v=0") or len(value.encode()) > MAX_SDP
        or "\0" in value):
        raise ValueError("Invalid session offer")
    return value


@dataclass
class Settings:
    """固定の上流URL・保護トークン参照・通話時間・音声設定をまとめる。"""
    key_file: str | None = None
    key_header: str = "api-key"
    create_url: str | None = None
    voice_live_url: str | None = None
    sideband_url: str | None = None
    # CLIではHTTPS/WSSの443番を検査する。ローカル単体試験だけは固定したTLSサーバーの
    # 一時ポートを使うが、ブラウザーが任意の送信先を指定することはできない。
    max_session_seconds: int = 60
    voice_session: dict = field(default_factory=lambda: {
        "modalities": ["text", "audio"], "instructions": "Respond briefly in Japanese.",
        "voice": {"type": "azure-realtime-native", "name": "diya"},
        "turn_detection": {"type": "server_vad", "threshold": 0.5, "prefix_padding_ms": 300,
                           "silence_duration_ms": 500}, "max_response_output_tokens": 128, "tools": []})


class Relay:
    """ブラウザーとGatewayの間で認証・所有状態・本文を残さない証跡を管理する。"""
    def __init__(self, settings: Settings, upstream_ssl: ssl.SSLContext | bool = True):
        """TLS検証を必須にし、ローカルCSRF証明・通話一覧・観測値を初期化する。"""
        if not 5 <= settings.max_session_seconds <= 600:
            raise ValueError("Session duration must be between 5 and 600 seconds")
        if upstream_ssl is False:
            raise ValueError("Upstream TLS verification cannot be disabled")
        self.settings = settings
        self.upstream_ssl = upstream_ssl
        self.origin = ""
        self.csrf, self.cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.calls: dict[str, dict] = {}
        self.finished: set[str] = set()
        self.observers: dict[str, tuple] = {}
        self.closing: set[str] = set()
        self.session: ClientSession | None = None
        self.evidence = {"schema": "realtime-browser-v1", "runtimeVerified": False,
            "trustedUsageVerified": False, "trustedUsageNote": (
                "Server observer configured; real Azure acceptance and reconciliation with Foundry usage remain unverified."
                if settings.sideband_url else "No server observer is configured; browser/data-channel counters do not prove billable usage."),
            "upstreamStatuses": [], "serverCorrelations": [], "serverObservedEvents": {}, "serverObservedUsage": {},
            "serverObservedResponseStatuses": {}, "voiceLiveControlUsage": {}, "voiceLiveControlResponseStatuses": {},
            "serverCancelledResponses": 0, "controlCancelRequests": 0,
            "clientObservations": {}, "hangupConfirmed": 0, "hangupUnconfirmed": 0,
            "sideband": {"configured": bool(settings.sideband_url), "attached": 0, "closed": 0,
                "failures": 0, "usageEvents": 0, "usageWithoutStableId": 0, "billableUsageReconciled": False}}
        self.websockets: set = set()
        self.tasks: set[asyncio.Task] = set()

    def credentials(self):
        # 操作ごとに保護ファイルを読み直す。期限切れのトークンを手元で交換でき、
        # ブラウザーへ資格情報そのものを渡す必要もない。
        """操作の都度保護トークンを読み直し、更新された資格情報をブラウザーへ渡さず使う。"""
        if not self.settings.key_file:
            raise ValueError("A protected Gateway key file is required")
        try:
            return auth_headers(key_file=self.settings.key_file, key_header=self.settings.key_header)
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError("Gateway credential file is invalid") from None

    def allowed(self, request: web.Request, mutation=False):
        """loopback・Host・Originを照合し、状態変更にはcookieとCSRF証明も要求する。"""
        if request.remote != "127.0.0.1" or request.host != urlsplit(self.origin).netloc:
            raise web.HTTPForbidden(text="Loopback origin required")
        origin = request.headers.get("Origin")
        if origin is not None and origin != self.origin:
            raise web.HTTPForbidden(text="Origin rejected")
        if request.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin"):
            raise web.HTTPForbidden(text="Cross-site request rejected")
        if mutation and (origin != self.origin or request.cookies.get("core-session") != self.cookie
                         or not secrets.compare_digest(request.headers.get("X-Core-CSRF", ""), self.csrf)):
            raise web.HTTPForbidden(text="Session proof required")

    def record(self, event: dict, *, additive=False):
        """許可したイベント名と数値利用量だけを集計し、本文を保存しない。"""
        kind = event.get("type")
        name = kind if kind in KNOWN_EVENTS else "other"
        counts = self.evidence["serverObservedEvents"]
        counts[name] = counts.get(name, 0) + 1
        response = event.get("response")
        if kind == "response.done" and isinstance(response, dict):
            terminal_status = response.get("status")
            # response.done は正常完了だけを意味しません。固定の終端状態の件数だけ
            # を残し、任意のstatus・error・応答本文・IDは証跡へコピーしません。
            if type(terminal_status) is str and terminal_status in RESPONSE_DONE_STATUSES:
                target = self.evidence["serverObservedResponseStatuses" if additive else "voiceLiveControlResponseStatuses"]
                target[terminal_status] = min(target.get(terminal_status, 0) + 1, 1_000_000)
        if kind == "response.done" and isinstance(response, dict) and response.get("status") == "cancelled":
            self.evidence["serverCancelledResponses"] += 1
        usage = usage_numbers(event.get("usage") or (response.get("usage") if isinstance(response, dict) else None))
        if additive:
            for key, number in usage.items():
                values = self.evidence["serverObservedUsage"]
                values[key] = values.get(key, 0) + number
        else:
            self.evidence["voiceLiveControlUsage"].update(usage)
        return name, usage

    def record_response(self, response):
        """上流HTTP状態と許可形式の相関IDだけを証跡へ取り出す。"""
        self.evidence["upstreamStatuses"].append(response.status)
        correlation = correlations(response.headers)
        if correlation:
            self.evidence["serverCorrelations"].append(correlation)

    async def index(self, request):
        """ローカル試験画面を返し、JavaScriptが読めないセッションcookieを付ける。"""
        self.allowed(request)
        response = web.Response(text=HTML.read_text(encoding="utf-8"), content_type="text/html")
        response.set_cookie("core-session", self.cookie, httponly=True, samesite="Strict", path="/")
        return response

    async def bootstrap(self, request):
        """同じローカルセッションへ、機能選択とCSRF証明だけを渡す。"""
        self.allowed(request)
        if request.cookies.get("core-session") != self.cookie:
            raise web.HTTPForbidden(text="Session required")
        return web.json_response({"csrf": self.csrf, "realtime": bool(self.settings.create_url),
            "voiceLive": bool(self.settings.voice_live_url), "maxSeconds": self.settings.max_session_seconds,
            "serverObserverConfigured": bool(self.settings.sideband_url)})

    async def create(self, request):
        """SDPをGatewayへ送り、所有者チケットをサーバー側へ保持してからブラウザーへ応答する。

        不正な応答でも作成済み通話を停止できるよう、所有IDを先に保存する。
        Realtimeのサーバー観測を付けられなければ、通話停止を要求して失敗とする。
        """
        self.allowed(request, True)
        if not self.settings.create_url:
            raise web.HTTPNotFound()
        if len(self.calls) >= 4:
            raise web.HTTPConflict(text="End the existing calls first")
        value = await request.json()
        if not isinstance(value, dict) or set(value) != {"sdp"}:
            raise web.HTTPBadRequest(text="Only a session offer is accepted")
        try:
            sdp = validate_sdp(value["sdp"])
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid session offer") from None
        headers = {**self.credentials(), "Accept": "application/sdp"}
        async with self.session.post(self.settings.create_url, json={"sdp": sdp}, headers=headers,
                                     ssl=self.upstream_ssl, allow_redirects=False) as response:
            self.record_response(response)
            call_id = response.headers.get("X-Core-Call-Id", "")
            ticket = response.headers.get("X-Core-Call-Ticket", "")
            if response.status != 201:
                raise web.HTTPBadGateway(text="Gateway did not create a session")
            # 応答SDPの検査より先に正しい所有IDとチケットを保存する。SDPが壊れていても、
            # 作成済み通話の終了要求を出せるようにする。
            if not CALL_ID.fullmatch(call_id) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,4096}", ticket):
                self.evidence["hangupUnconfirmed"] += 1
                raise web.HTTPBadGateway(text="Gateway ownership response is invalid; inspect upstream session cleanup")
            local_id = secrets.token_urlsafe(24)
            self.calls[local_id] = {"callId": call_id, "ticket": ticket,
                "correlations": correlations(response.headers), "created": time.monotonic()}
            timer = asyncio.create_task(self.expire(local_id))
            self.tasks.add(timer); timer.add_done_callback(self.tasks.discard)
            if not response.headers.get("Content-Type", "").startswith("application/sdp"):
                await self.hangup_id(local_id)
                raise web.HTTPBadGateway(text="Gateway returned an invalid SDP answer")
            content = await response.content.read(MAX_SDP + 1)
            try:
                answer = validate_sdp(content.decode())
            except (ValueError, UnicodeError):
                await self.hangup_id(local_id)
                raise web.HTTPBadGateway(text="Gateway returned an invalid SDP answer") from None
        if self.settings.sideband_url:
            try:
                await self.attach_observer(local_id)
            except (ClientError, asyncio.TimeoutError, ValueError, OSError):
                self.evidence["sideband"]["failures"] += 1
                await self.hangup_id(local_id)
                raise web.HTTPBadGateway(text="Server observer could not attach; session stop requested") from None
        return web.json_response({"id": local_id, "sdp": answer})

    async def attach_observer(self, local_id):
        """所有通話IDとチケットで固定WSS観測口へ接続し、利用量イベントの読取を開始する。"""
        call = self.calls[local_id]
        parsed = urlsplit(self.settings.sideband_url)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if any(key.lower() in ("call_id", "api-key", "access_token") for key, _ in query):
            raise ValueError("Observer URL must not contain credentials or an existing call ID")
        url = parsed._replace(query=urlencode(query + [("call_id", call["callId"])] )).geturl()
        socket = await self.session.ws_connect(url, headers={**self.credentials(), "X-Core-Call-Ticket": call["ticket"]},
            protocols=["realtime"], ssl=self.upstream_ssl, max_msg_size=1024 * 1024,
            heartbeat=20, timeout=ClientWSTimeout(ws_close=5))
        self.record_response(socket._response)
        self.evidence["sideband"]["attached"] += 1
        task = asyncio.create_task(self.observe_call(local_id, socket))
        self.observers[local_id] = (socket, task)

    async def observe_call(self, local_id, socket):
        """サーバー側イベントを集計し、応答IDのハッシュで再送の二重計上を防ぐ。

        観測が切れたまま通話を黙って続けず、所有チケットによる停止を要求する。
        """
        seen = set()
        try:
            async for message in socket:
                if message.type == WSMsgType.TEXT:
                    event = json.loads(message.data)
                    if not isinstance(event, dict):
                        raise ValueError("Unexpected observer event")
                    response = event.get("response")
                    if event.get("type") == "response.done" and isinstance(response, dict):
                        stable_id = response.get("id") or event.get("event_id")
                        if isinstance(stable_id, str):
                            # 再接続や再送の二重計上を防ぐ。上流の応答IDそのものは
                            # 証跡に残さず、プロセス内のハッシュ集合だけに使う。
                            digest = hashlib.sha256(stable_id.encode()).digest()
                            if digest in seen:
                                continue
                            seen.add(digest)
                        else:
                            self.evidence["sideband"]["usageWithoutStableId"] += 1
                        _, usage = self.record(event, additive=True)
                        if usage:
                            self.evidence["sideband"]["usageEvents"] += 1
                    else:
                        self.record({"type": event.get("type")})
                elif message.type == WSMsgType.ERROR:
                    raise ClientError("Observer transport failed")
        except (ValueError, ClientError, asyncio.TimeoutError):
            self.evidence["sideband"]["failures"] += 1
        finally:
            self.evidence["sideband"]["closed"] += 1
            if local_id in self.calls and local_id not in self.closing:
                # サーバー側観測が失われたまま通話を続けない。所有チケットで
                # 通話終了を要求できる状態のうちに停止を試みる。
                self.evidence["sideband"]["failures"] += 1
                await self.hangup_id(local_id)

    async def hangup_id(self, local_id):
        """固定Gateway先へ所有通話の停止を要求し、確認済みと未確認を分けて記録する。

        HTTPの停止受付を最終課金額の確定とはみなさない。
        """
        call = self.calls.get(local_id)
        if not call:
            return False
        parsed = urlsplit(self.settings.create_url)
        # API版などは固定接続先のクエリから引き継ぐ。Locationヘッダーやブラウザー入力を
        # 送信先に使わず、検査済みIDだけをパスへ差し込む。
        url = parsed._replace(path=parsed.path.rstrip("/") + "/" + call["callId"] + "/hangup").geturl()
        self.closing.add(local_id)
        try:
            async with self.session.post(url, json={}, headers={**self.credentials(), "X-Core-Call-Ticket": call["ticket"]},
                    ssl=self.upstream_ssl, allow_redirects=False) as response:
                self.record_response(response)
                success = response.status in (200, 204)
        except (ClientError, asyncio.TimeoutError, ValueError, OSError):
            success = False
        self.evidence["hangupConfirmed" if success else "hangupUnconfirmed"] += 1
        if success:
            self.calls.pop(local_id, None)
            self.finished.add(local_id)
            if len(self.finished) > 64:
                self.finished.pop()
            observer = self.observers.pop(local_id, None)
            if observer:
                socket, task = observer
                if task is not asyncio.current_task():
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=2)
                    except asyncio.TimeoutError:
                        # 200で確認できるのは停止要求の受付であり、課金総量の確定ではない。
                        # 観測終了と請求量の照合を同一視しない。
                        await socket.close()
                        await task
                else:
                    await socket.close()
        self.closing.discard(local_id)
        return success

    async def expire(self, local_id):
        """このクライアントの通話時間が過ぎたら、作成した通話だけの停止を要求する。"""
        await asyncio.sleep(self.settings.max_session_seconds)
        await self.hangup_id(local_id)

    async def hangup(self, request):
        """同一ローカルセッションから、登録済み通話の終了操作だけを受ける。"""
        self.allowed(request, True)
        local_id = request.match_info["local_id"]
        if local_id in self.finished:
            return web.json_response({"hangupConfirmed": True})
        if local_id not in self.calls:
            raise web.HTTPNotFound(text="Unknown local session")
        confirmed = await self.hangup_id(local_id)
        return web.json_response({"hangupConfirmed": confirmed}, status=200 if confirmed else 502)

    async def voice(self, request):
        """Voice Liveの制御WSSを中継し、ブラウザーへGateway資格情報を渡さない。

        上流TLSと接続を先に確認し、SDPと取消要求だけを受ける。終了時には
        中継タスクとソケットを閉じ、音声本文を証跡へ残さない。
        """
        self.allowed(request)
        if (request.headers.get("Origin") != self.origin or request.cookies.get("core-session") != self.cookie
            or self.csrf not in [part.strip() for part in request.headers.get("Sec-WebSocket-Protocol", "").split(",")]):
            raise web.HTTPForbidden(text="WebSocket session proof required")
        if not self.settings.voice_live_url:
            raise web.HTTPNotFound()
        browser = web.WebSocketResponse(protocols=["core-realtime"], max_msg_size=MAX_SDP + 1024,
                                        heartbeat=20, receive_timeout=self.settings.max_session_seconds)
        # 上流TLSとGateway接続を確認してから、ローカル側をWebSocketへ切り替える。
        async with self.session.ws_connect(self.settings.voice_live_url, headers=self.credentials(),
                ssl=self.upstream_ssl, max_msg_size=1024 * 1024, heartbeat=20,
                protocols=["realtime"], timeout=ClientWSTimeout(ws_receive=self.settings.max_session_seconds, ws_close=5), autoping=True) as upstream:
            self.record_response(upstream._response)
            await browser.prepare(request)
            self.websockets.add(browser)
            async def offered():
                """ブラウザーの最初のSDPだけを、固定のVoice Live設定と一緒に送る。"""
                offer = await browser.receive_json(timeout=30)
                if not isinstance(offer, dict) or set(offer) != {"sdp"}:
                    raise ValueError("Only one fixed session offer is supported")
                await upstream.send_json({"type": "rtc.call.sdp.create", "sdp_offer": validate_sdp(offer["sdp"]),
                                          "session": self.settings.voice_session})
            async def downstream():
                """上流イベントからSDP応答と限定した観測値だけをブラウザーへ返す。"""
                async for message in upstream:
                    if message.type != WSMsgType.TEXT:
                        if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                            break
                        continue
                    event = json.loads(message.data)
                    if not isinstance(event, dict):
                        raise ValueError("Unexpected control event")
                    kind, usage = self.record(event)
                    if kind == "rtc.call.sdp.created":
                        await browser.send_json({"type": kind, "sdp_answer": validate_sdp(event.get("sdp_answer"))})
                    else:
                        # 応答の終端状態だけを転送する。発話・音声・session ID は除外する。
                        safe = {"type": kind, "usage": usage, "failed": kind in ("error", "rtc.call.error")}
                        terminal = event.get("response", {})
                        if kind == "response.done" and isinstance(terminal, dict) and terminal.get("status") in ("completed", "cancelled", "failed", "incomplete"):
                            safe["responseStatus"] = terminal["status"]
                        await browser.send_json(safe)
            async def control():
                """ブラウザーからの制御を取消要求だけに限定し、任意API呼出しをさせない。"""
                async for message in browser:
                    if message.type == WSMsgType.TEXT:
                        action = json.loads(message.data)
                        if action != {"action": "cancel"}:
                            raise ValueError("Unsupported control operation")
                        await upstream.send_json({"type": "response.cancel"})
                        self.evidence["controlCancelRequests"] += 1
                    elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
            pending = []
            try:
                await offered()
                pending = [asyncio.create_task(downstream()), asyncio.create_task(control())]
                done, _ = await asyncio.wait(pending, timeout=self.settings.max_session_seconds,
                                             return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            except (ValueError, ClientError, asyncio.TimeoutError):
                await browser.close(code=1008, message=b"Control channel closed; inspect body-free evidence")
            finally:
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                self.websockets.discard(browser)
                await browser.close()
        return browser

    async def observe(self, request):
        """ブラウザー観測を数値・既知状態だけに絞り、信頼済み課金量とは分けて保存する。"""
        self.allowed(request, True)
        value = await request.json()
        if not isinstance(value, dict):
            raise web.HTTPBadRequest()
        # ブラウザーからの値は信頼済みの請求量ではない。既知の数値・状態だけを受け取り、
        # SDP・本文・候補IPアドレス・任意キーは証跡に保存しない。
        result = {}
        for key in ("bytesSent", "bytesReceived", "packetsSent", "packetsReceived", "roundTripTime",
                    "localPort", "remotePort", "responseDone", "remoteAudioTracks", "audioPlayed", "cancelRequests", "cancelledResponses",
                    "totalAudioEnergy", "totalSamplesDuration"):
            item = value.get(key)
            if type(item) in (int, float) and 0 <= item <= 10**15:
                result[key] = item
        for key, choices in (("iceState", ("new", "checking", "connected", "completed", "failed", "disconnected", "closed")),
                             ("audioContextState", ("suspended", "running", "closed", "interrupted")),
                             ("dataChannelState", ("connecting", "open", "closing", "closed")),
                             ("protocol", ("udp", "tcp")), ("localCandidateType", ("host", "srflx", "prflx", "relay")),
                             ("remoteCandidateType", ("host", "srflx", "prflx", "relay"))):
            if value.get(key) in choices:
                result[key] = value[key]
        if type(value.get("sourcePlaybackStarted")) is bool:
            result["sourcePlaybackStarted"] = value["sourcePlaybackStarted"]
        counts = value.get("dataChannelEvents")
        if isinstance(counts, dict):
            # 任意のキーや bool/小数/負数/巨大数は受け付けず、本文を伴う構造も落とします。
            result["dataChannelEvents"] = {name: counts[name] for name in DATA_CHANNEL_EVENT_NAMES
                if type(counts.get(name)) is int and 0 <= counts[name] <= 1_000_000}
        statuses = value.get("responseStatuses")
        if isinstance(statuses, dict):
            result["responseStatuses"] = {name: statuses[name] for name in RESPONSE_DONE_STATUSES
                if type(statuses.get(name)) is int and 0 <= statuses[name] <= 1_000_000}
        result["usage"] = usage_numbers(value.get("usage"))
        self.evidence["clientObservations"] = result
        return web.json_response({"saved": True, "trustedUsageVerified": False})

    async def export(self, request):
        """本文・資格情報なしの証跡と、未終了の所有通話・制御チャネル件数を返す。"""
        self.allowed(request, True)
        return web.json_response({**self.evidence, "activeOwnedCalls": len(self.calls),
                                  "activeControlChannels": len(self.websockets)})


RELAY_KEY = web.AppKey("relay", Relay)


def create_app(settings: Settings, *, upstream_ssl: ssl.SSLContext | bool = True):
    """ルートと共通防御を持つローカルWebアプリを作る。この時点では上流へ接続しない。"""
    relay = Relay(settings, upstream_ssl)
    @web.middleware
    async def protect(request, handler):
        """応答をキャッシュ不可にし、例外の生本文を伏せ、ブラウザーの外部接続を制限する。"""
        try:
            response = await handler(request)
        except web.HTTPException as error:
            response = web.Response(status=error.status, text=error.text, headers=dict(error.headers))
        except (ClientError, asyncio.TimeoutError, ValueError, OSError, json.JSONDecodeError):
            response = web.Response(status=502, text="Relay operation failed; private output suppressed")
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"})
        return response
    app = web.Application(middlewares=[protect], client_max_size=MAX_SDP + 2048)
    app[RELAY_KEY] = relay
    app.add_routes([web.get("/", relay.index), web.get("/bootstrap", relay.bootstrap),
        web.post("/api/realtime", relay.create), web.post("/api/realtime/{local_id}/hangup", relay.hangup),
        web.get("/api/voice-live", relay.voice), web.post("/api/observe", relay.observe), web.post("/api/evidence", relay.export)])
    async def start(_):
        """外部プロキシを自動利用しない、上流通信専用のHTTPクライアントを作る。"""
        async def no_redirects(request, handler):
            """WSSも含め最初の転送応答で停止し、認証ヘッダーの別ホスト送信を防ぐ。"""
            response = await handler(request)
            # ws_connectにはallow_redirects指定がないため、クライアント共通処理で
            # 最初の転送応答を拒否する。別URLへ資格情報を持って追跡させない。
            if response.status in (301, 302, 303, 307, 308):
                response.release()
                raise ClientError("Gateway redirects are disabled")
            return response
        relay.session = ClientSession(timeout=ClientTimeout(total=45), trust_env=False,
                                      middlewares=[no_redirects])
    async def stop(_):
        """画面終了時に、このプロセスが作った通話・観測・タイマー・ソケットを回収する。"""
        for socket in list(relay.websockets):
            await socket.close()
        for local_id in list(relay.calls):
            await relay.hangup_id(local_id)
        for socket, task in list(relay.observers.values()):
            task.cancel()
            await socket.close()
        if relay.observers:
            await asyncio.gather(*(task for _, task in relay.observers.values()), return_exceptions=True)
        for task in list(relay.tasks):
            task.cancel()
        if relay.tasks:
            await asyncio.gather(*list(relay.tasks), return_exceptions=True)
        await relay.session.close()
    app.on_startup.append(start); app.on_cleanup.append(stop)
    return app


async def serve(args):
    """資格情報と固定URLを検査後、127.0.0.1だけで試験画面を起動する。

    起動だけでは通話を始めない。終了時はrunnerの後処理で所有通話を止める。
    """
    settings = Settings(key_file=args.gateway_key_file, key_header=args.key_header, create_url=gateway_url(args.create_url, "https"),
        voice_live_url=gateway_url(args.voice_live_url, "wss"), sideband_url=gateway_url(args.sideband_url, "wss"),
        max_session_seconds=args.max_seconds)
    if settings.create_url and not settings.sideband_url:
        raise ValueError("Realtime requires the registered server-observer Gateway URL")
    if settings.sideband_url and any(key.lower() == "call_id" for key, _ in parse_qsl(urlsplit(settings.sideband_url).query)):
        raise ValueError("Observer call ID is derived only from owned session state")
    if not re.fullmatch(r"[A-Za-z0-9:._-]{1,150}", args.voice_name):
        raise ValueError("Invalid explicit Voice Live voice name")
    settings.voice_session["voice"] = {"type": args.voice_type, "name": args.voice_name}
    if not settings.create_url and not settings.voice_live_url:
        raise ValueError("At least one explicitly configured Gateway URL is required")
    auth_headers(key_file=settings.key_file, key_header=settings.key_header)  # Validate a protected file before binding.
    app = create_app(settings)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", args.port)
    await site.start()
    actual_port = site._server.sockets[0].getsockname()[1]
    app[RELAY_KEY].origin = f"http://127.0.0.1:{actual_port}"
    print("Open " + app[RELAY_KEY].origin + "/ — no upstream call until Connect. Ctrl+C closes owned calls.", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def main():
    """試験用ローカル中継の引数を検査して起動し、秘密のないエラーで終了する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create-url")
    parser.add_argument("--voice-live-url")
    parser.add_argument("--sideband-url", help="Fixed registered Realtime observer Gateway WSS URL; call ID/ticket never comes from the browser")
    parser.add_argument("--gateway-key-file", required=True, help="Server-only protected subscription-key file")
    parser.add_argument("--key-header", default="api-key")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--max-seconds", type=int, default=60)
    parser.add_argument("--voice-type", choices=("azure-realtime-native", "azure-standard"), default="azure-realtime-native")
    parser.add_argument("--voice-name", default="diya")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("Invalid loopback port")
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, ClientError):
        parser.exit(1, "Local relay could not start; inspect protected inputs and fixed Gateway URLs.\n")


if __name__ == "__main__":
    main()
