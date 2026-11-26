"""社員認証の実XML式を合成値で実行する。JWT検証器と実APIMの購読挙動は別途受入する。"""
from policy_fixture_paths import policy_fixture_path
import base64
import copy
import json
import re
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from configuration_fixtures import CORE
from deployment.helpers.apim_authentication import (
    ENTRA_OPEN_PRODUCT_ID, entra_authentication_settings, merge_common_authentication_policy,
    read_managed_entra_authentication, render_service_policy,
    transition_common_authentication_policy, validate_common_authentication_policy,
    _render_inline_common_authentication_service_policy, entra_authentication_named_values,
    validate_entra_named_values, validate_entra_named_value, ENTRA_NAMED_VALUES,
)
from deployment.helpers.apim_audit import render_audit_policy, expand_audit_fragments
from deployment.helpers.apim_ingress import expand_trusted_ingress_fragment
from deployment.helpers.apim_browser_exception import expand_browser_exception_fragment
from test_common_authentication import CSHARP, compile_inbound, csharp_expression
from test_gateway_limits_telemetry import PWSH
from deployment.helpers.apim_common_authentication import render_common_authentication_fragments, expand_common_authentication_fragments


def policy_root(xml):
    raw = ET.fromstring(xml)
    enabled = raw.find('.//include-fragment[@fragment-id="core-entra-authentication-error"]') is not None
    return ET.fromstring(expand_common_authentication_fragments(xml,
        render_common_authentication_fragments(entra_enabled=enabled), entra_enabled=enabled))

TENANT = "11111111-1111-4111-8111-111111111111"
PERSON = "22222222-2222-4222-8222-222222222222"
CLIENT = "33333333-3333-4333-8333-333333333333"
AUDIENCE = "44444444-4444-4444-8444-444444444444"
OTHER = "55555555-5555-4555-8555-555555555555"
SECOND_CLIENT = "77777777-7777-4777-8777-777777777777"
PARENT = "66666666-6666-4666-8666-666666666666"
POLICIES = CORE / "apps/gateway/apim/policies"
LIMITS = {"requestRate": {"calls": 25, "renewalPeriodSeconds": 60},
          "tokensPerMinute": 20000, "tokenQuota": {"tokens": 1000000, "period": "Monthly"}}


def settings():
    return {"enabled": True, "tenantId": TENANT, "audience": AUDIENCE,
            "requiredScope": "Gateway.Invoke",
            "apiNames": ["openai-inference-v1", "second-model-api"], "userLimits": copy.deepcopy(LIMITS)}


def encoded(value):
    return base64.b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


class EntraRenderingTests(unittest.TestCase):
    def test_disabled_setting_retains_key_policy_without_token_validation(self):
        self.assertEqual(render_service_policy(), render_service_policy(entra={"enabled": False}))
        self.assertNotIn("validate-azure-ad-token", render_service_policy(entra={"enabled": False}))
        self.assertEqual(entra_authentication_settings({}), {"enabled": False})

    def test_native_validator_pins_tenant_resource_version_and_scope_without_client_restrictions(self):
        rendered = render_service_policy(entra=settings())
        node = policy_root(rendered).find(".//validate-azure-ad-token")
        self.assertEqual(node.get("tenant-id"), "{{core-entra-tenant-id}}")
        self.assertEqual(node.get("header-name"), "Authorization")
        self.assertEqual(node.get("output-token-variable-name"), "gatewayValidatedToken")
        self.assertEqual([n.text for n in node.findall("audiences/audience")], ["{{core-entra-api-audience}}"])
        self.assertEqual(node.findall("client-application-ids"), [])
        self.assertEqual(node.find("required-claims/claim[@name='ver']/value").text, "2.0")
        scope = node.find("required-claims/claim[@name='scp']")
        self.assertEqual(scope.get("separator"), " ")
        self.assertEqual(scope.find("value").text, "{{core-entra-required-scope}}")
        guard = policy_root(rendered).find(".//when/set-variable[@name='gatewayAuthenticationFailure']")
        self.assertIn('context.Product.Id != "' + ENTRA_OPEN_PRODUCT_ID + '"', guard.get("value"))
        self.assertIn("context.Subscription != null", guard.get("value"))
        self.assertNotIn("__ENTRA_OPEN_PRODUCT_ID__", rendered)

    def test_new_policy_uses_four_named_values_without_a_settings_blob_or_embedded_values(self):
        rendered = render_service_policy(entra=settings())
        deployed = rendered + "".join(render_common_authentication_fragments(entra_enabled=True).values())
        for value in (TENANT, AUDIENCE, "Gateway.Invoke", encoded(settings()), "__ENTRA_SETTINGS_BASE64__"):
            self.assertNotIn(value, deployed)
        for name in ENTRA_NAMED_VALUES:
            self.assertIn("{{" + name + "}}", deployed)
        root = policy_root(rendered)
        raw = root.find(".//set-variable[@name='gatewayEntraUserLimits']")
        self.assertEqual(raw.get("value"), "{{core-entra-user-limits}}")
        user = root.find(".//set-variable[@name='gatewayUserAuthentication']").get("value")
        result = root.find(".//set-variable[@name='gatewayEntraAuthenticationResult']").get("value")
        self.assertIn('context.Variables["gatewayEntraUserLimits"]', result)
        self.assertIn('context.Variables["gatewayEntraAuthenticationResult"]', user)
        self.assertEqual(len(root.findall(".//set-variable[@name='gatewayEntraUserLimits']")), 1)

    def test_enabled_policy_requires_explicit_limits_and_canonical_identifiers(self):
        for field, value in (("audience", "api://" + AUDIENCE), ("tenantId", ""),
                             ("allowedClientIds", [CLIENT]), ("apiNames", [{}]),
                             ("apiNames", ["bad/api"]), ("requiredScope", "bad scope"),
                             ("clientSecretFile", "SYNTHETIC_SECRET_REFERENCE"),
                             ("userLimits", {})):
            bad = settings(); bad[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                render_service_policy(entra=bad)
        for field, value in (("tokensPerMinute", 0), ("tokensPerMinute", True),
                             ("requestRate", {"calls": 1, "renewalPeriodSeconds": 301}),
                             ("tokenQuota", {"tokens": 1, "period": "Forever"})):
            bad = settings(); bad["userLimits"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): render_service_policy(entra=bad)
        compatible = settings(); compatible["apiNames"] = ["Custom-API.v1"]
        compatible["requiredScope"] = "Gateway_Invoke-" + "x" * 100
        policy_root(render_service_policy(entra=compatible))

    def test_explicit_null_disables_each_limit_without_accepting_missing_fields(self):
        for mask in range(8):
            value = settings()
            for index, field in enumerate(("requestRate", "tokensPerMinute", "tokenQuota")):
                if mask & (1 << index): value["userLimits"][field] = None
            policy = render_service_policy(entra=value)
            self.assertEqual(read_managed_entra_authentication(policy, expected_entra=value), value)
        for field in ("requestRate", "tokensPerMinute", "tokenQuota"):
            value = settings(); del value["userLimits"][field]
            with self.subTest(field=field), self.assertRaises(ValueError): render_service_policy(entra=value)

    def test_enabling_migrates_only_exact_owned_key_policy_with_explicit_flag(self):
        previous, _ = merge_common_authentication_policy(None)
        with self.assertRaises(ValueError): merge_common_authentication_policy(previous, entra=settings())
        current, changed = merge_common_authentication_policy(previous, entra=settings(), migrate_entra_authentication=True)
        self.assertTrue(changed)
        validate_common_authentication_policy(current, entra=settings())
        self.assertEqual(merge_common_authentication_policy(current, entra=settings()), (current, False))
        modified = previous.replace('fragment-id="core-common-authentication"', 'fragment-id="unowned-authentication"')
        with self.assertRaises(ValueError):
            merge_common_authentication_policy(modified, entra=settings(), migrate_entra_authentication=True)

    def test_current_bearer_support_needs_no_runtime_readiness_marker(self):
        self.assertNotIn("gatewayEntraOperationSupported", render_service_policy(entra=settings()))
        for filename in ("gateway.xml", "protocol.xml", "mcp.xml"):
            policy = (policy_fixture_path(POLICIES, filename)).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertNotIn("gatewayEntraOperationSupported", policy)
                authentication = render_audit_policy(policy)
                # 3テンプレートの共通認証確認は同じ部品にあるため、正式なincludeと正本を確認します。
                preparation_call = ET.fromstring(policy).find("./inbound/include-fragment[@fragment-id='core-api-request-preparation']")
                self.assertIsNotNone(preparation_call)
                preparation = render_audit_policy(policy_fixture_path(POLICIES, "core-api-request-preparation.xml").read_text(encoding="utf-8"))
                self.assertNotIn("gatewayEntraOperationSupported", preparation)
                guard = next(node for node in ET.fromstring(preparation).findall("./choose/when")
                             if "gatewayCommonAuthenticationValidated" in node.get("condition", ""))
                self.assertEqual(guard.find("return-response/set-status").get("code"), "503")
                if filename == "gateway.xml":
                    # HTTPのBearer結果参照も別部品へ移したため、その呼出しと正本を確認します。
                    self.assertIsNotNone(ET.fromstring(policy).find("./inbound/include-fragment[@fragment-id='core-api-authentication-result']"))
                    authentication = render_audit_policy(policy_fixture_path(POLICIES, "core-api-authentication-result.xml").read_text(encoding="utf-8"))
                    self.assertNotIn("gatewayEntraOperationSupported", authentication)
                self.assertIn("gatewayCommonAuthenticationValidated", preparation)
                self.assertIn("gatewayEntraAuthenticationValidated", authentication)
                inbound = list(ET.fromstring(policy).find("inbound"))
                source = next(node for node in inbound if node.get("name") == "gatewayApiSettingsSource")
                call = next(node for node in inbound if node.get("fragment-id") == "core-api-settings")
                base = next(node for node in inbound if node.tag == "base")
                preparation_call = next(node for node in inbound if node.get("fragment-id") == "core-api-request-preparation")
                self.assertEqual(source.get("value"), "__API_SETTINGS_BASE64__")
                self.assertEqual(inbound.index(call), inbound.index(source) + 1)
                self.assertLess(inbound.index(call), inbound.index(base))
                self.assertLess(inbound.index(base), inbound.index(preparation_call))
                for node in inbound[:inbound.index(base)]:
                    self.assertNotIn("context.Request.Headers", ET.tostring(node, encoding="unicode"))
                    self.assertNotIn("context.Request.Body", ET.tostring(node, encoding="unicode"))
        policy = (POLICIES / "api-templates" / "gateway.xml").read_text(encoding="utf-8")
        self.assertLess(policy.index("<base />"), policy.index('<set-header name="Authorization" exists-action="delete"'))

    def test_current_bearer_routing_uses_key_presence_without_api_selection(self):
        first = settings()
        second = settings(); second["apiNames"] = ["future-model-api"]
        policy = render_service_policy(entra=first)
        self.assertEqual(policy, render_service_policy(entra=second))
        root = policy_root(policy)
        bearer = next(node for node in root.find("inbound/choose") if node.find("validate-azure-ad-token") is not None)
        self.assertEqual(bearer.get("condition"), '@(!context.Request.Headers.ContainsKey("{{core-subscription-key-header}}"))')
        self.assertNotIn("Contains(api)", bearer.get("condition"))
        self.assertNotIn("__ENTRA_API_NAMES_CSHARP__", policy)


    def test_current_policy_does_not_accept_a_copied_retired_operation_guard(self):
        # Independently synthesize the retired capability gate; no legacy producer is required.
        gate = ET.fromstring('<choose><when condition="@(!context.Variables.ContainsKey(&amp;quot;gatewayEntraOperationSupported&amp;quot;))"><return-response><set-status code="403" reason="Synthetic retired operation guard" /></return-response></when></choose>'.replace('&amp;quot;', '&quot;'))
        current = policy_root(render_service_policy(entra=settings()))
        bearer = next(node for node in current.find("inbound/choose") if node.find("validate-azure-ad-token") is not None)
        bearer.insert(3, gate)
        with self.assertRaises(ValueError):
            validate_common_authentication_policy(ET.tostring(current, encoding="unicode"), entra=settings())

    def test_employee_profile_fragment_has_no_nested_includes(self):
        profile = ET.fromstring(render_audit_policy(policy_fixture_path(POLICIES, "subscription-profile.xml").read_text(encoding="utf-8")))
        initial = profile[0]
        self.assertEqual(initial.tag, "set-variable")
        self.assertEqual(initial.get("name"), "gatewaySubscriptionProfile")
        self.assertEqual(json.loads(initial.get("value")), {"requestRate": None, "tokensPerMinute": None, "tokenQuota": None})
        employee = profile.find("choose/when")
        self.assertEqual([node.tag for node in employee], ["include-fragment"])
        self.assertEqual(employee[0].get("fragment-id"), "core-entra-user-profile")
        fragment = ET.fromstring(render_audit_policy(policy_fixture_path(POLICIES, "entra-user-profile.xml").read_text(encoding="utf-8")))
        self.assertEqual(fragment.tag, "fragment")
        self.assertEqual([node.tag for node in fragment], ["set-variable"])
        self.assertFalse(fragment.findall(".//include-fragment"))
        self.assertEqual(fragment[0].get("name"), "gatewaySubscriptionProfile")

    def test_explicit_broker_and_entra_migrations_can_be_composed(self):
        from test_credential_login import settings as login_settings
        from authentication_source_fixture import credential_broker_inline_fixture
        login = login_settings()
        previous = ET.fromstring(credential_broker_inline_fixture(login))
        anonymous = previous.find("./inbound/choose[@id='core-common-authentication']/when")
        old_condition, count = re.subn(r' \|\| \(context\.Operation\.Id == "identity-callback"[^)]*\)', "", anonymous.get("condition"))
        self.assertEqual(count, 1); anonymous.set("condition", old_condition)
        current, changed = merge_common_authentication_policy(ET.tostring(previous, encoding="unicode"), login,
                                                              entra=settings(), migrate_credential_broker=True,
                                                              migrate_entra_authentication=True)
        self.assertTrue(changed); validate_common_authentication_policy(current, login, entra=settings())


class EntraPolicyTransitionTests(unittest.TestCase):
    def policy(self, entra):
        return merge_common_authentication_policy(None, entra=entra)[0]


    def test_current_subject_writer_is_owned_ordered_and_cannot_be_overwritten(self):
        previous = render_service_policy(entra=settings())
        inbound = policy_root(previous).find("inbound")
        writer = inbound.find("include-fragment[@id='core-audit-authenticated-subject']")
        self.assertGreater(list(inbound).index(writer), list(inbound).index(inbound.find("choose[@id='core-common-authentication']")))
        for kind in ("changed", "duplicate", "renamed"):
            document = policy_root(previous)
            actual = document.find("inbound/include-fragment[@id='core-audit-authenticated-subject']")
            if kind == "changed": actual.set("fragment-id", "unowned-subject")
            elif kind == "renamed": actual.set("id", "manual-subject")
            else: ET.SubElement(document.find("inbound"), "set-variable", {"name": "gatewayAuthenticatedSubject", "value": "{}"})
            candidate = ET.tostring(document, encoding="unicode")
            with self.subTest(change=kind), self.assertRaises(ValueError):
                read_managed_entra_authentication(candidate, expected_entra=settings())


    def test_current_audit_references_cannot_run_twice_or_after_custom_processing(self):
        for identifier in ("core-request-context", "core-audit-authenticated-subject"):
            for kind in ("duplicate", "wrong-section", "late", "attributes"):
                document=policy_root(render_service_policy(entra=settings()))
                inbound=document.find("inbound")
                reference=inbound.find("include-fragment[@id='" + identifier + "']")
                if kind == "duplicate":
                    ET.SubElement(inbound, "include-fragment", {"fragment-id":identifier})
                elif kind == "wrong-section":
                    inbound.remove(reference); document.find("outbound").append(reference)
                elif kind == "late":
                    inbound.remove(reference); inbound.append(ET.Element("set-variable", {"name":"manual", "value":"1"})); inbound.append(reference)
                else:
                    reference.set("extra", "unverified")
                with self.subTest(fragment=identifier, change=kind), self.assertRaises(ValueError):
                    read_managed_entra_authentication(ET.tostring(document,encoding="unicode"), expected_entra=settings())


    def test_reader_recognizes_only_fully_validated_managed_settings(self):
        self.assertIsNone(read_managed_entra_authentication(self.policy(None)))
        self.assertEqual(read_managed_entra_authentication(self.policy(settings()), expected_entra=settings()), settings())
        with self.assertRaises(ValueError):
            read_managed_entra_authentication(self.policy(settings()))
        with self.assertRaises(ValueError):
            fragments = render_common_authentication_fragments(entra_enabled=True)
            fragments['core-common-authentication'] = fragments['core-common-authentication'].replace(
                'tenant-id="{{core-entra-tenant-id}}"', 'tenant-id="' + OTHER + '"')
            read_managed_entra_authentication(render_service_policy(entra=settings()),
                                             expected_entra=settings(), fragments=fragments)

    def test_reader_rejects_tampered_authentication_and_error_blocks(self):
        previous = self.policy(settings())
        changes = [
            lambda p: p.replace(ENTRA_OPEN_PRODUCT_ID, "unowned-product"),
            lambda p: p.replace('failed-validation-httpcode="401"', 'failed-validation-httpcode="200"'),
            lambda p: p.replace('name="gatewayCommonAuthenticationValidated"', 'name="spoofedMarker"', 1),
            lambda p: p.replace("{{core-entra-user-limits}}", "{}"),
        ]
        no_error = ET.fromstring(previous)
        error = no_error.find("on-error")
        error.remove(error.find("include-fragment[@id='core-entra-authentication-error']"))
        for index, change in enumerate(changes):
            with self.subTest(change=index), self.assertRaises(ValueError):
                fragments = render_common_authentication_fragments(entra_enabled=True)
                original = fragments['core-common-authentication']
                fragments['core-common-authentication'] = change(original)
                self.assertNotEqual(fragments['core-common-authentication'], original)
                read_managed_entra_authentication(previous, expected_entra=settings(), fragments=fragments)
        with self.assertRaises(ValueError):
            read_managed_entra_authentication(ET.tostring(no_error, encoding="unicode"), expected_entra=settings())

    def test_change_and_disable_preserve_custom_global_processing(self):
        document = ET.fromstring(self.policy(settings()))
        for section in ("inbound", "outbound", "on-error"):
            ET.SubElement(document.find(section), "set-variable", {"name": "manual-" + section, "value": "kept"})
        document.find("backend").find("forward-request").set("timeout", "180")
        document.find("inbound").append(ET.Comment("manual comment kept"))
        previous = ET.tostring(document, encoding="unicode")
        validate_common_authentication_policy(previous, entra=settings())
        next_settings = settings()
        next_settings["userLimits"]["requestRate"]["calls"] = 15
        changed_policy, changed = transition_common_authentication_policy(
            previous, previous_entra=settings(), entra=next_settings)
        # 値だけの変更は4NV更新で反映し、共通XMLの再適用を必要としません。
        self.assertFalse(changed)
        self.assertEqual(changed_policy, previous)
        self.assertNotEqual(entra_authentication_named_values(settings()), entra_authentication_named_values(next_settings))
        self.assertEqual(read_managed_entra_authentication(changed_policy, expected_entra=next_settings), next_settings)
        self.assertEqual(transition_common_authentication_policy(
            changed_policy, previous_entra=next_settings, entra=next_settings), (changed_policy, False))
        disabled, changed = transition_common_authentication_policy(
            changed_policy, previous_entra=next_settings, entra={"enabled": False})
        self.assertTrue(changed)
        self.assertIsNone(read_managed_entra_authentication(disabled))
        self.assertNotIn("core-entra-authentication-error", disabled)
        for policy in (changed_policy, disabled):
            parsed = policy_root(policy)
            for section in ("inbound", "outbound", "on-error"):
                self.assertEqual(parsed.find(section + "/set-variable[@name='manual-" + section + "']").get("value"), "kept")
            self.assertEqual(parsed.find("backend/forward-request").get("timeout"), "180")
            self.assertIn("manual comment kept", policy)

    def test_transition_requires_explicit_compatible_previous_settings(self):
        previous = _render_inline_common_authentication_service_policy(entra=settings())
        for supplied in (None, {"enabled": False}):
            with self.subTest(previous=supplied), self.assertRaises(ValueError):
                transition_common_authentication_policy(previous, previous_entra=supplied, entra=None)
        # NamedValue XML does not encode its actual audience. Its separate typed
        # readback must reject a mismatched logical expectation before transition.
        name = "core-entra-api-audience"
        body = {"properties": {"displayName": name, "secret": False,
                               "value": entra_authentication_named_values(settings())[name]}}
        with self.assertRaises(ValueError):
            validate_entra_named_value(body, name, OTHER)
        changed, updated = transition_common_authentication_policy(
            self.policy(None), previous_entra=None, entra=settings())
        self.assertTrue(updated)
        self.assertEqual(read_managed_entra_authentication(changed, expected_entra=settings()), settings())

    def test_disabled_policy_rejects_orphan_owned_error_and_preserves_noop(self):
        document = ET.fromstring(self.policy(None))
        ET.SubElement(document.find("on-error"), "set-variable", {"name": "manual-error", "value": "kept"})
        previous = ET.tostring(document, encoding="unicode")
        self.assertEqual(transition_common_authentication_policy(
            previous, previous_entra=None, entra=None), (previous, False))
        ET.SubElement(document.find("on-error"), "choose", {"id": "core-entra-authentication-error"})
        orphan = ET.tostring(document, encoding="unicode")
        for operation in (
                lambda: validate_common_authentication_policy(orphan),
                lambda: read_managed_entra_authentication(orphan),
                lambda: transition_common_authentication_policy(orphan, previous_entra=None, entra=None),
                lambda: transition_common_authentication_policy(orphan, previous_entra=None, entra=settings())):
            with self.assertRaises(ValueError): operation()


class EntraNamedValueTests(unittest.TestCase):
    def test_four_values_have_canonical_guid_scope_and_typed_json_representations(self):
        value = settings()
        value["tenantId"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        value["audience"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        expected = entra_authentication_named_values(value)
        self.assertEqual(tuple(expected), ENTRA_NAMED_VALUES)
        self.assertEqual(expected["core-entra-user-limits"], json.dumps(LIMITS, sort_keys=True, separators=(",", ":")))
        supplied = {**expected, "core-entra-tenant-id": value["tenantId"].upper(),
                    "core-entra-api-audience": value["audience"].upper(),
                    "core-entra-user-limits": json.dumps(dict(reversed(list(LIMITS.items()))), indent=2)}
        self.assertEqual(validate_entra_named_values(supplied), expected)
        self.assertEqual(entra_authentication_named_values({"enabled": False}), {})

    def test_typed_named_value_readback_rejects_missing_extra_duplicate_and_invalid_limits(self):
        expected = entra_authentication_named_values(settings())
        limits = [[], None, {}, {**LIMITS, "tokensPerMinute": True},
                  {**LIMITS, "tokensPerMinute": 1.0}, {**LIMITS, "tokensPerMinute": 0},
                  {**LIMITS, "requestRate": {"calls": 1, "renewalPeriodSeconds": 301}},
                  {**LIMITS, "tokenQuota": {"tokens": 1, "period": "monthly"}},
                  {**LIMITS, "extra": None}]
        invalid = [{}, {**expected, "unexpected": "value"}]
        invalid.extend({key: value for key, value in expected.items() if key != omitted} for omitted in expected)
        invalid.extend({**expected, "core-entra-user-limits": json.dumps(value)} for value in limits)
        invalid.extend({**expected, "core-entra-user-limits": value} for value in (
            '{"requestRate":null,"tokensPerMinute":1,"tokensPerMinute":2,"tokenQuota":null}',
            '{"requestRate":{"calls":1,"calls":2,"renewalPeriodSeconds":1},"tokensPerMinute":null,"tokenQuota":null}',
            '{broken',
        ))
        invalid.extend({**expected, name: value} for name, value in (
            ("core-entra-tenant-id", "00000000-0000-0000-0000-000000000000"),
            ("core-entra-api-audience", "not-a-guid"), ("core-entra-required-scope", "bad scope"),
            ("core-entra-user-limits", LIMITS),
        ))
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_entra_named_values(values)

    def test_named_value_metadata_and_expected_scope_and_limits_match_exactly(self):
        expected = entra_authentication_named_values(settings())
        for name, value in expected.items():
            body = {"properties": {"displayName": name, "secret": False, "value": value}}
            validate_entra_named_value(body, name, value)
            for field, changed in (("displayName", "unowned"), ("secret", True), ("keyVault", {})):
                with self.subTest(name=name, field=field), self.assertRaises(ValueError):
                    validate_entra_named_value({"properties": {**body["properties"], field: changed}}, name, value)
        for name, wrong in (("core-entra-required-scope", "gateway.invoke"),
                            ("core-entra-user-limits", json.dumps({**LIMITS, "tokensPerMinute": None}))):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_entra_named_value({"properties": {"displayName": name, "secret": False, "value": wrong}}, name, expected[name])

    def test_explicit_null_limits_are_preserved_in_all_eight_combinations(self):
        for mask in range(8):
            value = settings()
            for index, name in enumerate(("requestRate", "tokensPerMinute", "tokenQuota")):
                if mask & (1 << index): value["userLimits"][name] = None
            with self.subTest(mask=mask):
                values = entra_authentication_named_values(value)
                self.assertEqual(validate_entra_named_values(values), values)
                self.assertEqual(json.loads(values["core-entra-user-limits"]), value["userLimits"])


class EntraExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH: raise unittest.SkipTest("Local PowerShell/Roslyn required; Azure was not tested")
        methods, inbound = compile_inbound(policy_root(render_service_policy(entra=settings())),
            after_variable=lambda name: " ApplyFault(context, " + json.dumps(name) + ");"
            if name in ("gatewayEntraTokenValidationCompleted", "gatewayEntraIdentityResult", "gatewayEntraAuthenticationResult") else "",
            trace_messages=True)
        # 変数設定とrequired-claimsの名前付きの値を合成server値へ接続し、実XMLの式を実行します。
        methods = methods.replace('return "{{core-entra-tenant-id}}";', 'return context.NamedTenant;')
        methods = methods.replace('return "{{core-entra-required-scope}}";', 'return context.NamedScope;')
        methods = methods.replace('return "{{core-entra-user-limits}}";', 'return context.NamedLimits;')
        methods += "\nstatic bool Guard(Context context,string name) { return false; }"
        base = CSHARP.split("public static class CommonAuthenticationFixture")[0]
        base = base.replace("public class Named { public string Id; }", "public class Named { public string Id; public string UrlTemplate; }")
        base = base.replace("public class Context {", "public class Response { public int StatusCode = 200; }\npublic class Context { public Response Response = new Response(); public string NamedTenant, NamedScope, NamedLimits; public JObject Fault;")
        base = base.replace("public Dictionary<string,object> Variables = new Dictionary<string,object>();",
                            "public TokenCounterVariables Variables = new TokenCounterVariables();")
        base = base.replace("public class Response {", r'''public class TokenCounterVariables : Dictionary<string,object> {
 public bool CounterLocked;
 public bool EnabledLocked;
 public bool ProfileLocked;
 public int UserAuthenticationReads, EntraMarkerReads;
 public int EnabledEntraMarkerReads;
 public int EnabledUserLimitsReads;
 public int ProfileEntraMarkerReads;
 public int ProfileDecisionReads;
 public int ProfileUserLimitsReads;
 void Check(string key) {
  if (ProfileLocked && key == "gatewayEntraUserLimits") {
   ProfileUserLimitsReads++;
   throw new Exception("Employee profile attempted to reopen the original employee limit settings");
  }
  if (ProfileLocked && key == "gatewayDecision") {
   ProfileDecisionReads++;
   throw new Exception("Employee profile attempted to reopen the handed-off authentication decision");
  }
  if (ProfileLocked && key == "gatewayEntraAuthenticationValidated") {
   ProfileEntraMarkerReads++;
   throw new Exception("Employee profile attempted to repeat employee authentication completion check");
  }
  if (EnabledLocked && key == "gatewayEntraAuthenticationValidated") {
   EnabledEntraMarkerReads++;
   throw new Exception("Token enabled attempted to repeat employee authentication completion check");
  }
  if (EnabledLocked && key == "gatewayEntraUserLimits") {
   EnabledUserLimitsReads++;
   throw new Exception("Token enabled attempted to reopen already verified employee limits");
  }
  if (CounterLocked && key == "gatewayUserAuthentication") {
   UserAuthenticationReads++;
   throw new Exception("Token counter attempted to reopen verified employee JSON");
  }
  if (CounterLocked && key == "gatewayEntraAuthenticationValidated") {
   EntraMarkerReads++;
   throw new Exception("Token counter attempted to repeat employee authentication completion check");
  }
 }
 public new object this[string key] { get { Check(key); return base[key]; } set { base[key] = value; } }
 public new bool ContainsKey(string key) { Check(key); return base.ContainsKey(key); }
 public new bool TryGetValue(string key, out object value) { Check(key); return base.TryGetValue(key, out value); }
 public T GetValueOrDefault<T>(string key, T fallback) { object value; return TryGetValue(key, out value) ? (T)value : fallback; }
}
public class Response {''')
        def method(name, filename, xpath, attribute="value", return_type="string"):
            from policy_fixture_paths import policy_fixture_path
            node = policy_root(render_audit_policy(policy_fixture_path(POLICIES, filename).read_text(encoding="utf-8"))).find(xpath)
            raw = node.get(attribute) if attribute else node.text
            raw = raw.replace("__AUTHORIZATION_BASE64__", encoded({"mode": "subscriptionKey"}))
            raw = raw.replace("__CREDENTIAL_CONNECTION_BASE64__", "")
            raw = raw.replace("__RATE_LIMITS_BASE64__", encoded({"subscriptionKey": {"enabled": True, "calls": 10, "renewalPeriodSeconds": 30, "overrides": []}}))
            raw = raw.replace("{{core-subscription-profile-enabled}}", "false")
            return "static " + return_type + " " + name + "(Context context) { " + csharp_expression(raw) + " }"
        methods += "\n" + "\n".join([
            method("Decision", "core-api-authentication-result.xml", ".//set-variable[@name='gatewayDecision']"),
            method("Protocol", "protocol.xml", ".//set-variable[@name='gatewayDecision']"),
            method("Mcp", "mcp.xml", ".//set-variable[@name='gatewayDecision']"),
            method("UserProfile", "entra-user-profile.xml", "set-variable[@name='gatewaySubscriptionProfile']"),
            method("Rate", "rate-limit.xml", ".//set-variable[@name='gatewayRateDecision']"),
            method("TokenEnabled", "token-limit.xml", ".//set-variable[@name='gatewayLlmTokenLimitEnabled']", return_type="bool"),
            method("TokenKey", "token-limit.xml", ".//set-variable[@name='gatewayTokenCounterKey']"),
            method("Parent", "audit-context.xml", ".//set-variable[@name='gatewayAgentTrace']"),
            method("Telemetry", "core-gateway-telemetry.xml", ".//trace/message", attribute=None),
        ])
        # 各APIのDecision直後の失敗分岐を実XMLから取り出し、既存アダプターで監査と応答を実行します。
        cls.decision_failure_reasons = {}
        telemetry = ET.fromstring(render_audit_policy(policy_fixture_path(POLICIES, "core-gateway-telemetry.xml").read_text(encoding="utf-8")))
        for api, label, filename in (("decision", "DecisionFailure", "gateway.xml"),
                                     ("protocol", "ProtocolFailure", "protocol.xml"),
                                     ("mcp", "McpFailure", "mcp.xml")):
            gateway = ET.parse(policy_fixture_path(POLICIES, filename)).getroot()
            decision_failure = next(node for node in gateway.findall("./inbound/choose")
                                    if "gatewayDecision" in node.find("when").get("condition", ""))
            cls.decision_failure_reasons[api] = decision_failure.find("when/return-response/set-status").get("reason")
            failure_policy = ET.Element("policies")
            ET.SubElement(failure_policy, "inbound").append(copy.deepcopy(decision_failure))
            # 共通認証用アダプターが自動展開しない監査フラグメントも正本から展開します。
            failure_branch = failure_policy.find("inbound/choose/when")
            telemetry_call = failure_branch.find("include-fragment[@fragment-id='core-gateway-telemetry']")
            telemetry_position = list(failure_branch).index(telemetry_call)
            failure_branch.remove(telemetry_call)
            for offset, node in enumerate(telemetry):
                failure_branch.insert(telemetry_position + offset, copy.deepcopy(node))
            failure_methods, failure_inbound = compile_inbound(failure_policy, trace_messages=True)
            # 同じfixtureへ実XMLブロックを組み込むので、自動生成された補助関数名だけを区別します。
            failure_methods = re.sub(r"\bExpression(\d+)\b", label + r"Expression\1", failure_methods)
            failure_inbound = re.sub(r"\bExpression(\d+)\b", label + r"Expression\1", failure_inbound)
            methods += "\n" + failure_methods + "\nstatic int " + label + "(Context context) { " + failure_inbound + " return 200; }"
        profile_template = ET.fromstring(render_audit_policy(policy_fixture_path(POLICIES, "subscription-profile.xml").read_text(encoding="utf-8")))
        profile_initial = profile_template.find('./set-variable[@name="gatewaySubscriptionProfile"]').get("value")
        source = base + r'''
public static class EntraFixture {
 __METHODS__
 // Fault injection belongs to this local adapter only; production XML has no special request branch.
 static void ApplyFault(Context context, string stage) {
  var fault = context.Fault;
  if (fault == null || (string)fault["stage"] != stage) return;
  var action = (string)fault["action"];
  if (action == "missing-jwt") context.Variables.Remove("gatewayValidatedToken");
  else if (action == "null-jwt") context.Variables["gatewayValidatedToken"] = null;
  else if (action == "null-claims") context.Token.Claims = null;
  else if (action == "missing-tenant") context.Variables.Remove("gatewayEntraTenantId");
  else if (action == "wrong-limits-type") context.Variables["gatewayEntraUserLimits"] = 42;
  else if (action == "missing-proof") context.Variables.Remove("gatewayEntraTokenValidationCompleted");
  else if (action == "false-proof") context.Variables["gatewayEntraTokenValidationCompleted"] = false;
  else if (action == "key-mixed") context.Request.Headers["X-Gateway-Key"] = new [] { "SYNTHETIC_KEY" };
  else if (action == "subscription-mixed") context.Subscription = new Named { Id="unexpected-subscription" };
  else if (action == "saved-subscription-mixed") context.Variables["gatewaySubscriptionId"] = "unexpected-subscription";
  else if (action == "identity-raw") context.Variables["gatewayEntraIdentityResult"] = (string)fault["value"];
  else if (action == "identity-tamper") {
   var identity = JObject.Parse((string)context.Variables["gatewayEntraIdentityResult"]);
   foreach (var property in ((JObject)fault["values"]).Properties()) {
    if (property.Value.Type == JTokenType.Null) identity.Remove(property.Name);
    else identity[property.Name] = property.Value;
   }
   context.Variables["gatewayEntraIdentityResult"] = identity.ToString(Formatting.None);
  } else throw new Exception("Unknown local adapter fault");
 }
 static int Authenticate(Context context) { __INBOUND__ return 200; }
 public static string Run(string json) {
  var output = new JObject();
  foreach (JObject item in JArray.Parse(json)) {
   var c = new Context(); c.Api.Id = (string)item["api"] ?? "openai-inference-v1";
   var namedSettings = JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String((string)item["serverSettings"] ?? "__DEFAULT_SETTINGS__")));
   c.NamedTenant = (string)namedSettings["tenantId"]; c.NamedScope = (string)namedSettings["requiredScope"];
   c.NamedLimits = item["limitsRaw"] != null ? (string)item["limitsRaw"] : namedSettings["userLimits"].ToString(Formatting.None);
   c.Operation.Id = "responses-create"; c.Operation.UrlTemplate = (string)item["path"] ?? "/responses";
   c.Request.Method = (string)item["method"] ?? "POST";
   c.Request.Headers["X-Core-L7-Ingress"] = new [] { "fixture-ingress" };
   foreach (var p in ((JObject)item["headers"]).Properties()) c.Request.Headers[p.Name] = p.Value is JArray ? p.Value.Select(x=>(string)x).ToArray() : new [] {(string)p.Value};
   if (item["query"] is JObject query) foreach(var p in query.Properties()) c.Request.Url.Query[p.Name] = new [] {(string)p.Value};
   if ((bool?)item["subscription"] == true) c.Subscription = new Named {Id=(string)item["subscriptionId"] ?? "subscription-a"};
   if (item["product"] == null) c.Product = new Named {Id="__OPEN_PRODUCT__"};
   else if (item["product"].Type != JTokenType.Null) c.Product = new Named {Id=(string)item["product"]};
   c.ValidSignature = (bool?)item["nativeAccepted"] ?? true;
   c.Token = new Jwt(); foreach(var p in ((JObject)item["claims"]).Properties()) c.Token.Claims[p.Name] = p.Value is JArray ? p.Value.Select(x=>(string)x).ToArray() : new [] {(string)p.Value};
   if (item["nullClaimArrays"] is JArray nullClaims) foreach (var name in nullClaims) c.Token.Claims[(string)name] = null;
   c.Fault = item["fault"] as JObject;
   c.Variables["gatewayAuditSettings"] = new JObject(new JProperty("backendService", (string)item["backend"] ?? "foundryOpenAi"),
     new JProperty("backendAuthentication", (string)item["backendAuthentication"] ?? "managedIdentity"),
     new JProperty("llmOperationIds", new JArray("responses-create")),
     new JProperty("targetAgent", "")).ToString(Formatting.None);
   c.Variables["gatewayControlSettings"] = new JObject(new JProperty("operationId", ""),
     new JProperty("tokenLimitOperationIds", new JArray("responses-create"))).ToString(Formatting.None);
   if ((bool?)item["legacySettings"] == true) {
    var mixed=JObject.Parse((string)c.Variables["gatewayAuditSettings"]);
    mixed["tokenLimitOperationIds"]=new JArray("responses-create");
    c.Variables["gatewayAuditSettings"]=mixed.ToString(Formatting.None);
    c.Variables.Remove("gatewayControlSettings");
   }
   if (item.Property("controlRaw") != null) c.Variables["gatewayControlSettings"]=
     item["controlRaw"].Type == JTokenType.Null ? null : (string)item["controlRaw"];
   c.Variables["gatewayAuditModel"] = (string)item["model"] ?? "synthetic-model";
   if (item["model"] != null) c.Variables["gatewayEffectiveModel"] = (string)item["model"];
   var result = new JObject(new JProperty("status", Authenticate(c)), new JProperty("nativeOutput", c.Variables.ContainsKey("gatewayValidatedToken")));
   result["bearerRequest"] = ((JObject)item["headers"]).Property("X-Gateway-Key") == null;
   result["headers"] = JObject.FromObject(c.ResponseHeaders);
   result["commonValidated"] = c.Variables.ContainsKey("gatewayCommonAuthenticationValidated") && (bool)c.Variables["gatewayCommonAuthenticationValidated"];
   result["entraValidated"] = c.Variables.ContainsKey("gatewayEntraAuthenticationValidated") && (bool)c.Variables["gatewayEntraAuthenticationValidated"];
   result["hasUserAuthentication"] = c.Variables.ContainsKey("gatewayUserAuthentication");
   if (c.Variables.ContainsKey("gatewayUserAuthentication")) result["userAuthentication"] = JObject.Parse((string)c.Variables["gatewayUserAuthentication"]);
   if (c.TraceMessages.Count > 0) result["failureAudit"] = JObject.Parse(c.TraceMessages.Last());
   if ((int)result["status"] == 200) {
    // Unit fixtures change only the downstream view after real common authentication succeeded.
    // This tests each API decision independently; it does not model an actual APIM Product mutation.
    if (item["decisionContext"] is JObject unit) {
     if (unit.Property("product") != null) c.Product = unit["product"].Type == JTokenType.Null
       ? null : new Named { Id=(string)unit["product"] };
     if ((bool?)unit["subscription"] == true) c.Subscription = new Named { Id="unexpected-subscription" };
     if (unit.Property("keyHeader") != null) {
      var keyHeader = (string)c.Variables["gatewayKeyHeader"];
      if (unit["keyHeader"].Type == JTokenType.Null) c.Request.Headers.Remove(keyHeader);
      else c.Request.Headers[keyHeader] = new [] { (string)unit["keyHeader"] };
     }
     foreach (var marker in new [] { "gatewayCommonAuthenticationValidated", "gatewayEntraAuthenticationValidated" }) {
      if (unit.Property(marker) != null) {
       if (unit[marker].Type == JTokenType.Null) c.Variables.Remove(marker);
       else c.Variables[marker] = (bool)unit[marker];
      }
     }
     if (unit.Property("userRaw") != null) {
      if (unit["userRaw"].Type == JTokenType.Null) c.Variables.Remove("gatewayUserAuthentication");
      else c.Variables["gatewayUserAuthentication"] = (string)unit["userRaw"];
     }
    }
    if (item["tamper"] is JObject tamper) {
     var user = JObject.Parse((string)c.Variables["gatewayUserAuthentication"]);
     foreach(var p in tamper.Properties()) user[p.Name] = p.Value;
     c.Variables["gatewayUserAuthentication"] = user.ToString(Formatting.None);
    }
    if ((bool?)item["dropMarker"] == true) c.Variables.Remove("gatewayEntraAuthenticationValidated");
    // 実APIでは先行する操作設定の準備が成功した結果を、この位置より前に保存します。
    c.Variables["gatewayOperationSelection"] = new JObject(new JProperty("allowed", true),
      new JProperty("settingId", "")).ToString(Formatting.None);
    c.Variables["gatewayDecision"] = Decision(c);
    result["decision"] = JObject.Parse((string)c.Variables["gatewayDecision"]);
    if ((bool?)item["httpDecisionOnly"] != true) {
     result["protocol"] = JObject.Parse(Protocol(c)); result["mcp"] = JObject.Parse(Mcp(c));
    }
    foreach (var api in new [] { "decision", "protocol", "mcp" }) {
     if (result[api] == null || (bool)result[api]["allowed"]) continue;
     // statusは共通認証の結果です。各APIの実拒否枝へそのAPIの引渡し結果を渡します。
     c.Variables["gatewayDecision"] = result[api].ToString(Formatting.None);
     c.ResponseHeaders.Clear();
     var traceCount = c.TraceMessages.Count;
     var failureStatus = api == "decision" ? DecisionFailure(c)
       : api == "protocol" ? ProtocolFailure(c) : McpFailure(c);
     result[api + "Failure"] = new JObject(new JProperty("status", failureStatus),
       new JProperty("headers", JObject.FromObject(c.ResponseHeaders)),
       new JProperty("auditCount", c.TraceMessages.Count - traceCount),
       new JProperty("audit", JObject.Parse(c.TraceMessages.Last())));
    }
    c.Variables["gatewayDecision"] = result["decision"].ToString(Formatting.None);
    if ((bool)result["decision"]["allowed"]) {
     try {
      c.Variables["gatewayRateLimitExempt"] = (bool?)item["rateExempt"] ?? false;
       // 実認証と全APIの引渡し後、社員profileへ進む直前だけ成功印/allowedの孤立対照を注入します。
       var profileDecisionBefore = (string)c.Variables["gatewayDecision"];
       object profileEntraBefore;
       var profileEntraHadValue = c.Variables.TryGetValue("gatewayEntraAuthenticationValidated", out profileEntraBefore);
       object profileLimitsBefore, profileUserBefore;
       var profileLimitsHadValue = c.Variables.TryGetValue("gatewayEntraUserLimits", out profileLimitsBefore);
       var profileUserHadValue = c.Variables.TryGetValue("gatewayUserAuthentication", out profileUserBefore);
       string normalProfileBefore = null;
       if ((bool?)item["restoreProfileAfterRead"] == true) {
        normalProfileBefore = ((JObject)JObject.Parse((string)profileUserBefore)["limits"]).ToString(Formatting.None);
       }
      if (item.Property("profileEntraValidated") != null) {
       if (item["profileEntraValidated"].Type == JTokenType.Null) c.Variables.Remove("gatewayEntraAuthenticationValidated");
       else c.Variables["gatewayEntraAuthenticationValidated"] = (bool)item["profileEntraValidated"];
      }
       if (item.Property("profileDecisionAllowed") != null) {
        var profileDecision = JObject.Parse((string)c.Variables["gatewayDecision"]);
        profileDecision["allowed"] = item["profileDecisionAllowed"];
        c.Variables["gatewayDecision"] = profileDecision.ToString(Formatting.None);
       }
       // profile用の本人再照合を撤去した対照です。正式な認証/引渡し結果はこの直前まで正常です。
       if (item.Property("profileDecisionRaw") != null) {
        if (item["profileDecisionRaw"].Type == JTokenType.Null) c.Variables.Remove("gatewayDecision");
        else c.Variables["gatewayDecision"] = (string)item["profileDecisionRaw"];
       }
       if (item["profileDecisionPatch"] is JObject profilePatch) {
        var profileDecision = JObject.Parse((string)c.Variables["gatewayDecision"]);
        foreach (var property in profilePatch.Properties()) profileDecision[property.Name] = property.Value.DeepClone();
        c.Variables["gatewayDecision"] = profileDecision.ToString(Formatting.None);
       }
      // 実認証と全APIの引渡しは正常のまま、社員profile直前の一致する内部枠だけを変更します。
       // UserProfileの取得結果を観測し、不正値の孤立対照は計数前に正常profileへ復元します。
      if (item.Property("profileRequestRate") != null) {
       var profileUser = JObject.Parse((string)c.Variables["gatewayUserAuthentication"]);
       var profileLimits = JObject.Parse((string)c.Variables["gatewayEntraUserLimits"]);
       ((JObject)profileUser["limits"])["requestRate"] = item["profileRequestRate"].DeepClone();
       profileLimits["requestRate"] = item["profileRequestRate"].DeepClone();
       c.Variables["gatewayUserAuthentication"] = profileUser.ToString(Formatting.None);
       c.Variables["gatewayEntraUserLimits"] = profileLimits.ToString(Formatting.None);
      }
       // 実認証後の内部faultだけを社員profileへ渡し、JObjectの再検査を行わない取得を観測します。
      if (item["profileLimits"] is JObject) {
       var profileUser = JObject.Parse((string)c.Variables["gatewayUserAuthentication"]);
       profileUser["limits"] = item["profileLimits"].DeepClone();
       c.Variables["gatewayUserAuthentication"] = profileUser.ToString(Formatting.None);
       c.Variables["gatewayEntraUserLimits"] = item["profileLimits"].ToString(Formatting.None);
      }
       // 認証済みsnapshot側だけの有効/不正なlimitsを、profile取得区間へ人工的に渡します。
       if (item.Property("profileSnapshotLimits") != null) {
        var profileUser = JObject.Parse((string)c.Variables["gatewayUserAuthentication"]);
        profileUser["limits"] = item["profileSnapshotLimits"].DeepClone();
        c.Variables["gatewayUserAuthentication"] = profileUser.ToString(Formatting.None);
       }
       if ((bool?)item["profileSnapshotLimitsMissing"] == true) {
        var profileUser = JObject.Parse((string)c.Variables["gatewayUserAuthentication"]);
        profileUser.Remove("limits");
        c.Variables["gatewayUserAuthentication"] = profileUser.ToString(Formatting.None);
       }
       if (item.Property("profileUserRaw") != null) {
        if (item["profileUserRaw"].Type == JTokenType.Null) c.Variables.Remove("gatewayUserAuthentication");
        else c.Variables["gatewayUserAuthentication"] = (string)item["profileUserRaw"];
       }
      if ((bool?)item["profileUserLimitsMissing"] == true) c.Variables.Remove("gatewayEntraUserLimits");
       if (item.Property("profileUserLimitsRaw") != null) c.Variables["gatewayEntraUserLimits"] = (string)item["profileUserLimitsRaw"];
       if (item["profileOriginalLimits"] is JObject originalLimits) c.Variables["gatewayEntraUserLimits"] = originalLimits.ToString(Formatting.None);
       c.Variables.ProfileLocked = true;
       try {
        c.Variables["gatewaySubscriptionProfile"] = (string)result["decision"]["method"] == "user" ? UserProfile(c) : __PROFILE_INITIAL__;
        } catch (Exception error) {
         // 取得式から出た例外をadapterで観測し、検査での拒否や公開HTTP応答は追加しません。
         result["profileReadError"] = error.GetType().Name;
         throw;
        } finally {
        c.Variables.ProfileLocked = false;
        // profileだけの孤立対照を復元し、rate/TokenEnabled/Counter/監査の確認へ持ち越しません。
        c.Variables["gatewayDecision"] = profileDecisionBefore;
        if (profileEntraHadValue) c.Variables["gatewayEntraAuthenticationValidated"] = profileEntraBefore;
        else c.Variables.Remove("gatewayEntraAuthenticationValidated");
        if (profileLimitsHadValue) c.Variables["gatewayEntraUserLimits"] = profileLimitsBefore;
        else c.Variables.Remove("gatewayEntraUserLimits");
        if (profileUserHadValue) c.Variables["gatewayUserAuthentication"] = profileUserBefore;
        else c.Variables.Remove("gatewayUserAuthentication");
        result["profileEntraReads"] = c.Variables.ProfileEntraMarkerReads;
        result["profileDecisionReads"] = c.Variables.ProfileDecisionReads;
        result["profileOriginalLimitsReads"] = c.Variables.ProfileUserLimitsReads;
       }
      result["profile"] = JObject.Parse((string)c.Variables["gatewaySubscriptionProfile"]);
       if (normalProfileBefore != null) {
        // 返却された人工snapshotはprofileへ記録済みです。後続は正式認証済みの正常枠で確認します。
        c.Variables["gatewaySubscriptionProfile"] = normalProfileBefore;
        result["countedProfile"] = JObject.Parse(normalProfileBefore);
       }
      result["rate"] = JObject.Parse(Rate(c));
      // 共通認証・API引渡し・profile・rateが成功した後、TokenEnabled直前だけ孤立対照を注入します。
      var tokenDecisionBefore = (string)c.Variables["gatewayDecision"];
      object tokenEntraBefore;
      var tokenEntraHadValue = c.Variables.TryGetValue("gatewayEntraAuthenticationValidated", out tokenEntraBefore);
      object tokenLimitsBefore;
      var tokenLimitsHadValue = c.Variables.TryGetValue("gatewayEntraUserLimits", out tokenLimitsBefore);
       object tokenControlsBefore;
       var tokenControlsHadValue = c.Variables.TryGetValue("gatewayControlSettings", out tokenControlsBefore);
      if (item.Property("tokenEntraValidated") != null) {
       if (item["tokenEntraValidated"].Type == JTokenType.Null) c.Variables.Remove("gatewayEntraAuthenticationValidated");
       else c.Variables["gatewayEntraAuthenticationValidated"] = (bool)item["tokenEntraValidated"];
      }
      if (item["tokenUserLimits"] is JObject) c.Variables["gatewayEntraUserLimits"] = item["tokenUserLimits"].ToString(Formatting.None);
      if ((bool?)item["tokenUserLimitsMissing"] == true) c.Variables.Remove("gatewayEntraUserLimits");
      if (item.Property("tokenEnabledAllowed") != null) {
       var enabledDecision = JObject.Parse((string)c.Variables["gatewayDecision"]);
       enabledDecision["allowed"] = item["tokenEnabledAllowed"];
       c.Variables["gatewayDecision"] = enabledDecision.ToString(Formatting.None);
      }
       // 内部一覧の対照は認証/profile/rateの成功後だけ注入し、利用者の認証失敗と区別します。
       if ((bool?)item["tokenOperationListMissing"] == true || item.Property("tokenOperationList") != null) {
        var tokenControls = JObject.Parse((string)c.Variables["gatewayControlSettings"]);
        if ((bool?)item["tokenOperationListMissing"] == true) tokenControls.Remove("tokenLimitOperationIds");
        else tokenControls["tokenLimitOperationIds"] = item["tokenOperationList"].DeepClone();
        c.Variables["gatewayControlSettings"] = tokenControls.ToString(Formatting.None);
       }
      c.Variables.EnabledLocked = true;
      try { result["tokenEnabled"] = TokenEnabled(c); }
       catch (Exception error) {
        // 実有効化式から伝播した例外を、この呼出し用adapterだけで観測します。
        // falseへの変換や公開応答は追加せず、先行profileのlimitsRejectedとも分けます。
        result["tokenEnabledError"] = error.GetType().Name;
       }
      finally {
       c.Variables.EnabledLocked = false;
       // この単体対照の変更をCounterや監査へ持ち越さず、有効化式だけを観測します。
       c.Variables["gatewayDecision"] = tokenDecisionBefore;
       if (tokenEntraHadValue) c.Variables["gatewayEntraAuthenticationValidated"] = tokenEntraBefore;
       else c.Variables.Remove("gatewayEntraAuthenticationValidated");
       if (tokenLimitsHadValue) c.Variables["gatewayEntraUserLimits"] = tokenLimitsBefore;
       else c.Variables.Remove("gatewayEntraUserLimits");
        if (tokenControlsHadValue) c.Variables["gatewayControlSettings"] = tokenControlsBefore;
        else c.Variables.Remove("gatewayControlSettings");
      }
      result["tokenEnabledEntraReads"] = c.Variables.EnabledEntraMarkerReads;
      result["tokenEnabledUserLimitsReads"] = c.Variables.EnabledUserLimitsReads;
       if ((bool?)result["tokenEnabled"] == true) {
       // TokenEnabled成功後の単体対照だけがdecisionを差し替えます。正式入口でのID変更ではありません。
       if (item["tokenCounterDecision"] is JObject counterPatch) {
        var counterDecision = JObject.Parse((string)c.Variables["gatewayDecision"]);
        foreach (var property in counterPatch.Properties()) {
         if (property.Value.Type == JTokenType.Null) counterDecision.Remove(property.Name);
         else counterDecision[property.Name] = property.Value.DeepClone();
        }
        c.Variables["gatewayDecision"] = counterDecision.ToString(Formatting.None);
       }
       c.Variables.CounterLocked = true;
       try { result["tokenKey"] = TokenKey(c); }
       finally {
        c.Variables.CounterLocked = false;
        c.Variables["gatewayDecision"] = result["decision"].ToString(Formatting.None);
       }
       result["tokenKeyUserReads"] = c.Variables.UserAuthenticationReads;
       result["tokenKeyEntraReads"] = c.Variables.EntraMarkerReads;
      }
       // 例外時は後続Counter/通常監査へ進みません。既存on-errorの応答はこのadapterでは作りません。
       if (result["tokenEnabledError"] == null) {
        c.Request.Headers["X-Core-Agent-Parent-Id"] = new [] { "__PARENT__" };
        c.Variables["gatewayAgentTrace"] = Parent(c); result["audit"] = JObject.Parse(Telemetry(c));
       }
     } catch { result["limitsRejected"] = true; }
    }
   }
   output[(string)item["id"]] = result;
  }
  return output.ToString(Formatting.None);
 }
}'''.replace("__METHODS__", methods).replace("__INBOUND__", inbound).replace("__PARENT__", PARENT).replace("__OPEN_PRODUCT__", ENTRA_OPEN_PRODUCT_ID).replace("__DEFAULT_SETTINGS__", encoded(settings())).replace("__PROFILE_INITIAL__", json.dumps(profile_initial))
        claims = {"ver": "2.0", "tid": TENANT, "oid": PERSON, "azp": CLIENT, "scp": "Other.Read Gateway.Invoke"}
        cases = []
        def add(name, **changes):
            cases.append({"id": name, "headers": {"Authorization": "Bearer SYNTHETIC_TOKEN"}, "claims": copy.deepcopy(claims), **changes})
        add("employee"); add("other-client-same-user", claims={**claims, "azp": SECOND_CLIENT})
        other_scope = settings(); other_scope["requiredScope"] = "Custom.Invoke"
        add("configured-scope-accepted", serverSettings=encoded(other_scope),
            claims={**claims, "scp": "Other.Read Custom.Invoke"})
        add("configured-scope-rejected", serverSettings=encoded(other_scope))
        upper_tenant = settings(); upper_tenant["tenantId"] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
        add("canonical-named-tenant", serverSettings=encoded(upper_tenant),
            claims={**claims, "tid": upper_tenant["tenantId"].lower()})
        for label, raw in (("malformed", '{broken'), ("array", '[]'), ("scalar", '42'), ("null", 'null'),
                           ("quoted", '"synthetic \\\" JSON string"')):
            add("invalid-named-limits-" + label, limitsRaw=raw)
        add("employee-legacy-mixed",legacySettings=True)
        for label, raw in (('malformed', '{broken'), ('null', None), ('blank', ''), ('array', '[]'), ('scalar', '42')):
            add('employee-'+label+'-new-control',legacySettings=True,controlRaw=raw)
        add("arbitrary-client-same-user", claims={**claims, "azp": OTHER})
        no_client = copy.deepcopy(claims); del no_client["azp"]
        add("optional-azp-missing", claims=no_client)
        for name, value in (("duplicate", [CLIENT, CLIENT]), ("invalid", "invalid"),
                            ("zero", "00000000-0000-0000-0000-000000000000"), ("empty", "")):
            add("optional-azp-" + name, claims={**claims, "azp": value})
        add("other-api-same-user", api="second-model-api")
        add("other-model-same-user", model="synthetic-other-model")
        for mask in range(8):
            value = settings()
            for index, field in enumerate(("requestRate", "tokensPerMinute", "tokenQuota")):
                if mask & (1 << index): value["userLimits"][field] = None
            add("nullable-" + str(mask), serverSettings=encoded(value))
        high_rate = settings(); high_rate["userLimits"] = {
            "requestRate": {"calls": 1000, "renewalPeriodSeconds": 60}, "tokensPerMinute": None, "tokenQuota": None}
        add("high-request-rate-only", serverSettings=encoded(high_rate))
        for field in ("requestRate", "tokensPerMinute", "tokenQuota"):
            value = settings(); del value["userLimits"][field]
            add("missing-limit-" + field, serverSettings=encoded(value))
        for field, invalid in (("requestRate", {}), ("tokensPerMinute", "0"), ("tokenQuota", {})):
            value = settings(); value["userLimits"][field] = invalid
            add("invalid-limit-" + field, serverSettings=encoded(value))
        add("other-user", claims={**claims, "oid": OTHER})
        add("revision", api="openai-inference-v1;rev=2")
        add("missing-product", product=None); add("wrong-product", product="existing-product")
        add("keyless-native-subscription", subscription=True)
        # Common Product checks remain mandatory above. These cases isolate the three API decisions
        # after successful common authentication, proving they trust its result without repeating Product checks.
        add("decision-product-null", decisionContext={"product": None})
        add("decision-product-different", decisionContext={"product": "existing-product"})
        # 共通認証後のヘッダー変更だけを再現し、HTTPの本人情報引継ぎを確認します。
        add("decision-key-header-removed", headers={"X-Gateway-Key": "SYNTHETIC_KEY", "Authorization": "Basic arbitrary"},
            subscription=True, decisionContext={"keyHeader": None}, httpDecisionOnly=True)
        add("decision-key-header-added", decisionContext={"keyHeader": "SYNTHETIC_LATER_HEADER"}, httpDecisionOnly=True)
        for marker in ("gatewayCommonAuthenticationValidated", "gatewayEntraAuthenticationValidated"):
            for state, value in (("missing", None), ("failed", False)):
                add("decision-" + marker + "-" + state, decisionContext={"product": None, marker: value})
        add("decision-subscription-mixed", decisionContext={"product": None, "subscription": True})
        add("decision-user-missing", decisionContext={"product": None, "userRaw": None})
        add("decision-user-malformed", decisionContext={"product": None, "userRaw": "{broken"})
        for name, tamper in (("caller", {"callerId": "user:untrusted"}),
                             ("allocation", {"allocationId": "user:untrusted"}),
                             ("principal", {"principalId": "invalid"}),
                             ("schema", {"schemaVersion": 2})):
            add("decision-user-" + name + "-changed", decisionContext={"product": None}, tamper=tamper)
        for name, value in (("tid", OTHER), ("oid", "invalid"), ("ver", "1.0"), ("scp", "Gateway.Invoke.Extra")):
            add("wrong-" + name, claims={**claims, name: value})
        for name in ("tid", "oid", "ver", "scp"):
            add("duplicate-" + name, claims={**claims, name: [claims[name], claims[name]]})
            missing = copy.deepcopy(claims); del missing[name]; add("missing-" + name, claims=missing)
            add("null-value-" + name, claims={**claims, name: None})
            add("empty-array-" + name, claims={**claims, name: []})
            add("null-array-" + name, nullClaimArrays=[name])
        add("zero-oid", claims={**claims, "oid": "00000000-0000-0000-0000-000000000000"})
        add("zero-tid", claims={**claims, "tid": "00000000-0000-0000-0000-000000000000"})
        add("invalid-tid", claims={**claims, "tid": "invalid"})
        for label, tenant in (("invalid", "invalid"), ("zero", "00000000-0000-0000-0000-000000000000")):
            configuration = settings(); configuration["tenantId"] = tenant
            add("invalid-named-tenant-" + label, serverSettings=encoded(configuration))
        for field in ("calls", "renewalPeriodSeconds", "tokensPerMinute", "tokens"):
            for label, number in (("out-of-range", 2147483648), ("overflow", 10**30)):
                configuration = settings()
                if field in ("calls", "renewalPeriodSeconds"): configuration["userLimits"]["requestRate"][field] = number
                elif field == "tokens": configuration["userLimits"]["tokenQuota"][field] = number
                else: configuration["userLimits"][field] = number
                add("invalid-named-" + field + "-" + label, serverSettings=encoded(configuration))
        for action in ("missing-jwt", "null-jwt", "null-claims"):
            add("fault-before-identity-" + action, fault={"stage": "gatewayEntraTokenValidationCompleted", "action": action})
        for action in ("missing-tenant", "wrong-limits-type"):
            add("fault-after-identity-" + action, fault={"stage": "gatewayEntraIdentityResult", "action": action})
        for action in ("missing-proof", "false-proof"):
            add("fault-identity-" + action, fault={"stage": "gatewayEntraIdentityResult", "action": action})
        for label, values in (("request", {"requestId": OTHER}), ("proof", {"proof": "untrusted"}),
                              ("schema", {"schemaVersion": 2}), ("principal", {"principalId": None}),
                              ("caller", {"callerId": "user:untrusted"}), ("client", {"clientId": "invalid"})):
            add("fault-identity-" + label, fault={"stage": "gatewayEntraIdentityResult", "action": "identity-tamper", "values": values})
            add("fault-failure-subject-" + label, limitsRaw="[]",
                fault={"stage": "gatewayEntraAuthenticationResult", "action": "identity-tamper", "values": values})
        for action in ("missing-proof", "false-proof", "key-mixed", "subscription-mixed", "saved-subscription-mixed"):
            add("fault-failure-subject-" + action, limitsRaw="[]", fault={"stage": "gatewayEntraAuthenticationResult", "action": action})
        add("fault-identity-malformed", fault={"stage": "gatewayEntraIdentityResult", "action": "identity-raw", "value": "{broken"})
        add("native-rejected", nativeAccepted=False)
        add("new-api-same-user", api="future-model-api")
        add("new-api-missing-product", api="future-model-api", product=None)
        add("new-api-wrong-product", api="future-model-api", product="existing-product")
        add("new-api-key", api="future-model-api", headers={"X-Gateway-Key": "SYNTHETIC_KEY", "Authorization": "Basic arbitrary"}, subscription=True)
        add("new-api-invalid-key-valid-bearer", api="future-model-api", headers={"X-Gateway-Key": "SYNTHETIC_KEY", "Authorization": "Bearer SYNTHETIC_TOKEN"})
        add("business-session-operation", path="/sessions"); add("business-foundry-services", backend="foundryServices")
        add("business-provider-auth", backendAuthentication="keyVaultHeader")
        add("no-auth", headers={}); add("basic", headers={"Authorization": "Basic arbitrary"})
        add("duplicate-auth", headers={"Authorization": ["Bearer one", "Bearer two"]})
        add("query-credential", query={"subscription-key": "SYNTHETIC_KEY"})
        for name, key, sub in (("key", "SYNTHETIC_KEY", True), ("invalid-key", "SYNTHETIC_KEY", False),
                               ("blank-key", " ", True), ("empty-key", "", True), ("no-key-values", [], True),
                               ("duplicate-key", ["one", "two"], True),
                               ("repeated-key-values", ["SYNTHETIC_KEY", "SYNTHETIC_KEY"], True)):
            add(name, headers={"X-Gateway-Key": key, "Authorization": "Basic arbitrary"}, subscription=sub)
        for name, product in (("key-product", "existing-product"), ("key-api-scope", None), ("key-all-api-scope", None), ("key-service-scope", None)):
            add(name, headers={"X-Gateway-Key": "SYNTHETIC_KEY", "Authorization": "Bearer arbitrary"}, subscription=True, product=product)
        add("key-duplicate-auth", headers={"X-Gateway-Key": "SYNTHETIC_KEY", "Authorization": ["Bearer one", "Basic two"]}, subscription=True)
        for name, subscription_id in (("blank-native-subscription", " "), ("empty-native-subscription", "")):
            add(name, headers={"X-Gateway-Key": "SYNTHETIC_KEY", "Authorization": "Bearer SYNTHETIC_TOKEN"}, subscription=True, subscriptionId=subscription_id)
        for name in ("invalid-key-fallback", "stopped-key-fallback", "out-of-scope-key-fallback"):
            add(name, headers={"X-Gateway-Key": "SYNTHETIC_KEY", "Authorization": "Bearer SYNTHETIC_TOKEN"})
        for name, key in (("blank-key-valid-bearer", " "), ("empty-key-valid-bearer", ""), ("duplicate-key-valid-bearer", ["one", "two"])):
            # Isolate header validation from missing subscription metadata: even a supplied native
            # subscription and valid Bearer must not rescue an empty or multi-value key header.
            add(name, headers={"X-Gateway-Key": key, "Authorization": "Bearer SYNTHETIC_TOKEN"}, subscription=True)
        add("invalid-key-valid-bearer", headers={"X-Gateway-Key": "SYNTHETIC_KEY", "Authorization": "Bearer SYNTHETIC_TOKEN"})
        add("drop-marker", dropMarker=True); add("tamper-caller", tamper={"callerId": "user:untrusted"})
        # 旧一致拒否対照は、profile直前だけ型として有効なsnapshotを変更する対照へ限定します。
        add("tamper-unlimited", profileSnapshotLimits={**LIMITS, "tokensPerMinute": None})
        add("profile-entra-marker-missing", profileEntraValidated=None)
        add("profile-entra-marker-failed", profileEntraValidated=False)
        add("profile-isolated-allowed-false", profileDecisionAllowed=False)
        cls.profile_decision_cases = []
        for label, changes in (
            ("missing", {"profileDecisionRaw": None}), ("malformed", {"profileDecisionRaw": "{broken"}),
            ("caller", {"profileDecisionPatch": {"callerId": "user:untrusted"}}),
            ("allocation", {"profileDecisionPatch": {"allocationId": "user:untrusted"}}),
        ):
            identifier = "profile-isolated-decision-" + label
            cls.profile_decision_cases.append(identifier)
            add(identifier, **changes)
        add("token-counter-decision-fields-removed", tokenCounterDecision={"allowed": None, "callerId": None, "allocationId": None})
        add("token-counter-other-person", tokenCounterDecision={"verifiedPrincipalId": OTHER})
        add("token-enabled-isolated-marker-missing", tokenEntraValidated=None)
        add("token-enabled-isolated-marker-false", tokenEntraValidated=False)
        add("token-enabled-isolated-allowed-false", tokenEnabledAllowed=False)
        add("token-enabled-isolated-original-missing", tokenUserLimitsMissing=True)
        add("token-enabled-isolated-original-mismatch", tokenUserLimits={**LIMITS, "tokensPerMinute": None})
        cls.token_operation_errors = {}
        cls.token_operation_controls = {}
        for caller, context in (("user", {}), ("key", {
            "headers": {"X-Gateway-Key": "SYNTHETIC_KEY", "Authorization": "Basic arbitrary"},
            "subscription": True,
        })):
            for exempt in (False, True):
                suffix = caller + ("-exempt" if exempt else "-counted")
                for label, value, exception in (
                    ("missing", None, "ArgumentNullException"), ("null", None, "InvalidCastException"),
                    ("object", {}, "InvalidCastException"), ("string", "responses-create", "InvalidCastException"),
                    ("integer", 42, "InvalidCastException"),
                ):
                    identifier = "token-operations-" + label + "-" + suffix
                    cls.token_operation_errors[identifier] = (caller, exception)
                    fault = {"tokenOperationListMissing": True} if label == "missing" else {"tokenOperationList": value}
                    add(identifier, **context, **fault, rateExempt=exempt)
                for label, operations, matches in (
                    ("empty", [], False), ("other", ["unrelated-operation"], False),
                    ("matching", ["unrelated-operation", "responses-create"], True),
                ):
                    identifier = "token-operations-" + label + "-" + suffix
                    cls.token_operation_controls[identifier] = (caller, matches and not exempt)
                    add(identifier, **context, tokenOperationList=operations, rateExempt=exempt)
        # 一覧faultは先行認証/社員profileを通過した場合だけ注入するため、正規の先行停止は変わりません。
        add("token-operations-native-stopped", nativeAccepted=False, tokenOperationList={})
        add("token-operations-profile-stopped", profileSnapshotLimits=None, tokenOperationList=None)
        add("profile-user-limits-missing", profileUserLimitsMissing=True)
        add("profile-original-limits-malformed", profileUserLimitsRaw="{broken")
        add("profile-original-limits-mismatch", profileOriginalLimits={**LIMITS, "tokensPerMinute": None})
        cls.profile_object_cases = {}
        cls.common_profile_settings_inputs = {}
        cls.profile_acquisition_cases = {"token-operations-profile-stopped": "NullReferenceException"}
        for label, limits in (
            ("null", None), ("array", []), ("empty", {}),
            ("extra", {**LIMITS, "unexpected": None}),
            ("missing-tpm", {"requestRate": LIMITS["requestRate"], "tokenQuota": LIMITS["tokenQuota"], "unexpected": None}),
            ("quota-period", {**LIMITS, "tokenQuota": {**LIMITS["tokenQuota"], "period": "monthly"}}),
        ):
            identifier = "profile-structure-" + label
            if isinstance(limits, dict):
                cls.profile_object_cases[identifier] = limits
                cls.common_profile_settings_inputs["common-" + identifier] = limits
                add(identifier, profileSnapshotLimits=limits, restoreProfileAfterRead=True)
            else:
                cls.profile_acquisition_cases[identifier] = "NullReferenceException"
                add(identifier, profileSnapshotLimits=limits)
        for label, fault, exception in (
            ("user-missing", {"profileUserRaw": None}, "KeyNotFoundException"),
            ("user-malformed", {"profileUserRaw": "{broken"}, "JsonReaderException"),
            ("limits-missing", {"profileSnapshotLimitsMissing": True}, "NullReferenceException"),
            ("limits-scalar", {"profileSnapshotLimits": 42}, "NullReferenceException"),
        ):
            identifier = "profile-acquisition-" + label
            cls.profile_acquisition_cases[identifier] = exception
            add(identifier, **fault)
        for label, limits in (
            ("tpm-missing", {key: value for key, value in LIMITS.items() if key != "tokensPerMinute"}),
            ("quota-missing", {key: value for key, value in LIMITS.items() if key != "tokenQuota"}),
            ("tpm-type", {**LIMITS, "tokensPerMinute": 1.5}),
            ("quota-type", {**LIMITS, "tokenQuota": {**LIMITS["tokenQuota"], "tokens": 1.5}}),
            ("tpm-range", {**LIMITS, "tokensPerMinute": 0}),
            ("quota-range", {**LIMITS, "tokenQuota": {**LIMITS["tokenQuota"], "tokens": 0}}),
        ):
            identifier = "profile-token-limits-" + label
            cls.profile_object_cases[identifier] = limits
            cls.common_profile_settings_inputs["common-" + identifier] = limits
            add(identifier, profileLimits=limits, restoreProfileAfterRead=True)
        cls.profile_rate_boundaries = {}
        for label, calls, period, allowed in (('minimum', 1, 1, True), ('maximum', 1000000, 300, True),
                                              ('calls-over', 1000001, 50, False), ('period-over', 11, 301, False)):
            identifier = "profile-rate-range-" + label
            cls.profile_rate_boundaries[identifier] = (calls, period, allowed)
            rate = {"calls": calls, "renewalPeriodSeconds": period}
            add(identifier, profileRequestRate=rate, rateExempt=True, restoreProfileAfterRead=not allowed)
            if not allowed:
                cls.common_profile_settings_inputs["common-" + identifier] = {**LIMITS, "requestRate": rate}
        # 同じ不正値を正式なNamed Limitsとして渡すと、実common認証がprofileより前に500で停止します。
        for identifier, limits in cls.common_profile_settings_inputs.items():
            add(identifier, limitsRaw=json.dumps(limits))
        with tempfile.TemporaryDirectory(prefix="core-entra-auth-") as folder:
            p = Path(folder)
            (p / "fixture.cs").write_text(source, encoding="utf-8")
            (p / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
            (p / "run.ps1").write_text("$ErrorActionPreference='Stop'\n$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)\nAdd-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701,1702'\n[EntraFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))\n", encoding="utf-8")
            r = subprocess.run([PWSH, "-NoProfile", "-File", str(p / "run.ps1")], capture_output=True, text=True, timeout=60)
            if r.returncode: raise AssertionError(r.stderr)
            if "SYNTHETIC_TOKEN" in r.stdout: raise AssertionError("Synthetic credential reached output")
            cls.results = json.loads(r.stdout)

    def test_employee_has_verified_identity_finite_limits_and_safe_audit(self):
        row = self.results["employee"]; caller = "user:" + TENANT + ":" + PERSON
        self.assertNotIn("decisionFailure", row)
        self.assertEqual(row["status"], 200); self.assertEqual(row["decision"]["callerId"], caller)
        self.assertEqual(row["decision"]["allocationId"], caller); self.assertEqual(row["profile"], LIMITS)
        self.assertEqual(row["audit"]["verifiedPrincipalId"], PERSON)
        self.assertEqual(row["audit"]["verifiedTenantId"], TENANT); self.assertEqual(row["audit"]["verifiedClientId"], CLIENT)
        self.assertEqual(row["audit"]["parentLinkStatus"], "candidate")
        self.assertEqual(row["audit"]["parentClaimActorId"], caller)
        self.assertNotIn("subscriptionId", row["audit"])
        self.assertTrue(row["protocol"]["allowed"]); self.assertTrue(row["mcp"]["allowed"])

    def test_user_counters_share_revision_and_distinguish_other_people(self):
        first = self.results["employee"]
        self.assertEqual(first["tokenKey"], "core-tokens-v1:user:" + TENANT + ":" + PERSON)
        self.assertEqual(first["rate"]["counterKey"], "ai-gateway-rate-v3:user:" + base64.b64encode((TENANT + ":" + PERSON).encode()).decode())
        for name in ("other-client-same-user", "arbitrary-client-same-user", "other-api-same-user", "new-api-same-user", "other-model-same-user", "revision"):
            self.assertEqual(self.results[name]["rate"]["counterKey"], first["rate"]["counterKey"])
            self.assertEqual(self.results[name]["tokenKey"], first["tokenKey"])
        self.assertNotEqual(self.results["other-user"]["tokenKey"], first["tokenKey"])
        self.assertEqual(self.results["other-user"]["tokenKey"], "core-tokens-v1:user:" + TENANT + ":" + OTHER)
        other_counter = self.results["other-user"]["rate"]["counterKey"]
        self.assertEqual(other_counter, "ai-gateway-rate-v3:user:" + base64.b64encode((TENANT + ":" + OTHER).encode()).decode())
        self.assertNotEqual(other_counter, first["rate"]["counterKey"])
        normalized = self.results["canonical-named-tenant"]
        self.assertEqual(normalized["status"], 200)
        self.assertEqual(normalized["decision"]["verifiedTenantId"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        self.assertEqual(normalized["rate"]["counterKey"], "ai-gateway-rate-v3:user:" + base64.b64encode(
            ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa:" + PERSON).encode()).decode())

    def test_employee_profile_uses_the_verified_result_without_rechecking_allowed_or_success_marker(self):
        normal = self.results["employee"]
        for name in ("profile-entra-marker-missing", "profile-entra-marker-failed", "profile-isolated-allowed-false"):
            row = self.results[name]
            with self.subTest(isolated_before_profile=name):
                self.assertEqual(row["status"], 200)
                self.assertTrue(row["commonValidated"])
                for api in ("decision", "protocol", "mcp"):
                    self.assertTrue(row[api]["allowed"])
                self.assertFalse(row.get("limitsRejected", False))
                self.assertEqual(row["profile"], LIMITS)
                self.assertEqual(row["profileEntraReads"], 0)
                self.assertEqual(row["rate"], normal["rate"])
                self.assertTrue(row["tokenEnabled"])
                self.assertEqual(row["tokenKey"], normal["tokenKey"])
                self.assertEqual(row["audit"]["verifiedTenantId"], normal["audit"]["verifiedTenantId"])
                self.assertEqual(row["audit"]["verifiedPrincipalId"], normal["audit"]["verifiedPrincipalId"])
        # 元の認証や引渡しを省かず、実UserProfileを呼んだ全対照で成功印への非アクセスを確認します。
        for name, row in self.results.items():
            if "profileEntraReads" in row:
                with self.subTest(profile_invocation=name):
                    self.assertEqual(row["profileEntraReads"], 0)

    def test_employee_profile_reads_verified_limits_without_reopening_the_decision(self):
        normal = self.results["employee"]
        for identifier in self.profile_decision_cases:
            row = self.results[identifier]
            with self.subTest(isolated_before_profile=identifier):
                self.assertEqual(row["status"], 200)
                self.assertTrue(row["commonValidated"])
                for api in ("decision", "protocol", "mcp"):
                    self.assertEqual(row[api], normal[api])
                self.assertFalse(row.get("limitsRejected", False))
                self.assertEqual(row["profile"], LIMITS)
                self.assertEqual(row["profileDecisionReads"], 0)
                self.assertEqual(row["profileEntraReads"], 0)
                # 孤立対照の復元後は、確認済みの同じ社員へ回数/トークン枠と監査を引き継ぎます。
                self.assertEqual(row["rate"], normal["rate"])
                self.assertTrue(row["tokenEnabled"])
                self.assertEqual(row["tokenKey"], normal["tokenKey"])
                self.assertEqual(row["audit"]["verifiedTenantId"], normal["audit"]["verifiedTenantId"])
                self.assertEqual(row["audit"]["verifiedPrincipalId"], normal["audit"]["verifiedPrincipalId"])
        # 人工JObjectの取得と取得失敗の停止も、不要なdecisionを読み直しません。
        for identifier, row in self.results.items():
            if "profileDecisionReads" in row:
                with self.subTest(profile_invocation=identifier):
                    self.assertEqual(row["profileDecisionReads"], 0)

    def test_token_counter_uses_handed_off_identity_without_reopening_employee_json(self):
        normal_key = self.results["employee"]["tokenKey"]
        self.assertEqual(self.results["token-counter-decision-fields-removed"]["tokenKey"], normal_key)
        self.assertEqual(self.results["token-counter-other-person"]["tokenKey"], self.results["other-user"]["tokenKey"])
        for name, row in self.results.items():
            if "tokenKey" in row:
                with self.subTest(case=name):
                    self.assertEqual(row["tokenKeyUserReads"], 0)
                    self.assertEqual(row["tokenKeyEntraReads"], 0)

    def test_token_enabled_uses_verified_profile_without_rechecking_prior_authentication_or_limits(self):
        for name in ("token-enabled-isolated-marker-missing", "token-enabled-isolated-marker-false",
                     "token-enabled-isolated-allowed-false", "token-enabled-isolated-original-missing",
                     "token-enabled-isolated-original-mismatch"):
            row = self.results[name]
            with self.subTest(isolated_after_profile=name):
                self.assertEqual(row["status"], 200)
                self.assertTrue(row["commonValidated"])
                for api in ("decision", "protocol", "mcp"):
                    self.assertTrue(row[api]["allowed"])
                self.assertEqual(row["profile"], LIMITS)
                self.assertTrue(row["tokenEnabled"])
                self.assertFalse(row.get("limitsRejected", False))
                self.assertEqual(row["tokenKey"], self.results["employee"]["tokenKey"])
        for name, row in self.results.items():
            if "tokenEnabled" in row:
                with self.subTest(case=name):
                    self.assertEqual(row["tokenEnabledEntraReads"], 0)
                    self.assertEqual(row["tokenEnabledUserLimitsReads"], 0)

    def test_employee_profile_uses_the_verified_snapshot_without_reopening_original_limits(self):
        normal = self.results["employee"]
        for identifier in ("profile-user-limits-missing", "profile-original-limits-malformed", "profile-original-limits-mismatch", "tamper-unlimited"):
            row = self.results[identifier]
            with self.subTest(isolated_before_profile=identifier):
                self.assertEqual(row["status"], 200)
                self.assertTrue(row["commonValidated"])
                for api in ("decision", "protocol", "mcp"):
                    self.assertEqual(row[api], normal[api])
                self.assertFalse(row.get("limitsRejected", False))
                expected = {**LIMITS, "tokensPerMinute": None} if identifier == "tamper-unlimited" else LIMITS
                self.assertEqual(row["profile"], expected)
                self.assertEqual(row["profileOriginalLimitsReads"], 0)
                self.assertEqual(row["profileEntraReads"], 0)
                self.assertEqual(row["profileDecisionReads"], 0)
                self.assertEqual(row["rate"], normal["rate"])
                self.assertTrue(row["tokenEnabled"])
                self.assertEqual(row["tokenKey"], normal["tokenKey"])
                self.assertEqual(row["audit"]["verifiedPrincipalId"], normal["audit"]["verifiedPrincipalId"])
        # limitsを取得できない停止も含め、元設定への再アクセスを各実UserProfile呼出しで検出します。
        for identifier, row in self.results.items():
            if "profileOriginalLimitsReads" in row:
                with self.subTest(profile_invocation=identifier):
                    self.assertEqual(row["profileOriginalLimitsReads"], 0)

    def test_employee_profile_returns_the_obtained_object_without_revalidating_settings(self):
        normal = self.results["employee"]
        for identifier, expected in self.profile_object_cases.items():
            row = self.results[identifier]
            with self.subTest(isolated_after_authentication=identifier):
                self.assertEqual(row["status"], 200)
                self.assertTrue(row["commonValidated"])
                for api in ("decision", "protocol", "mcp"):
                    self.assertEqual(row[api], normal[api])
                self.assertFalse(row.get("limitsRejected", False))
                self.assertNotIn("profileReadError", row)
                self.assertEqual(row["profile"], expected)
                # 人工的な不正JObjectの返却だけを観測し、計数には正式な正常profileを渡します。
                self.assertEqual(row["countedProfile"], LIMITS)
                self.assertEqual(row["rate"], normal["rate"])
                self.assertTrue(row["tokenEnabled"])
                self.assertEqual(row["tokenKey"], normal["tokenKey"])
                for reads in ("profileEntraReads", "profileDecisionReads", "profileOriginalLimitsReads"):
                    self.assertEqual(row[reads], 0)

    def test_employee_profile_acquisition_errors_stop_before_rate_and_counter(self):
        for name, exception in self.profile_acquisition_cases.items():
            row = self.results[name]
            with self.subTest(isolated_after_authentication=name):
                self.assertEqual(row["status"], 200)
                self.assertTrue(row["commonValidated"])
                for api in ("decision", "protocol", "mcp"):
                    self.assertTrue(row[api]["allowed"])
                self.assertTrue(row["limitsRejected"])
                self.assertEqual(row["profileReadError"], exception)
                for reads in ("profileEntraReads", "profileDecisionReads", "profileOriginalLimitsReads"):
                    self.assertEqual(row[reads], 0)
                for stage in ("profile", "countedProfile", "rate", "tokenEnabled", "tokenKey"):
                    self.assertNotIn(stage, row)

    def test_common_authentication_rejects_the_same_invalid_settings_before_profile_acquisition(self):
        # profile直前の人工snapshot変更と違い、正式な会社設定の不備は先行commonの500で停止します。
        for identifier in self.common_profile_settings_inputs:
            row = self.results[identifier]
            with self.subTest(formal_configuration=identifier):
                self.assertEqual(row["status"], 500)
                self.assertFalse(row["commonValidated"])
                self.assertFalse(row["entraValidated"])
                self.assertFalse(row["hasUserAuthentication"])
                self.assertEqual(row["failureAudit"]["code"], "INVALID_GATEWAY_CONFIGURATION")
                self.assertEqual(row["failureAudit"]["authenticatedSubject"]["principalId"], PERSON)
                for stage in ("decision", "profile", "profileReadError", "profileOriginalLimitsReads", "rate", "tokenEnabled", "tokenKey"):
                    self.assertNotIn(stage, row)

    def test_token_operation_list_errors_propagate_after_authentication_even_when_exempt(self):
        # 欠落のcast結果は.NET nullなのでAnyで例外になり、JSON null/別型はJArrayへのcastで失敗します。
        # 免除判定がAnyより先に短絡すると欠落だけfalseへ戻るため、免除あり/なし双方を対照にします。
        for identifier, (caller, exception) in self.token_operation_errors.items():
            row = self.results[identifier]
            with self.subTest(case=identifier):
                self.assertEqual(row["status"], 200)  # 共通認証の結果。TokenEnabled用の公開HTTP状態は作りません。
                self.assertTrue(row["commonValidated"])
                for api in ("decision", "protocol", "mcp"):
                    self.assertTrue(row[api]["allowed"])
                baseline = self.results["employee" if caller == "user" else "key"]
                self.assertEqual(row["profile"], baseline["profile"])
                self.assertEqual(row["rate"]["counterKey"], baseline["rate"]["counterKey"])
                self.assertFalse(row.get("limitsRejected", False))
                self.assertEqual(row["tokenEnabledError"], exception)
                self.assertEqual(row["tokenEnabledEntraReads"], 0)
                self.assertEqual(row["tokenEnabledUserLimitsReads"], 0)
                for stage in ("tokenEnabled", "tokenKey", "audit"):
                    self.assertNotIn(stage, row)

    def test_token_operation_lists_keep_empty_nonmatching_and_exempt_enforcement_disabled(self):
        for identifier, (caller, enabled) in self.token_operation_controls.items():
            row = self.results[identifier]
            with self.subTest(case=identifier):
                baseline = self.results["employee" if caller == "user" else "key"]
                self.assertEqual(row["status"], 200)
                self.assertTrue(row["commonValidated"])
                self.assertEqual(row["decision"], baseline["decision"])
                self.assertEqual(row["profile"], baseline["profile"])
                self.assertFalse(row.get("limitsRejected", False))
                self.assertNotIn("tokenEnabledError", row)
                self.assertEqual(row["tokenEnabled"], enabled)
                self.assertEqual(row["tokenEnabledEntraReads"], 0)
                self.assertEqual(row["tokenEnabledUserLimitsReads"], 0)
                if enabled:
                    self.assertEqual(row["tokenKey"], baseline["tokenKey"])
                else:
                    self.assertNotIn("tokenKey", row)
                self.assertIn("audit", row)

    def test_prior_authentication_and_profile_failures_stop_before_token_operation_faults(self):
        native = self.results["token-operations-native-stopped"]
        self.assertEqual(native["status"], 401)
        self.assertFalse(native["nativeOutput"])
        for stage in ("decision", "profile", "rate", "tokenEnabled", "tokenEnabledError", "tokenKey"):
            self.assertNotIn(stage, native)
        profile = self.results["token-operations-profile-stopped"]
        self.assertEqual(profile["status"], 200)
        self.assertTrue(profile["commonValidated"])
        self.assertTrue(profile["decision"]["allowed"])
        self.assertTrue(profile["limitsRejected"])
        self.assertEqual(profile["profileReadError"], "NullReferenceException")
        for stage in ("profile", "rate", "tokenEnabled", "tokenEnabledError", "tokenKey"):
            self.assertNotIn(stage, profile)

    def test_employee_profile_returns_rate_values_and_keeps_cleanup_exemption(self):
        for identifier, (calls, period, allowed) in self.profile_rate_boundaries.items():
            row = self.results[identifier]
            with self.subTest(case=identifier):
                self.assertEqual(row["status"], 200)
                self.assertTrue(row["commonValidated"])
                self.assertTrue(row["entraValidated"])
                for api in ("decision", "protocol", "mcp"):
                    self.assertTrue(row[api]["allowed"])
                self.assertFalse(row.get("limitsRejected", False))
                self.assertNotIn("profileReadError", row)
                self.assertEqual(row["profile"], {**LIMITS, "requestRate": {"calls": calls, "renewalPeriodSeconds": period}})
                if not allowed:
                    self.assertEqual(row["countedProfile"], LIMITS)
                expected_rate = (calls, period) if allowed else (LIMITS["requestRate"]["calls"], LIMITS["requestRate"]["renewalPeriodSeconds"])
                self.assertEqual((row["rate"]["calls"], row["rate"]["renewalPeriodSeconds"]), expected_rate)
                self.assertFalse(row["rate"]["enabled"])
                self.assertFalse(row["tokenEnabled"])
                self.assertNotIn("tokenKey", row)
                self.assertEqual(row["rate"]["counterKey"], self.results["employee"]["rate"]["counterKey"])

    def test_a_new_api_with_the_owned_product_uses_the_same_verified_bearer_identity(self):
        row = self.results["new-api-same-user"]
        self.assertEqual(row["status"], 200)
        self.assertTrue(row["nativeOutput"])
        self.assertTrue(row["decision"]["allowed"])
        self.assertEqual(row["decision"]["method"], "user")
        self.assertEqual(row["decision"]["verifiedPrincipalId"], PERSON)
        self.assertTrue(row["protocol"]["allowed"])
        self.assertTrue(row["mcp"]["allowed"])

    def test_token_limits_require_current_control_settings_without_legacy_fallback(self):
        for case in ('employee-legacy-mixed', *('employee-'+suffix+'-new-control' for suffix in ('malformed', 'null', 'blank', 'array', 'scalar'))):
            invalid=self.results[case]
            self.assertTrue(invalid['decision']['allowed'])
            self.assertEqual(invalid['profile'], LIMITS)
            self.assertIn('rate', invalid)
            self.assertFalse(invalid.get('limitsRejected', False))
            self.assertIn('tokenEnabledError', invalid)
            self.assertNotIn('tokenEnabled',invalid)
            self.assertNotIn('tokenKey',invalid)

    def test_client_claim_is_optional_audit_data_and_does_not_change_user_authorization(self):
        first = self.results["employee"]
        self.assertEqual(self.results["arbitrary-client-same-user"]["audit"]["verifiedClientId"], OTHER)
        for name in ("optional-azp-missing", "optional-azp-duplicate", "optional-azp-invalid", "optional-azp-zero", "optional-azp-empty"):
            row = self.results[name]
            with self.subTest(case=name):
                self.assertEqual(row["status"], 200)
                self.assertTrue(row["decision"]["allowed"])
                self.assertEqual(row["audit"]["verifiedPrincipalId"], PERSON)
                self.assertEqual(row["audit"].get("verifiedClientId", ""), "")
                self.assertEqual(row["tokenKey"], first["tokenKey"])

    def test_common_bearer_identity_applies_before_backend_and_session_specific_constraints(self):
        for name in ("business-session-operation", "business-foundry-services", "business-provider-auth"):
            row = self.results[name]
            with self.subTest(case=name):
                self.assertEqual(row["status"], 200)
                self.assertTrue(row["decision"]["allowed"])
                self.assertEqual(row["decision"]["verifiedPrincipalId"], PERSON)

    def test_key_precedence_and_legacy_counter_are_preserved(self):
        key = self.results["key"]
        self.assertEqual(key["status"], 200); self.assertFalse(key["nativeOutput"])
        self.assertEqual(key["decision"]["method"], "subscriptionKey")
        self.assertEqual(key["tokenKey"], "core-tokens-v1:subscription-a")
        self.assertEqual(key["rate"]["counterKey"], "ai-gateway-rate-v3:subscriptionKey:" + base64.b64encode(b"subscription-a").decode())
        for name in ("key", "key-product", "key-api-scope", "key-all-api-scope", "key-service-scope", "key-duplicate-auth", "new-api-key"):
            self.assertEqual(self.results[name]["status"], 200)
            self.assertFalse(self.results[name]["nativeOutput"])
            for api in ("decision", "protocol", "mcp"):
                with self.subTest(case=name, api=api):
                    self.assertTrue(self.results[name][api]["allowed"])
                    self.assertEqual(self.results[name][api]["method"], "subscriptionKey")
                    self.assertEqual(self.results[name][api]["callerId"], "subscription:subscription-a")
        for name in ("invalid-key", "blank-key", "empty-key", "no-key-values", "duplicate-key", "repeated-key-values",
                     "invalid-key-valid-bearer", "blank-native-subscription", "empty-native-subscription",
                     "invalid-key-fallback", "stopped-key-fallback", "out-of-scope-key-fallback",
                     "blank-key-valid-bearer", "empty-key-valid-bearer", "duplicate-key-valid-bearer", "new-api-invalid-key-valid-bearer"):
            self.assertEqual(self.results[name]["status"], 401); self.assertFalse(self.results[name]["nativeOutput"])
            for stage in ("decision", "protocol", "mcp", "profile", "rate", "tokenEnabled", "tokenKey"):
                self.assertNotIn(stage, self.results[name], "Common authentication must reject before API decisions and limits run")

    def test_bearer_requires_keyless_owned_open_product_context(self):
        for name in ("missing-product", "wrong-product", "keyless-native-subscription", "new-api-missing-product", "new-api-wrong-product"):
            self.assertEqual(self.results[name]["status"], 401)
            self.assertFalse(self.results[name]["nativeOutput"])

    def test_api_decisions_use_verified_common_identity_without_repeating_product_checks(self):
        baseline = self.results["employee"]
        for name in ("decision-product-null", "decision-product-different"):
            row = self.results[name]
            with self.subTest(case=name):
                self.assertEqual(row["status"], 200)
                self.assertTrue(row["nativeOutput"])
                for api in ("decision", "protocol", "mcp"):
                    self.assertEqual(row[api], baseline[api])
                    self.assertTrue(row[api]["allowed"])
                    self.assertEqual(row[api]["method"], "user")
                self.assertEqual(row["profile"], baseline["profile"])
                self.assertEqual(row["rate"], baseline["rate"])
                self.assertEqual(row["tokenKey"], baseline["tokenKey"])
                self.assertEqual(row["audit"]["authenticatedSubject"], baseline["audit"]["authenticatedSubject"])
                self.assertEqual(row["audit"]["authenticatedSubject"]["status"], "verified")
                self.assertEqual(row["audit"]["authorization"], "allowed")
                self.assertNotIn("subscriptionId", row["audit"])
        for name, baseline_name in (("decision-key-header-removed", "key"),
                                    ("decision-key-header-added", "employee")):
            row = self.results[name]
            with self.subTest(case=name):
                self.assertEqual(row["status"], 200)
                self.assertEqual(row["decision"], self.results[baseline_name]["decision"])
                self.assertNotIn("protocol", row)
                self.assertNotIn("mcp", row)

    def test_all_api_decisions_require_entra_success_and_inherit_verified_identity(self):
        rejected = [
            "decision-gatewayEntraAuthenticationValidated-" + state
            for state in ("missing", "failed")
        ]
        rejected += ["decision-user-missing", "decision-user-malformed"]
        for name in rejected:
            row = self.results[name]
            with self.subTest(case=name):
                self.assertEqual(row["status"], 200, "The isolated decision fixture starts from successful common authentication")
                self.assertEqual(row["decision"], {"allowed": False})
                for api in ("decision", "protocol", "mcp"):
                    self.assertFalse(row[api]["allowed"])
                    failure = row[api + "Failure"]
                    with self.subTest(api=api):
                        self.assertEqual(failure["status"], 500)
                        self.assertEqual(self.decision_failure_reasons[api], "Gateway processing failed")
                        self.assertEqual(failure["auditCount"], 1)
                        self.assertEqual(failure["audit"]["phase"], "policy-error")
                        self.assertEqual(failure["audit"]["code"], "AUTHENTICATION_PROCESSING_ERROR")
                        self.assertEqual(failure["audit"]["status"], 500)
                        self.assertEqual(failure["headers"]["apim-request-id"], failure["audit"]["requestId"])
                        self.assertEqual(failure["headers"]["Cache-Control"], "no-store")
                        for detail in ("{broken", "JsonReaderException", "KeyNotFoundException"):
                            self.assertNotIn(detail, json.dumps(failure))
                for stage in ("profile", "rate", "tokenEnabled", "tokenKey"):
                    self.assertNotIn(stage, row)
        # 共通処理が本人情報と呼出者・利用枠を照合した後、この単体fixtureだけが値を変更します。
        # 全APIは再照合せずJSONの値を引き継ぎます。後段の利用枠・監査まで通ることは意味しません。
        for name, field, value in (("decision-user-caller-changed", "callerId", "user:untrusted"),
                                   ("decision-user-allocation-changed", "allocationId", "user:untrusted"),
                                   ("decision-user-principal-changed", "verifiedPrincipalId", "invalid"),
                                   ("decision-user-schema-changed", None, None),
                                   ("tamper-caller", "callerId", "user:untrusted")):
            row = self.results[name]
            expected = copy.deepcopy(self.results["employee"]["decision"])
            if field is not None:
                expected[field] = value
            with self.subTest(changed_identity=name):
                self.assertEqual(row["decision"], expected)
                for api in ("protocol", "mcp"):
                    self.assertEqual(row[api], expected, api)
        # この単体fixtureは先行部品を呼ばず、common印を認証後に変更します。
        # 実際のAPI順序では、common印の欠落・falseはcore-api-request-preparationが503で停止します。
        # 全APIのDecisionはその確認を繰り返さず、Entra印を確認して共通検証済み本人情報を引き継ぎます。
        for state in ("missing", "failed"):
            row = self.results["decision-gatewayCommonAuthenticationValidated-" + state]
            with self.subTest(common_marker=state):
                self.assertEqual(row["decision"], self.results["employee"]["decision"])
                for api in ("protocol", "mcp"):
                    self.assertEqual(row[api], self.results["employee"]["decision"], api)
        # APIMの購読は通常書換えできません。この単体fixtureは認証後に人工的に差し替えます。
        # 全APIの引渡しは購読主体を選び、実際のBearer入口での購読混在は先行する共通認証が拒否します。
        mixed = self.results["decision-subscription-mixed"]
        self.assertEqual(mixed["decision"], {"allowed": True, "method": "subscriptionKey",
                                           "callerId": "subscription:unexpected-subscription"})
        for api in ("protocol", "mcp"):
            self.assertEqual(mixed[api], mixed["decision"], api)

    def test_configured_null_limits_disable_only_the_selected_enforcement(self):
        for mask in range(8):
            row = self.results["nullable-" + str(mask)]
            self.assertEqual(row["status"], 200)
            self.assertFalse(row.get("limitsRejected", False))
            self.assertEqual(row["rate"]["enabled"], not bool(mask & 1))
            self.assertEqual(row["tokenEnabled"], (mask & 6) != 6)
            if row["tokenEnabled"]:
                self.assertEqual(row["tokenKey"], self.results["employee"]["tokenKey"])
            else:
                self.assertNotIn("tokenKey", row)
            for index, field in enumerate(("requestRate", "tokensPerMinute", "tokenQuota")):
                self.assertEqual(row["profile"][field], None if mask & (1 << index) else LIMITS[field])
            self.assertEqual(row["audit"]["verifiedPrincipalId"], PERSON)
            self.assertEqual(row["audit"]["usageStatus"], "native-log-join-required")
        row = self.results["high-request-rate-only"]
        self.assertEqual(row["status"], 200); self.assertTrue(row["rate"]["enabled"])
        self.assertEqual(row["rate"]["calls"], 1000); self.assertEqual(row["rate"]["renewalPeriodSeconds"], 60)
        self.assertFalse(row["tokenEnabled"])
        for field in ("requestRate", "tokensPerMinute", "tokenQuota"):
            self.assertEqual(self.results["invalid-limit-" + field]["status"], 500)
        for label in ("malformed", "array", "scalar", "null", "quoted"):
            self.assertEqual(self.results["invalid-named-limits-" + label]["status"], 500)

    def test_invalid_context_and_unsupported_routes_fail_closed(self):
        for name, row in self.results.items():
            if (name.startswith(("wrong-", "missing-", "duplicate-")) and not name.startswith("missing-limit-")) or name in ("zero-oid", "native-rejected", "no-auth", "basic", "query-credential"):
                with self.subTest(case=name): self.assertIn(row["status"], (401, 403))
        self.assertFalse(self.results["drop-marker"]["decision"]["allowed"])

    def test_native_required_claims_reject_values_before_the_identity_expression(self):
        for name in ("wrong-ver", "wrong-scp", "missing-ver", "missing-scp", "configured-scope-rejected"):
            row = self.results[name]
            with self.subTest(case=name):
                self.assertEqual(row["status"], 401)
                self.assertFalse(row["nativeOutput"])
                self.assertNotIn("tokenEnabled", row)
                self.assertNotIn("tokenKey", row)
        accepted = self.results["configured-scope-accepted"]
        self.assertEqual(accepted["status"], 200)
        self.assertTrue(accepted["nativeOutput"])
        self.assertTrue(accepted["decision"]["allowed"])

    def test_identity_expression_retains_single_value_and_principal_checks(self):
        for name in ("duplicate-ver", "duplicate-scp", "duplicate-tid", "duplicate-oid",
                     "wrong-tid", "wrong-oid", "missing-tid", "missing-oid", "zero-oid"):
            row = self.results[name]
            with self.subTest(case=name):
                self.assertEqual(row["status"], 401)
                self.assertTrue(row["nativeOutput"])

    def test_null_claim_values_arrays_and_invalid_principal_are_authentication_failures(self):
        for name, row in self.results.items():
            if name.startswith(("null-value-", "null-array-", "empty-array-")) or name in ("zero-tid", "invalid-tid"):
                with self.subTest(case=name):
                    self.assertEqual(row["status"], 401)
                    self.assertFalse(row["commonValidated"])
                    self.assertFalse(row["entraValidated"])
                    self.assertFalse(row["hasUserAuthentication"])

    def test_company_setting_failures_are_500_and_keep_the_verified_actor_for_audit(self):
        for name, row in self.results.items():
            if name.startswith(("invalid-named-", "invalid-limit-", "missing-limit-")):
                with self.subTest(case=name):
                    self.assertEqual(row["status"], 500)
                    self.assertEqual(row["failureAudit"]["code"], "INVALID_GATEWAY_CONFIGURATION")
                    self.assertEqual(row["failureAudit"]["authenticatedSubject"]["principalId"], PERSON)
                    self.assertEqual(row["failureAudit"]["authenticatedSubject"]["status"], "verified")
                    self.assertEqual(row["failureAudit"]["authorization"], "unconfirmed")

    def test_unexpected_faults_do_not_forge_authentication_success(self):
        for name, row in self.results.items():
            if name.startswith(("fault-before-identity-", "fault-after-identity-", "fault-identity-")):
                with self.subTest(case=name):
                    self.assertEqual(row["status"], 500)
                    self.assertEqual(row["failureAudit"]["code"], "AUTHENTICATION_PROCESSING_ERROR")
                    subject = row["failureAudit"]["authenticatedSubject"]
                    expected = "unverified" if name.startswith("fault-before-identity-") else "verified" if name.startswith("fault-after-identity-") else "inconsistent"
                    self.assertEqual(subject["status"], expected)
                    if expected != "verified": self.assertEqual(set(subject), {"status"})

    def test_failure_subject_rejects_mismatched_proof_identity_and_key_subscription_mix(self):
        for name, row in self.results.items():
            if name.startswith("fault-failure-subject-"):
                with self.subTest(case=name):
                    self.assertEqual(row["status"], 500)
                    self.assertEqual(row["failureAudit"]["code"], "INVALID_GATEWAY_CONFIGURATION")
                    self.assertEqual(row["failureAudit"]["authenticatedSubject"], {"status": "inconsistent"})

    def test_errors_keep_request_correlation_and_no_store_without_a_500_login_challenge(self):
        for name, row in self.results.items():
            if "failureAudit" not in row: continue
            with self.subTest(case=name):
                self.assertEqual(row["headers"]["Cache-Control"], "no-store")
                self.assertEqual(row["headers"]["apim-request-id"], row["failureAudit"]["requestId"])
                self.assertFalse(row["commonValidated"])
                self.assertFalse(row["entraValidated"])
                self.assertFalse(row["hasUserAuthentication"])
                self.assertNotIn("decision", row)
                if row["status"] == 401 and row["bearerRequest"]: self.assertEqual(row["headers"]["WWW-Authenticate"], "Bearer")
                else: self.assertNotIn("WWW-Authenticate", row["headers"])

    def test_normal_user_authentication_retains_the_exact_seven_field_contract(self):
        user = self.results["employee"]["userAuthentication"]
        self.assertEqual(set(user), {"schemaVersion", "callerId", "allocationId", "tenantId", "principalId", "clientId", "limits"})
        self.assertEqual(user["schemaVersion"], 1)
        self.assertEqual(user["limits"], LIMITS)
        self.assertTrue(self.results["employee"]["commonValidated"])
        self.assertTrue(self.results["employee"]["entraValidated"])


if __name__ == "__main__": unittest.main()
