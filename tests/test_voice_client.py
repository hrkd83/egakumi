"""音声WebSocket clientのイベント処理と終了判定を検証する。

合成したPCMと偽socketで、対応音声形式・送受信・認識・usageの扱いを調べる。実音声が聞こえるか、課金値が一致するかは実機で確認する。
"""
import asyncio
import base64
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import coverage_client
import voice_client


class VoiceContractTests(unittest.TestCase):
    """音声WebSocket clientのイベント処理と終了判定を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def test_upgrade_and_text_are_not_audio_success(self):
        evidence = voice_client.Evidence("realtime", b"x" * 4800)
        evidence.result["webSocketConnected"] = True
        evidence.event(json.dumps({"type": "response.done", "response": {"status": "completed"}}))
        self.assertFalse(evidence.finish()["succeeded"])

    def test_audio_terminal_usage_are_inspected_without_payloads(self):
        evidence = voice_client.Evidence("realtime", b"x" * 4800)
        evidence.result["webSocketConnected"] = True
        evidence.event(json.dumps({"type": "response.output_audio.delta", "delta": base64.b64encode(b"private-audio").decode()}))
        evidence.event(json.dumps({"type": "response.done", "response": {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 20}, "output": "private-transcript"}}))
        result = evidence.finish()
        self.assertTrue(result["succeeded"]); self.assertEqual(result["usage"]["output_tokens"], 20)
        self.assertNotIn("private-audio", json.dumps(result)); self.assertNotIn("private-transcript", json.dumps(result))

    def test_voice_live_and_realtime_are_distinct_contracts(self):
        realtime = voice_client.settings("realtime", "marin")["session"]
        self.assertIn("audio", realtime)
        self.assertEqual(realtime["max_output_tokens"], 512)
        self.assertEqual(realtime["instructions"], "Reply only OK.")
        voice = voice_client.settings("voice-live", "en-US-AvaNeural")["session"]
        self.assertIn("input_audio_format", voice)
        self.assertEqual(voice["max_response_output_tokens"], 4096)
        self.assertEqual(voice["reasoning_effort"], "low")
        self.assertEqual(voice["instructions"], "Reply only OK.")
        with self.assertRaises(ValueError): voice_client.settings("unknown", "marin")

    def test_terminal_status_reason_are_allowlisted_without_provider_details(self):
        """出力枠不足は接続障害と区別し、未知の説明文は証跡へ流さない。"""
        for status, reason, expected in (("incomplete", "max_output_tokens", "max_output_tokens"),
                                         ("failed", "PRIVATE-provider-reason", "unknown")):
            evidence = voice_client.Evidence("realtime", b"x" * 4800)
            evidence.event(json.dumps({"type": "response.done", "response": {
                "status": status, "status_details": {"reason": reason, "error": {"message": "PRIVATE"}}}}))
            result = evidence.finish()
            self.assertEqual(result["responseStatus"], status)
            self.assertEqual(result["responseStatusReason"], expected)
            self.assertTrue(result["serviceError"])
            self.assertFalse(result["succeeded"])
            self.assertNotIn("PRIVATE", json.dumps(result))

    def test_streaming_asr_requires_explicit_compatible_text_model_and_language(self):
        options = {"expected_transcript": "Hello workforce", "language": "en-US", "model": "gpt-6.1-sol"}
        session = voice_client.settings("voice-live", "en-US-AvaNeural", **options)["session"]
        self.assertEqual(session["input_audio_transcription"], {"model": "azure-speech", "language": "en-US"})
        self.assertEqual(session["input_audio_sampling_rate"], 24000)
        self.assertEqual(session["max_response_output_tokens"], 4096)
        for product, changes in (("realtime", {}), ("voice-live", {"model": "gpt-realtime"}),
                                 ("voice-live", {"model": None}), ("voice-live", {"language": None}),
                                 ("voice-live", {"expected_transcript": ""}), ("voice-live", {"expected_transcript": "private\nwords"})):
            with self.subTest(product=product, changes=changes), self.assertRaises(ValueError):
                voice_client.settings(product, "en-US-AvaNeural", **{**options, **changes})
        with self.assertRaises(ValueError):
            voice_client.settings("voice-live", "en-US-AvaNeural", model="gpt-6.1-sol")

    def test_partial_or_output_transcript_is_not_completed_input_recognition(self):
        evidence = voice_client.Evidence("voice-live", b"x" * 4800, expected_transcript="Hello workforce", language="en-US", model="gpt-6.1-sol")
        evidence.result.update(webSocketConnected=True, serviceTerminal=True, sessionModelMatched=True, transcriptionSettingsAcknowledged=True)
        for event in ({"type": "input_audio_buffer.committed", "item_id": "input-one"},
                      {"type": "conversation.item.input_audio_transcription.delta", "item_id": "input-one", "content_index": 0, "delta": "Hello workforce"},
                      {"type": "response.audio_transcript.done", "transcript": "Hello workforce"},
                      {"type": "response.audio.delta", "delta": base64.b64encode(b"pcm-output").decode()}):
            evidence.event(json.dumps(event))
        result = evidence.finish()
        self.assertTrue(result["voiceRoundtripSucceeded"])
        self.assertFalse(result["streamingRecognitionSucceeded"])
        self.assertFalse(result["inputTranscriptionCompleted"])
        evidence.event(json.dumps({"type": "conversation.item.input_audio_transcription.completed", "item_id": "input-one", "content_index": 0, "transcript": "Different words"}))
        self.assertTrue(evidence.finish()["inputTranscriptionCompleted"])
        self.assertFalse(evidence.finish()["expectedTranscriptObserved"])
        self.assertFalse(evidence.finish()["succeeded"])

    def test_transcription_buffers_are_bounded_and_do_not_emit_text(self):
        evidence = voice_client.Evidence("voice-live", b"x" * 4800, expected_transcript="Hello workforce", language="en-US", model="gpt-6.1-sol")
        with self.assertRaises(ValueError):
            evidence.event(json.dumps({"type": "conversation.item.input_audio_transcription.delta", "item_id": "input-one", "content_index": 0, "delta": "PRIVATE" * 2000}))
        self.assertNotIn("PRIVATE", json.dumps(evidence.finish()))

    def test_credentials_are_not_permitted_in_wss_url(self):
        for url in ("ws://gateway.invalid", "wss://user:secret@gateway.invalid", "wss://gateway.invalid?access_token=private"):
            with self.subTest(url=url), self.assertRaises(ValueError): voice_client.endpoint(url)

    def test_redirects_cannot_forward_gateway_credentials(self):
        from websockets.exceptions import InvalidStatus
        from websockets.http11 import Response
        from websockets.datastructures import Headers
        response = Response(302, "Found", Headers({"Location": "wss://another.invalid/private"}))
        failure = InvalidStatus(response)
        client = voice_client.FixedTargetConnect("wss://gateway.invalid/voice", additional_headers={"Ocp-Apim-Subscription-Key": "protected"})
        self.assertIs(client.process_redirect(failure), failure)

    def test_wav_validation_and_voice_event_error_are_strict(self):
        self.assertEqual(len(voice_client.pcm_from_wav(coverage_client.silence_wav())), 48000)
        with self.assertRaises(ValueError): voice_client.pcm_from_wav(b"not-a-wave")
        evidence = voice_client.Evidence("voice-live", b"x" * 4800)
        evidence.result["webSocketConnected"] = True
        evidence.event('{"type":"error","error":{"message":"private-provider-error"}}')
        self.assertFalse(evidence.finish()["succeeded"])
        self.assertNotIn("private-provider-error", json.dumps(evidence.finish()))


class FakeSocket:
    """合成した音声イベントだけを送受信するWebSocketの代役。実サービスへ接続しない。"""
    def __init__(self, events): self.events = iter(events); self.sent = []
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def send(self, value): self.sent.append(json.loads(value))
    async def recv(self): return json.dumps(next(self.events))
    async def ping(self):
        self.sent.append({"type": "ping"})
        done = asyncio.get_running_loop().create_future(); done.set_result(None)
        return done
    async def close(self, code=1000): self.sent.append({"type": "close", "code": code})


class VoiceFlowTests(unittest.IsolatedAsyncioTestCase):
    """音声WebSocket clientのイベント処理と終了判定を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    async def test_rejected_upgrade_keeps_status_and_correlation_without_body_or_credentials(self):
        """HTTPで止まった接続の診断にも、秘密や上流本文を混ぜない。"""
        from websockets.exceptions import InvalidStatus
        from websockets.http11 import Response
        from websockets.datastructures import Headers
        response = Response(403, "Forbidden", Headers({"apim-request-id": "11111111-1111-4111-8111-111111111111",
                                                      "Authorization": "Bearer private-credential"}), body=b"private-response")
        class RejectedSocket:
            async def __aenter__(self): raise InvalidStatus(response)
            async def __aexit__(self, *args): pass
        result = await voice_client.probe("wss://gateway.invalid/voice", "realtime", "marin", b"x" * 4800, {},
                                          connector=lambda *args, **kwargs: RejectedSocket())
        self.assertFalse(result["succeeded"])
        self.assertEqual(result["handshakeHttpStatus"], 403)
        self.assertEqual(result["correlationIds"], {"apim-request-id": "11111111-1111-4111-8111-111111111111"})
        self.assertNotIn("private-", json.dumps(result))

    @staticmethod
    def transcription_events():
        return [{"type": "session.created", "session": {"model": "gpt-6.1-sol"}},
                {"type": "session.updated", "session": {"input_audio_transcription": {"model": "azure-speech", "language": "en-US", "phrase_list": []}}},
                {"type": "input_audio_buffer.committed", "item_id": "own-input"}]

    @staticmethod
    def completed_input(text="Hello workforce", item="own-input"):
        return {"type": "conversation.item.input_audio_transcription.completed", "item_id": item, "content_index": 0, "transcript": text}

    @staticmethod
    def audio_response():
        return [{"type": "response.audio.delta", "delta": base64.b64encode(b"synthetic-pcm-output").decode()},
                {"type": "response.done", "response": {"status": "completed"}}]

    async def test_cancel_requires_service_cancelled_terminal_and_never_retries(self):
        """取消要求の送信だけで合格にせず、応答が既に完了した場合も区別する。"""
        for product, voice in (("realtime", "marin"), ("voice-live", "en-US-AvaNeural")):
            for terminal, expected in (("cancelled", True), ("completed", False)):
                with self.subTest(product=product, terminal=terminal):
                    socket = FakeSocket([{"type": "session.updated"}, self.audio_response()[0],
                                         {"type": "response.done", "response": {"status": terminal}}])
                    result = await voice_client.probe("wss://gateway.invalid/voice", product, voice, b"x" * 4800, {},
                        connector=lambda *args, **kwargs: socket, scenario="cancel")
                    self.assertEqual(result["scenarioSucceeded"], expected)
                    self.assertEqual(result["cancellationConfirmed"], expected)
                    self.assertEqual(sum(event["type"] == "response.cancel" for event in socket.sent), 1)
                    create = next(event for event in socket.sent if event["type"] == "response.create")
                    self.assertEqual(create["response"]["instructions"], "Count from one to ten. Finish in under ten seconds.")
                    self.assertEqual(result["connectionAttempts"], 1)

    async def test_mid_response_disconnect_only_proves_client_close(self):
        socket = FakeSocket([{"type": "session.updated"}, self.audio_response()[0]])
        result = await voice_client.probe("wss://gateway.invalid/voice", "voice-live", "en-US-AvaNeural", b"x" * 4800, {},
            connector=lambda *args, **kwargs: socket, scenario="disconnect")
        self.assertTrue(result["scenarioSucceeded"] and result["clientClosedMidResponse"])
        self.assertFalse(result["voiceRoundtripSucceeded"] or result["serviceStopVerified"])
        self.assertEqual(socket.sent[-1], {"type": "close", "code": 1000})

    async def test_keepalive_pong_then_audio_use_the_same_connection(self):
        socket = FakeSocket([{"type": "session.updated"}, *self.audio_response()])
        result = await voice_client.probe("wss://gateway.invalid/voice", "realtime", "marin", b"x" * 4800, {},
            connector=lambda *args, **kwargs: socket, scenario="keepalive", keepalive_seconds=0)
        self.assertTrue(result["succeeded"] and result["keepalivePong"] and result["voiceRoundtripSucceeded"])
        self.assertEqual([event["type"] for event in socket.sent][:2], ["session.update", "ping"])
        self.assertEqual(result["connectionAttempts"], 1)

    async def test_control_scenarios_do_not_replace_streaming_asr_acceptance(self):
        with self.assertRaisesRegex(ValueError, "separate"):
            await voice_client.probe("wss://gateway.invalid/voice", "voice-live", "en-US-AvaNeural", b"x" * 4800, {},
                scenario="cancel", expected_transcript="Hello workforce", language="en-US", model="gpt-6.1-sol")

    async def test_real_protocol_flow_waits_for_ack_and_sends_one_audio_request(self):
        socket = FakeSocket([{"type": "session.created"}, {"type": "session.updated"},
                             {"type": "response.audio.delta", "delta": base64.b64encode(b"pcm-output").decode()},
                             {"type": "response.done", "response": {"status": "completed"}}])
        def connect(url, **kwargs):
            self.assertIsNone(kwargs["proxy"])
            self.assertEqual(kwargs["additional_headers"]["Authorization"], "Bearer protected")
            return socket
        result = await voice_client.probe("wss://gateway.invalid/voice", "voice-live", "en-US-AvaNeural", b"x" * 4800,
                                          {"Authorization": "Bearer protected"}, connector=connect)
        self.assertTrue(result["succeeded"])
        self.assertTrue(result["voiceRoundtripSucceeded"])
        self.assertFalse(result["streamingRecognitionSucceeded"])
        self.assertEqual([event["type"] for event in socket.sent], ["session.update", "input_audio_buffer.append", "input_audio_buffer.commit", "response.create"])
        self.assertNotIn("protected", json.dumps(result))

    async def test_voice_live_waits_for_asr_after_audio_response_and_correlates_input(self):
        deltas = [{"type": "conversation.item.input_audio_transcription.delta", "item_id": "own-input", "content_index": 0, "delta": part}
                  for part in ("Hello work", "force")]
        socket = FakeSocket([*self.transcription_events(), *deltas, *self.audio_response(), self.completed_input("Hello, workforce!")])
        result = await voice_client.probe("wss://gateway.invalid/voice-live", "voice-live", "en-US-AvaNeural", b"x" * 4800, {},
                                          connector=lambda *args, **kwargs: socket, expected_transcript="Hello workforce", language="en-US", model="gpt-6.1-sol")
        self.assertTrue(result["succeeded"])
        self.assertTrue(result["voiceRoundtripSucceeded"] and result["streamingRecognitionSucceeded"])
        self.assertEqual(result["streamingRecognitionProduct"], "voice-live/azure-speech")
        self.assertEqual(result["selectedChatModel"], "gpt-6.1-sol")
        self.assertEqual(result["inputTranscriptionDeltaEvents"], 2)
        self.assertTrue(result["inputItemMatched"] and result["inputTranscriptionCompleted"] and result["expectedTranscriptObserved"])
        self.assertEqual(socket.sent[0]["session"]["input_audio_transcription"], {"model": "azure-speech", "language": "en-US"})
        self.assertEqual([event["type"] for event in socket.sent], ["session.update", "input_audio_buffer.append", "input_audio_buffer.commit", "response.create"])
        for secret_text in ("Hello", "workforce", "own-input", "synthetic-pcm-output"):
            self.assertNotIn(secret_text, json.dumps(result))

    async def test_asr_completed_before_response_still_waits_for_audio_terminal(self):
        socket = FakeSocket([*self.transcription_events(), self.completed_input(), *self.audio_response()])
        result = await voice_client.probe("wss://gateway.invalid/voice-live", "voice-live", "en-US-AvaNeural", b"x" * 4800, {},
                                          connector=lambda *args, **kwargs: socket, expected_transcript="Hello workforce", language="en-US", model="gpt-6.1-sol")
        self.assertTrue(result["succeeded"])

    async def test_wrong_item_cannot_complete_own_asr_and_partial_timeout_is_failure(self):
        class WaitingSocket(FakeSocket):
            """音声WebSocket clientのイベント処理と終了判定を検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
            async def recv(self):
                try: return await super().recv()
                except RuntimeError:
                    await asyncio.Future()
        socket = WaitingSocket([*self.transcription_events(), *self.audio_response(), self.completed_input(item="other-input"),
                                {"type": "conversation.item.input_audio_transcription.delta", "item_id": "own-input", "content_index": 0, "delta": "Hello workforce"}])
        result = await voice_client.probe("wss://gateway.invalid/voice-live", "voice-live", "en-US-AvaNeural", b"x" * 4800, {}, timeout=0.02,
                                          connector=lambda *args, **kwargs: socket, expected_transcript="Hello workforce", language="en-US", model="gpt-6.1-sol")
        self.assertEqual(result["failureClass"], "TimeoutError")
        self.assertTrue(result["voiceRoundtripSucceeded"])
        self.assertFalse(result["inputTranscriptionCompleted"])
        self.assertFalse(result["streamingRecognitionSucceeded"] or result["succeeded"])
        self.assertNotIn("Hello workforce", json.dumps(result))

    async def test_asr_failure_is_distinct_from_audio_roundtrip_without_error_text(self):
        failed = {"type": "conversation.item.input_audio_transcription.failed", "item_id": "own-input", "content_index": 0,
                  "error": {"message": "PRIVATE-TRANSCRIPTION-ERROR"}}
        socket = FakeSocket([*self.transcription_events(), *self.audio_response(), failed])
        result = await voice_client.probe("wss://gateway.invalid/voice-live", "voice-live", "en-US-AvaNeural", b"x" * 4800, {},
                                          connector=lambda *args, **kwargs: socket, expected_transcript="Hello workforce", language="en-US", model="gpt-6.1-sol")
        self.assertTrue(result["voiceRoundtripSucceeded"])
        self.assertTrue(result["inputTranscriptionFailed"])
        self.assertFalse(result["streamingRecognitionSucceeded"] or result["succeeded"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    async def test_service_model_or_asr_mismatch_stops_before_submitting_audio(self):
        for mismatch in ("model", "asr"):
            events = self.transcription_events()
            if mismatch == "model": events[0]["session"]["model"] = "gpt-realtime"
            else: events[1]["session"]["input_audio_transcription"]["model"] = "whisper-1"
            socket = FakeSocket(events)
            result = await voice_client.probe("wss://gateway.invalid/voice-live", "voice-live", "en-US-AvaNeural", b"x" * 4800, {},
                                              connector=lambda *args, **kwargs: socket, expected_transcript="Hello workforce", language="en-US", model="gpt-6.1-sol")
            self.assertEqual(result["failureClass"], "ValueError")
            self.assertFalse(result["succeeded"])
            self.assertEqual([event["type"] for event in socket.sent], ["session.update"])


if __name__ == "__main__": unittest.main()
