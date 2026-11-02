#!/usr/bin/env python3
"""短い合成音声をWSSで1往復させ、音声応答と必要なら文字起こしを確認する。

入力はTTS試験などで作成した24kHz・16bit・モノラルPCM WAV、固定WSS接続先、
保護した資格情報である。WebSocketの接続成功やテキストだけの応答では、
音声往復の成功にしない。音声データの受信とサービスの正常終端まで確認する。
Voice Liveの入力音声認識を試す場合は、互換性を確認したテキストモデル、言語、
期待する合成発話を明示し、サービスが設定を受け付けた後に音声を送る。
証跡には音声・発話本文を残さず、バイト数、ハッシュ、イベント数、合否を保存する。
実行時は音声・モデル利用料が発生し得る。モデル変更やAzure資源の作成は行わない。

公式仕様:
https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-how-to#audio-input-transcription
https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-api-reference-2025-10-01#conversationiteminput_audio_transcriptioncompleted
"""
from __future__ import annotations
import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import wave
from websockets.asyncio.client import connect

from integration_client import auth_headers, correlations, private_write, usage_numbers

EVENTS = frozenset(("session.created", "session.updated", "input_audio_buffer.committed", "input_audio_buffer.speech_started",
                   "input_audio_buffer.speech_stopped", "response.created", "response.done", "response.audio.delta", "response.audio.done",
                   "response.output_audio.delta", "response.output_audio.done", "error",
                   "conversation.item.input_audio_transcription.delta", "conversation.item.input_audio_transcription.completed",
                   "conversation.item.input_audio_transcription.failed"))

# 音声入力認識に使うテキストモデルは、公式仕様で互換性を確認したものに限定する。
# 新しいモデルを加える場合は別途互換性を確認し、失敗時の代用として切り替えない。
ASR_CHAT_MODELS = frozenset(("gpt-4.1",))
SCENARIOS = ("roundtrip", "cancel", "disconnect", "keepalive")


def words(value):
    """合成発話を英数字の単語列へ揃え、表記差を除いて期待語句と照合する。"""
    return re.findall(r"[a-z0-9]+", value.casefold())


def recognition_options(product, expected_transcript, language, model):
    """明示したVoice Live互換モデル・言語・短い期待発話の組合せを検査する。"""
    if expected_transcript is None:
        if language is not None or model is not None:
            raise ValueError("Transcription settings require an explicit expected transcript")
        return None
    if product != "voice-live" or model not in ASR_CHAT_MODELS:
        raise ValueError("Streaming ASR requires Voice Live and an explicitly supported text model")
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}", language):
        raise ValueError("Use an explicit supported transcription locale")
    if not isinstance(expected_transcript, str) or not 1 <= len(expected_transcript) <= 256 or not words(expected_transcript):
        raise ValueError("Use a short synthetic expected transcript")
    if any(ord(character) < 32 for character in expected_transcript):
        raise ValueError("Expected transcript must not contain control characters")
    return {"model": "azure-speech", "language": language}


class FixedTargetConnect(connect):
    """WSSリダイレクトを拒否し、Gatewayの資格情報を別ホストへ転送しない。"""
    def process_redirect(self, exc):
        """転送先へ再接続せず、元のリダイレクトをエラーとして返す。"""
        return exc


def pcm_from_wav(data):
    """短い24kHz・モノラル・16bit PCM WAVだけを受け、音声ヘッダーからPCMを取り出す。"""
    if len(data) > 2 * 1024 * 1024: raise ValueError("Use a short synthetic WAV")
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != 24000 or source.getcomptype() != "NONE":
                raise ValueError("Expected uncompressed mono 16-bit PCM WAV at 24 kHz")
            pcm = source.readframes(source.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError("Invalid synthetic WAV") from exc
    if not 4800 <= len(pcm) <= 24000 * 2 * 15: raise ValueError("Use 0.1 to 15 seconds of synthetic speech")
    return pcm


def endpoint(value):
    """秘密をURLへ含めない、明示されたWSS接続先を検査する。"""
    parsed = urlsplit(value)
    if parsed.scheme != "wss" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("An explicit WSS Gateway endpoint is required")
    if any(key in parsed.query.lower() for key in ("token=", "api-key=", "subscription-key=", "sig=")):
        raise ValueError("Credentials must be in protected headers, not the URL")
    return value


def settings(product, voice, *, expected_transcript=None, language=None, model=None):
    """RealtimeとVoice Liveそれぞれの契約に合うsession.updateを作る。

    両製品の形式は同一ではないため、別製品の設定へ黙って置き換えない。
    """
    transcription = recognition_options(product, expected_transcript, language, model)
    if not re.fullmatch(r"[A-Za-z0-9:._-]{1,150}", voice): raise ValueError("Explicit valid voice required")
    if product == "realtime":
        # 音声の短い返答にも text/audio token を使う。有限の 512 と短文指示を
        # 組み合わせ、接続は正常なのに 128 の上限で途中終了する事態を避ける。
        session = {"type": "realtime", "output_modalities": ["audio"], "max_output_tokens": 512, "tools": [],
                   "instructions": "Reply only OK.",
                   "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000}, "turn_detection": None},
                             "output": {"format": {"type": "audio/pcm", "rate": 24000}, "voice": voice}}}
    elif product == "voice-live":
        session = {"modalities": ["text", "audio"], "input_audio_format": "pcm16", "output_audio_format": "pcm16",
                   "voice": {"type": "azure-standard", "name": voice}, "turn_detection": None,
                   "input_audio_sampling_rate": 24000, "max_response_output_tokens": 128, "tools": []}
        if transcription: session["input_audio_transcription"] = transcription
    else: raise ValueError("Choose the actual product, not a substitute")
    return {"type": "session.update", "session": session}


class Evidence:
    """音声と文字起こしを一時検査し、本文を含まない数値・ハッシュ・合否を集計する。"""
    def __init__(self, product, input_pcm, *, expected_transcript=None, language=None, model=None, scenario="roundtrip"):
        """入力音声量と要求した認識条件を記録し、本文を保存しない集計状態を用意する。"""
        if scenario not in SCENARIOS:
            raise ValueError("Choose an explicit supported voice scenario")
        if expected_transcript is not None and scenario != "roundtrip":
            raise ValueError("Keep input recognition acceptance separate from interruption scenarios")
        self.transcription = recognition_options(product, expected_transcript, language, model)
        self.scenario = scenario
        self.expected_words = words(expected_transcript) if expected_transcript is not None else []
        self.model, self.input_item = model, None
        self.transcripts = {}
        self.result = {"product": product, "startedAt": datetime.now(timezone.utc).isoformat(), "webSocketConnected": False,
                       "audioInputBytes": len(input_pcm), "audioOutputBytes": 0, "eventCounts": {}, "usage": {},
                       "serviceTerminal": False, "serviceError": False, "succeeded": False, "connectionAttempts": 1,
                       "streamingRecognitionRequested": bool(self.transcription),
                       "streamingRecognitionProduct": "voice-live/azure-speech" if self.transcription else None,
                       "selectedChatModel": model, "transcriptionLanguage": language,
                       "sessionModelMatched": False, "transcriptionSettingsAcknowledged": False,
                       "scenario": scenario, "cancelSent": False, "cancellationConfirmed": False,
                       "clientClosedMidResponse": False, "keepalivePong": False,
                       "serviceStopVerified": False}
        self.hasher = hashlib.sha256()

    def recognition_state(self):
        """実際に送信した入力itemに対応する認識状態だけを取り出す。"""
        return self.transcripts.get(self.input_item, {})

    def recognition_terminal(self):
        """対応する認識itemが完了または失敗に達したかを調べる。"""
        state = self.recognition_state()
        return bool(state.get("completed") or state.get("failed"))

    def transcription_event(self, value):
        """途中認識と確定認識を区別し、正しい入力itemの最終発話で期待語句を判定する。"""
        item = value.get("item_id")
        if not isinstance(item, str) or not 1 <= len(item) <= 200 or type(value.get("content_index")) is not int:
            raise ValueError("Invalid input transcription identity")
        if value["content_index"] != 0: return
        if item not in self.transcripts:
            if len(self.transcripts) >= 8: raise ValueError("Too many input transcription items")
            self.transcripts[item] = {"partial": "", "deltas": 0, "completed": False, "failed": False, "matched": False}
        state = self.transcripts[item]
        if value["type"].endswith(".delta"):
            delta = value.get("delta")
            if not isinstance(delta, str) or len(state["partial"]) + len(delta) > 8192:
                raise ValueError("Invalid or oversized transcription delta")
            state["partial"] += delta
            state["deltas"] += 1
        elif value["type"].endswith(".completed"):
            transcript = value.get("transcript")
            if not isinstance(transcript, str) or len(transcript) > 8192:
                raise ValueError("Invalid final input transcription")
            # 部分認識の一致だけで合格にしない。最終確定の文字列を使い、
            # 別の入力itemの完了イベントで今回の認識を成功扱いにしない。
            observed = words(transcript)
            count = len(self.expected_words)
            state["matched"] = bool(count and any(observed[index:index + count] == self.expected_words for index in range(len(observed) - count + 1)))
            state["completed"] = True
            state["partial"] = ""
        else:
            state["failed"] = True
            state["partial"] = ""

    def event(self, raw):
        """既知イベントを読み、設定受付・音声受信・終端・利用量の状態を更新する。"""
        if not isinstance(raw, str) or len(raw) > 8 * 1024 * 1024: raise ValueError("Invalid service event")
        value = json.loads(raw)
        if not isinstance(value, dict): raise ValueError("Invalid service event")
        kind = value.get("type")
        if kind in EVENTS: self.result["eventCounts"][kind] = self.result["eventCounts"].get(kind, 0) + 1
        if kind == "error": self.result["serviceError"] = True
        if self.transcription:
            if kind in ("session.created", "session.updated"):
                session = value.get("session", {})
                if not isinstance(session, dict): raise ValueError("Invalid session acknowledgement")
                if "model" in session:
                    self.result["sessionModelMatched"] = session["model"] == self.model
                if kind == "session.updated":
                    transcription = session.get("input_audio_transcription")
                    self.result["transcriptionSettingsAcknowledged"] = isinstance(transcription, dict) and all(
                        transcription.get(key) == expected for key, expected in self.transcription.items())
            elif kind == "input_audio_buffer.committed":
                item = value.get("item_id")
                if not isinstance(item, str) or not 1 <= len(item) <= 200 or self.input_item not in (None, item):
                    raise ValueError("Expected exactly one committed input item")
                self.input_item = item
            elif kind in ("conversation.item.input_audio_transcription.delta", "conversation.item.input_audio_transcription.completed", "conversation.item.input_audio_transcription.failed"):
                self.transcription_event(value)
        if kind in ("response.audio.delta", "response.output_audio.delta"):
            audio = base64.b64decode(value.get("delta", ""), validate=True)
            if self.result["audioOutputBytes"] + len(audio) > 8 * 1024 * 1024: raise ValueError("Audio response exceeds inspection limit")
            self.result["audioOutputBytes"] += len(audio); self.hasher.update(audio)
        if kind == "response.done":
            response = value.get("response", {})
            if not isinstance(response, dict): raise ValueError("Invalid terminal event")
            # 生の provider エラー・発話は保存しない。正常終端と出力枠不足を
            # 区別するため、既知の status/reason だけを固定値へ分類して記録する。
            status = response.get("status")
            self.result["responseStatus"] = status if status in ("completed", "cancelled", "failed", "incomplete") else "unknown"
            details = response.get("status_details")
            reason = details.get("reason") if isinstance(details, dict) else None
            self.result["responseStatusReason"] = (reason if reason in (
                "max_output_tokens", "content_filter", "turn_detected", "client_cancelled")
                else None if reason is None else "unknown")
            self.result["serviceTerminal"] = response.get("status") == "completed"
            self.result["cancellationConfirmed"] = self.result["cancelSent"] and response.get("status") == "cancelled"
            self.result["serviceStopVerified"] = response.get("status") in ("completed", "cancelled")
            self.result["serviceError"] |= response.get("status") in ("failed", "incomplete") or (
                response.get("status") == "cancelled" and self.scenario != "cancel")
            self.result["usage"].update(usage_numbers(response.get("usage")))
    def finish(self):
        """接続だけで成功にせず、音声出力・正常終端と必要なら認識の条件を総合判定する。"""
        result = dict(self.result)
        result["audioOutputSha256"] = self.hasher.hexdigest() if result["audioOutputBytes"] else None
        result["voiceRoundtripSucceeded"] = result["webSocketConnected"] and result["serviceTerminal"] and not result["serviceError"] and result["audioOutputBytes"] > 0
        state = self.recognition_state()
        result.update(inputTranscriptionCompleted=bool(state.get("completed")), inputTranscriptionFailed=bool(state.get("failed")),
                      inputTranscriptionDeltaEvents=state.get("deltas", 0), expectedTranscriptObserved=bool(state.get("matched")),
                      inputItemMatched=bool(self.input_item and state))
        result["streamingRecognitionSucceeded"] = bool(self.transcription and result["webSocketConnected"]
            and result["sessionModelMatched"] and result["transcriptionSettingsAcknowledged"] and result["inputItemMatched"]
            and result["inputTranscriptionCompleted"] and result["expectedTranscriptObserved"] and not result["inputTranscriptionFailed"]
            and not result["serviceError"])
        result["succeeded"] = result["voiceRoundtripSucceeded"] and (not self.transcription or result["streamingRecognitionSucceeded"])
        # 取消・途中切断は通常の往復成功とは分けます。切断成功は端末の socket が
        # 閉じたことだけであり、サービスの課金停止を確認した意味にはしません。
        if self.scenario == "cancel":
            result["succeeded"] = bool(result["webSocketConnected"] and result["audioOutputBytes"] > 0
                and result["cancellationConfirmed"] and not result["serviceError"])
        elif self.scenario == "disconnect":
            result["succeeded"] = bool(result["webSocketConnected"] and result["audioOutputBytes"] > 0
                and result["clientClosedMidResponse"] and not result["serviceError"])
        elif self.scenario == "keepalive":
            result["succeeded"] = bool(result["succeeded"] and result["keepalivePong"])
        result["scenarioSucceeded"] = result["succeeded"]
        return result


async def probe(url, product, voice, pcm, headers, *, timeout=60, connector=None, expected_transcript=None, language=None, model=None,
                scenario="roundtrip", keepalive_seconds=5):
    """設定受付を待ってから合成音声を送り、音声往復と認識の結果を1回だけ確認する。

    接続先の自動切替・再試行をせず、サービスが要求したモデルと認識設定を
    受け付けていない場合は、入力音声を送る前に停止する。
    """
    if type(keepalive_seconds) not in (int, float) or not 0 <= keepalive_seconds <= 15:
        raise ValueError("Keepalive idle interval must be between zero and 15 seconds")
    evidence = Evidence(product, pcm, expected_transcript=expected_transcript, language=language, model=model, scenario=scenario)
    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout):
            async with (connector or FixedTargetConnect)(endpoint(url), additional_headers=headers, open_timeout=15, close_timeout=5,
                                               max_size=8 * 1024 * 1024, proxy=None) as socket:
                evidence.result["webSocketConnected"] = True
                await socket.send(json.dumps(settings(product, voice, expected_transcript=expected_transcript, language=language, model=model)))
                # サービスの設定受付を先に確認し、意図しない設定のまま音声を送信しない。
                while not evidence.result["eventCounts"].get("session.updated") and not evidence.result["serviceError"]:
                    evidence.event(await socket.recv())
                if evidence.result["serviceError"]: return evidence.finish()
                if evidence.transcription and not (evidence.result["sessionModelMatched"] and evidence.result["transcriptionSettingsAcknowledged"]):
                    raise ValueError("Service did not acknowledge the selected text model and ASR settings")
                if scenario == "keepalive":
                    # 既存の同じ WSS を短時間保ち、Ping/Pong 後にも音声が往復するかを
                    # 確認します。数時間の idle timeout 保証や上流の請求量にはしません。
                    await asyncio.sleep(keepalive_seconds)
                    pong = await socket.ping()
                    await pong
                    evidence.result["keepalivePong"] = True
                    evidence.result["keepaliveIdleSeconds"] = keepalive_seconds
                for offset in range(0, len(pcm), 24000):
                    await socket.send(json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[offset:offset + 24000]).decode()}))
                await socket.send(json.dumps({"type": "input_audio_buffer.commit"}))
                if evidence.transcription:
                    while not evidence.input_item and not evidence.result["serviceError"]:
                        evidence.event(await socket.recv())
                    if evidence.result["serviceError"]: return evidence.finish()
                create = {"type": "response.create"}
                if product == "realtime" and scenario == "cancel":
                    # 一語の OK は取消前に完了し得るため、取消ケースだけ短い数唱を
                    # 要求する。session の 512 枠と全体 timeout は変えず再送もしない。
                    create["response"] = {"instructions": "Count from one to ten. Finish in under ten seconds."}
                await socket.send(json.dumps(create))
                while not (evidence.result["serviceTerminal"] and (not evidence.transcription or evidence.recognition_terminal())) and not evidence.result["serviceError"]:
                    evidence.event(await socket.recv())
                    if evidence.result["audioOutputBytes"] and scenario == "cancel" and not evidence.result["cancelSent"]:
                        # 最初の実音声 delta 後に一回だけ取り消し、cancelled 終端を待つ。
                        # 既に completed だった場合を取消成功へ読み替えたり再試行しません。
                        await socket.send(json.dumps({"type": "response.cancel"}))
                        evidence.result["cancelSent"] = True
                    if evidence.result["cancellationConfirmed"]:
                        break
                    if evidence.result["audioOutputBytes"] and scenario == "disconnect" and not evidence.result["serviceTerminal"]:
                        await socket.close(code=1000)
                        evidence.result["clientClosedMidResponse"] = True
                        break
    except Exception as exc:
        evidence.result["failureClass"] = type(exc).__name__
        # Upgrade前の拒否は、接続後の音声エラーと分けて記録します。
        # 応答本文や認証ヘッダーは保存せず、HTTP状態と既知の相関IDだけを残します。
        response = getattr(exc, "response", None)
        if response is not None and isinstance(getattr(response, "status_code", None), int):
            evidence.result["handshakeHttpStatus"] = response.status_code
            evidence.result["correlationIds"] = correlations(response.headers)
    result = evidence.finish(); result["durationMs"] = round((time.monotonic() - started) * 1000, 2)
    return result


def main():
    """音声資材と接続設定を検査し、本文のない試験結果を保護ファイルへ保存する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True); parser.add_argument("--product", choices=("realtime", "voice-live"), required=True)
    parser.add_argument("--voice", required=True); parser.add_argument("--audio-file", type=Path, required=True)
    parser.add_argument("--expected-transcript", help="Short synthetic expected words; enables a separate Voice Live input-ASR check without logging text")
    parser.add_argument("--language", help="Explicit Voice Live transcription locale, for example en-US")
    parser.add_argument("--model", choices=sorted(ASR_CHAT_MODELS), help="Compatible text model already selected by the Gateway's fixed Voice Live backend; required for ASR")
    parser.add_argument("--token-file"); parser.add_argument("--key-file"); parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--scenario", choices=SCENARIOS, default="roundtrip",
                        help="One bounded connection: normal audio, service cancellation, client disconnect, or short idle Ping/Pong")
    parser.add_argument("--keepalive-seconds", type=float, default=5, help="Keepalive scenario idle seconds, from zero through 15")
    parser.add_argument("--output", required=True); args = parser.parse_args()
    try:
        pcm = pcm_from_wav(args.audio_file.read_bytes())
        result = asyncio.run(probe(args.url, args.product, args.voice, pcm, auth_headers(args.token_file, args.key_file), timeout=args.timeout,
                                  expected_transcript=args.expected_transcript, language=args.language, model=args.model,
                                  scenario=args.scenario, keepalive_seconds=args.keepalive_seconds))
        private_write(args.output, json.dumps(result, indent=2)); print(json.dumps(result))
        return 0 if result["succeeded"] else 1
    except (ValueError, OSError) as exc:
        print(json.dumps({"succeeded": False, "failureClass": type(exc).__name__})); return 2


if __name__ == "__main__": raise SystemExit(main())
