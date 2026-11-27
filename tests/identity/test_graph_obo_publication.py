"""Evaluate the APIM policies generated for the Graph OBO registration."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from tests.support.arm_helpers import compiled_policy_value
from tests.support.bicep_helpers import compile_bicep
from tests import REPOSITORY


ROOT = REPOSITORY


class GraphOboPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_bicep("apps/api-gateway/api-publication.bicep")

    def setUp(self):
        definition = json.loads((ROOT / "apps/api-gateway/apis/graph-profile-v1.json").read_text(encoding="utf-8"))
        self.api = {**definition, "apiType": "http", "backendHeaders": {}}
        self.parameters = {"apiDefinitions": [self.api], "credentialLogin": {}, "graphOboEnabled": True,
                           "publicHostName": "gateway.example.com", "subscriptionKeyHeader": "api-key"}

    def policy(self):
        xml = compiled_policy_value(self.template, self.parameters)
        self.assertNotIn("__GRAPH_OBO_", xml)
        return ET.fromstring(xml)

    def test_registered_graph_dispatches_bearer_and_key_to_separate_paths(self):
        root = self.policy()
        inbound = root.find("inbound")
        auth = inbound.find("include-fragment[@fragment-id='core-api-authentication-result']")
        selector = inbound.find("set-variable[@name='gatewayGraphObo']")
        self.assertIn('["method"] == "user"', selector.get("value"))
        auth_removal = inbound.find("set-header[@name='Authorization']")
        dispatch = next(node for node in inbound.findall("choose")
                        if node.find("when/choose/when/send-request[@response-variable-name='gatewayGraphOboResponse']") is not None)
        self.assertTrue(any('settings["backendAuthentication"] = "entraObo"' in node.get("value", "")
                            for node in inbound.findall("choose/when/set-variable[@name='gatewayAuditSettings']")))
        self.assertLess(list(inbound).index(auth), list(inbound).index(selector))
        self.assertLess(list(inbound).index(selector), list(inbound).index(auth_removal))
        self.assertLess(list(inbound).index(auth_removal), list(inbound).index(dispatch))
        oid_gate = dispatch.find("when/choose/when")
        self.assertEqual(oid_gate.get("condition"),
                         '@(true && context.Operation.Id == "graph_profile_self" && context.Operation.UrlTemplate == "/me" && context.Request.Method == "GET")')
        exchange = oid_gate.find("send-request")
        self.assertEqual(exchange.findtext("set-url"), "{{core-credential-broker-url}}/v1/graph/obo")
        self.assertEqual(exchange.findtext("set-method"), "POST")
        self.assertIsNone(dispatch.find("when//include-fragment[@fragment-id='core-credential-connection']"))
        self.assertIsNotNone(dispatch.find("otherwise/include-fragment[@fragment-id='core-credential-session-use']"))
        self.assertIn('["accessToken"]', dispatch.findtext("when/set-header[@name='Authorization']/value"))
        outbound = root.find("outbound/choose")
        self.assertIn('["verifiedPrincipalId"]', outbound.find("when/set-variable[@name='gatewayGraphOboResult']").get("value"))
        self.assertIsNotNone(outbound.find("otherwise/include-fragment[@fragment-id='core-credential-connection-outbound']"))

    def test_legacy_graph_registration_keeps_the_cm_route_without_a_prepared_obo_identity(self):
        self.parameters["graphOboEnabled"] = False
        root = self.policy()
        self.assertIsNone(root.find("inbound//send-request[@response-variable-name='gatewayGraphOboResponse']"))
        self.assertIsNotNone(root.find("inbound/include-fragment[@fragment-id='core-credential-session-use']"))
        self.assertIsNotNone(root.find("outbound/include-fragment[@fragment-id='core-credential-connection-outbound']"))

    def test_other_apis_keep_cm_and_unexpected_graph_route_blocks_exchange(self):
        for field, change in (("backendUrl", "https://backend.example.com/v1.0"),
                              ("urlTemplate", "/messages"), ("backendUrlTemplate", "/messages")):
            with self.subTest(field=field):
                source = self.api if field == "backendUrl" else self.api["operations"][0]
                original = source[field]
                source[field] = change
                try:
                    root = self.policy()
                    exchange_gate = next(node for node in root.findall("inbound//when")
                                         if node.find("send-request[@response-variable-name='gatewayGraphOboResponse']") is not None)
                    self.assertTrue(exchange_gate.get("condition").startswith("@(false && "))
                finally:
                    source[field] = original
        self.api["name"] = "other-graph-profile"
        root = self.policy()
        self.assertIsNone(root.find("inbound//send-request[@response-variable-name='gatewayGraphOboResponse']"))
        self.assertIsNotNone(root.find("inbound/include-fragment[@fragment-id='core-credential-session-use']"))

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell with the .NET compiler is required")
    def test_compiled_policy_accepts_only_matching_graph_profile_and_parses_known_challenges(self):
        root = self.policy()
        inbound = root.find("inbound//set-variable[@name='gatewayGraphOboResult']").get("value")
        outbound = root.find("outbound//set-variable[@name='gatewayGraphOboResult']").get("value")
        code = """using System;
using System.Collections.Generic;
using System.Text;
using Newtonsoft.Json.Linq;
public interface IResponse {
    int StatusCode { get; }
    MessageBody Body { get; }
}
public sealed class MessageBody {
    public string Text;
    public T As<T>(bool preserveContent = false) { return (T)(object)Encoding.UTF8.GetBytes(Text); }
}
public sealed class Response : IResponse {
    public int StatusCode { get; set; }
    public MessageBody Body { get; set; }
    public Dictionary<string, string> Headers = new Dictionary<string, string>();
}
public sealed class Context {
    public Response Response;
    public Dictionary<string, object> Variables = new Dictionary<string, object>();
}
public static class VariableExtensions {
    public static T GetValueOrDefault<T>(this Dictionary<string, object> variables, string key, T fallback) {
        return variables.TryGetValue(key, out var value) && value is T typed ? typed : fallback;
    }
}
public static class PolicyFixture {
    static string Broker(Context context) __BROKER__
    static string Graph(Context context) __GRAPH__
    public static string Run(string input) {
        var results = new JArray();
        foreach (var item in JArray.Parse(input)) {
            var response = new Response {
                StatusCode = (int)item["status"],
                Body = new MessageBody { Text = item["response"].ToString() }
            };
            response.Headers["WWW-Authenticate"] = (string)item["challenge"] ?? "";
            response.Headers["Retry-After"] = (string)item["retryAfter"] ?? "";
            var context = new Context { Response = response };
            context.Variables["gatewayDecision"] = item["decision"]?.ToString();
            context.Variables["gatewayGraphOboResponse"] = response;
            results.Add(JObject.Parse((string)item["source"] == "Graph" ? Graph(context) : Broker(context)));
        }
        return results.ToString(Newtonsoft.Json.Formatting.None);
    }
}""".replace("__BROKER__", inbound[1:]).replace("__GRAPH__", outbound[1:])
        owner = "11111111-1111-1111-1111-111111111111"
        another_owner = "22222222-2222-2222-2222-222222222222"
        graph_token = "synthetic.graph.token"
        malformed = "\r\n".join([graph_token, "invalid"])
        decision = {"verifiedPrincipalId": owner}
        claims = base64.b64encode(b'{"access_token":{"acrs":{"essential":true}}}').decode("ascii")
        challenge = f'Bearer error="insufficient_claims", claims="{claims}"'
        cases = [
            {"source": "Broker", "status": 200, "response": {"accessToken": graph_token}},
            {"source": "Broker", "status": 200, "response": {"accessToken": malformed}},
            {"source": "Broker", "status": 401, "response": {
                "error": {"code": "GraphReauthenticationRequired", "description": "synthetic private detail"},
                "claims": claims,
            }},
            {"source": "Graph", "status": 200, "decision": decision, "response": {"id": owner}},
            {"source": "Graph", "status": 200, "decision": decision, "response": {"id": another_owner}},
            {"source": "Graph", "status": 403, "decision": decision, "response": {}, "challenge": challenge},
            {"source": "Graph", "status": 403, "decision": decision, "response": {}, "challenge": challenge.replace(claims, "broken")},
            {"source": "Graph", "status": 429, "decision": decision, "response": {}, "retryAfter": "30"},
            {"source": "Graph", "status": 429, "decision": decision, "response": {}, "retryAfter": "30, 40"},
        ]
        with tempfile.TemporaryDirectory(prefix="graph-obo-policy-") as directory:
            path = Path(directory)
            (path / "policy.cs").write_text(code, encoding="utf-8")
            (path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
            script = path / "run.ps1"
            script.write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[PolicyFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""", encoding="utf-8")
            invocation = subprocess.run(["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                                        check=False, capture_output=True, text=True, timeout=30)
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        self.assertEqual(json.loads(invocation.stdout), [
            {"status": 200, "accessToken": graph_token}, {"status": 503},
            {"status": 401, "claims": claims}, {"status": 200}, {"status": 403},
            {"status": 401, "claims": claims}, {"status": 403},
            {"status": 429, "retryAfter": "30"}, {"status": 429},
        ])
