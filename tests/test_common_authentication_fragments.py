"""STEP4の部品化で認証本体・実行順・専用部品の所有境界を変えないことを確認する。"""
import base64
import copy
import json
from pathlib import Path
import re
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from authentication_source_fixture import (
    PREVIOUS_EXPLANATION, annotated_authentication_source, previous_authentication_fixture,
)
from deployment.helpers.apim_audit import expand_audit_fragments
from deployment.helpers.apim_ingress import (
    expand_trusted_ingress_fragment,
)
from deployment.helpers.apim_browser_exception import expand_browser_exception_fragment
from deployment.helpers import apim_common_authentication as fragments
from deployment.helpers import credential_login
from deployment.helpers.apim_authentication import (
    _render_inline_common_authentication_service_policy,
    read_managed_entra_authentication, render_service_policy,
    transition_common_authentication_policy, validate_common_authentication_policy,
)
from test_audit_fragment_publication import audit_graph
from test_credential_login import settings as login_settings
from test_entra_gateway_authentication import settings as entra_settings
from test_entra_publication import APIM, GLOBAL, source_fixture
import entra_publication as publication


POLICIES = Path(__file__).resolve().parents[1] / "apps/apim/policies"
AUTH = "core-common-authentication"
ERROR = "core-entra-authentication-error"
TOKENS = re.compile(
    r'@"(?:[^"]|"")*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
    r'/\*[\s\S]*?\*/|//[^\n]*|[A-Za-z_][A-Za-z_0-9]*|\d+(?:\.\d+)?|'
    r'=>|==|!=|<=|>=|&&|\|\||\?\?|\?\.|[^\s]')

def code_value(value):
    if value is None or not value.strip():
        return ""
    if not value.lstrip().startswith(("@{", "@(")):
        return value
    return [token for token in TOKENS.findall(value) if not token.startswith(("/*", "//"))]


def code_shape(node):
    return [node.tag, sorted((key, code_value(value)) for key, value in node.attrib.items()),
            code_value(node.text), [code_shape(child) for child in node]]


def expand_all(xml, *, login=None, entra_enabled=True):
    generated = fragments.render_common_authentication_fragments(entra_enabled=entra_enabled)
    xml = fragments.expand_common_authentication_fragments(xml, generated, entra_enabled=entra_enabled)
    xml = expand_trusted_ingress_fragment(xml)
    xml = expand_browser_exception_fragment(xml, login=login)
    return expand_audit_fragments(xml)


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


def operation_error_graph(*, entra_enabled=True):
    """API名に依存しない3種別とCM6操作を持つ、独立した合成生成元。"""
    current, _ = authentication_graph(entra_enabled=entra_enabled)
    api_id = APIM + "/apis/synthetic-analysis"
    constraints = {"submit": {"kind": "documentAnalysis"}, "poll": {"kind": "documentAnalysisStatus"},
                   "redact": {"kind": "pii"}, "passthrough": {"kind": "bodyModel"}}
    root = ET.fromstring(current.policies[APIM + "/apis/model-a/policies/policy"])
    seed = root.find("inbound/set-variable[@name='gatewayApiSettingsSource']")
    seed.set("value", base64.b64encode(("http\n{}\n" + json.dumps(
        {"operationConstraints": constraints}, separators=(",", ":"))).encode()).decode())
    current.policies[api_id + "/policies/policy"] = ET.tostring(root, encoding="unicode")
    current.api_properties[api_id] = {"type": "http", "path": "synthetic-analysis", "subscriptionRequired": True}
    current.operations[api_id] = tuple(constraints)
    current.products[api_id] = ("model-users",)
    cm_api = APIM + "/apis/" + credential_login.API_ID
    current.policies[cm_api + "/policies/policy"] = publication.OPEN_PRODUCT_POLICY
    current.api_properties[cm_api] = {"type": "http", "path": credential_login.API_PATH, "subscriptionRequired": True}
    current.operations[cm_api] = tuple(credential_login.OPERATIONS)
    current.products[cm_api] = ("model-users",)
    sites = []
    for owner, names in ((api_id, constraints), (cm_api, credential_login.OPERATIONS)):
        for name in names:
            eligible = owner == cm_api or name != "passthrough"
            include = ('<include-fragment id="' + ERROR + '" fragment-id="' + ERROR + '" />'
                       if entra_enabled and eligible else "")
            resource_id = owner + "/operations/" + name + "/policies/policy"
            current.policies[resource_id] = ('<policies><inbound><base /></inbound><backend><base /></backend>'
                '<outbound><base /></outbound><on-error>' + include +
                '<set-variable name="syntheticLocalError" value="@(502)" />'
                '<return-response><set-status code="502" reason="Synthetic downstream error" /></return-response>'
                '</on-error></policies>')
            if eligible:
                sites.append(resource_id)
    return current, sites


class CommonAuthenticationFragmentPreservationTests(unittest.TestCase):

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


    def test_before_inline_current_is_readable_only_through_verified_transition(self):
        for cm in (False, True):
            login = login_settings() if cm else None
            with self.subTest(cm=cm):
                previous = _render_inline_common_authentication_service_policy(login, entra=entra_settings())
                self.assertEqual(read_managed_entra_authentication(previous, login,
                                 expected_entra=entra_settings()), entra_settings())
                candidate, changed = transition_common_authentication_policy(
                    previous, login, previous_entra=entra_settings(), entra=entra_settings())
                self.assertTrue(changed)
                validate_common_authentication_policy(candidate, login, entra=entra_settings())
                self.assertEqual(candidate, transition_common_authentication_policy(
                    candidate, login, previous_entra=entra_settings(), entra=entra_settings())[0])
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
                     "alter-claim", "nested-include", "duplicate-body", "alter-literal",
                     "alter-error-header", "extra-decision-attribute", "reordered-decisions", "directive"):
            generated = copy.deepcopy(original)
            if mode.startswith("missing"):
                generated.pop(AUTH if mode == "missing-common" else ERROR)
            elif mode == "additional":
                generated["unrepresented"] = '<fragment><set-variable name="extra" value="true" /></fragment>'
            else:
                identifier = ERROR if mode == "alter-error-header" else AUTH

                def mutate(root):
                    if mode == "alter-expression":
                        root.find(".//set-variable[@name='gatewayCommonAuthenticationValidated']").set("value", "@(false)")
                    elif mode == "alter-claim":
                        root.find(".//required-claims/claim/value").text = "unreviewed-scope"
                    elif mode == "nested-include":
                        ET.SubElement(root, "include-fragment", {"fragment-id": "core-gateway-telemetry"})
                    elif mode == "alter-literal":
                        node = root.find(".//set-variable[@name='gatewayAuthenticationFailure']")
                        value = node.get("value")
                        self.assertIn("Credentials must use the configured headers", value)
                        node.set("value", value.replace("Credentials must use the configured headers", "Unreviewed failure", 1))
                    elif mode == "alter-error-header":
                        root.find(".//set-header[@name='WWW-Authenticate']/value").text = "Bearer changed"
                    elif mode == "extra-decision-attribute":
                        root[0].set("unreviewed", "true")
                    elif mode == "reordered-decisions":
                        root[0][0], root[0][1] = root[0][1], root[0][0]
                    elif mode == "directive":
                        node = root.find(".//set-variable[@name='gatewayAuthenticationFailure']")
                        value = node.get("value")
                        self.assertIn("@{", value)
                        node.set("value", value.replace("@{", "@{\n#if FALSE\n", 1))
                    else:
                        root.append(copy.deepcopy(root[0]))
                generated[identifier] = changed_xml(generated[identifier], mutate)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                fragments.expand_common_authentication_fragments(global_xml, generated, entra_enabled=True)

    def test_current_dictionary_is_used_without_rewriting_it_in_both_states(self):
        for enabled in (False, True):
            settings = entra_settings() if enabled else None
            global_xml = render_service_policy(entra=settings)
            generated = fragments.render_common_authentication_fragments(entra_enabled=enabled)
            before = copy.deepcopy(generated)
            with self.subTest(entra=enabled):
                self.assertEqual(read_managed_entra_authentication(
                    global_xml, expected_entra=settings, fragments=generated), settings)
                expanded = ET.fromstring(fragments.expand_common_authentication_fragments(
                    global_xml, generated, entra_enabled=enabled))
                self.assertIsNotNone(expanded.find("./inbound/choose[@id='" + AUTH + "']"))
                self.assertEqual(expanded.find("./on-error/choose[@id='" + ERROR + "']") is not None, enabled)
                self.assertEqual(generated, before)

    def test_current_expansion_requires_a_dictionary_for_the_explicit_state(self):
        for enabled in (False, True):
            settings = entra_settings() if enabled else None
            global_xml = render_service_policy(entra=settings)
            wrong_state = fragments.render_common_authentication_fragments(entra_enabled=not enabled)
            for label, generated in (("missing", None), ("wrong-type", []), ("wrong-state", wrong_state)):
                with self.subTest(entra=enabled, dictionary=label), self.assertRaises(ValueError):
                    fragments.expand_common_authentication_fragments(global_xml, generated, entra_enabled=enabled)

    def test_current_reader_rejects_unexpected_text_around_the_complete_fragment(self):
        for enabled in (False, True):
            settings = entra_settings() if enabled else None
            global_xml = render_service_policy(entra=settings)
            original = fragments.render_common_authentication_fragments(entra_enabled=enabled)
            for identifier in original:
                for location in ("before", "after"):
                    generated = copy.deepcopy(original)
                    root = ET.fromstring(generated[identifier])
                    if location == "before":
                        root.text = "unreviewed wrapper text"
                    else:
                        root[0].tail = "unreviewed wrapper tail"
                    generated[identifier] = ET.tostring(root, encoding="unicode")
                    with self.subTest(entra=enabled, fragment=identifier, location=location), self.assertRaises(ValueError):
                        read_managed_entra_authentication(global_xml, expected_entra=settings, fragments=generated)

    def test_current_reader_rejects_modified_owned_outbound_correlation_header(self):
        for enabled in (False, True):
            settings = entra_settings() if enabled else None
            global_xml = render_service_policy(entra=settings)
            generated = fragments.render_common_authentication_fragments(entra_enabled=enabled)
            for mode in ("header-name", "header-expression"):
                root = ET.fromstring(global_xml)
                header = root.find("outbound/set-header")
                self.assertIsNotNone(header)
                if mode == "header-name":
                    header.set("name", "unreviewed-correlation-header")
                else:
                    header.find("value").text = '@("unreviewed")'
                changed = ET.tostring(root, encoding="unicode")
                with self.subTest(entra=enabled, mode=mode), self.assertRaises(ValueError):
                    read_managed_entra_authentication(changed, expected_entra=settings, fragments=generated)

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


class OperationAuthenticationErrorBoundaryTests(unittest.TestCase):
    def test_generated_source_accepts_all_nine_sites_and_retains_global_validation_in_both_states(self):
        for enabled in (False, True):
            current, sites = operation_error_graph(entra_enabled=enabled)
            with self.subTest(entra=enabled):
                self.assertEqual(len(sites), 9)
                publication._audit_fragment_scope(current.policies, APIM, api_properties=current.api_properties)
                names = [api_id.rsplit("/", 1)[-1] for api_id in current.operations]
                actual = publication.source_manifest(source_fixture(current), APIM, names, current.policies[GLOBAL])
                self.assertEqual(actual, current)

    def test_previous_source_without_operation_handoffs_keeps_its_existing_scope(self):
        current, sites = operation_error_graph()
        for target in sites:
            current.policies[target] = changed_xml(current.policies[target], lambda root:
                root.find("on-error").remove(root.find("on-error/include-fragment")))
        publication._audit_fragment_scope(current.policies, APIM, api_properties=current.api_properties)

    def test_operation_handler_requires_exact_attributes_direct_first_position_and_single_call(self):
        for mode in ("duplicate", "late", "nested", "inbound", "outbound", "wrong-id", "missing-id",
                     "forged-fragment", "extra-attribute", "child", "text", "base"):
            for index in (0, 3):
                current, sites = operation_error_graph()
                target = sites[index]
                def mutate(root):
                    section = root.find("on-error")
                    include = section[0]
                    if mode == "duplicate": section.append(copy.deepcopy(include))
                    elif mode == "late": section.remove(include); section.insert(1, include)
                    elif mode in ("inbound", "outbound"):
                        section.remove(include); root.find(mode).append(include)
                    elif mode == "nested":
                        section.remove(include)
                        branch = ET.Element("choose")
                        ET.SubElement(branch, "when", {"condition": "@(true)"}).append(include)
                        section.insert(0, branch)
                    elif mode == "wrong-id": include.set("id", "copied-error")
                    elif mode == "missing-id": del include.attrib["id"]
                    elif mode == "forged-fragment": include.set("fragment-id", "unreviewed-error")
                    elif mode == "extra-attribute": include.set("extra", "unexpected")
                    elif mode == "child": ET.SubElement(include, "set-variable", {"name": "hidden", "value": "true"})
                    elif mode == "text": include.text = "unexpected text"
                    else: ET.SubElement(section, "base")
                current.policies[target] = changed_xml(current.policies[target], mutate)
                with self.subTest(mode=mode, operation=target.rsplit("/", 3)[0]), self.assertRaises(ValueError):
                    publication._audit_fragment_scope(current.policies, APIM, api_properties=current.api_properties)

    def test_handler_is_not_authorized_by_http_type_api_or_model_name_alone(self):
        for mode in ("other-kind", "unknown-operation", "api", "product", "browser", "fragment", "mcp", "websocket"):
            current, sites = operation_error_graph()
            original = ET.fromstring(current.policies[sites[0]]).find("on-error")[0]
            api_id = sites[0].split("/operations/")[0]
            if mode == "other-kind":
                target = api_id + "/operations/passthrough/policies/policy"
                root = ET.fromstring(current.policies[target]); parent = root.find("on-error")
            elif mode == "unknown-operation":
                target = api_id + "/operations/unrepresented/policies/policy"
                root = ET.fromstring(publication.OPEN_PRODUCT_POLICY); parent = root.find("on-error")
            elif mode == "api":
                target = api_id + "/policies/policy"
                root = ET.fromstring(current.policies[target]); parent = root.find("on-error")
            elif mode in ("mcp", "websocket"):
                target = sites[0]
                current.api_properties[api_id]["type"] = mode
                def change_type(root):
                    seed = root.find("inbound/set-variable[@name='gatewayApiSettingsSource']")
                    lines = base64.b64decode(seed.get("value")).decode().split("\n")
                    lines[0] = mode
                    seed.set("value", base64.b64encode("\n".join(lines).encode()).decode())
                current.policies[api_id + "/policies/policy"] = changed_xml(
                    current.policies[api_id + "/policies/policy"], change_type)
                with self.subTest(scope=mode), self.assertRaises(ValueError):
                    publication._audit_fragment_scope(current.policies, APIM, api_properties=current.api_properties)
                continue
            elif mode == "fragment":
                target = APIM + "/policyFragments/unrelated"
                root = ET.Element("fragment"); parent = root
            else:
                target = (APIM + "/products/unrelated/policies/policy" if mode == "product" else
                          APIM + "/apis/credential-manager-browser/operations/launch/policies/policy")
                root = ET.fromstring(publication.OPEN_PRODUCT_POLICY); parent = root.find("on-error")
            parent.insert(0, copy.deepcopy(original))
            current.policies[target] = ET.tostring(root, encoding="unicode")
            with self.subTest(scope=mode), self.assertRaises(ValueError):
                publication._audit_fragment_scope(current.policies, APIM, api_properties=current.api_properties)

    def test_operation_handler_requires_verified_kind_seed_and_registered_cm_api_source(self):
        for mode in ("changed-kind", "bad-seed", "bad-constraints", "bad-entry", "bad-kind", "bad-name",
                     "orphan-cm", "wrong-cm-type"):
            current, sites = operation_error_graph()
            api_id = sites[0].split("/operations/")[0]
            if mode not in ("orphan-cm", "wrong-cm-type"):
                def mutate(root):
                    seed = root.find("inbound/set-variable[@name='gatewayApiSettingsSource']")
                    if mode == "bad-seed": seed.set("value", "@(context.Request.Headers.GetValueOrDefault(\"settings\", \"\"))")
                    else:
                        lines = base64.b64decode(seed.get("value")).decode().split("\n")
                        control = json.loads(lines[2])
                        if mode == "changed-kind": control["operationConstraints"]["submit"]["kind"] = "bodyModel"
                        elif mode == "bad-constraints": control["operationConstraints"] = []
                        elif mode == "bad-entry": control["operationConstraints"]["submit"] = 1
                        elif mode == "bad-kind": control["operationConstraints"]["submit"]["kind"] = {"untrusted": True}
                        else: control["operationConstraints"]["../submit"] = control["operationConstraints"].pop("submit")
                        lines[2] = json.dumps(control)
                        seed.set("value", base64.b64encode("\n".join(lines).encode()).decode())
                current.policies[api_id + "/policies/policy"] = changed_xml(current.policies[api_id + "/policies/policy"], mutate)
            elif mode == "orphan-cm":
                del current.policies[APIM + "/apis/" + credential_login.API_ID + "/policies/policy"]
            else: current.api_properties[APIM + "/apis/" + credential_login.API_ID]["type"] = "mcp"
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                publication._audit_fragment_scope(current.policies, APIM, api_properties=current.api_properties)

    def test_operation_calls_do_not_replace_missing_disabled_or_changed_global_authentication(self):
        for mode in ("missing-fragment", "missing-validator", "missing-global-error", "duplicate-global-error", "disabled"):
            current, sites = operation_error_graph(entra_enabled=mode != "disabled")
            if mode == "disabled":
                current.policies[APIM + "/policyFragments/" + ERROR] = fragments.render_entra_authentication_error_fragment()
                current.policies[sites[0]] = changed_xml(current.policies[sites[0]], lambda root:
                    root.find("on-error").insert(0, ET.Element("include-fragment", {"id": ERROR, "fragment-id": ERROR})))
            elif mode == "missing-fragment": del current.policies[APIM + "/policyFragments/" + ERROR]
            elif mode == "missing-validator":
                current.policies[APIM + "/policyFragments/" + AUTH] = fragments.render_common_authentication_fragment(entra_enabled=False)
            else:
                def mutate(root):
                    errors = root.find("on-error")
                    include = errors.find("include-fragment[@id='" + ERROR + "']")
                    if mode == "missing-global-error": errors.remove(include)
                    else: errors.append(copy.deepcopy(include))
                current.policies[GLOBAL] = changed_xml(current.policies[GLOBAL], mutate)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                publication._audit_fragment_scope(current.policies, APIM, api_properties=current.api_properties)

    def test_cm_site_requires_its_complete_attribute_free_base_only_parent(self):
        cm_policy = APIM + "/apis/" + credential_login.API_ID + "/policies/policy"
        for mode in ("empty", "missing-section", "extra-section", "root-attribute", "section-attribute",
                     "base-attribute", "base-child", "extra-policy", "wrong-type"):
            current, _ = operation_error_graph()
            def mutate(root):
                if mode == "empty": root.clear()
                elif mode == "missing-section": root.remove(root.find("on-error"))
                elif mode == "extra-section": ET.SubElement(root, "inbound")
                elif mode == "root-attribute": root.set("extra", "untrusted")
                elif mode == "section-attribute": root.find("inbound").set("extra", "untrusted")
                elif mode == "base-attribute": root.find("inbound/base").set("extra", "untrusted")
                elif mode == "base-child": ET.SubElement(root.find("inbound/base"), "set-variable", {"name": "hidden", "value": "true"})
                elif mode == "extra-policy": ET.SubElement(root.find("inbound"), "set-variable", {"name": "hidden", "value": "true"})
            if mode == "wrong-type": current.api_properties[cm_policy.removesuffix("/policies/policy")]["type"] = "mcp"
            else: current.policies[cm_policy] = changed_xml(current.policies[cm_policy], mutate)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                publication._audit_fragment_scope(current.policies, APIM, api_properties=current.api_properties)


class OperationAuthenticationErrorRendererBoundaryTests(unittest.TestCase):
    @staticmethod
    def template():
        return ('<policies><inbound><base /></inbound><backend><base /></backend><outbound><base /></outbound>'
                '<on-error>' + fragments.OPERATION_ENTRA_AUTHENTICATION_ERROR_MARKER +
                '\n<return-response><set-status code="502" reason="Synthetic downstream error" /></return-response>'
                '</on-error></policies>')

    def test_renderer_inserts_one_fixed_first_call_only_when_enabled_and_retains_other_errors(self):
        for enabled in (False, True):
            value = fragments.render_operation_entra_authentication_error(self.template(), entra_enabled=enabled)
            with self.subTest(entra=enabled):
                self.assertNotIn(fragments.OPERATION_ENTRA_AUTHENTICATION_ERROR_MARKER, value)
                root = ET.fromstring(value)
                calls = root.findall("on-error/include-fragment")
                self.assertEqual(len(calls), int(enabled))
                if enabled:
                    self.assertIs(root.find("on-error")[0], calls[0])
                    self.assertEqual(calls[0].attrib, {"id": ERROR, "fragment-id": ERROR})
                self.assertEqual(root.find("on-error/return-response/set-status").get("code"), "502")

    def test_renderer_rejects_non_boolean_state_and_misplaced_duplicate_or_nested_markers(self):
        for state in (None, 0, 1, "false", {}, []):
            with self.subTest(state=state), self.assertRaises(ValueError):
                fragments.render_operation_entra_authentication_error(self.template(), entra_enabled=state)
        marker = fragments.OPERATION_ENTRA_AUTHENTICATION_ERROR_MARKER
        cases = (self.template().replace(marker, marker + '\n' + marker),
                 self.template().replace(marker, '<set-variable name="before" value="true" />' + marker),
                 self.template().replace(marker, '<choose><when condition="@(true)">' + marker + '</when></choose>'),
                 '<fragment>' + marker + '</fragment>',
                 self.template().replace('<on-error>' + marker, '<on-error>').replace('<inbound>', '<inbound>' + marker))
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                fragments.render_operation_entra_authentication_error(value, entra_enabled=True)

    def test_operation_expander_uses_only_the_canonical_error_source_and_global_expander_stays_global(self):
        value = fragments.render_operation_entra_authentication_error(self.template(), entra_enabled=True)
        expanded = ET.fromstring(fragments.expand_operation_entra_authentication_error(value))
        handler = expanded.find("on-error")[0]
        expected = ET.fromstring(fragments.render_entra_authentication_error_fragment())[0]
        self.assertEqual(code_shape(handler), code_shape(expected))
        self.assertEqual(expanded.find("on-error/return-response/set-status").get("code"), "502")
        with self.assertRaises(ValueError):
            fragments.expand_common_authentication_fragments(value,
                fragments.render_common_authentication_fragments(entra_enabled=True), entra_enabled=True)

    def test_operation_expander_rejects_other_authentication_calls_and_changed_scope_or_attributes(self):
        value = fragments.render_operation_entra_authentication_error(self.template(), entra_enabled=True)
        for mode in ("duplicate", "late", "inbound", "nested", "wrong-id", "missing-id", "forged-fragment", "extra-attribute",
                     "child", "text", "common-auth", "other-auth-call", "copied-decision", "fragment"):
            def mutate(root):
                errors = root.find("on-error")
                include = errors[0]
                if mode == "duplicate": errors.append(copy.deepcopy(include))
                elif mode == "late": errors.remove(include); errors.append(include)
                elif mode == "inbound": errors.remove(include); root.find("inbound").append(include)
                elif mode == "nested":
                    errors.remove(include)
                    branch = ET.SubElement(errors, "choose")
                    ET.SubElement(branch, "when", {"condition": "@(true)"}).append(include)
                elif mode == "wrong-id": include.set("id", "copied-error")
                elif mode == "missing-id": del include.attrib["id"]
                elif mode == "forged-fragment": include.set("fragment-id", "unreviewed-error")
                elif mode == "extra-attribute": include.set("extra", "unexpected")
                elif mode == "child": ET.SubElement(include, "set-variable", {"name": "hidden", "value": "true"})
                elif mode == "text": include.text = "unexpected text"
                elif mode == "common-auth": include.attrib.update({"id": AUTH, "fragment-id": AUTH})
                elif mode == "other-auth-call": ET.SubElement(root.find("inbound"), "include-fragment", {"id": AUTH, "fragment-id": AUTH})
                elif mode == "copied-decision": ET.SubElement(errors, "choose", {"id": ERROR})
            changed = ('<fragment><include-fragment id="' + ERROR + '" fragment-id="' + ERROR + '" /></fragment>'
                       if mode == "fragment" else changed_xml(value, mutate))
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                fragments.expand_operation_entra_authentication_error(changed)


class CommonAuthenticationCompiledSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bicep_helpers import compile_bicep
        cls.template = compile_bicep("apps/apim/api-publication.bicep")

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


class CommonAuthenticationExpressionTokenTests(unittest.TestCase):
    def tokens(self, expression):
        return fragments._previous_authentication_expression_tokens(expression)

    def test_comments_are_skipped_but_raw_regular_character_and_verbatim_literals_are_preserved(self):
        source = r'''@{ var s = "// /* #if $\""; var c = '\''; var v = @"/* ""quoted"" #if"; return s; }'''
        previous = source.replace("var s", '/* #if "fake" */ var s', 1).replace("return s", "// prior explanation\n return s", 1)
        self.assertEqual(self.tokens(source), self.tokens(previous))
        for before, after in (("quoted", "changed"), ("#if", "#else"), ("\\'", "\\\"")):
            with self.subTest(literal=before):
                self.assertNotEqual(self.tokens(source), self.tokens(source.replace(before, after, 1)))
        self.assertEqual(self.tokens('@{ return @"line\nnext"; }')[-3], '@"line\nnext"')

    def test_adjacent_operators_identifiers_and_numbers_keep_token_boundaries(self):
        for first, second in (("a + +b", "a++b"), ("a ? .b", "a?.b"), ("a & &b", "a&&b"),
                              ("name value", "namevalue"), ("1 e2", "1e2"), ("1 .5", "1.5")):
            with self.subTest(expression=first):
                self.assertNotEqual(self.tokens("@{ return " + first + "; }"),
                                    self.tokens("@{ return " + second + "; }"))
        self.assertEqual(self.tokens("@{ return a /* separated */ + +b; }"),
                         self.tokens("@{return a+ +b;}"))

    def test_directives_interpolation_raw_or_incomplete_literals_and_unknown_tokens_stop(self):
        sources = ('@{\n#if FALSE\n return true;\n#endif\n}', '@{ return $"{value}"; }',
                   '@{ return $@"{value}"; }', '@{ return @$"{value}"; }', '@{ return """raw"""; }',
                   '@{ /* unclosed }', '@{ return "unclosed; }', "@{ return ''; }",
                   '@{ return "line\nnext"; }', '@{ return name`other; }', '@{ return 名前; }')
        for source in sources:
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.tokens(source)
        self.assertNotEqual(self.tokens("@{ return true; }"), self.tokens("@{ // hidden return true; }"))


class CommonAuthenticationPreviousSourceBoundaryTests(unittest.TestCase):
    def test_previous_dictionary_is_opt_in_and_expands_actual_old_raw_without_changing_input(self):
        for enabled in (False, True):
            _, _, _, settings, current, previous, global_xml = previous_authentication_fixture(enabled)
            before = copy.deepcopy(previous)
            with self.subTest(enabled=enabled):
                with self.assertRaises(ValueError):
                    fragments.expand_common_authentication_fragments(global_xml, previous, entra_enabled=enabled)
                with self.assertRaises(ValueError):
                    read_managed_entra_authentication(global_xml, expected_entra=settings, fragments=previous)
                with self.assertRaises(ValueError):
                    validate_common_authentication_policy(global_xml, entra=settings, fragments=previous)
                expanded = fragments.expand_previous_common_authentication_fragments(global_xml, previous, entra_enabled=enabled)
                self.assertIn(PREVIOUS_EXPLANATION.strip(), expanded)
                self.assertEqual(read_managed_entra_authentication(global_xml, expected_entra=settings,
                    fragments=previous, allow_previous_explanations=True), settings)
                self.assertEqual(previous, before)
                self.assertNotEqual(previous[AUTH], current[AUTH])

    def test_fixed_global_owned_blocks_are_verified_and_transition_produces_current_strict_policy(self):
        _, _, _, settings, _, previous, global_xml = previous_authentication_fixture()
        transitioned, changed = transition_common_authentication_policy(global_xml, previous_entra=settings, entra=settings,
            previous_fragments=previous, allow_previous_explanations=True)
        self.assertTrue(changed)
        self.assertNotIn(PREVIOUS_EXPLANATION.strip(), transitioned)
        validate_common_authentication_policy(transitioned, entra=settings)
        for mode in ("header-name", "header-expression", "order", "owned-id"):
            root = ET.fromstring(global_xml)
            if mode == "header-name": root.find("outbound/set-header").set("name", "unreviewed-header")
            elif mode == "header-expression": root.find("outbound/set-header/value").text = '@("unreviewed")'
            elif mode == "owned-id": root.find("inbound/include-fragment").set("id", "unreviewed-id")
            else:
                inbound = root.find("inbound")
                inbound[0], inbound[1] = inbound[1], inbound[0]
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                read_managed_entra_authentication(ET.tostring(root, encoding="unicode"), expected_entra=settings,
                    fragments=previous, allow_previous_explanations=True)

    def test_dictionary_completeness_and_explicit_state_are_required(self):
        _, _, _, settings, _, previous, global_xml = previous_authentication_fixture()
        for mode in ("missing-common", "missing-error", "additional", "wrong-state", "no-dictionary"):
            changed = copy.deepcopy(previous)
            if mode.startswith("missing"):
                changed.pop(AUTH if mode == "missing-common" else ERROR)
            elif mode == "additional": changed["unreviewed"] = changed[AUTH]
            elif mode == "no-dictionary": changed = None
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                read_managed_entra_authentication(global_xml, expected_entra=None if mode == "wrong-state" else settings,
                    fragments=changed, allow_previous_explanations=True)

    def test_complete_fragment_wrapper_cannot_drop_unexpected_text_before_or_after_its_decision(self):
        _, _, _, settings, _, previous, global_xml = previous_authentication_fixture()
        for identifier in (AUTH, ERROR):
            for location in ("before", "after"):
                changed = copy.deepcopy(previous)
                root = ET.fromstring(changed[identifier])
                if location == "before": root.text = "unreviewed wrapper text"
                else: root[0].tail = "unreviewed wrapper tail"
                changed[identifier] = ET.tostring(root, encoding="unicode")
                with self.subTest(fragment=identifier, location=location), self.assertRaises(ValueError):
                    read_managed_entra_authentication(global_xml, expected_entra=settings,
                        fragments=changed, allow_previous_explanations=True)

    def test_previous_reader_does_not_relax_actual_raw_readback_or_new_checkpoint_validation(self):
        from test_publish_apis import publish

        _, _, plan, settings, _, previous, global_xml = previous_authentication_fixture()
        with self.assertRaises(ValueError):
            publish.entra_publication._same_xml(annotated_authentication_source(previous[AUTH]), previous[AUTH], fragment=True)
        source = {"apis": [{"name": rid.rsplit("/", 1)[-1]} for rid in plan["publishedApiIds"]],
                  "policies": [{"suffix": "/policyFragments/" + name, "value": value} for name, value in previous.items()]}
        with self.assertRaises(ValueError):
            publish.source_checkpoint(plan, source, global_xml)

    def test_comparison_is_limited_to_known_owned_roots_and_requires_actual_transition_dictionary(self):
        _, _, _, settings, _, previous, global_xml = previous_authentication_fixture()
        unowned = ET.fromstring('<set-variable id="custom" name="custom" value="@(true)" />')
        with self.assertRaises(ValueError):
            fragments._previous_authentication_source_matches(unowned, unowned)
        with self.assertRaises(ValueError):
            transition_common_authentication_policy(global_xml, previous_entra=settings, entra=settings,
                allow_previous_explanations=True)

    def test_code_literals_claims_nonexpression_values_and_xml_order_cannot_change(self):
        _, _, _, settings, _, previous, global_xml = previous_authentication_fixture()
        for mode in ("success-code", "literal", "claim", "nonexpression", "extra-attribute", "order", "nested", "directive"):
            changed = copy.deepcopy(previous)
            root = ET.fromstring(changed[AUTH if mode != "nonexpression" else ERROR])
            if mode == "success-code":
                root.find(".//set-variable[@name='gatewayCommonAuthenticationValidated']").set("value", "@(false)")
            elif mode == "literal":
                node = root.find(".//set-variable[@name='gatewayAuthenticationFailure']")
                node.set("value", node.get("value").replace("Credentials must use the configured headers", "Different failure", 1))
            elif mode == "claim": root.find(".//required-claims/claim/value").text = "unreviewed-scope"
            elif mode == "nonexpression": root.find(".//set-header[@name='WWW-Authenticate']/value").text = "Bearer changed"
            elif mode == "extra-attribute": root.find("choose").set("extra", "true")
            elif mode == "order": root[0][0], root[0][1] = root[0][1], root[0][0]
            elif mode == "nested": ET.SubElement(root[0], "include-fragment", {"fragment-id": "unreviewed"})
            else:
                node = root.find(".//set-variable[@name='gatewayAuthenticationFailure']")
                node.set("value", node.get("value").replace("@{", "@{\n#if FALSE\n", 1))
            changed[AUTH if mode != "nonexpression" else ERROR] = ET.tostring(root, encoding="unicode")
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                read_managed_entra_authentication(global_xml, expected_entra=settings,
                    fragments=changed, allow_previous_explanations=True)

if __name__ == "__main__":
    unittest.main()
