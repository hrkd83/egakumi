"""接続ログインの限定例外を実C#で確認し、通常APIへの拡大と変数上書きを拒否する。"""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from deployment.helpers.apim_authentication import render_service_policy, validate_common_authentication_policy
from deployment.helpers.apim_browser_exception import (
    BROWSER_EXCEPTION_ID, BROWSER_EXCEPTION_VARIABLE,
    expand_browser_exception_fragment, render_browser_exception_fragment,
)
from deployment.helpers.apim_common_authentication import render_common_authentication_fragments
from test_audit_fragment_publication import audit_graph
from test_common_authentication import CSHARP, PWSH, compile_inbound
from test_credential_login import settings as login_settings
from test_entra_gateway_authentication import settings as entra_settings, policy_root
from test_entra_publication import APIM, GLOBAL, source_fixture
import entra_publication as publication


INCLUDE = '<include-fragment id="core-credential-browser-exception" fragment-id="core-credential-browser-exception" />'
SERVICE = '<policies><inbound>' + INCLUDE + '</inbound></policies>'
ROUTES = {"launch": ("GET", "launch"), "launch-post": ("POST", "launch"),
          "callback": ("GET", "callback"), "identity-callback": ("GET", "identity-callback")}


class BrowserExceptionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("PowerShell with Roslyn is required; Azure was not tested")
        good = {"X-Core-L7-Ingress": "fixture-ingress"}
        cases = []
        for operation, (method, path) in ROUTES.items():
            case = {"id": operation, "api": "credential-manager-browser", "operation": operation,
                    "method": method, "path": "/auth/connections/browser/" + path, "headers": good}
            cases.append(case)
            for field, value in (("api", "other-api"), ("operation", "other-operation"),
                                 ("method", "DELETE"), ("path", case["path"] + "/extra")):
                cases.append({**case, "id": operation + "-" + field, field: value})
        for field in ("nullApi", "nullOperation"):
            cases.append({**cases[0], "id": field, field: True})
        cases.append({**cases[0], "id": "missing-ingress", "headers": {}})
        cases.append({**cases[0], "id": "original-path-mismatch", "originalPath": "/other/path"})
        cases.append({"id": "ordinary-key", "headers": {**good, "X-Gateway-Key": "SYNTHETIC_KEY"}, "subscription": True})
        cls.results = {}
        for configured in (False, True):
            login = login_settings() if configured else None
            methods, inbound = compile_inbound(ET.fromstring(render_service_policy(login)),
                browser_exception_fragment=render_browser_exception_fragment(login), browser_exception_login=login)
            methods += '\nstatic bool Guard(Context context, string name) { throw new Exception("Unexpected guard case"); }'
            source = CSHARP.replace("__METHODS__", methods).replace("__INBOUND__", inbound)
            source = source.replace('c.ValidSignature = (bool?)item["validSignature"] ?? true;',
                'if ((bool?)item["nullApi"] == true) c.Api = null; '
                'if ((bool?)item["nullOperation"] == true) c.Operation = null; '
                'if (item["originalPath"] != null) c.Request.OriginalUrl.Path = (string)item["originalPath"]; '
                'c.ValidSignature = (bool?)item["validSignature"] ?? true;')
            source = source.replace('new JProperty("authenticated",',
                'new JProperty("browserException", c.Variables.ContainsKey("gatewayCredentialBrowserException") ? (object)(bool)c.Variables["gatewayCredentialBrowserException"] : null), '
                'new JProperty("anonymous", c.Variables.ContainsKey("gatewayCommonAuthenticationAnonymous") && (bool)c.Variables["gatewayCommonAuthenticationAnonymous"]), '
                'new JProperty("authenticated",')
            with tempfile.TemporaryDirectory(prefix="core-browser-exception-") as folder:
                base = Path(folder).resolve()
                base.relative_to(Path(tempfile.gettempdir()).resolve())
                (base / "policy.cs").write_text(source, encoding="utf-8")
                (base / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
                (base / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[CommonAuthenticationFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""", encoding="utf-8")
                result = subprocess.run([PWSH, "-NoProfile", "-File", str(base / "run.ps1")],
                                        capture_output=True, text=True, timeout=60)
                if result.returncode:
                    raise AssertionError(result.stderr)
                cls.results[configured] = json.loads(result.stdout)

    def test_only_the_four_exact_routes_take_the_exception(self):
        for operation in ROUTES:
            with self.subTest(operation=operation):
                result = self.results[True][operation]
                self.assertEqual(result["status"], 200)
                self.assertTrue(result["browserException"])
                self.assertTrue(result["anonymous"])
                self.assertFalse(result["authenticated"])
        for name, result in self.results[True].items():
            if name not in ROUTES and name not in ("ordinary-key", "missing-ingress"):
                with self.subTest(case=name):
                    self.assertEqual(result["status"], 401)
                    self.assertFalse(result["browserException"])
                    self.assertFalse(result["anonymous"])

    def test_unconfigured_login_never_enables_the_exception(self):
        for operation in ROUTES:
            with self.subTest(operation=operation):
                result = self.results[False][operation]
                self.assertEqual(result["status"], 401)
                self.assertFalse(result["browserException"])
                self.assertFalse(result["anonymous"])

    def test_ingress_rejection_runs_first_and_ordinary_keys_still_authenticate(self):
        for configured in (False, True):
            with self.subTest(configured=configured):
                rejected = self.results[configured]["missing-ingress"]
                self.assertEqual(rejected["status"], 403)
                self.assertIsNone(rejected["browserException"])
                normal = self.results[configured]["ordinary-key"]
                self.assertEqual(normal["status"], 200)
                self.assertTrue(normal["authenticated"])
                self.assertFalse(normal["anonymous"])


class BrowserExceptionScopeTests(unittest.TestCase):
    def test_current_choose_needs_no_false_template_in_any_configuration(self):
        for login in (None, login_settings()):
            for entra in (None, entra_settings()):
                with self.subTest(cm=bool(login), entra=bool(entra)):
                    root = policy_root(render_service_policy(login, entra=entra))
                    choose = root.find("inbound/choose[@id='core-common-authentication']")
                    conditions = [node.get("condition") for node in choose.findall("when")]
                    self.assertEqual(conditions[0], '@((bool)context.Variables["gatewayCredentialBrowserException"])')
                    self.assertNotIn("@(false)", conditions)
                    self.assertEqual(len(choose.findall("otherwise")), 1)
                    self.assertEqual(list(choose)[-1].tag, "otherwise")

    def test_generated_source_is_one_leaf_and_expansion_is_exact(self):
        for login in (None, login_settings()):
            generated = render_browser_exception_fragment(login)
            root = ET.fromstring(generated)
            self.assertEqual(len(root), 1)
            self.assertEqual(root[0].attrib.keys(), {"id", "name", "value"})
            self.assertEqual(root[0].get("name"), BROWSER_EXCEPTION_VARIABLE)
            self.assertFalse(root.findall(".//include-fragment"))
            self.assertNotIn("__CREDENTIAL_BROWSER_", generated)
            self.assertEqual(expand_browser_exception_fragment(SERVICE, generated, login=login),
                             expand_browser_exception_fragment(SERVICE, login=login))
            root[0].set("value", "@{ return true; }")
            with self.assertRaises(ValueError):
                expand_browser_exception_fragment(SERVICE, ET.tostring(root, encoding="unicode"), login=login)

    def test_common_placement_identity_and_writer_cannot_be_overridden(self):
        for mode in ("duplicate", "forged", "outbound", "late", "writer", "condition", "missing"):
            root = ET.fromstring(render_service_policy())
            fragments = render_common_authentication_fragments(entra_enabled=False)
            inbound = root.find("inbound")
            include = inbound.find("include-fragment[@id='" + BROWSER_EXCEPTION_ID + "']")
            if mode == "duplicate": inbound.append(copy.deepcopy(include))
            elif mode == "forged": include.set("fragment-id", "other-fragment")
            elif mode == "outbound": inbound.remove(include); root.find("outbound").append(include)
            elif mode == "late": inbound.remove(include); inbound.append(include)
            elif mode == "writer": ET.SubElement(inbound, "set-variable", {"name": BROWSER_EXCEPTION_VARIABLE, "value": "@(true)"})
            elif mode == "condition":
                authentication = ET.fromstring(fragments['core-common-authentication'])
                authentication.find("choose/when").set("condition", "@(true)")
                fragments['core-common-authentication'] = ET.tostring(authentication, encoding="unicode")
            else: inbound.remove(include)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_common_authentication_policy(ET.tostring(root, encoding="unicode"), fragments=fragments)

    def test_graph_requires_complete_fixed_predicate_and_common_only_scope(self):
        for mode in ("missing-source", "changed-predicate", "api-writer", "api-include"):
            current, _ = audit_graph()
            resource_id = APIM + "/policyFragments/" + BROWSER_EXCEPTION_ID
            if mode == "missing-source": del current.policies[resource_id]
            elif mode == "changed-predicate":
                root = ET.fromstring(current.policies[resource_id])
                root[0].set("value", "@{ return true; }")
                current.policies[resource_id] = ET.tostring(root, encoding="unicode")
            else:
                api_id = APIM + "/apis/model-a/policies/policy"
                root = ET.fromstring(current.policies[api_id])
                attrs = {"id": BROWSER_EXCEPTION_ID, "name": BROWSER_EXCEPTION_VARIABLE, "value": "@(true)"} if mode == "api-writer" else {"id": BROWSER_EXCEPTION_ID, "fragment-id": BROWSER_EXCEPTION_ID}
                ET.SubElement(root.find("inbound"), "set-variable" if mode == "api-writer" else "include-fragment", attrs)
                current.policies[api_id] = ET.tostring(root, encoding="unicode")
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                publication.source_manifest(source_fixture(current), APIM, ["model-a", "model-b"], current.policies[GLOBAL])
