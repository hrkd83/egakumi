"""モデル・画像・音声の試験clientが、本文を残さず正否を判定することを検証する。

合成PNG・PDF・WAV、HTTP応答、SSEを使う。正常な終端や構造を確認し、壊れたデータを見かけのstatusだけで合格にしない。
"""
import asyncio
import base64
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import coverage_client as probe
try:
    import httpx2
except ImportError:
    httpx2 = None


class ContractTests(unittest.TestCase):
    """モデル・画像・音声の試験clientが、本文を残さず正否を判定することを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def test_prepare_creates_small_reproducible_synthetic_assets_and_no_network(self):
        with tempfile.TemporaryDirectory() as temp:
            data = probe.assets(Path(temp))
            self.assertEqual(data["azureRequests"], 0)
            self.assertEqual((Path(temp) / "blue.png").read_bytes(), probe.png_blue())
            self.assertTrue((Path(temp) / "marker.pdf").read_bytes().endswith(b"%%EOF\n"))
            self.assertIn(probe.PDF_MARKER.encode(), (Path(temp) / "marker.pdf").read_bytes())
            with wave.open(str(Path(temp) / "silence.wav")) as audio:
                self.assertEqual(audio.getnframes(), 24000)
            self.assertIn("not a successful speech", data["speechNote"])
            self.assertEqual((Path(temp) / "manifest.json").stat().st_mode & 0o777, 0o600)

    def test_inline_vision_and_pdf_preserve_stateless_models_contract(self):
        image = probe.request_for("vision", model="approved")
        pdf = probe.request_for("pdf", model="approved")
        for request in (image, pdf):
            self.assertFalse(request.json["store"])
            self.assertNotIn("file_id", json.dumps(request.json))
        self.assertEqual(image.json["max_completion_tokens"], 512)
        self.assertEqual(pdf.json["max_output_tokens"], 512)
        self.assertIn("data:image/png;base64,", image.json["messages"][0]["content"][1]["image_url"]["url"])
        self.assertIn("data:application/pdf;base64,", pdf.json["input"][0]["content"][1]["file_data"])

    def test_canonical_operation_and_profile_choose_distinct_input_payload_and_header(self):
        for operation, profile, content_key in (("chat-completions", "vision", "messages"),
                                                ("responses-create", "pdf", "input"),
                                                ("chat-completions", "audio-chat", "messages")):
            request = probe.request_for_operation(operation, profile, model="approved", audio=probe.silence_wav())
            self.assertEqual((request.operation_id, request.profile_id), (operation, profile))
            self.assertEqual(request.headers["X-Egakumi-Profile"], profile)
            self.assertIn(content_key, request.json)
            self.assertFalse(request.json["store"])
        audio = probe.request_for_operation("chat-completions", "audio-chat", model="approved", audio=probe.silence_wav())
        self.assertEqual(audio.json["messages"][0]["content"][1]["type"], "input_audio")
        for pair in (("responses-create", "vision"), ("chat-completions", "pdf"), ("chat-completions", "unknown")):
            with self.assertRaises(ValueError): probe.request_for_operation(*pair, model="approved")
        with self.assertRaises(ValueError):
            probe.request_for("vision", operation_id="responses-create", profile_id="pdf", model="approved")

    def test_agent_modes_remain_explicit_and_stateless(self):
        for mode in ("fixed", "model", "pii"):
            request = probe.request_for("agent", agent_mode=mode)
            self.assertEqual(request.json["metadata"]["sample_mode"], mode)
            self.assertFalse(request.json["store"])
        with self.assertRaises(ValueError): probe.request_for("agent", agent_mode="other")

    def test_agent_pii_requires_masked_synthetic_contact_in_json_and_stream(self):
        request = probe.request_for("agent", agent_mode="pii")
        self.assertIn("alice@example.com", request.json["input"])
        def result(text):
            return probe.inspect_json(request, {"status": "completed", "output": [{"content": [{"type": "output_text", "text": text}]}]})
        self.assertFalse(result("OK")["expectedContentObserved"])
        self.assertFalse(result(probe.PII_INPUT)["syntheticContactMasked"])
        self.assertTrue(result("Contact: *****************.")["syntheticContactMasked"])
        stream = probe.AgentStream(0, request)
        for delta in ("Contact: ", "*****************."):
            stream.event(json.dumps({"type": "response.output_text.delta", "delta": delta}))
        stream.event(json.dumps({"type": "response.completed", "response": {"status": "completed"}}))
        self.assertTrue(stream.result()["syntheticContactMasked"])
        self.assertNotIn("Contact:", json.dumps(stream.result()))

    def test_chat_vision_and_detailed_speech_require_expected_content(self):
        image = probe.request_for("vision", model="approved")
        self.assertTrue(probe.inspect_json(image, {"choices": [{"finish_reason": "stop", "message": {"content": "Blue"}}]})["expectedContentObserved"])
        speech = probe.request_for("stt", audio=probe.silence_wav(rate=16000), expected_text="workforce")
        result = probe.inspect_json(speech, {"RecognitionStatus": "Success", "NBest": [{"Display": "Hello workforce."}]})
        self.assertTrue(result["shapeValid"] and result["expectedContentObserved"])

    def test_vision_expected_word_does_not_override_incomplete_chat_terminal(self):
        """画像の正解語があっても、Chat の正常終端 stop が必要。"""
        image = probe.request_for("vision", model="approved")
        for reason in ("stop", "length", "content_filter", "tool_calls", None):
            choice = {"message": {"content": "Blue"}}
            if reason is not None:
                choice["finish_reason"] = reason
            with self.subTest(reason=reason):
                result = probe.inspect_json(image, {"choices": [choice]})
                self.assertTrue(result["expectedContentObserved"])
                self.assertEqual(result["shapeValid"], reason == "stop")

    def test_vector_shape_and_nonfinite_outputs_are_rejected(self):
        request = probe.request_for("embedding", model="approved")
        self.assertFalse(probe.inspect_json(request, {"data": [{"embedding": [1.0]}]})["shapeValid"])
        self.assertFalse(probe.inspect_json(request, {"data": [{"embedding": [float("nan")]}, {"embedding": [1]}]})["shapeValid"])
        self.assertFalse(probe.inspect_json(request, {"data": [{"embedding": [1]}, {"embedding": [1, 2]}]})["shapeValid"])
        result = probe.inspect_json(request, {"data": [{"index": 0, "embedding": [1, 2]}, {"index": 1, "embedding": [2, 3]}]})
        self.assertTrue(result["shapeValid"]); self.assertEqual(result["dimensions"], 2)
        self.assertFalse(probe.inspect_json(request, {"data": [{"index": 0, "embedding": [1]}, {"index": 0, "embedding": [2]}]})["shapeValid"])

    def test_rerank_duplicate_and_wrong_top_result_are_not_success(self):
        request = probe.request_for("rerank", model="approved")
        duplicate = {"results": [{"index": 0, "relevance_score": 1}, {"index": 0, "relevance_score": 0.5}]}
        self.assertFalse(probe.inspect_json(request, duplicate)["shapeValid"])
        wrong = {"results": [{"index": 1, "relevance_score": 1}, {"index": 0, "relevance_score": 0.5}]}
        self.assertFalse(probe.inspect_json(request, wrong)["rankingExpected"])

    def test_messages_stream_needs_output_stop_reason_and_terminal(self):
        self.assertEqual(probe.request_for("messages", model="approved").json["max_tokens"], 1024)
        self.assertEqual(probe.request_for("messages", model="approved", max_tokens=256).json["max_tokens"], 256)
        with self.assertRaises(ValueError): probe.request_for("messages", model="approved", max_tokens=1025)
        stream = probe.MessagesStream()
        for event in ({"type": "content_block_delta", "delta": {"text": "PRIVATE-CONTENT"}},
                      {"type": "message_stop"}):
            stream.line("data: " + json.dumps(event)); stream.line("")
        self.assertFalse(stream.result()["normalTerminal"])
        stream.line('data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}'); stream.line("")
        self.assertTrue(stream.result()["normalTerminal"])
        self.assertNotIn("PRIVATE-CONTENT", json.dumps(stream.result()))

    def test_artifact_writer_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "source"; target.write_text("keep")
            link = Path(temp) / "link"; link.symlink_to(target)
            with self.assertRaises(OSError): probe.private_bytes(link, b"replace")
            self.assertEqual(target.read_text(), "keep")

    def test_generated_png_is_decoded_and_truncation_or_bad_crc_is_rejected(self):
        self.assertEqual(probe.inspect_png(probe.png_blue())["width"], 32)
        for invalid in (probe.png_blue()[:40], probe.png_blue()[:-1], probe.png_blue()[:45] + b"x" + probe.png_blue()[46:]):
            with self.subTest(size=len(invalid)), self.assertRaises(ValueError): probe.inspect_png(invalid)

    def test_stt_uses_16k_and_wss_conversion_preserves_duration(self):
        original = probe.silence_wav(rate=16000)
        converted = probe.upsample_speech(original)
        with wave.open(io.BytesIO(converted)) as audio:
            self.assertEqual(audio.getframerate(), 24000)
            self.assertEqual(audio.getnframes(), 24000)
            self.assertEqual(len(audio.readframes(audio.getnframes())), 48000)
        self.assertEqual(probe.request_for("stt", audio=original).headers["Content-Type"], "audio/wav")
        with self.assertRaises(ValueError): probe.request_for("stt", audio=converted)


@unittest.skipIf(httpx2 is None, "Use the shared integration environment")
class TransportTests(unittest.IsolatedAsyncioTestCase):
    """モデル・画像・音声の試験clientが、本文を残さず正否を判定することを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    async def test_pdf_marker_must_be_observed_without_leaking_body_or_token(self):
        request = probe.request_for("pdf", model="approved")
        def handler(incoming):
            self.assertEqual(incoming.headers["authorization"], "Bearer private-token")
            self.assertEqual(incoming.headers["X-Egakumi-Profile"], "pdf")
            self.assertFalse(json.loads(incoming.content)["store"])
            self.assertEqual(json.loads(incoming.content)["max_output_tokens"], 512)
            # Azure Responses は正常でも error:null を含む。本文の marker と
            # completed を確認しつつ、null をエラーとして拒否しないことも通す。
            return httpx2.Response(200, json={"status": "completed", "error": None, "output": [{"content": [{"type": "output_text", "text": probe.PDF_MARKER}]}]})
        result = await probe.probe("https://gateway.invalid/pdf", request, {"Authorization": "Bearer private-token"}, transport=httpx2.MockTransport(handler))
        self.assertTrue(result["succeeded"])
        self.assertNotIn("private-token", json.dumps(result))
        self.assertNotIn(probe.PDF_MARKER, json.dumps(result))
        self.assertEqual(result["httpAttempts"], 1)
        self.assertEqual((result["operationId"], result["profileId"]), ("responses-create", "pdf"))

    async def test_multipart_edit_uses_binary_part_not_a_json_wrapper(self):
        def handler(incoming):
            self.assertTrue(incoming.headers["content-type"].startswith("multipart/form-data; boundary="))
            self.assertIn(probe.png_blue(), incoming.content)
            return httpx2.Response(200, json={"data": [{"b64_json": base64.b64encode(probe.png_blue()).decode()}]})
        result = await probe.probe("https://gateway.invalid/edit", probe.request_for("image-edit", model="approved"), {}, transport=httpx2.MockTransport(handler))
        self.assertTrue(result["succeeded"]); self.assertEqual(result["width"], 32)

    async def test_429_is_not_retried_and_provider_error_body_is_discarded(self):
        count = 0
        def handler(request):
            nonlocal count; count += 1
            return httpx2.Response(429, json={"error": {"message": "PRIVATE-PROVIDER-TEXT"}})
        result = await probe.probe("https://gateway.invalid/embed", probe.request_for("embedding", model="approved"), {}, transport=httpx2.MockTransport(handler))
        self.assertFalse(result["succeeded"]); self.assertEqual(count, 1)
        self.assertNotIn("PRIVATE-PROVIDER-TEXT", json.dumps(result))

    async def test_tts_roundtrip_preserves_playable_private_wav(self):
        def handler(request): return httpx2.Response(200, content=probe.silence_wav(rate=16000), headers={"content-type": "audio/wav"})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "audio.wav"
            result = await probe.probe("https://gateway.invalid/tts", probe.request_for("tts", voice="en-US-AvaNeural"), {}, transport=httpx2.MockTransport(handler), artifact=path)
            self.assertTrue(result["succeeded"])
            self.assertEqual(path.read_bytes(), probe.silence_wav(rate=16000))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            # Playable audio alone does not establish spoken-content correctness.
            self.assertNotIn("recognitionSucceeded", result)

    async def test_speech_success_needs_recognized_words(self):
        request = probe.request_for("stt", audio=probe.silence_wav(rate=16000), expected_text="workforce")
        def handler(incoming): return httpx2.Response(200, json={"RecognitionStatus": "Success", "DisplayText": "different words"})
        result = await probe.probe("https://gateway.invalid/stt", request, {}, transport=httpx2.MockTransport(handler))
        self.assertFalse(result["succeeded"])

    async def test_truncated_pcm_data_is_not_a_playable_tts_success(self):
        def handler(request): return httpx2.Response(200, content=probe.silence_wav(rate=16000)[:60])
        result = await probe.probe("https://gateway.invalid/tts", probe.request_for("tts", voice="en-US-AvaNeural"), {}, transport=httpx2.MockTransport(handler))
        self.assertFalse(result["succeeded"])


if __name__ == "__main__": unittest.main()
