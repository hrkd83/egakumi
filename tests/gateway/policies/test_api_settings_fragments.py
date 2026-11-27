"""API設定の1組をbase前に展開し、旧APIと共存して生成元/実読取を照合する。"""
import base64
import copy
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_api_settings import (
    API_SETTINGS_ID, API_SETTINGS_VARIABLES, expand_api_settings_fragment,
    render_api_settings_fragment, validate_api_settings_scope,
)
from deployment.helpers.apim_audit import CORE_API_SETTINGS_MARKER, _with_source_line_comments
from tests.gateway.publication.test_entra_publication import APIM, GLOBAL, fixture, response, source_fixture
import entra_publication as publication


POLICIES = Path(__file__).resolve().parents[3] / "apps/api-gateway/policies"
FRAGMENT = APIM + "/policyFragments/" + API_SETTINGS_ID
SETTINGS = {"apiType": "http", "audit": '{"backendService":"synthetic","operationBackends":{}}',
            "control": '{"operationConstraints":{},"tokenLimitOperationIds":[]}'}


def settings_pair(settings=None):
    settings = SETTINGS if settings is None else settings
    encoded = base64.b64encode("\n".join(settings[name] for name in ("apiType", "audit", "control")).encode()).decode()
    return ('<set-variable name="gatewayApiSettingsSource" value="' + encoded + '" />'
            '<include-fragment id="core-api-settings" fragment-id="core-api-settings" />')


def policy(pair=None, *, guard="", after=""):
    return ('<policies><inbound>' + (settings_pair() if pair is None else pair) + guard + '<base />' + after
            + '</inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>')


def settings_graph():
    current, transport = fixture()
    # model-aは新しい設定入力、model-bは旧2変数の初期化として新旧共存を確認します。
    identifier = APIM + "/apis/model-b/policies/policy"
    root = ET.fromstring(current.policies[identifier])
    inbound = root.find("inbound")
    for node in list(inbound):
        if node.get("name") == "gatewayApiSettingsSource" or node.get("fragment-id") == API_SETTINGS_ID:
            inbound.remove(node)
    for index, (name, value) in enumerate((("gatewayAuditSettings", SETTINGS["audit"]),
                                          ("gatewayControlSettings", SETTINGS["control"]))):
        inbound.insert(index, ET.Element("set-variable", {"name": name, "value": value}))
    current.policies[identifier] = ET.tostring(root, encoding="unicode")
    current.policies[FRAGMENT] = render_api_settings_fragment()
    for rid in (identifier, FRAGMENT):
        transport.inventory[rid] = response(rid, {"format": "rawxml", "value": current.policies[rid]})
    return current, transport


class ApiSettingsFragmentTests(unittest.TestCase):
    def test_generated_leaf_decodes_once_and_retains_the_same_named_csharp_comments(self):
        rendered = render_api_settings_fragment()
        root = ET.fromstring(rendered)
        self.assertEqual(tuple(node.get("name") for node in root), API_SETTINGS_VARIABLES)
        self.assertTrue(all(node.tag == "set-variable" and set(node.attrib) == {"name", "value"} for node in root))
        self.assertEqual(rendered.count("Convert.FromBase64String"), 1)
        self.assertNotIn(CORE_API_SETTINGS_MARKER, rendered)
        source = (POLICIES / "fragments/settings/core-api-settings/core-api-settings.cs").read_text(encoding="utf-8")
        for line in _with_source_line_comments(source).splitlines():
            if line.lstrip().startswith("/*"):
                self.assertIn(line.strip(), root[1].get("value"))
        self.assertFalse(root.findall(".//include-fragment"))
        self.assertLessEqual(len(rendered.encode("utf-8")), 512 * 1024)

    def test_expansion_preserves_base_and_later_profile_or_model_updates(self):
        after = '<set-variable name="gatewayControlSettings" value="profile-update" /><set-variable name="gatewayAuditSettings" value="model-update" />'
        value = policy(after=after)
        publication._xml(value, inherited=True)
        expanded = ET.fromstring(expand_api_settings_fragment(value))
        nodes = list(expanded.find("inbound"))
        self.assertEqual([node.get("name") for node in nodes[:4]], ["gatewayApiSettingsSource", *API_SETTINGS_VARIABLES])
        self.assertEqual(nodes[4].tag, "base")
        self.assertEqual([node.get("value") for node in nodes[5:]], ["profile-update", "model-update"])
        validate_api_settings_scope({APIM + "/apis/model-a/policies/policy": value,
                                     FRAGMENT: render_api_settings_fragment()}, APIM)

    def test_previous_metadata_and_cm_base_only_are_unchanged(self):
        for value in (policy('<set-variable name="gatewayAuditSettings" value="previous" /><set-variable name="gatewayControlSettings" value="previous" />'),
                      publication.OPEN_PRODUCT_POLICY):
            with self.subTest(policy=value):
                self.assertEqual(expand_api_settings_fragment(value), value)
                publication._xml(value, inherited=True)
                validate_api_settings_scope({APIM + "/apis/credential-manager-login/policies/policy": value}, APIM)

    def test_handshake_and_mcp_fixed_metadata_guard_remain_supported(self):
        for suffix, settings, guard in (
            ("/apis/socket/operations/onHandshake/policies/policy", {**SETTINGS, "apiType": "websocket"}, ""),
            ("/apis/native-mcp/policies/policy", {**SETTINGS, "apiType": "mcp"}, publication.PRM_GUARD),
        ):
            with self.subTest(suffix=suffix):
                value = policy(settings_pair(settings), guard=guard)
                publication._xml(value, inherited=True)
                validate_api_settings_scope({APIM + suffix: value, FRAGMENT: render_api_settings_fragment()}, APIM)

    def test_handshake_type_and_one_initialization_per_api_are_required(self):
        api = APIM + "/apis/socket"
        handshake = api + "/operations/onHandshake/policies/policy"
        fragment = {FRAGMENT: render_api_settings_fragment()}
        with self.assertRaises(ValueError):
            validate_api_settings_scope({**fragment, handshake: policy()}, APIM)
        websocket = policy(settings_pair({**SETTINGS, "apiType": "websocket"}))
        with self.assertRaises(ValueError):
            validate_api_settings_scope({**fragment, handshake: websocket}, APIM,
                                        api_properties={api: {"type": "http"}})
        with self.assertRaises(ValueError):
            validate_api_settings_scope({**fragment, handshake: websocket, api + "/policies/policy": policy()}, APIM)
        validate_api_settings_scope({**fragment, handshake: websocket}, APIM,
                                    api_properties={api: {"type": "websocket"}})

    def test_partial_duplicate_nested_late_or_expression_seed_cannot_bypass_inheritance(self):
        mutations = (
            lambda root: root.find("inbound").remove(root.find("inbound/include-fragment")),
            lambda root: root.find("inbound").append(copy.deepcopy(root.find("inbound/include-fragment"))),
            lambda root: root.find("inbound/include-fragment").set("id", "different"),
            lambda root: setattr(root.find("inbound/include-fragment"), "text", "hidden"),
            lambda root: ET.SubElement(root.find("inbound/include-fragment"), "base"),
            lambda root: root.find("inbound/set-variable").set("value", "@(context.Request.Headers.GetValueOrDefault(\"settings\", \"\"))"),
            *(lambda root, value=value: root.find("inbound/set-variable").set("value", base64.b64encode(value).decode())
              for value in (b'http\n[]\n{}', b'http\n{}\n[]', b'http\n{}', b'http\n{}\n{}\n',
                            b'unknown\n{}\n{}', b'http\n{\n}\n{}')),
        )
        for mutate in mutations:
            root = ET.fromstring(policy())
            mutate(root)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                publication._xml(ET.tostring(root, encoding="unicode"), inherited=True)
        root = ET.fromstring(policy())
        inbound = root.find("inbound")
        node = inbound.find("include-fragment")
        inbound.remove(node)
        inbound.append(node)
        with self.assertRaises(ValueError):
            publication._xml(ET.tostring(root, encoding="unicode"), inherited=True)


class ApiSettingsPublicationTests(unittest.TestCase):
    def test_new_api_and_previous_api_share_complete_source_and_get_dependencies(self):
        current, transport = settings_graph()
        new = ET.fromstring(current.policies[APIM + "/apis/model-a/policies/policy"])
        self.assertIsNotNone(new.find("inbound/set-variable[@name='gatewayApiSettingsSource']"))
        self.assertIsNotNone(new.find("inbound/include-fragment[@fragment-id='core-api-settings']"))
        previous = ET.fromstring(current.policies[APIM + "/apis/model-b/policies/policy"])
        self.assertEqual([node.get("name") for node in previous.find("inbound") if node.tag == "set-variable"],
                         ["gatewayAuditSettings", "gatewayControlSettings"])
        self.assertIsNone(previous.find(".//set-variable[@name='gatewayApiSettingsSource']"))
        self.assertIsNone(previous.find(".//include-fragment[@fragment-id='core-api-settings']"))
        source = source_fixture(current)
        manifest = publication.source_manifest(source, APIM, ["model-a", "model-b"], current.policies[GLOBAL])
        self.assertEqual(manifest, current)
        snapshot = publication.read_policy_graph(manifest, transport)
        self.assertIn(FRAGMENT, snapshot)
        self.assertTrue(all(method == "GET" for method, *_ in transport.calls))

    def test_missing_source_wrong_scope_or_changed_fixed_expression_are_rejected(self):
        for mode in ("missing", "global", "other-operation", "other-fragment", "decoded-writer", "changed-expression"):
            current, _ = settings_graph()
            if mode == "missing":
                del current.policies[FRAGMENT]
            elif mode == "changed-expression":
                root = ET.fromstring(current.policies[FRAGMENT])
                root[1].set("value", '@{ return "{}"; }')
                current.policies[FRAGMENT] = ET.tostring(root, encoding="unicode")
            elif mode == "decoded-writer":
                current.policies[GLOBAL] = policy(pair='<set-variable name="gatewayApiSettings" value="{}" />')
            else:
                identifier = GLOBAL if mode == "global" else APIM + (
                    "/apis/model-a/operations/responses/policies/policy" if mode == "other-operation"
                    else "/policyFragments/unrelated")
                current.policies[identifier] = ("<fragment>" + settings_pair() + "</fragment>" if mode == "other-fragment" else policy())
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                publication.source_manifest(source_fixture(current), APIM, ["model-a", "model-b"], current.policies[GLOBAL])

    def test_readback_rejects_mutated_fragment_without_writes(self):
        current, transport = settings_graph()
        properties = transport.inventory[FRAGMENT]["body"]["properties"]
        root = ET.fromstring(properties["value"])
        root[0].set("value", root[0].get("value").replace('gatewayApiSettingsSource', 'differentSource', 1))
        properties["value"] = ET.tostring(root, encoding="unicode")
        with self.assertRaises(ValueError):
            publication.read_policy_graph(current, transport)
        self.assertTrue(all(method == "GET" for method, *_ in transport.calls))


if __name__ == "__main__":
    unittest.main()
