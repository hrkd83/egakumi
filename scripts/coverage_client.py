#!/usr/bin/env python3
"""画像・PDF・埋め込み・音声・Agentなどの短い試験要求を作り、応答を確認する。

prepareは合成画像・PDF・無音WAVなどの小さな資材をローカル生成し、通信しない。
runは明示された接続先へ要求を1回送り、応答の形・内容の条件・正常終端を調べる。
画像や音声はサイズと形式を検査し、SSEは途中の出力だけで合格にしない。
証跡には合否、件数、数値、形式などを残し、応答本文や資格情報を載せない。
実行モードではモデル推論・音声生成などの利用料金が発生し得る。Azureの
リソースを作る処理は含まない。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import time
import uuid
import wave
from xml.sax.saxutils import escape
import zlib

from integration_client import auth_headers, correlations, endpoint, json_shape, private_write, usage_numbers, StreamSummary

KINDS = ("embedding", "rerank", "messages", "image-generate", "image-edit", "vision", "pdf", "audio-chat", "tts", "stt", "agent")
PROFILE_REQUESTS = {("chat-completions", "vision"): "vision",
                    ("responses-create", "pdf"): "pdf",
                    ("chat-completions", "audio-chat"): "audio-chat"}
MAX_BODY = 32 * 1024 * 1024
PDF_MARKER = "WORKFORCE-PDF-CHECK-42"
PII_INPUT = "Contact: alice@example.com."


def png_blue() -> bytes:
    """青色1ピクセルのPNGをローカル生成し、画像入力の合成資材にする。"""
    def chunk(kind, data):
        """PNGチャンクへ長さとCRCを付けて、正しいバイナリー形式を作る。"""
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    rows = b"".join(b"\0" + b"\x00\x00\xff" * 32 for _ in range(32))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 32, 32, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def marker_pdf() -> bytes:
    """短い固定文字を含む最小PDFを作り、実際に入力が読まれたかの確認に使う。"""
    content = f"BT /F1 18 Tf 30 100 Td ({PDF_MARKER}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 160] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"]
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result)); result.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    result.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(result)


def silence_wav(seconds=1, rate=24000) -> bytes:
    """外部音声を使わず、小さな無音WAVを生成する。"""
    target = io.BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(rate)
        audio.writeframes(b"\0\0" * rate * seconds)
    return target.getvalue()


def private_bytes(path: Path, data: bytes):
    """生成資材を所有者だけが読めるバイナリーファイルとして保存する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600); stream.write(data)


def assets(directory: Path) -> dict:
    """画像・PDF・音声の小さな合成資材をまとめて生成し、通信せず参照先を返す。"""
    result = {"azureRequests": 0, "assets": {}}
    for name, value in (("blue.png", png_blue()), ("marker.pdf", marker_pdf()), ("silence.wav", silence_wav())):
        private_bytes(directory / name, value)
        result["assets"][name] = {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    result["speechNote"] = "silence.wav is a silence/boundary fixture, not a successful speech-recognition fixture; use synthesized TTS speech for STT success"
    private_write(directory / "manifest.json", json.dumps(result, indent=2))
    return result


def inspect_png(data):
    """上限内のPNGをCRC・走査線まで復号し、画像として有効か確認する。"""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"): raise ValueError("Expected PNG")
    offset, header, compressed, ended, palette = 8, None, bytearray(), False, False
    while offset + 12 <= len(data):
        length = struct.unpack("!I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        if length > MAX_BODY or offset + 12 + length > len(data): raise ValueError("Truncated PNG")
        chunk = data[offset + 8:offset + 8 + length]
        crc = struct.unpack("!I", data[offset + 8 + length:offset + 12 + length])[0]
        if zlib.crc32(kind + chunk) & 0xffffffff != crc: raise ValueError("PNG checksum mismatch")
        if kind == b"IHDR":
            if header is not None or offset != 8 or length != 13: raise ValueError("Invalid PNG header")
            header = struct.unpack("!2I5B", chunk)
        elif kind == b"PLTE": palette = bool(length and length % 3 == 0)
        elif kind == b"IDAT": compressed.extend(chunk)
        elif kind == b"IEND":
            ended = length == 0
            offset += 12 + length
            break
        offset += 12 + length
    if not header or not ended or offset != len(data): raise ValueError("Incomplete PNG")
    width, height, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if not channels or not (0 < width <= 4096 and 0 < height <= 4096) or depth != 8 or compression or filtering or interlace or (color == 3 and not palette):
        raise ValueError("Unsupported PNG format for this bounded probe")
    stride = width * channels + 1; expected = stride * height
    if expected > MAX_BODY: raise ValueError("PNG exceeds inspection limit")
    decoder = zlib.decompressobj(); pixels = decoder.decompress(compressed, expected + 1)
    if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data or len(pixels) != expected or any(pixels[i] > 4 for i in range(0, len(pixels), stride)):
        raise ValueError("Invalid PNG scanlines")
    return {"width": width, "height": height, "decodedBytes": len(pixels)}


def upsample_speech(data):
    """短いTTS資材の16kHz PCMを24kHzへ補間し、WSS音声試験の入力形式へ揃える。"""
    with wave.open(io.BytesIO(data), "rb") as source:
        if source.getframerate() != 16000 or source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getcomptype() != "NONE":
            raise ValueError("Expected a 16 kHz mono PCM TTS fixture")
        frames = source.getnframes(); raw = source.readframes(frames)
    if not 1600 <= frames <= 16000 * 15 or len(raw) != frames * 2: raise ValueError("Invalid short speech fixture")
    samples = struct.unpack("<" + "h" * frames, raw)
    converted = []
    for index in range(frames * 3 // 2):
        left, fraction = divmod(index * 2, 3)
        converted.append(int((samples[left] * (3 - fraction) + samples[min(left + 1, frames - 1)] * fraction) / 3))
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(24000)
        target.writeframes(struct.pack("<" + "h" * len(converted), *converted))
    return output.getvalue()


@dataclass
class Request:
    """1回の試験要求のHTTP方式・ヘッダー・本文を保持する値オブジェクト。"""
    kind: str
    json: dict | None = None
    content: bytes | None = None
    data: dict | None = None
    files: dict | None = None
    headers: dict = field(default_factory=dict)
    expected_text: str | None = None
    operation_id: str | None = None
    profile_id: str | None = None


def request_for(kind: str, *, model=None, voice=None, audio=None, stream=False, expected_text=None, agent_mode="fixed", max_tokens=1024,
                operation_id=None, profile_id=None) -> Request:
    """試験種別に合った短い固定要求を作る。ネットワーク送信はまだ行わない。"""
    if kind not in KINDS: raise ValueError("Unsupported modality")
    if kind not in ("tts", "stt", "agent") and not model: raise ValueError("An explicit approved model deployment is required")
    if stream and kind not in ("messages", "agent"): raise ValueError("Streaming is supported here only for Messages and Agent Responses")
    request = Request(kind, expected_text=expected_text)
    if kind in PROFILE_REQUESTS.values():
        expected = next(pair for pair, value in PROFILE_REQUESTS.items() if value == kind)
        selected = (operation_id or expected[0], profile_id or expected[1])
        if selected != expected:
            raise ValueError("The input profile must match its canonical operation")
        request.operation_id, request.profile_id = selected
        # 同じURLの通常Chat/Responsesへ入力権限を暗黙追加せず、明示profileを選ぶ。
        request.headers["X-Egakumi-Profile"] = request.profile_id
    elif operation_id is not None or profile_id is not None:
        raise ValueError("Operation/profile selection is supported for profiled input probes")
    if kind == "embedding": request.json = {"model": model, "input": ["gateway", "network"], "encoding_format": "float"}
    elif kind == "rerank": request.json = {"model": model, "query": "gateway", "documents": ["API gateway", "a blue flower"], "top_n": 2}
    elif kind == "messages":
        if type(max_tokens) is not int or not 1 <= max_tokens <= 1024: raise ValueError("Choose a bounded Messages output limit")
        request.json = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": "Reply with OK."}], "stream": stream}
        request.headers["anthropic-version"] = "2023-06-01"
    elif kind == "image-generate": request.json = {"model": model, "prompt": "A plain blue square on white, no text.", "n": 1, "quality": "low", "size": "1024x1024"}
    elif kind == "image-edit":
        request.data = {"model": model, "prompt": "Change the square to green.", "n": "1", "quality": "low"}
        request.files = {"image": ("blue.png", png_blue(), "image/png")}
    elif kind == "vision":
        # GPT-6.1 は短い回答でも内部の推論に出力枠を使うため、64 では回答前に
        # 上限へ達することがあります。Chat 試験と同じ 512 に限定し、推論と回答の両方の枠を確保します。
        request.json = {"model": model, "store": False, "max_completion_tokens": 512, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Name the single color in English."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png_blue()).decode(), "detail": "low"}}]}]}
        request.expected_text = "blue"
    elif kind == "pdf":
        item = {"type": "input_file", "filename": "marker.pdf", "file_data": "data:application/pdf;base64," + base64.b64encode(marker_pdf()).decode()}
        # PDF の読み取りも推論分を含めて 512 の枠を確保します。短い marker の
        # 出力前に枠が尽きる準備不足と、ファイル経路の不具合を混同しないためです。
        request.json = {"model": model, "store": False, "max_output_tokens": 512,
                        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Return the exact printed marker only."}, item]}]}
        request.expected_text = PDF_MARKER
    elif kind == "audio-chat":
        if not audio or len(audio) > 4 * 1024 * 1024:
            raise ValueError("A short synthetic spoken WAV is required for Audio Chat")
        with wave.open(io.BytesIO(audio), "rb") as source:
            if source.getcomptype() != "NONE" or source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise ValueError("Audio Chat requires mono 16-bit PCM WAV")
        request.json = {"model": model, "store": False, "max_completion_tokens": 512,
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": "Reply with OK after listening to this audio."},
                            {"type": "input_audio", "input_audio": {"data": base64.b64encode(audio).decode(), "format": "wav"}}]}]}
        request.expected_text = expected_text or "OK"
    elif kind == "tts":
        if not voice or not re.fullmatch(r"[A-Za-z0-9:._-]{1,150}", voice): raise ValueError("An explicit valid Speech voice is required")
        language = "-".join(voice.split("-")[:2])
        request.content = f'<speak version="1.0" xml:lang="{escape(language)}"><voice name="{escape(voice)}">Hello workforce.</voice></speak>'.encode()
        request.headers = {"Content-Type": "application/ssml+xml", "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm", "User-Agent": "workforce-coverage"}
    # 無音は境界確認には使えるが認識成功の証拠にはならない。STTの成功試験は
    # 先にTTSで作った短い発話を使い、期待する語句が返るかも検査する。
    elif kind == "stt":
        if not audio: raise ValueError("A short synthetic spoken WAV is required; generate it with TTS first")
        if len(audio) > 4 * 1024 * 1024: raise ValueError("Audio fixture exceeds the probe limit")
        with wave.open(io.BytesIO(audio), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != 16000 or source.getcomptype() != "NONE": raise ValueError("Short STT requires mono 16-bit PCM WAV at 16 kHz")
            if len(source.readframes(source.getnframes())) != source.getnframes() * 2: raise ValueError("Truncated speech fixture")
        # 公開 HTTP は標準の WAV MIME にします。CRS 3.2 の 920470 は codec 等の
        # 追加パラメーターを拒否するため、Speech 固有の値は APIM の固定 header が
        # 裏側だけに付けます。音声自体は上で mono/16-bit/16kHz PCM と確認済みです。
        request.content = audio; request.headers["Content-Type"] = "audio/wav"
    elif kind == "agent":
        if agent_mode not in ("fixed", "model", "pii"): raise ValueError("Unknown Hosted Agent sample mode")
        request.json = {"input": PII_INPUT if agent_mode == "pii" else "Reply with OK.", "stream": stream, "store": False, "metadata": {"sample_mode": agent_mode}}
    return request


def request_for_operation(operation_id, profile_id, **kwargs):
    """統合後の操作IDと入力profileから、既存の合成試験payloadを選ぶ。"""
    kind = PROFILE_REQUESTS.get((operation_id, profile_id))
    if kind is None:
        raise ValueError("Unsupported canonical operation/input profile probe")
    return request_for(kind, operation_id=operation_id, profile_id=profile_id, **kwargs)


def output_text(value):
    """応答から検査中だけ使う出力文字列を抽出する。証跡への本文保存には使わない。"""
    return " ".join(part.get("text", "") for item in value.get("output", []) if isinstance(item, dict)
                    for part in item.get("content", []) if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str))


def finite(value):
    """応答中の数値がboolでなく有限値であることを確認する。"""
    return type(value) in (int, float) and math.isfinite(value)


def inspect_json(request, value) -> dict:
    """モデル種別ごとの応答条件を判定し、本文の代わりに形式・数値・合否を返す。"""
    result = {"shapeValid": False, "usage": {}}
    # Responses は正常時も error:null を返す。null をエラーと取り違えない。
    if not isinstance(value, dict) or value.get("error") is not None: return result
    result["usage"] = usage_numbers(value.get("usage"))
    if request.kind == "embedding":
        data = value.get("data", [])
        vectors = [item.get("embedding") for item in data if isinstance(item, dict)] if isinstance(data, list) else []
        result["shapeValid"] = (len(vectors) == len(request.json["input"]) and all(isinstance(v, list) and v and all(finite(n) for n in v) for v in vectors)
                                and len({len(v) for v in vectors if isinstance(v, list)}) == 1
                                and all(type(item.get("index")) is int for item in data)
                                and {item["index"] for item in data} == set(range(len(vectors))))
        if result["shapeValid"]: result.update(vectorCount=len(vectors), dimensions=len(vectors[0]))
    elif request.kind == "rerank":
        data = value.get("results")
        result["shapeValid"] = isinstance(data, list) and len(data) == request.json["top_n"] and all(
            isinstance(item, dict) and type(item.get("index")) is int and 0 <= item["index"] < len(request.json["documents"])
            and finite(item.get("relevance_score")) and 0 <= item["relevance_score"] <= 1 for item in data)
        if result["shapeValid"]:
            result["shapeValid"] &= len({i["index"] for i in data}) == len(data)
            result["ranking"] = [i["index"] for i in data]
            result["scores"] = [i["relevance_score"] for i in data]
            result["rankingExpected"] = data[0]["index"] == 0
    elif request.kind == "messages":
        content = value.get("content")
        result["shapeValid"] = value.get("type") == "message" and value.get("stop_reason") in ("end_turn", "max_tokens", "stop_sequence", "tool_use") and isinstance(content, list) and any(isinstance(v, dict) and v.get("type") == "text" and isinstance(v.get("text"), str) and v["text"] for v in content)
    elif request.kind in ("image-generate", "image-edit"):
        data = value.get("data")
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict) and isinstance(data[0].get("b64_json"), str):
            try:
                image = base64.b64decode(data[0]["b64_json"], validate=True)
                metadata = inspect_png(image)
                result.update(shapeValid=True, imageCount=1, imageBytes=len(image), imageSha256=hashlib.sha256(image).hexdigest(), **metadata)
            except (ValueError, TypeError, zlib.error): pass
        # 画像URLへは自動アクセスしない。別のStorage/CDNの認証契約が必要な可能性が
        # あるため、URLが返っただけではこの画像試験の成功にしない。
    elif request.kind in ("vision", "audio-chat"):
        choices = value.get("choices", [])
        text = " ".join(item.get("message", {}).get("content", "") for item in choices if isinstance(item, dict)
                        and isinstance(item.get("message"), dict) and isinstance(item["message"].get("content"), str)) if isinstance(choices, list) else ""
        # 画像入力の Chat も無ツールの短文応答。期待語が含まれていても length 等で
        # 途中終了した場合は、通常 Chat と同じ終端検査で未合格にする。
        result["shapeValid"] = json_shape("chat", value)
        result["expectedContentObserved"] = bool(request.expected_text and request.expected_text.lower() in text.lower())
    elif request.kind in ("pdf", "agent"):
        text = output_text(value)
        result["shapeValid"] = value.get("status") == "completed" and bool(text)
        if request.expected_text: result["expectedContentObserved"] = request.expected_text.lower() in text.lower()
        if request.kind == "agent": result.update(agent_output_verdict(request, text))
    elif request.kind == "stt":
        text = value.get("DisplayText")
        if not isinstance(text, str):
            best = value.get("NBest", [])
            text = best[0].get("Display") if isinstance(best, list) and best and isinstance(best[0], dict) else None
        result["shapeValid"] = value.get("RecognitionStatus") == "Success" and isinstance(text, str) and bool(text.strip())
        if request.expected_text: result["expectedContentObserved"] = request.expected_text.lower() in (text or "").lower()
    return result


def agent_output_verdict(request, text):
    """Agentの合成応答を条件と照合し、空応答や失敗を成功扱いにしない。"""
    mode = request.json["metadata"]["sample_mode"]
    if mode == "pii":
        valid = text.startswith("Contact:") and len(text.removeprefix("Contact:").strip()) > 1 and "alice@example.com" not in text.lower()
        return {"syntheticContactMasked": valid, "expectedContentObserved": valid}
    if mode == "fixed": return {"expectedContentObserved": "synthetic-hosted-agent-ok" in text}
    return {}


class AgentStream(StreamSummary):
    """AgentのSSEを一時検査し、合成応答本文を証跡に入れず結果だけを残す。"""
    def __init__(self, started, request):
        """Agent出力と終端・失敗の観測状態を初期化する。"""
        super().__init__("responses", started)
        self.request, self.synthetic_text = request, ""
    def event(self, data):
        """SSEイベントを検査し、出力と正常終端の条件を積み上げる。"""
        super().event(data)
        value = json.loads(data)
        if isinstance(value, dict) and value.get("type") == "response.output_text.delta" and isinstance(value.get("delta"), str):
            if len(self.synthetic_text) + len(value["delta"]) > 4096: raise ValueError("Synthetic Agent output exceeds the inspection limit")
            self.synthetic_text += value["delta"]
    def result(self):
        """Agentのストリーム終端と合成出力の検査結果をまとめる。"""
        return {**super().result(), **agent_output_verdict(self.request, self.synthetic_text)}


class MessagesStream:
    """Claude MessagesのSSEを読み、開始・出力・停止と利用量を確認する。"""
    def __init__(self):
        """Messagesのイベント種別と終端確認に必要な状態を初期化する。"""
        self.pending = []; self.events = 0; self.text = False; self.stopped = False; self.reason = False; self.failed = False; self.usage = {}
    def line(self, line):
        """1行ずつSSEを読み、応答本文を検査中のみに保持する。"""
        if line.startswith("data:"):
            self.pending.append(line[5:].lstrip())
            if sum(map(len, self.pending)) > 1024 * 1024: raise ValueError("Oversized SSE event")
        elif not line and self.pending:
            value = json.loads("\n".join(self.pending)); self.pending = []; self.events += 1
            if not isinstance(value, dict): raise ValueError("Invalid SSE object")
            kind = value.get("type")
            self.failed |= kind == "error"
            self.text |= kind == "content_block_delta" and bool(value.get("delta", {}).get("text"))
            self.reason |= kind == "message_delta" and value.get("delta", {}).get("stop_reason") in ("end_turn", "max_tokens", "stop_sequence", "tool_use")
            self.stopped |= kind == "message_stop"
            self.usage.update(usage_numbers(value.get("usage")))
            if kind == "message_start" and isinstance(value.get("message"), dict): self.usage.update(usage_numbers(value["message"].get("usage")))
    def result(self):
        """Messagesの終端・出力・エラーを合否として返す。"""
        return {"eventCount": self.events, "outputObserved": self.text, "normalTerminal": self.stopped and self.reason and not self.failed, "usage": self.usage}


async def probe(url, request, headers, *, stream=False, transport=None, artifact=None, timeout=120):
    """要求を1回送信し、応答種別に応じてJSON・SSE・画像・音声を検査する。

    資格情報はヘッダーだけに渡し、自動再試行や別URLへの転送をしない。
    結果には本文ではなく状態・検査結果・許可した相関IDを残す。
    """
    import httpx2
    started = time.monotonic()
    result = {"kind": request.kind, "startedAt": datetime.now(timezone.utc).isoformat(), "requestId": str(uuid.uuid4()),
              "status": None, "httpAttempts": 0, "succeeded": False, "inferenceRetryCount": 0}
    if request.operation_id:
        result.update(operationId=request.operation_id, profileId=request.profile_id)
    request_headers = {**headers, **request.headers, "x-ms-client-request-id": result["requestId"]}
    kwargs = {k: getattr(request, k) for k in ("json", "content", "data", "files") if getattr(request, k) is not None}
    try:
        async with httpx2.AsyncClient(transport=transport or httpx2.AsyncHTTPTransport(retries=0), follow_redirects=False, trust_env=False, timeout=timeout) as client:
            result["httpAttempts"] = 1
            async with client.stream("POST", endpoint(url), headers=request_headers, **kwargs) as response:
                result.update(status=response.status_code, correlationIds=correlations(response.headers))
                if stream:
                    parser = MessagesStream() if request.kind == "messages" else AgentStream(started, request)
                    if response.status_code == 200 and response.headers.get("content-type", "").split(";")[0] == "text/event-stream":
                        async for line in response.aiter_lines(): parser.line(line)
                        summary = parser.result(); result.update(summary)
                        result["succeeded"] = bool(summary.get("normalTerminal") and summary.get("outputObserved") and summary.get("expectedContentObserved", True))
                else:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_BODY: raise ValueError("Response exceeds local inspection limit")
                        body.extend(chunk)
                    result["responseBytes"] = len(body)
                    if 200 <= response.status_code < 300:
                        if request.kind == "tts":
                            with wave.open(io.BytesIO(body), "rb") as audio:
                                result.update(audioChannels=audio.getnchannels(), audioRate=audio.getframerate(), audioFrames=audio.getnframes(), audioSampleBytes=audio.getsampwidth())
                                pcm = audio.readframes(audio.getnframes()); compression = audio.getcomptype()
                            result["shapeValid"] = result["audioChannels"] == 1 and result["audioRate"] == 16000 and result["audioFrames"] > 0 and result["audioSampleBytes"] == 2 and len(pcm) == result["audioFrames"] * 2 and compression == "NONE"
                            result["artifactSha256"] = hashlib.sha256(body).hexdigest()
                            if result["shapeValid"] and artifact: private_bytes(Path(artifact), bytes(body))
                        else:
                            parsed = json.loads(body); result.update(inspect_json(request, parsed))
                            if artifact and request.kind in ("image-generate", "image-edit") and result["shapeValid"]:
                                private_bytes(Path(artifact), base64.b64decode(parsed["data"][0]["b64_json"], validate=True))
                        result["succeeded"] = bool(result.get("shapeValid") and result.get("expectedContentObserved", True) and result.get("rankingExpected", True))
    except Exception as exc:
        # 例外メッセージには上流の本文・URL・資格情報が入るため、種類だけを記録する。
        result["failureClass"] = type(exc).__name__
    result["durationMs"] = round((time.monotonic() - started) * 1000, 2)
    return result


def main():
    """prepareのローカル資材生成と、runの実API呼出しを区別して実行する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare"); prepare.add_argument("--directory", type=Path, required=True)
    convert = commands.add_parser("convert-audio"); convert.add_argument("--input", type=Path, required=True); convert.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run"); run.add_argument("kind", choices=KINDS); run.add_argument("--url", required=True)
    run.add_argument("--model"); run.add_argument("--voice"); run.add_argument("--audio-file", type=Path)
    run.add_argument("--operation-id", choices=("chat-completions", "responses-create"))
    run.add_argument("--profile-id", choices=("vision", "pdf", "audio-chat"))
    run.add_argument("--expected-text"); run.add_argument("--stream", action="store_true")
    run.add_argument("--agent-mode", choices=("fixed", "model", "pii"), default="fixed")
    run.add_argument("--max-tokens", type=int, default=1024, help="Messages only; bounded allowance also covers adaptive-thinking output")
    run.add_argument("--token-file"); run.add_argument("--key-file"); run.add_argument("--artifact", type=Path)
    run.add_argument("--timeout", type=int, default=120); run.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = assets(args.directory)
    elif args.command == "convert-audio":
        if args.input.resolve() == args.output.resolve(): raise ValueError("Keep the original 16 kHz fixture")
        converted = upsample_speech(args.input.read_bytes()); private_bytes(args.output, converted)
        result = {"inputRate": 16000, "outputRate": 24000, "outputBytes": len(converted), "sha256": hashlib.sha256(converted).hexdigest(), "azureRequests": 0}
    else:
        request = request_for(args.kind, model=args.model, voice=args.voice, audio=args.audio_file.read_bytes() if args.audio_file else None, stream=args.stream, expected_text=args.expected_text, agent_mode=args.agent_mode, max_tokens=args.max_tokens,
                              operation_id=args.operation_id, profile_id=args.profile_id)
        result = asyncio.run(probe(args.url, request, auth_headers(args.token_file, args.key_file), stream=args.stream, artifact=args.artifact, timeout=args.timeout))
        private_write(args.output, json.dumps(result, indent=2))
    print(json.dumps(result))
    return 0 if args.command != "run" or result["succeeded"] else 1


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (ValueError, OSError, KeyError, wave.Error, EOFError) as exc:
        print(json.dumps({"succeeded": False, "failureClass": type(exc).__name__})); raise SystemExit(2)
