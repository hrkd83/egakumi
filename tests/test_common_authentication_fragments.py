"""STEP4の部品化で認証本体・実行順・専用部品の所有境界を変えないことを確認する。"""
import copy
import hashlib
import json
from pathlib import Path
import re
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from deployment.helpers.apim_audit import expand_audit_fragments, render_audit_fragments, render_audit_policy
from deployment.helpers.apim_ingress import (
    expand_trusted_ingress_fragment, render_trusted_ingress_fragment,
    render_legacy_trusted_ingress_fragment,
)
from deployment.helpers.apim_browser_exception import expand_browser_exception_fragment
from deployment.helpers import apim_common_authentication as fragments
from deployment.helpers.apim_authentication import (
    _render_service_policy, _render_inline_common_authentication_service_policy,
    read_managed_entra_authentication, render_service_policy,
    transition_common_authentication_policy, validate_common_authentication_policy,
)
from test_audit_fragment_publication import audit_graph
from test_credential_login import settings as login_settings
from test_entra_gateway_authentication import settings as entra_settings
from test_entra_publication import APIM, GLOBAL
import entra_publication as publication


POLICIES = Path(__file__).resolve().parents[1] / "apps/gateway/apim/policies"
AUTH = "core-common-authentication"
ERROR = "core-entra-authentication-error"
TOKENS = re.compile(
    r'@"(?:[^"]|"")*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
    r'/\*[\s\S]*?\*/|//[^\n]*|[A-Za-z_][A-Za-z_0-9]*|\d+(?:\.\d+)?|'
    r'=>|==|!=|<=|>=|&&|\|\||\?\?|\?\.|[^\s]')

# 部品化前の公開生成元を、独立した合成設定で展開して取得した固定値。
# 説明コメントと文字列外の空白だけを除き、XML・C#の全トークンを固定する。
# 現行生成元から期待値を作り直して、実装の変更を見逃すことはしない。
BEFORE_CURRENT = {
    (False, False): "bf1bde0d50f69d95a5ce4e52099718b5669ebc5ff99cdef029c2b1985734bd4c",
    (False, True): "8318248106d06f5d1321bf3bdbe82abb23c6fb2dcf06a5f8d120cd9ebe838a18",
    (True, False): "029e2fbcccf2fce0f6f0df3bfc824ab4aacfde3d0321ab51ecc32393b38bf629",
    (True, True): "6aba239e3d354d825c717b9ba2f8f033e7de9e7f6b1f1f3f15a3191741261c68",
}
BEFORE_OLD_MODES = {
    "inline_audit_v2": "5eecb6707cfbd0fd09aaf8001d9a5629b5115f91b44392fc09973e29f145b360",
    "inline_trusted_ingress": "40f884de46d2b5dabe1cab5c305053c4cf27f3478df40b767a2e1e02b41acca8",
    "omit_explanations": "40f884de46d2b5dabe1cab5c305053c4cf27f3478df40b767a2e1e02b41acca8",
}


# STEP5で承認した監査readerのサイズ差だけを、STEP4の独立した旧hashへ投影します。
# 項目・型・組合せ・本人の上限を含むトークン列が一致しない変更は投影しません。
APPROVED_OBSERVER_BOUND = TOKENS.findall(
    '((string)obs[n]).Length<=(n=="subscriptionId"?256:!obsU&&n=="callerId"?269:253)')
PREVIOUS_OBSERVER_BOUND = TOKENS.findall('((string)obs[n]).Length<=253')
APPROVED_TOP_LEVEL_BOUND = TOKENS.findall(
    'allowed&&(string)decision["method"]=="subscriptionKey"'
    '&&!String.IsNullOrWhiteSpace(subscription)&&subscription.Length<=256'
    '&&(string)decision["callerId"]=="subscription:"+subscription')
PREVIOUS_TOP_LEVEL_BOUND = TOKENS.findall(
    'allowed&&(string)decision["method"]=="subscriptionKey"'
    '&&!String.IsNullOrWhiteSpace(subscription)&&subscription.Length<=240'
    '&&(string)decision["callerId"]=="subscription:"+subscription')


def code_value(value, *, project_observer=False):
    if value is None or not value.strip():
        return ""
    if not value.lstrip().startswith(("@{", "@(")):
        return value
    tokens = [token for token in TOKENS.findall(value) if not token.startswith(("/*", "//"))]
    if project_observer:
        projected, index = [], 0
        while index < len(tokens):
            for approved, previous in ((APPROVED_OBSERVER_BOUND, PREVIOUS_OBSERVER_BOUND),
                                       (APPROVED_TOP_LEVEL_BOUND, PREVIOUS_TOP_LEVEL_BOUND)):
                if tokens[index:index + len(approved)] == approved:
                    projected.extend(previous)
                    index += len(approved)
                    break
            else:
                projected.append(tokens[index])
                index += 1
        return projected
    return tokens


def code_shape(node, *, project_observer=False):
    return [node.tag, sorted((key, code_value(value, project_observer=project_observer)) for key, value in node.attrib.items()),
            code_value(node.text, project_observer=project_observer),
            [code_shape(child, project_observer=project_observer) for child in node]]


def project_reviewed_subject(root):
    """現正本か固定旧式に完全一致するwriterだけを旧式へ投影する。挿入・変更・移動は隠さない。"""
    nodes = root.findall("./inbound/set-variable[@id='core-audit-authenticated-subject'][@name='gatewayAuthenticatedSubject']")
    if len(nodes) != 1:
        raise AssertionError("The single ordered audit writer is required for the STEP4 projection")
    previous = ET.fromstring(render_audit_policy((POLICIES / "service-inline-audit-v2.xml").read_text(encoding="utf-8")))
    previous_value = previous.find("./inbound/set-variable[@name='gatewayAuthenticatedSubject']").get("value")
    current = ET.fromstring(render_audit_fragments()["core-audit-authenticated-subject"])[0].get("value")
    if code_value(nodes[0].get("value")) in (code_value(current), code_value(previous_value)):
        nodes[0].set("value", previous_value)
    return root


def expand_all(xml, *, login=None, entra_enabled=True):
    generated = fragments.render_common_authentication_fragments(entra_enabled=entra_enabled)
    xml = fragments.expand_common_authentication_fragments(xml, generated, entra_enabled=entra_enabled)
    xml = expand_trusted_ingress_fragment(xml)
    xml = expand_browser_exception_fragment(xml, login=login)
    return expand_audit_fragments(xml)


def fingerprint(xml, *, login=None, entra_enabled=True):
    expanded = expand_all(xml, login=login, entra_enabled=entra_enabled)
    root = project_reviewed_subject(ET.fromstring(expanded))
    # Retain the independently recorded authentication hashes. Only the exact
    # reviewed gRPC exemption removal is projected back to its frozen condition;
    # any other ingress alteration remains visible to the comparison.
    current = ET.fromstring(render_trusted_ingress_fragment()).find("choose/when").get("condition")
    previous = ET.fromstring(render_legacy_trusted_ingress_fragment()).find("choose/when").get("condition")
    for branch in root.findall("./inbound/choose[@id='core-trusted-ingress']/when"):
        if code_value(branch.get("condition")) == code_value(current):
            branch.set("condition", previous)
    encoded = json.dumps(code_shape(root, project_observer=True), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def authentication_graph(*, entra_enabled=True):
    current, transport = audit_graph()
    current.policies[GLOBAL] = render_service_policy(entra=entra_settings() if entra_enabled else None)
    current.policies.update({APIM + "/policyFragments/" + identifier: value
                            for identifier, value in fragments.render_common_authentication_fragments(
                                entra_enabled=entra_enabled).items()})
    return current, transport


def changed_xml(value, mutate):
    root = ET.fromstring(value)
    mutate(root)
    return ET.tostring(root, encoding="unicode")


class CommonAuthenticationFragmentPreservationTests(unittest.TestCase):
    def test_unreviewed_authentication_writer_or_budget_changes_are_not_projected_away(self):
        value = expand_all(render_service_policy(entra=entra_settings()))
        self.assertEqual(fingerprint(value), BEFORE_CURRENT[(False, True)])
        for mode in ("authentication", "writer", "observer-bound", "top-level-bound", "ingress"):
            root = ET.fromstring(value)
            if mode == "writer":
                node = root.find("./inbound/set-variable[@name='gatewayAuthenticatedSubject']")
                node.set("value", node.get("value").replace("gatewayCommonAuthenticationValidated", "unreviewedMarker", 1))
            elif mode == "authentication":
                node = root.find("./inbound/choose[@id='core-common-authentication']")
                success = node.find(".//set-variable[@name='gatewayCommonAuthenticationValidated']")
                success.set("value", "@(false)")
            elif mode == "observer-bound":
                nodes = [node for node in root.iter() if node.text and "?256:" in node.text]
                self.assertTrue(nodes)
                nodes[0].text = nodes[0].text.replace("?256:", "?257:", 1)
            elif mode == "ingress":
                branch = root.find("./inbound/choose[@id='core-trusted-ingress']/when")
                branch.set("condition", branch.get("condition").replace("X-Core-L7-Ingress", "X-Unreviewed-Ingress"))
            else:
                nodes = [node for node in root.iter() if node.text and re.search(r"subscription\.Length\s*<=\s*256", node.text)]
                self.assertTrue(nodes)
                nodes[0].text = re.sub(r"subscription\.Length\s*<=\s*256", "subscription.Length<=257", nodes[0].text, count=1)
            with self.subTest(mode=mode):
                self.assertNotEqual(fingerprint(ET.tostring(root, encoding="unicode")), BEFORE_CURRENT[(False, True)])

    def test_equivalent_xml_attribute_quoting_and_spacing_still_resolve_the_owned_source(self):
        original = render_service_policy(entra=entra_settings())
        for mode in ("single-quote", "attribute-spacing"):
            value = original
            for identifier in (AUTH, ERROR):
                for attribute in ("id", "fragment-id"):
                    before = attribute + '="' + identifier + '"'
                    after = (attribute + "='" + identifier + "'" if mode == "single-quote"
                             else attribute + ' = "' + identifier + '"')
                    value = value.replace(before, after)
            with self.subTest(mode=mode):
                validate_common_authentication_policy(value, entra=entra_settings())
                self.assertEqual(read_managed_entra_authentication(value, expected_entra=entra_settings()), entra_settings())

    def test_all_current_configurations_keep_the_independent_before_token_shape(self):
        for cm, entra_enabled in BEFORE_CURRENT:
            login = login_settings() if cm else None
            with self.subTest(cm=cm, entra=entra_enabled):
                value = render_service_policy(login, entra=entra_settings() if entra_enabled else None)
                self.assertEqual(fingerprint(value, login=login, entra_enabled=entra_enabled),
                                 BEFORE_CURRENT[(cm, entra_enabled)])

    def test_three_fixed_previous_modes_keep_their_before_token_shape(self):
        for mode, expected in BEFORE_OLD_MODES.items():
            with self.subTest(mode=mode):
                previous = _render_service_policy(entra=entra_settings(), **{mode: True})
                self.assertEqual(fingerprint(previous), expected)

    def test_before_inline_current_is_readable_only_through_verified_transition(self):
        for cm in (False, True):
            login = login_settings() if cm else None
            with self.subTest(cm=cm):
                previous = _render_inline_common_authentication_service_policy(login, entra=entra_settings())
                self.assertEqual(fingerprint(previous, login=login), BEFORE_CURRENT[(cm, True)])
                self.assertEqual(read_managed_entra_authentication(previous, login,
                                 expected_entra=entra_settings()), entra_settings())
                candidate, changed = transition_common_authentication_policy(
                    previous, login, previous_entra=entra_settings(), entra=entra_settings())
                self.assertTrue(changed)
                validate_common_authentication_policy(candidate, login, entra=entra_settings())
                self.assertEqual(fingerprint(candidate, login=login), BEFORE_CURRENT[(cm, True)])
                altered = previous.replace("Invalid subscription key", "Unreviewed key exception", 1)
                self.assertNotEqual(altered, previous)
                with self.assertRaises(ValueError):
                    read_managed_entra_authentication(altered, login, expected_entra=entra_settings())

    def test_success_markers_remain_at_the_end_of_their_success_paths(self):
        root = ET.fromstring(fragments.render_common_authentication_fragment(entra_enabled=True))
        choose = root.find("choose")
        key = choose.find("otherwise")
        self.assertEqual([(node.get("name"), node.get("value")) for node in list(key)[-2:]],
                         [("gatewaySubscriptionId", "@(context.Subscription.Id)"),
                          ("gatewayCommonAuthenticationValidated", "@(true)")])
        bearer = next(branch for branch in choose.findall("when")
                      if branch.find(".//validate-azure-ad-token") is not None)
        self.assertEqual([(node.get("name"), node.get("value")) for node in list(bearer)[-2:]],
                         [("gatewayEntraAuthenticationValidated", "@(true)"),
                          ("gatewayCommonAuthenticationValidated", "@(true)")])
        authentication = bearer.find(".//set-variable[@name='gatewayEntraAuthenticationResult']").get("value")
        fields = re.findall(r'new JProperty\("([^"]+)"', authentication)
        self.assertEqual(fields, ["schemaVersion", "callerId", "allocationId", "tenantId",
                                  "principalId", "clientId", "limits"])


class CommonAuthenticationSourceSectionTests(unittest.TestCase):
    def test_same_named_xml_owns_all_five_complete_sections_and_keeps_explanations(self):
        directory = POLICIES / "fragments/authentication" / AUTH
        xml_path = directory / (AUTH + ".xml")
        source_path = directory / (AUTH + ".cs")
        source = source_path.read_text(encoding="utf-8")
        sections = fragments.extract_common_authentication_sections(source)
        self.assertEqual(tuple(sections), fragments.COMMON_AUTHENTICATION_SECTIONS)
        self.assertEqual(fragments.COMMON_AUTHENTICATION_SOURCE, source_path.relative_to(POLICIES).as_posix())
        self.assertGreaterEqual(sum(line.lstrip().startswith("// 目的：") for line in source.splitlines()), 5)
        value = fragments.render_common_authentication_fragment(entra_enabled=True)
        deployed = ET.fromstring(value)
        text = "\n".join(item for node in deployed.iter() for item in (*node.attrib.values(), node.text or ""))
        for section, body in sections.items():
            marker = "__CORE_COMMON_AUTHENTICATION_" + section.upper().replace("-", "_") + "_CSHARP__"
            references = [path for path in POLICIES.rglob("*.xml") if marker in path.read_text(encoding="utf-8")]
            self.assertEqual(references, [xml_path], section)
            comments = ["/*" + line.lstrip()[2:].replace("*/", "* /") + " */"
                        for line in body.splitlines() if line.lstrip().startswith("//")]
            self.assertTrue(comments, section)
            for comment in comments:
                self.assertIn(comment, text, section)

    def test_section_parser_rejects_duplicate_missing_unknown_nested_and_orphan_delimiters(self):
        source = (POLICIES / fragments.COMMON_AUTHENTICATION_SOURCE).read_text(encoding="utf-8")
        first = fragments.COMMON_AUTHENTICATION_SECTIONS[0]
        mutations = {
            "duplicate": source + "\n// BEGIN " + first + "\nreturn true;\n// END " + first + "\n",
            "missing-end": source.replace("// END " + first, "// no END", 1),
            "missing-section": re.sub(r"(?ms)^// BEGIN " + first + r"\n.*?^// END " + first + r"\n", "", source),
            "unknown-section": source.replace("// BEGIN " + first, "// BEGIN unknown-section", 1).replace(
                "// END " + first, "// END unknown-section", 1),
            "nested": source.replace("// BEGIN " + first + "\n", "// BEGIN " + first + "\n// BEGIN entra-identity\n", 1),
            "mismatched-end": source.replace("// END " + first, "// END entra-identity", 1),
            "orphan-end-before": "// END unknown-section\n" + source,
            "orphan-end-after": source + "\n// END unknown-section\n",
            "malformed-begin": "// BEGIN unknown_section\n// END unknown_section\n" + source,
            "outside-code-before": "return true;\n" + source,
            "outside-code-after": source + "\nreturn true;\n",
        }
        for mode, value in mutations.items():
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                fragments.extract_common_authentication_sections(value)

    def test_each_source_section_needs_one_and_only_one_xml_marker(self):
        original = Path.read_text
        marker = "__CORE_COMMON_AUTHENTICATION_KEY_REQUEST_VALIDATION_CSHARP__"
        for mode in ("missing", "duplicate", "unknown"):
            def read(path, *args, **kwargs):
                value = original(path, *args, **kwargs)
                if path.name == AUTH + ".xml":
                    replacement = "" if mode == "missing" else marker + marker if mode == "duplicate" else "__CORE_COMMON_AUTHENTICATION_UNKNOWN_CSHARP__"
                    return value.replace(marker, replacement, 1)
                return value
            with self.subTest(mode=mode), mock.patch.object(Path, "read_text", read), self.assertRaises(ValueError):
                fragments.render_common_authentication_fragment(entra_enabled=True)


class CommonAuthenticationFragmentBoundaryTests(unittest.TestCase):
    def test_the_complete_generated_graph_is_accepted_in_both_states(self):
        for enabled in (False, True):
            current, _ = authentication_graph(entra_enabled=enabled)
            with self.subTest(entra=enabled):
                publication._audit_fragment_scope(current.policies, APIM)

    def test_generated_sources_are_complete_leaf_fragments_and_fit_the_actual_limit(self):
        for enabled in (False, True):
            generated = fragments.render_common_authentication_fragments(entra_enabled=enabled)
            self.assertEqual(set(generated), {AUTH, ERROR} if enabled else {AUTH})
            for identifier, value in generated.items():
                with self.subTest(entra=enabled, fragment=identifier):
                    root = ET.fromstring(value)
                    self.assertEqual(root.tag, "fragment")
                    self.assertEqual(len(root), 1)
                    self.assertEqual(root[0].tag, "choose")
                    self.assertEqual(root[0].attrib, {"id": identifier})
                    self.assertLess(len(value.encode("utf-8")), 512 * 1024)
                    self.assertFalse(list(root.iter("include-fragment")))
                    self.assertNotRegex(value, r"__[A-Z0-9_]+__")
                    self.assertNotIn("// BEGIN", value)

    def test_expansion_rejects_modified_missing_and_unrepresented_fragment_bodies(self):
        global_xml = render_service_policy(entra=entra_settings())
        original = fragments.render_common_authentication_fragments(entra_enabled=True)
        for mode in ("missing-common", "missing-error", "additional", "alter-expression",
                     "alter-claim", "nested-include", "duplicate-body"):
            generated = copy.deepcopy(original)
            if mode.startswith("missing"):
                generated.pop(AUTH if mode == "missing-common" else ERROR)
            elif mode == "additional":
                generated["unrepresented"] = '<fragment><set-variable name="extra" value="true" /></fragment>'
            else:
                def mutate(root):
                    if mode == "alter-expression":
                        root.find(".//set-variable[@name='gatewayCommonAuthenticationValidated']").set("value", "@(false)")
                    elif mode == "alter-claim":
                        root.find(".//required-claims/claim/value").text = "unreviewed-scope"
                    elif mode == "nested-include":
                        ET.SubElement(root, "include-fragment", {"fragment-id": "core-gateway-telemetry"})
                    else:
                        root.append(copy.deepcopy(root[0]))
                generated[AUTH] = changed_xml(generated[AUTH], mutate)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                fragments.expand_common_authentication_fragments(global_xml, generated, entra_enabled=True)

    def test_fragments_cannot_include_an_authentication_fragment(self):
        generated = fragments.render_common_authentication_fragments(entra_enabled=True)
        for identifier in (AUTH, ERROR):
            value = '<fragment><include-fragment id="' + identifier + '" fragment-id="' + identifier + '" /></fragment>'
            with self.subTest(fragment=identifier), self.assertRaises(ValueError):
                fragments.expand_common_authentication_fragments(value, generated, entra_enabled=True)

    def test_graph_requires_both_registered_sources_and_rejects_a_changed_body(self):
        for identifier in (AUTH, ERROR):
            for mode in ("missing", "changed"):
                current, _ = authentication_graph()
                resource_id = APIM + "/policyFragments/" + identifier
                if mode == "missing":
                    del current.policies[resource_id]
                else:
                    current.policies[resource_id] = changed_xml(current.policies[resource_id], lambda root: root[0].set("id", "unreviewed-decision"))
                with self.subTest(fragment=identifier, mode=mode), self.assertRaises(ValueError):
                    publication._audit_fragment_scope(current.policies, APIM)

    def test_global_step4_and_on_error_references_require_their_exact_scope_and_order(self):
        for identifier in (AUTH, ERROR):
            for mode in ("missing", "duplicate", "forged", "wrong-id", "extra-attribute",
                         "child", "text", "other-section", "nested", "late"):
                current, _ = authentication_graph()
                def mutate(root):
                    section = root.find("inbound" if identifier == AUTH else "on-error")
                    include = section.find("include-fragment[@id='" + identifier + "']")
                    if mode == "missing": section.remove(include)
                    elif mode == "duplicate": section.append(copy.deepcopy(include))
                    elif mode == "forged": include.set("fragment-id", "unreviewed-authentication")
                    elif mode == "wrong-id": include.set("id", "copied-authentication")
                    elif mode == "extra-attribute": include.set("extra", "unexpected")
                    elif mode == "child": ET.SubElement(include, "set-variable", {"name": "hidden", "value": "true"})
                    elif mode == "text": include.text = "unexpected text"
                    elif mode == "other-section":
                        section.remove(include); root.find("outbound").append(include)
                    elif mode == "nested":
                        section.remove(include)
                        branch = ET.SubElement(section, "choose")
                        ET.SubElement(branch, "when", {"condition": "@(true)"}).append(include)
                    elif identifier == AUTH:
                        section.remove(include); section.append(include)
                    else:
                        section.remove(include); section.insert(0, include)
                current.policies[GLOBAL] = changed_xml(current.policies[GLOBAL], mutate)
                with self.subTest(fragment=identifier, mode=mode), self.assertRaises(ValueError):
                    publication._audit_fragment_scope(current.policies, APIM)

    def test_api_other_fragment_and_product_cannot_call_or_write_owned_authentication(self):
        for identifier in (AUTH, ERROR):
            for scope in ("api", "product", "fragment"):
                for writer in (False, True):
                    current, _ = authentication_graph()
                    if scope == "fragment":
                        resource_id = APIM + "/policyFragments/unrelated"
                        root = ET.Element("fragment"); parent = root
                    else:
                        resource_id = (APIM + "/apis/model-a/policies/policy" if scope == "api"
                                       else APIM + "/products/unrelated/policies/policy")
                        root = ET.fromstring(current.policies[resource_id]) if scope == "api" else ET.fromstring(
                            "<policies><inbound/><backend/><outbound/><on-error/></policies>")
                        parent = root.find("inbound")
                    attrs = ({"name": "gatewayCommonAuthenticationValidated", "value": "@(true)"}
                             if writer else {"id": identifier, "fragment-id": identifier})
                    ET.SubElement(parent, "set-variable" if writer else "include-fragment", attrs)
                    current.policies[resource_id] = ET.tostring(root, encoding="unicode")
                    with self.subTest(fragment=identifier, scope=scope, writer=writer), self.assertRaises(ValueError):
                        publication._audit_fragment_scope(current.policies, APIM)


class CommonAuthenticationCompiledSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bicep_helpers import compile_bicep
        cls.template = compile_bicep("apps/gateway/apim/api-publication.bicep")

    def test_real_bicep_assignments_manifest_and_python_sources_are_identical(self):
        from test_api_publication_size import SizeExpression, expand
        for enabled in (False, True):
            parameters = {"apiDefinitions": [], "names": {"apim": "apim-test-fixture"},
                          "publishApis": True, "entraAuthentication": {"enabled": enabled}}
            expanded = expand(self.template, parameters)
            reader = SizeExpression("[true()]", self.template["variables"], {}, self.template.get("functions", []))
            source = reader.resolve(self.template["outputs"]["entraPublicationSource"]["value"], expanded["parameters"])
            published = {entry["suffix"]: entry["value"] for entry in source["policies"]}
            actual = {resource["name"].rsplit("/", 1)[-1]: resource["properties"]["value"]
                      for resource in expanded["resources"] if resource["type"].endswith("/policyFragments")}
            expected = fragments.render_common_authentication_fragments(entra_enabled=enabled)
            for identifier, value in expected.items():
                with self.subTest(entra=enabled, fragment=identifier):
                    self.assertIn(identifier, actual)
                    self.assertEqual(actual[identifier].replace("\r\n", "\n"), value)
                    self.assertEqual(published["/policyFragments/" + identifier].replace("\r\n", "\n"), value)
            if not enabled:
                self.assertNotIn(ERROR, actual)
                self.assertNotIn("/policyFragments/" + ERROR, published)


if __name__ == "__main__":
    unittest.main()
