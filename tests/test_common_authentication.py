"""共通キー検証を実XMLのC#式で確認する。APIM標準の状態・範囲検証は実Azureで別途受入する。"""
from policy_fixture_paths import policy_fixture_path
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from configuration_fixtures import CORE, api_fixture, fixture, configuration
from deployment.helpers.apim_audit import expand_audit_fragments
from deployment.helpers.apim_ingress import expand_trusted_ingress_fragment
from deployment.helpers.apim_browser_exception import expand_browser_exception_fragment
from deployment.helpers.apim_authentication import render_service_policy
from deployment.helpers.apim_common_authentication import render_common_authentication_fragments, expand_common_authentication_fragments
from deployment.helpers.apim_api_request import expand_api_request_preparation_fragment
from test_gateway_limits_telemetry import PWSH

SERVICE_POLICY = CORE / "apps/gateway/apim/policies/service.xml"

class CommonAuthenticationConfigurationTests(unittest.TestCase):
    def test_key_settings_do_not_require_a_gateway_audience(self):
        from deployment.helpers.apim_authentication import common_authentication_settings
        self.assertEqual(common_authentication_settings({"gateway":{"authentication":{}}}),
                         {"core-subscription-key-header":"api-key"})

    def test_key_header_is_case_insensitive_and_api_setting_matches(self):
        from deployment.helpers.apim_authentication import common_authentication_settings
        for header in ("api-key", "Ocp-Apim-Subscription-Key", "X-Gateway-Key"):
            raw=api_fixture()
            raw["gateway"]["authentication"]["subscriptionKeyHeader"]=header
            raw["apiDefinitions"][0]["subscriptionKeyHeader"]=header.lower()
            with self.subTest(header=header):
                actual=configuration.normalize_configuration(raw,base_directory=CORE/"config")
                self.assertEqual(actual["apiDefinitions"][0]["subscriptionKeyHeader"],header)
                self.assertEqual(common_authentication_settings(actual),{"core-subscription-key-header":header})

    def test_common_key_header_cannot_collide_with_credentials_or_transport(self):
        from deployment.helpers.apim_authentication import common_authentication_settings
        for header in ("Authorization","Host","Content-Type","Cookie","X-Core-L7-Ingress",
                       "X-Core-Connection-Session","X-Core-Connection-Poll","X-Core-Connection-Transaction",
                       "X-Core-Connection-Custom","x-Ms-ClIeNt-ReQuEsT-iD","X-APPGW-TRACE-ID","bad header",""):
            with self.subTest(header=header),self.assertRaises(ValueError):
                common_authentication_settings({"gateway":{"authentication":{"subscriptionKeyHeader":header}}})

    def test_common_key_header_is_fixed_in_public_schemas_and_retained_in_internal_schema(self):
        for name in ("params","options","configuration"):
            schema=json.loads((CORE/f"schemas/{name}.schema.json").read_text(encoding="utf-8"))
            settings=schema["properties"]["gateway"]["properties"]["authentication"]
            with self.subTest(schema=name):
                if name == "configuration":
                    self.assertIn("subscriptionKeyHeader",settings["properties"])
                    self.assertEqual(settings["properties"]["subscriptionKeyHeader"]["default"], "api-key")
                else:
                    self.assertNotIn("subscriptionKeyHeader",settings["properties"])

def csharp_expression(raw):
    """XMLから取り出した式だけをC#本文へ変換し、判定自体を再実装しない。"""
    raw = raw.strip()
    if raw.startswith("@{") and raw.endswith("}"):
        return raw[2:-1]
    if raw.startswith("@(") and raw.endswith(")"):
        return "return " + raw[2:-1] + ";"
    return "return " + json.dumps(raw) + ";"


def compile_inbound(root, browser_exception_fragment=None, *, browser_exception_login=None, after_variable=None, trace_messages=False):
    """choose/set-variableを実式のまま実行する小さなローカル試験用アダプター。"""
    # 監査・入口確認・ブラウザー例外の部品を実式として実行する。
    # CMを構成した試験は、その設定から生成した例外部品を渡す。既知includeを無処理扱いにしない。
    enabled = root.find('.//include-fragment[@fragment-id="core-entra-authentication-error"]') is not None
    xml = expand_common_authentication_fragments(ET.tostring(root, encoding="unicode"),
        render_common_authentication_fragments(entra_enabled=enabled), entra_enabled=enabled)
    xml = expand_trusted_ingress_fragment(xml)
    xml = expand_browser_exception_fragment(xml, browser_exception_fragment, login=browser_exception_login)
    root = ET.fromstring(expand_audit_fragments(xml))
    methods = []

    def value(raw):
        name = "Expression" + str(len(methods))
        body = csharp_expression(raw).replace("{{core-subscription-key-header}}", "X-Gateway-Key")
        body = body.replace("__L7_INGRESS_REQUIRED__", "true").replace("__L7_INGRESS_EXPECTED__", "fixture-ingress")
        body = body.replace("{{core-l7-ingress-enabled}}", "true").replace("{{core-l7-ingress-secret}}", "fixture-ingress")
        methods.append("static dynamic " + name + "(Context context) { " + body + " }")
        return name + "(context)"

    def emit(node):
        if node.tag == "set-variable":
            emitted = "context.Variables[" + json.dumps(node.get("name")) + "] = " + value(node.get("value")) + ";"
            # 試験専用の故障注入。運用ポリシーへ特別なリクエスト分岐を追加しない。
            return emitted + (after_variable(node.get("name")) if after_variable else "")
        if node.tag == "choose":
            branches = []
            for branch in node:
                prefix = ("if" if not branches else "else if") if branch.tag == "when" else "else"
                condition = " ((bool)" + value(branch.get("condition")) + ")" if branch.tag == "when" else ""
                branches.append(prefix + condition + " { " + "\n".join(emit(child) for child in branch) + " }")
            return "\n".join(branches)
        if node.tag == "return-response":
            status = node.find("set-status")
            headers = "\n".join("context.ResponseHeaders.Remove(" + json.dumps(header.get("name")) + ");"
                                if header.get("exists-action") == "delete" else
                                "context.ResponseHeaders[" + json.dumps(header.get("name")) + "] = (string)" + value(header.find("value").text) + ";"
                                for header in node.findall("set-header"))
            return headers + "\nreturn " + status.get("code") + ";"
        if node.tag == "validate-azure-ad-token":
            # 署名・issuer・audience等の標準検証はValidSignatureで代用し、実Azureで別途受入する。
            # required-claimsの値条件だけは実XMLから生成する。claimの値数1の確認は後続の実式に任せる。
            conditions = ["context.ValidSignature", "context.Token != null", "context.Token.Claims != null"]
            for claim in node.findall("./required-claims/claim"):
                name = json.dumps(claim.get("name"))
                values = claim.findall("value")
                match = claim.get("match", "all")
                if not values or match not in ("all", "any"):
                    raise AssertionError("Unsupported required-claims policy")
                required = "new [] { " + ", ".join("(string)" + value(item.text) for item in values) + " }"
                actual = "context.Token.Claims[" + name + "].Where(claimValue => claimValue != null)"
                separator = claim.get("separator")
                if separator is not None:
                    actual += ".SelectMany(claimValue => claimValue.Split(new [] { " + json.dumps(separator) + " }, StringSplitOptions.None))"
                conditions.append("(context.Token.Claims.ContainsKey(" + name + ") && context.Token.Claims[" + name + "] != null && "
                                  + required + (".All" if match == "all" else ".Any") + "(required => " + actual + ".Contains(required)))")
            failed = node.get("failed-validation-httpcode", "401")
            output = json.dumps(node.get("output-token-variable-name"))
            return "if (!(" + " && ".join(conditions) + ")) { return " + failed + "; } context.Variables[" + output + "] = context.Token;"
        if node.tag == "trace":
            return "context.TraceMessages.Add((string)" + value(node.find("message").text) + ");" if trace_messages else ""
        if node.tag in ("set-header", "include-fragment"):
            return ""
        raise AssertionError("Unsupported authentication policy node: " + node.tag)

    source = "\n".join(emit(node) for node in root.find("inbound"))
    return "\n".join(methods), source


CSHARP = r'''
using System;
using System.Linq;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Named { public string Id; public string UrlTemplate = "/tools/{tool}/invoke"; }
public class Url { public string Path; public Dictionary<string,string[]> Query = new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase); }
public class Request {
 public string Method; public string IpAddress = "192.0.2.10"; public Url Url = new Url(); public Url OriginalUrl = new Url();
 public Dictionary<string,string[]> Headers = new Dictionary<string,string[]>(StringComparer.OrdinalIgnoreCase);
 public object Body { get { throw new Exception("Common authentication read a request body"); } }
}
public class Jwt { public Dictionary<string,string[]> Claims = new Dictionary<string,string[]>(); }
public class Context {
 public Guid RequestId = Guid.Parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
 public DateTime Timestamp = DateTime.Parse("2026-01-02T03:04:05Z", null, System.Globalization.DateTimeStyles.RoundtripKind);
 public Request Request = new Request(); public Named Api = new Named(); public Named Operation = new Named();
 public Named Subscription; public Named Product;
 public Dictionary<string,object> Variables = new Dictionary<string,object>();
 public Dictionary<string,string> ResponseHeaders = new Dictionary<string,string>();
 public List<string> TraceMessages = new List<string>();
 public Jwt Token; public bool ValidSignature;
}
public static class Extensions {
 // APIMの型付き変数取得を再現します。型不一致を既定値へ読み替えず、実式のcatchで扱わせます。
 public static T GetValueOrDefault<T>(this Dictionary<string,object> values,string key,T fallback) {
  object value; return values.TryGetValue(key,out value) ? (T)value : fallback;
 }
 public static string GetValueOrDefault(this Dictionary<string,string[]> values,string key,string fallback) {
  return values.ContainsKey(key) ? String.Join(" ",values[key]) : fallback;
 }
}
public static class CommonAuthenticationFixture {
 __METHODS__
 static int Authenticate(Context context) { __INBOUND__ return 200; }
 public static string Run(string json) {
  var output = new JObject();
  foreach (JObject item in JArray.Parse(json)) {
   var c = new Context(); c.Api.Id = (string)item["api"] ?? "gui-added-api"; c.Operation.Id = (string)item["operation"] ?? "invoke";
   c.Request.Method = (string)item["method"] ?? "POST";
   c.Request.Url.Path = (string)item["path"] ?? "/tools/gui/invoke"; c.Request.OriginalUrl.Path = c.Request.Url.Path;
   c.ValidSignature = (bool?)item["validSignature"] ?? true;
   foreach (var property in ((JObject)(item["headers"] ?? new JObject())).Properties()) {
    c.Request.Headers[property.Name] = property.Value is JArray ? property.Value.Select(x=>(string)x).ToArray() : new [] {(string)property.Value};
   }
   foreach (var property in ((JObject)(item["query"] ?? new JObject())).Properties()) c.Request.Url.Query[property.Name] = new [] {(string)property.Value};
   if ((bool?)item["subscription"] == true) { c.Subscription = new Named {Id=(string)item["subscriptionId"] ?? "fixture-subscription"}; c.Product = new Named {Id="fixture-product"}; }
   if (item["claims"] is JObject claims) {
    c.Token = new Jwt(); foreach (var property in claims.Properties()) c.Token.Claims[property.Name] = property.Value is JArray ? property.Value.Select(x=>(string)x).ToArray() : new [] {(string)property.Value};
   }
   if ((bool?)item["grpc"] == true) c.Variables["coreNativeGrpc"] = true;
   if (item["challenge"] != null) c.Variables["gatewayMcpChallenge"] = (string)item["challenge"];
   if (item["marker"] != null) c.Variables["gatewayCommonAuthenticationValidated"] = (bool)item["marker"];
   if (item["requestContext"] != null) c.Variables["gatewayRequestContext"] = item["requestContext"].ToString(Formatting.None);
   int status = item["guard"] != null ? (Guard(c, (string)item["guard"]) ? 503 : 200) : Authenticate(c);
   output[(string)item["id"]] = new JObject(new JProperty("status",status),
     new JProperty("authenticated",c.Variables.ContainsKey("gatewayCommonAuthenticationValidated") && (bool)c.Variables["gatewayCommonAuthenticationValidated"]),
     new JProperty("hasValidatedToken",c.Variables.ContainsKey("gatewayValidatedToken")),
     new JProperty("challenge",c.ResponseHeaders.ContainsKey("WWW-Authenticate") ? c.ResponseHeaders["WWW-Authenticate"] : null),
     new JProperty("requestContext",c.Variables.ContainsKey("gatewayRequestContext") ? JObject.Parse((string)c.Variables["gatewayRequestContext"]) : null));
  }
  return output.ToString(Formatting.None);
 }
}
'''


class CommonAuthenticationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH: raise unittest.SkipTest("PowerShell with Roslyn is required; Azure was not tested")
        methods,inbound=compile_inbound(ET.fromstring(render_service_policy()))
        guards=[]
        for filename in ("gateway.xml","protocol.xml","mcp.xml"):
            policy=ET.fromstring(expand_api_request_preparation_fragment(
                (policy_fixture_path(CORE / "apps/gateway/apim/policies", filename)).read_text(encoding="utf-8")))
            condition=next(node.get("condition") for node in policy.findall("./inbound/choose/when") if "gatewayCommonAuthenticationValidated" in node.get("condition",""))
            guards.append('if (name == '+json.dumps(filename.split(".")[0])+') { '+csharp_expression(condition)+' }')
        methods+='\nstatic bool Guard(Context context,string name) { '+''.join(guards)+' throw new Exception("Unknown policy"); }'
        good={"X-Core-L7-Ingress":"fixture-ingress"}
        key={**good,"X-Gateway-Key":"SYNTHETIC_KEY"}
        bearer={**good,"Authorization":"Bearer synthetic-token"}
        cases=[
          {"id":"missing","headers":good},
          {"id":"key","headers":key,"subscription":True},
          {"id":"unknown-key","headers":key},
          {"id":"blank-subscription","headers":key,"subscription":True,"subscriptionId":" "},
          {"id":"blank-key","headers":{**key,"X-Gateway-Key":" "},"subscription":True},
          {"id":"duplicate-key","headers":{**key,"X-Gateway-Key":["one","two"]},"subscription":True},
          {"id":"bearer-only","headers":bearer},
          {"id":"key-with-bearer","headers":{**key,"Authorization":"Bearer synthetic-token"},"subscription":True},
          {"id":"key-with-unrelated-auth","headers":{**key,"Authorization":"Basic invalid"},"subscription":True},
          {"id":"key-with-duplicate-auth","headers":{**key,"Authorization":["Bearer a","Bearer b"]},"subscription":True},
          {"id":"invalid-key-with-bearer","headers":{**key,"Authorization":"Bearer synthetic-token"}},
          {"id":"query-key","headers":key,"subscription":True,"query":{"subscription-key":"forbidden"}},
          {"id":"query-api-key","headers":key,"subscription":True,"query":{"api-key":"forbidden"}},
          {"id":"wrong-alias","headers":{**good,"api-key":"SYNTHETIC_KEY"},"subscription":True},
          {"id":"missing-ingress","headers":{"X-Gateway-Key":"SYNTHETIC_KEY"},"subscription":True},
          {"id":"overwrite-request-context","headers":key,"subscription":True,
           "requestContext":{"requestId":"untrusted","apiId":"untrusted","operationId":"untrusted"}},
          {"id":"grpc","headers":{"X-Gateway-Key":"SYNTHETIC_KEY"},"subscription":True,"grpc":True},
          {"id":"unregistered-metadata","api":"unregistered-metadata-api","operation":"metadata-gateway","method":"GET","path":"/unregistered/metadata","headers":good},
          {"id":"unregistered-login","api":"unregistered-login-api","operation":"login","method":"GET","path":"/unregistered/login","headers":good},
        ]
        for policy in ("gateway","protocol","mcp"):
            for marker in (None,False,True):
                case={"id":f"guard-{policy}-{marker}","guard":policy}
                if marker is not None: case["marker"]=marker
                cases.append(case)
        with tempfile.TemporaryDirectory(prefix="core-key-auth-") as folder:
            base=Path(folder)
            (base/"policy.cs").write_text(CSHARP.replace("__METHODS__",methods).replace("__INBOUND__",inbound),encoding="utf-8")
            (base/"cases.json").write_text(json.dumps(cases),encoding="utf-8")
            (base/"run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[CommonAuthenticationFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""",encoding="utf-8")
            result=subprocess.run([PWSH,"-NoProfile","-File",str(base/"run.ps1")],capture_output=True,text=True,timeout=60)
            if result.returncode: raise AssertionError(result.stderr)
            cls.results=json.loads(result.stdout)

    def test_only_verified_keys_authenticate_the_request(self):
        for name in ("key","key-with-bearer","key-with-unrelated-auth","key-with-duplicate-auth"):
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["status"],200)
                self.assertTrue(self.results[name]["authenticated"])
                self.assertFalse(self.results[name]["hasValidatedToken"])

    def test_missing_invalid_duplicate_and_query_credentials_are_rejected(self):
        for name in ("missing","unknown-key","blank-subscription","blank-key","duplicate-key","bearer-only","invalid-key-with-bearer","query-key","query-api-key","wrong-alias","unregistered-metadata","unregistered-login"):
            with self.subTest(case=name): self.assertEqual(self.results[name]["status"],401)

    def test_key_errors_do_not_advertise_a_bearer_login(self):
        for result in self.results.values(): self.assertIsNone(result["challenge"])

    def test_common_authentication_keeps_the_trusted_ingress_requirement(self):
        self.assertEqual(self.results["missing-ingress"]["status"],403)
        self.assertEqual(self.results["grpc"]["status"],403)
        self.assertFalse(self.results["grpc"]["authenticated"])

    def test_request_identity_is_initialized_even_before_ingress_or_authentication_rejection(self):
        expected={"requestId":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","apiId":"gui-added-api","operationId":"invoke",
                  "requestReceivedAt":"2026-01-02T03:04:05Z","httpMethod":"POST",
                  "operationTemplate":"/tools/{tool}/invoke","apimPeerIp":"192.0.2.10",
                  "reportedUserAgent":None,"appGatewayTraceId":None}
        for name in ("key","unknown-key","missing","missing-ingress","grpc","overwrite-request-context"):
            with self.subTest(case=name):
                self.assertEqual(self.results[name]["requestContext"],expected)

    def test_api_guard_requires_the_common_authentication_marker(self):
        for policy in ("gateway","protocol","mcp"):
            for marker in (None,False,True):
                self.assertEqual(self.results[f"guard-{policy}-{marker}"]["status"],200 if marker is True else 503)

class CommonAuthenticationWiringTests(unittest.TestCase):
    def test_common_request_context_merge_is_first_unique_and_idempotent(self):
        from deployment.helpers.apim_authentication import merge_common_authentication_policy,validate_common_authentication_policy
        policy='<policies><inbound><set-header name="X-Manual" exists-action="delete"/></inbound><backend><forward-request timeout="123"/></backend><outbound/><on-error/></policies>'
        merged,changed=merge_common_authentication_policy(policy)
        self.assertTrue(changed)
        root=ET.fromstring(merged)
        self.assertEqual(root.find('inbound')[0].get('id'),'core-request-context')
        self.assertEqual(root.find('inbound')[0].tag,'include-fragment')
        self.assertEqual(root.find('inbound')[0].get('fragment-id'),'core-request-context')
        expanded=ET.fromstring(expand_audit_fragments(merged))
        self.assertEqual(len(expanded.findall('.//set-variable[@name="gatewayRequestContext"]')),1)
        self.assertEqual(root.find('./backend/forward-request').get('timeout'),'123')
        self.assertIsNotNone(root.find('./inbound/set-header[@name="X-Manual"]'))
        validate_common_authentication_policy(merged)
        self.assertEqual(merge_common_authentication_policy(merged),(merged,False))

    def test_common_request_context_rejects_changed_owned_initializer_or_manual_overwrite(self):
        from deployment.helpers.apim_authentication import merge_common_authentication_policy,render_service_policy
        for kind in ('owned-reference','manual-overwrite','duplicate-reference','nested-reference'):
            root=ET.fromstring(render_service_policy())
            if kind=='owned-reference':
                root.find('./inbound/include-fragment[@id="core-request-context"]').set('fragment-id','untrusted')
            elif kind=='manual-overwrite':
                ET.SubElement(root.find('inbound'),'set-variable',{'name':'gatewayRequestContext','value':'untrusted'})
            elif kind=='duplicate-reference':
                ET.SubElement(root.find('inbound'),'include-fragment',{'fragment-id':'core-request-context'})
            else:
                reference=root.find('./inbound/include-fragment[@id="core-request-context"]')
                root.find('inbound').remove(reference)
                ET.SubElement(root.find('inbound'),'choose').append(reference)
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                merge_common_authentication_policy(ET.tostring(root,encoding='unicode'))

    def test_broker_migration_replaces_only_owned_cache_and_anonymous_route(self):
        from deployment.helpers.apim_authentication import merge_common_authentication_policy, render_service_policy, validate_common_authentication_policy, _render_inline_common_authentication_service_policy
        from browser_exception_projection import project_browser_exception_to_previous
        from test_credential_login import settings
        import re
        login = settings()
        root = ET.fromstring(project_browser_exception_to_previous(_render_inline_common_authentication_service_policy(login), login))
        inbound = root.find("inbound")
        anonymous = inbound.find("choose[@id='core-common-authentication']/when")
        anonymous.set("condition", re.sub(r' \|\| \(context\.Operation\.Id == "identity-callback"[^)]*\)', '', anonymous.get("condition")))
        for index, node in enumerate((
            ET.Element("set-variable", {"id": "core-credential-login-cache-value", "name": "gatewayCredentialCached", "value": ""}),
            ET.Element("set-variable", {"id": "core-credential-login-cache-key", "name": "gatewayCredentialCacheKey", "value": "old-cache-key"}),
            ET.fromstring('<choose id="core-credential-login-cache"><when condition="@(!String.IsNullOrEmpty((string)context.Variables[&quot;gatewayCredentialCacheKey&quot;]))"><cache-lookup-value key="@((string)context.Variables[&quot;gatewayCredentialCacheKey&quot;])" variable-name="gatewayCredentialCached" default-value="" caching-type="internal" /></when></choose>'),
        )):
            inbound.insert(index + 1, node)
        ET.SubElement(inbound, "set-header", {"name": "X-Manual", "exists-action": "delete"})
        old = ET.tostring(root, encoding="unicode")
        with self.assertRaises(ValueError):
            merge_common_authentication_policy(old, login)
        merged, changed = merge_common_authentication_policy(old, login, migrate_credential_broker=True)
        self.assertTrue(changed)
        self.assertNotIn("core-credential-login-cache", merged)
        self.assertIn("identity-callback", project_browser_exception_to_previous(merged, login))
        self.assertIsNotNone(ET.fromstring(merged).find(
            "inbound/include-fragment[@fragment-id='core-credential-browser-exception']"))
        self.assertIsNotNone(ET.fromstring(merged).find("inbound/set-header[@name='X-Manual']"))
        validate_common_authentication_policy(merged, login)
        self.assertFalse(merge_common_authentication_policy(merged, login, migrate_credential_broker=True)[1])

        # 同じ所有IDに追加処理がある場合、その処理まで巻き込んで削除しない。
        ET.SubElement(inbound.find("choose[@id='core-credential-login-cache']/when"), "return-response")
        with self.assertRaises(ValueError):
            merge_common_authentication_policy(ET.tostring(root, encoding="unicode"), login, migrate_credential_broker=True)

    def test_broker_migration_rejects_partial_cache_or_modified_authorization(self):
        from deployment.helpers.apim_authentication import merge_common_authentication_policy, _render_inline_common_authentication_service_policy
        from test_credential_login import settings
        login = settings()
        for kind in ("partial", "modified"):
            root = ET.fromstring(_render_inline_common_authentication_service_policy(login))
            if kind == "partial":
                ET.SubElement(root.find("inbound"), "cache-lookup-value", {"id":"core-credential-login-cache", "caching-type":"internal"})
            else:
                root.find(".//set-variable[@name='gatewayCommonAuthenticationValidated']").set("value", "@(false)")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                merge_common_authentication_policy(ET.tostring(root, encoding="unicode"), login, migrate_credential_broker=True)

    def test_common_merge_accepts_factory_policy_without_optional_error_section(self):
        from deployment.helpers.apim_authentication import merge_common_authentication_policy,validate_common_authentication_policy
        factory='<policies><inbound/><backend><forward-request/></backend><outbound/></policies>'
        merged,changed=merge_common_authentication_policy(factory)
        self.assertTrue(changed)
        root=ET.fromstring(merged)
        self.assertEqual([node.tag for node in root],['inbound','backend','outbound','on-error'])
        self.assertIsNotNone(root.find('./on-error/set-header[@id="core-error-request-id"]'))
        self.assertIsNotNone(root.find('./backend/forward-request'))
        validate_common_authentication_policy(merged)

    def test_common_merge_adds_owned_sections_without_changing_existing_backend(self):
        from deployment.helpers.apim_authentication import merge_common_authentication_policy,validate_common_authentication_policy
        policy='<policies><!-- operator backend note --><backend><forward-request timeout="123"/></backend></policies>'
        merged,changed=merge_common_authentication_policy(policy)
        self.assertTrue(changed)
        root=ET.fromstring(merged)
        self.assertEqual([node.tag for node in root],['inbound','backend','outbound','on-error'])
        self.assertEqual(root.find('./backend/forward-request').get('timeout'),'123')
        self.assertIn('operator backend note',merged)
        validate_common_authentication_policy(merged)

    def test_common_merge_preserves_omitted_backend(self):
        from deployment.helpers.apim_authentication import merge_common_authentication_policy,validate_common_authentication_policy
        merged,changed=merge_common_authentication_policy('<policies><inbound/></policies>')
        self.assertTrue(changed)
        self.assertIsNone(ET.fromstring(merged).find('backend'))
        validate_common_authentication_policy(merged)
        self.assertFalse(merge_common_authentication_policy(merged)[1])

    def test_common_merge_rejects_unknown_duplicate_reordered_sections_and_modified_blocks(self):
        from deployment.helpers.apim_authentication import merge_common_authentication_policy,render_service_policy
        invalid=[
            '<policies><inbound/><other/><outbound/></policies>',
            '<policies><inbound/><inbound/></policies>',
            '<policies><outbound/><inbound/></policies>',
            render_service_policy().replace('id="core-common-authentication" fragment-id="core-common-authentication"',
                                            'id="core-common-authentication" fragment-id="untrusted-authentication"'),
        ]
        for policy in invalid:
            with self.subTest(policy=policy),self.assertRaisesRegex(ValueError,'incompatible'):
                merge_common_authentication_policy(policy)

    def test_common_policy_requires_key_and_apis_keep_authorization(self):
        global_root=ET.parse(SERVICE_POLICY).getroot()
        self.assertEqual(global_root.findall(".//validate-azure-ad-token"),[])
        self.assertEqual(global_root.findall(".//validate-jwt"),[])
        for filename in ("gateway.xml","protocol.xml","mcp.xml"):
            policy=ET.parse(policy_fixture_path(CORE / "apps/gateway/apim/policies", filename)).getroot()
            with self.subTest(policy=filename):
                if filename == "gateway.xml":
                    # HTTPの認証情報引渡しは同名フラグメントのC#正本にあります。
                    fragment=policy.find("./inbound/include-fragment[@id='core-api-authentication-result']")
                    self.assertIsNotNone(fragment)
                    self.assertEqual(fragment.get("fragment-id"),"core-api-authentication-result")
                    decision=(CORE / "apps/gateway/apim/policies/fragments/authentication/core-api-authentication-result/core-api-authentication-result.cs").read_text(encoding="utf-8")
                else:
                    decision=policy.find("./inbound/set-variable[@name='gatewayDecision']").get("value")
                self.assertIn("context.Subscription",decision)
                self.assertIn("subscription:",decision)
                self.assertEqual(policy.findall(".//validate-jwt"),[])

    def test_api_decisions_follow_the_inherited_common_key_authorization(self):
        for filename in ("gateway.xml", "protocol.xml", "mcp.xml"):
            inbound=ET.parse(policy_fixture_path(CORE / "apps/gateway/apim/policies", filename)).getroot().find("inbound")
            with self.subTest(policy=filename):
                decision=inbound.find("include-fragment[@id='core-api-authentication-result'][@fragment-id='core-api-authentication-result']"
                                      if filename == "gateway.xml" else "set-variable[@name='gatewayDecision']")
                self.assertIsNotNone(decision)
                self.assertLess(list(inbound).index(inbound.find("base")),
                                list(inbound).index(decision))

    def test_common_merge_retains_manual_cors_and_rejects_obsolete_browser_blocks(self):
        from deployment.helpers.apim_authentication import merge_common_authentication_policy,MIGRATION_REQUIRED
        manual='<policies><inbound><cors><allowed-origins><origin>https://portal.example.com</origin></allowed-origins></cors></inbound><backend><forward-request/></backend><outbound/><on-error/></policies>'
        merged,changed=merge_common_authentication_policy(manual)
        self.assertTrue(changed)
        self.assertIsNotNone(ET.fromstring(merged).find("./inbound/cors"))
        obsolete=manual.replace('<cors>','<cors id="core-browser-login-cors">')
        with self.assertRaisesRegex(ValueError,"incompatible"):
            merge_common_authentication_policy(obsolete)

if __name__=="__main__": unittest.main()
