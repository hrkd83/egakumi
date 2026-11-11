"""Realtime接続開始・監視・停止のpolicyを検証する。

実XMLから取り出した式を合成入力で実行し、署名ticket、所有者、期限、call IDの結び付きを調べる。SDPや秘密値を通常出力へ残さない。
"""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

CORE = Path(__file__).resolve().parents[1]
POLICIES = CORE / "apps/gateway/apim/policies"
PWSH = os.environ.get("WORKFORCE_PWSH") or shutil.which("pwsh")
if not PWSH:
    candidate = CORE / ".tools/powershell/7.6.7/pwsh"
    PWSH = str(candidate) if candidate.exists() else None


def expression(filename, xpath, attribute=None):
    """XML内の指定位置からAPIMの実C#式を取り出す。想定外の形式なら試験を止める。"""
    node = ET.parse(POLICIES / filename).find(xpath)
    value = node.get(attribute) if attribute else node.text
    assert value.strip().startswith("@{") and value.strip().endswith("}")
    return value.strip()[2:-1].replace("{{core-realtime-ticket-key}}", "a" * 128)


SOURCE = r'''
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
public class Body {
  public string Text;
  public T As<T>(bool preserveContent=false) { return typeof(T) == typeof(string) ? (T)(object)Text : (T)(object)JObject.Parse(Text); }
}
public interface IResponse { int StatusCode { get; } Body Body { get; } }
public class Response : IResponse {
  public int StatusCode { get; set; } public Body Body { get; set; }
  public Dictionary<string,string[]> Headers = new Dictionary<string,string[]>();
}
public class Request {
  public Body Body = new Body();
  public Dictionary<string,string[]> Headers = new Dictionary<string,string[]>();
  public Dictionary<string,string> MatchedParameters = new Dictionary<string,string>();
  public Url Url = new Url();
}
public class Url { public Dictionary<string,string[]> Query = new Dictionary<string,string[]>(); }
public class Jwt { public Dictionary<string,string[]> Claims = new Dictionary<string,string[]>(); }
public class Named { public string Id; }
public class Context {
  public Named Subscription;
  public Dictionary<string,object> Variables = new Dictionary<string,object>();
  public Request Request = new Request();
  public Response Response = new Response();
}
public static class HeaderExtensions {
  public static string GetValueOrDefault(this Dictionary<string,string[]> h,string name,string fallback) {
    return h.ContainsKey(name) ? String.Join(",",h[name]) : fallback;
  }
}
public static class TicketFixture {
  public static string Offer(Context context) { __OFFER__ }
  public static string SecretRequest(Context context) { __SECRET_REQUEST__ }
  public static string SecretToken(Context context) { __SECRET_TOKEN__ }
  public static string RawSdp(Context context) { return __RAW_SDP__; }
  public static string CallLocation(Context context) { __CALL_LOCATION__ }
  public static string AnswerStatus(Context context) { __ANSWER_STATUS__ }
  public static string CleanupUrl(Context context) { __CLEANUP_URL__ }
  public static bool CleanupAccepted(Context context) { __CLEANUP_ACCEPTED__ }
  public static string CleanupFailureBody(Context context) { __CLEANUP_FAILURE_BODY__ }
  public static string Sign(Context context) { __SIGN__ }
  public static bool Verify(Context context) { __VERIFY__ }
  public static bool Observe(Context context) { __OBSERVE__ }
  public static Context NewContext(string caller="caller-a",string contract="contract-a",int ttl=600,string tenant="11111111-1111-4111-8111-111111111111",string principal="22222222-2222-4222-8222-222222222222") {
    var context = new Context();
    context.Subscription = new Named { Id=caller };
    context.Variables["gatewayDecision"] = new JObject(new JProperty("allowed",true),new JProperty("method","subscriptionKey"),new JProperty("callerId","subscription:"+caller),new JProperty("contractId",contract)).ToString();
    context.Variables["gatewayOperationConstraint"] = new JObject(new JProperty("sessionTtlSeconds",ttl),new JProperty("model","fixed-model"),new JProperty("maxOutputTokens",64)).ToString();
    context.Variables["gatewayValidatedToken"] = new Jwt { Claims = new Dictionary<string,string[]> { {"tid",new string[]{tenant}}, {"oid",new string[]{principal}} } };
    context.Variables["realtimeCallId"] = "rtc_synthetic-a";
    context.Variables["realtimeSdp"] = "v=0\r\ns=synthetic\r\n";
    context.Request.MatchedParameters["callId"] = "rtc_synthetic-a";
    return context;
  }
  public static bool Check(string token,string caller="caller-a",string contract="contract-a",string call="rtc_synthetic-a") {
    var context = NewContext(caller,contract);
    context.Request.Headers["X-Core-Call-Ticket"] = new string[] {token};
    context.Request.MatchedParameters["callId"] = call;
    return Verify(context);
  }
  public static string Run() {
    var context = NewContext();
    var ticket = Sign(context);
    var body = JObject.Parse(SecretRequest(context));
    var checks = new JObject();
    checks["owner"] = Check(ticket);
    checks["otherCaller"] = Check(ticket,"caller-b");
    var sameLabels = NewContext(principal:"33333333-3333-4333-8333-333333333333");
    sameLabels.Request.Headers["X-Core-Call-Ticket"] = new string[]{ticket};
    checks["otherVerifiedPrincipalSameLabels"] = Verify(sameLabels);
    sameLabels = NewContext(tenant:"44444444-4444-4444-8444-444444444444");
    sameLabels.Request.Headers["X-Core-Call-Ticket"] = new string[]{ticket};
    checks["otherTenantSameLabels"] = Verify(sameLabels);
    sameLabels = NewContext(); sameLabels.Request.Headers["X-Core-Call-Ticket"] = new string[]{ticket};
    ((Jwt)sameLabels.Variables["gatewayValidatedToken"]).Claims.Remove("oid");
    checks["missingVerifiedPrincipal"] = Verify(sameLabels);
    sameLabels = NewContext(); sameLabels.Request.Headers["X-Core-Call-Ticket"] = new string[]{ticket};
    ((Jwt)sameLabels.Variables["gatewayValidatedToken"]).Claims["oid"] = new string[]{"22222222-2222-4222-8222-222222222222","33333333-3333-4333-8333-333333333333"};
    checks["ambiguousVerifiedPrincipal"] = Verify(sameLabels);
    sameLabels = NewContext(principal:"33333333-3333-4333-8333-333333333333");
    sameLabels.Request.Headers["X-Core-Call-Ticket"] = new string[]{ticket};
    sameLabels.Request.Url.Query["call_id"] = new string[]{"rtc_synthetic-a"};
    checks["observerOtherVerifiedPrincipalSameLabels"] = Observe(sameLabels);
    sameLabels = NewContext(tenant:"44444444-4444-4444-8444-444444444444");
    sameLabels.Request.Headers["X-Core-Call-Ticket"] = new string[]{ticket};
    sameLabels.Request.Url.Query["call_id"] = new string[]{"rtc_synthetic-a"};
    checks["observerOtherTenantSameLabels"] = Observe(sameLabels);

    sameLabels = NewContext(); sameLabels.Request.Headers["X-Core-Call-Ticket"] = new string[]{ticket};
    sameLabels.Subscription.Id = "another-subscription";
    checks["otherSubscriptionSameLabels"] = Verify(sameLabels);
    sameLabels.Request.Url.Query["call_id"] = new string[]{"rtc_synthetic-a"};
    checks["observerOtherSubscriptionSameLabels"] = Observe(sameLabels);
    sameLabels = NewContext(); sameLabels.Request.Headers["X-Core-Call-Ticket"] = new string[]{ticket};
    sameLabels.Subscription = null;
    checks["missingVerifiedSubscription"] = Verify(sameLabels);
    sameLabels = NewContext(); sameLabels.Request.Headers["X-Core-Call-Ticket"] = new string[]{ticket};
    sameLabels.Subscription.Id = "";
    checks["emptyVerifiedSubscription"] = Verify(sameLabels);
    checks["otherContract"] = Check(ticket,"caller-a","contract-b");
    checks["otherCall"] = Check(ticket,"caller-a","contract-a","rtc_synthetic-b");
    checks["pathInjection"] = Check(ticket,"caller-a","contract-a","../../other");
    checks["modified"] = Check(ticket.Substring(0,ticket.Length-1)+(ticket.EndsWith("a")?"b":"a"));
    checks["missing"] = Check("");
    checks["malformed"] = Check("not.a.valid.ticket");
    checks["oversized"] = Check(new string('a',5000));
    checks["expired"] = Check(Sign(NewContext("caller-a","contract-a",-1)));
    var parts = ticket.Split('.');
    var encoded = parts[0].Replace('-','+').Replace('_','/');
    encoded += new string('=',(4-encoded.Length%4)%4);
    var changed = JObject.Parse(Encoding.UTF8.GetString(Convert.FromBase64String(encoded)));
    changed["callId"] = "rtc_synthetic-b";
    var changedPayload = Convert.ToBase64String(Encoding.UTF8.GetBytes(changed.ToString(Formatting.None))).TrimEnd('=').Replace('+','-').Replace('/','_');
    checks["forgedPayload"] = Check(changedPayload+"."+parts[1],"caller-a","contract-a","rtc_synthetic-b");
    context.Request.Body.Text = "{\"sdp\":\"v=0\\r\\ns=fixture\\r\\n\"}";
    checks["offerValid"] = !String.IsNullOrEmpty(Offer(context));
    context.Request.Body.Text = "{\"sdp\":\"v=0\",\"model\":\"unapproved\"}";
    checks["offerExtraModel"] = !String.IsNullOrEmpty(Offer(context));
    context.Request.Body.Text = "{\"sdp\":\"not-sdp\"}";
    checks["offerInvalid"] = !String.IsNullOrEmpty(Offer(context));
    context.Request.Body.Text = "{\"sdp\":42}";
    checks["offerNotString"] = !String.IsNullOrEmpty(Offer(context));
    checks["fixedSecretRequest"] = (string)body["session"]["model"] == "fixed-model" && (int)body["session"]["max_output_tokens"] == 64
      && ((JArray)body["session"]["tools"]).Count == 0 && (string)body["session"]["output_modalities"][0] == "audio"
      && (int)body["expires_after"]["seconds"] == 60 && body["sdp"] == null && body["session"]["tracing"].Type == JTokenType.Null;
    checks["rawSdpUnchanged"] = RawSdp(context) == "v=0\r\ns=synthetic\r\n";
    context.Response.Headers["Location"] = new string[] {"/openai/v1/realtime/calls/rtc_synthetic-a"};
    checks["callLocationValid"] = CallLocation(context) == "rtc_synthetic-a";
    foreach (var item in new string[] {"", "/other/path/rtc_synthetic-a", "/realtime/calls/", "/realtime/calls/rtc_unsafe:bad", "/realtime/calls/" + new string('a',161)}) {
      context.Response.Headers["Location"] = new string[] {item};
      if (CallLocation(context) != "") { throw new Exception("Unapproved synthetic Location accepted"); }
    }
    checks["callLocationsInvalid"] = true;
    checks["cleanupFixedTarget"] = CleanupUrl(context) == "https://registered-fixture.openai.azure.com/openai/v1/realtime/calls/rtc_synthetic-a/hangup";
    context.Response.Headers["Location"] = new string[] {"https://untrusted.invalid/realtime/calls/rtc_synthetic-a"};
    checks["cleanupIgnoresResponseHost"] = CleanupUrl(context).StartsWith("https://registered-fixture.openai.azure.com/");
    checks["cleanupMissingUnconfirmed"] = !CleanupAccepted(context);
    context.Variables["realtimeCleanupResponse"] = null;
    checks["cleanupTimeoutUnconfirmed"] = !CleanupAccepted(context);
    foreach (var status in new int[] {200, 201, 204, 299}) {
      context.Variables["realtimeCleanupResponse"] = new Response {StatusCode=status, Body=new Body {Text="PRIVATE_PROVIDER_BODY"}};
      if (!CleanupAccepted(context)) { throw new Exception("Successful cleanup was not accepted"); }
    }
    checks["cleanupSuccessStatuses"] = true;
    foreach (var status in new int[] {0, 199, 300, 401, 403, 404, 429, 500}) {
      context.Variables["realtimeCleanupResponse"] = new Response {StatusCode=status, Body=new Body {Text="PRIVATE_PROVIDER_BODY"}};
      if (CleanupAccepted(context)) { throw new Exception("Failed cleanup was treated as accepted"); }
    }
    checks["cleanupFailureStatuses"] = true;
    context.Variables["realtimeAnswerStatus"] = "REALTIME_CALL_MIME_JSON";
    foreach (var accepted in new bool[] {true, false}) {
      context.Variables["realtimeCleanupAccepted"] = accepted;
      var failure = JObject.Parse(CleanupFailureBody(context));
      if ((string)failure["error"]["code"] != "REALTIME_CALL_MIME_JSON"
        || (string)failure["error"]["cleanupStatus"] != (accepted ? "accepted" : "unconfirmed")
        || failure["error"].Count() != 2 || failure.ToString().Contains("PRIVATE") || failure.ToString().Contains("rtc_synthetic")) {
        throw new Exception("Cleanup failure leaked provider details or lost the recovery state");
      }
    }
    checks["cleanupFixedFailureBody"] = true;

    var validSdp = "v=0\r\no=- 123 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n";
    context.Response.Body = new Body {Text=validSdp};
    foreach (var item in new string[] {"application/sdp", "text/plain", "  APPLICATION/SDP ; charset=utf-8", "Text/Plain; charset=UTF-8"}) {
      context.Response.Headers["Content-Type"] = new string[] {item};
      if (AnswerStatus(context) != "VALID") { throw new Exception("Approved synthetic SDP response rejected"); }
    }
    checks["validSdpAndPlain"] = true;
    foreach (var item in new string[][] {
      new [] {"", "REALTIME_CALL_MIME_MISSING"}, new [] {"application/json", "REALTIME_CALL_MIME_JSON"},
      new [] {"application/problem+json", "REALTIME_CALL_MIME_JSON"}, new [] {"text/html", "REALTIME_CALL_MIME_HTML"},
      new [] {"application/xhtml+xml", "REALTIME_CALL_MIME_HTML"}, new [] {"application/octet-stream", "REALTIME_CALL_MIME_OTHER"},
      new [] {"application/sdp-invalid", "REALTIME_CALL_MIME_OTHER"}}) {
      context.Response.Headers["Content-Type"] = new string[] {item[0]};
      if (AnswerStatus(context) != item[1]) { throw new Exception("Unexpected fixed media type classification"); }
    }
    checks["unsafeMimeClassified"] = true;
    context.Response.Headers["Content-Type"] = new string[] {"text/plain"};
    foreach (var item in new string[] {"", "{}", "<html>PRIVATE</html>", validSdp + "\0", validSdp.Replace("v=0", "v=1"),
      validSdp.Replace("o=- 123 1 IN IP4 127.0.0.1\r\n", ""), validSdp.Replace("s=-\r\n", ""),
      validSdp.Replace("t=0 0\r\n", ""), validSdp.Replace("m=audio", "m=video"), validSdp.Replace("t=0 0", "t=invalid"),
      validSdp.Replace("o=- 123 1 IN IP4 127.0.0.1", "o=invalid")}) {
      context.Response.Body.Text = item;
      if (AnswerStatus(context) != "REALTIME_CALL_SDP_INVALID") { throw new Exception("Malformed synthetic SDP was accepted"); }
    }
    checks["invalidSdpRejected"] = true;
    context.Response.Body.Text = validSdp + "a=" + new string('x',65536 - validSdp.Length - 4) + "\r\n";
    checks["exactSdpByteLimit"] = AnswerStatus(context) == "VALID";
    context.Response.Body.Text += "x";
    checks["aboveOldSdpLimit"] = AnswerStatus(context) == "VALID";
    context.Response.Body.Text = validSdp + "a=" + new string('\u3042',30000) + "\r\n";
    checks["utf8AboveOldSdpLimit"] = AnswerStatus(context) == "VALID";
    checks["missingSecretResponse"] = SecretToken(context) == "";
    context.Variables["realtimeSecretResponse"] = null;
    checks["timeoutSecretResponse"] = SecretToken(context) == "";
    var expires = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 60;
    var response = new Response {StatusCode=200, Body=new Body()};
    context.Variables["realtimeSecretResponse"] = response;
    response.Body.Text = new JObject(new JProperty("value", "synthetic-ephemeral-not-a-credential"),new JProperty("expires_at", expires)).ToString();
    checks["validSecret"] = SecretToken(context) == "synthetic-ephemeral-not-a-credential";
    response.StatusCode = 401;
    checks["failedSecretStatus"] = SecretToken(context) == "";
    response.StatusCode = 200;
    foreach (var item in new string[] {"", "bad\r\nInjected: value", "with space", "with\ttab", "\u0000", "\u00e9", new string('a',8193)}) {
      response.Body.Text = new JObject(new JProperty("value",item),new JProperty("expires_at",expires)).ToString();
      if (SecretToken(context) != "") { throw new Exception("Unsafe synthetic token was accepted"); }
    }
    checks["invalidSecrets"] = true;
    foreach (var item in new string[] {"not-json", "{}", "[]", "{\"value\":42}", "{\"value\":\"synthetic\",\"expires_at\":1}",
      "{\"value\":\"synthetic\",\"expires_at\":\"untrusted\"}", new string('a',16385)}) {
      response.Body.Text = item;
      if (SecretToken(context) != "") { throw new Exception("Malformed synthetic secret response was accepted"); }
    }
    checks["malformedSecretResponses"] = true;
    context.Request.Headers["X-Core-Call-Ticket"] = new string[] {ticket};
    context.Request.Url.Query["call_id"] = new string[] {"rtc_synthetic-a"};
    checks["observerOwner"] = Observe(context);
    context.Request.Url.Query["model"] = new string[] {"unapproved"};
    checks["observerModelInjection"] = Observe(context);
    context.Request.Url.Query.Remove("model");
    context.Request.Url.Query["call_id"] = new string[] {"rtc_synthetic-b"};
    checks["observerOtherCall"] = Observe(context);
    context.Request.Url.Query["call_id"] = new string[] {"rtc_synthetic-a", "rtc_synthetic-b"};
    checks["observerDuplicateCall"] = Observe(context);
    context.Request.Url.Query["call_id"] = new string[] {"rtc_synthetic-a"};
    context.Variables["gatewayDecision"] = new JObject(new JProperty("callerId","caller-b"),new JProperty("contractId","contract-a")).ToString();
    checks["observerOtherCaller"] = Observe(context);
    return checks.ToString(Formatting.None);
  }
}'''


class PolicyStructureTests(unittest.TestCase):
    """Realtime接続開始・監視・停止のpolicyを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    def test_exception_constructors_stay_within_apim_supported_types(self):
        """APIMは.NETの全型を使えない。実際に拒否された派生例外を再導入しない。

        公式許可表: https://learn.microsoft.com/azure/api-management/api-management-policy-expressions
        通常のC#コンパイルだけではこの制約を検出できないため、全公開XMLも調べる。
        """
        constructors = []
        for path in POLICIES.glob("*.xml"):
            for name in re.findall(r"\bthrow\s+new\s+([A-Za-z_][A-Za-z0-9_.]*)\s*\(", path.read_text()):
                constructors.append(name)
                with self.subTest(policy=path.name, exception=name):
                    self.assertIn(name, ("Exception", "System.Exception"))
        self.assertTrue(constructors)

    def test_ticket_is_bound_to_verified_gateway_decision_and_not_forwarded(self):
        create = ET.parse(POLICIES / "realtime-create.xml")
        hangup = ET.parse(POLICIES / "realtime-hangup.xml")
        self.assertEqual(create.find("./inbound")[0].tag, "base")
        self.assertEqual(hangup.find("./inbound")[0].tag, "base")
        self.assertIsNotNone(hangup.find('./inbound/set-header[@name="X-Core-Call-Ticket"][@exists-action="delete"]'))
        self.assertIsNotNone(create.find('./outbound/set-header[@name="Cache-Control"]'))
        observer = ET.parse(POLICIES / "realtime-observer.xml")
        self.assertEqual(observer.getroot().tag, "fragment")
        self.assertIsNotNone(observer.find('./set-header[@name="X-Core-Call-Ticket"][@exists-action="delete"]'))

    def test_authorization_and_offer_validation_precede_fixed_private_secret_request(self):
        """親の認証を先に完了し、利用者のURLや認証headerを短期token取得へコピーしない。"""
        create = ET.parse(POLICIES / "realtime-create.xml")
        inbound = list(create.find("./inbound"))
        request = create.find("./inbound/send-request")
        self.assertEqual(inbound[0].tag, "base")
        self.assertLess(inbound.index(create.find("./inbound/choose")), inbound.index(request))
        self.assertEqual(request.get("mode"), "new")
        self.assertEqual(request.get("ignore-error"), "true")
        self.assertEqual(request.find("set-url").text, "__REALTIME_CLIENT_SECRETS_URL__")
        self.assertEqual(request.find("authentication-managed-identity").get("resource"), "https://ai.azure.com")
        self.assertIsNone(request.find("authentication-managed-identity").get("client-id"))
        self.assertIsNone(request.find('./set-header[@name="Authorization"]'))
        self.assertIsNone(create.find(".//trace"))
        self.assertIsNone(create.find(".//cache-store-value"))
        self.assertEqual(create.find('./inbound/set-header[@name="Content-Type"]/value').text, "application/sdp")
        self.assertEqual(create.find('./inbound/rewrite-uri').get("template"), "/realtime/calls")

    def test_secret_failure_is_fixed_and_audited_without_upstream_diagnostics(self):
        """下流のエラー本文・tokenを使わず、固定コードだけで失敗を報告する。"""
        create = ET.parse(POLICIES / "realtime-create.xml")
        failure = create.find('./inbound/choose/when/set-variable[@name="gatewayAuditCode"]/..')
        self.assertEqual(failure.find('./set-variable[@name="gatewayAuditCode"]').get("value"), "REALTIME_TOKEN_ACQUISITION_FAILED")
        self.assertIsNotNone(failure.find('./include-fragment[@fragment-id="core-gateway-telemetry"]'))
        response = failure.find("return-response")
        self.assertEqual(response.find("set-status").get("code"), "502")
        self.assertEqual(json.loads(response.find("set-body").text), {"error": {"code": "RealtimeTokenAcquisitionFailed"}})
        self.assertNotIn("realtimeSecretResponse", ET.tostring(response, encoding="unicode"))
        self.assertNotIn("realtimeEphemeralToken", ET.tostring(response, encoding="unicode"))
        # 成功時もtokenは裏側Authorizationにだけ設定し、公開応答のheader/bodyでは参照しない。
        self.assertIn("realtimeEphemeralToken", create.find('./inbound/set-header[@name="Authorization"]/value').text)
        self.assertNotIn("realtimeEphemeralToken", ET.tostring(create.find("outbound"), encoding="unicode"))

    def test_bicep_binds_secret_endpoint_to_the_registered_backend(self):
        """実Bicep出力を展開し、送信先が操作の利用者入力に依存しないことを調べる。"""
        from arm_helpers import PolicyExpression

        class RealtimeExpression(PolicyExpression):
            def resolve(self, value, parameters, index=0):
                if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
                    return RealtimeExpression(value, self.variables, self.cache).evaluate(parameters, index)
                return value

            def evaluate(self, parameters, index=0, tree=None, bindings=None):
                tree = self.tree if tree is None else tree
                if tree[0] == "call" and tree[1] == "flatten":
                    values = self.evaluate(parameters, index, tree[2][0], bindings)
                    return [entry for group in values for entry in group]
                return super().evaluate(parameters, index, tree, bindings)

        from bicep_helpers import bicep_executable
        bicep = bicep_executable()
        with tempfile.TemporaryDirectory(prefix="realtime-publication-") as folder:
            output = Path(folder) / "publication.json"
            result = subprocess.run([bicep, "build", str(CORE / "apps/gateway/apim/api-publication.bicep"), "--outfile", str(output)],
                env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": tempfile.gettempdir() + "/workforce-bicep-bundle"},
                capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr)
            template = json.loads(output.read_text())
        api = {"name": "model-webrtc", "apiType": "http", "backendUrl": "https://registered-fixture.openai.azure.com/openai/v1",
               "operations": [{"name": "realtime-create"}],
               "authorization": {"operationConstraints": {"realtime-create": {"kind": "realtimeCreate"}}}}
        expression = template["resources"]["operationPolicies"]["properties"]["value"]
        xml = RealtimeExpression(expression, template["variables"]).evaluate({"apiDefinitions": [api]}, 0)
        root = ET.fromstring(xml)
        self.assertEqual(root.find("./inbound/send-request/set-url").text,
                         "https://registered-fixture.openai.azure.com/openai/v1/realtime/client_secrets")
        self.assertNotIn("__REALTIME_CLIENT_SECRETS_URL__", xml)
        self.assertEqual(root.find("./inbound/send-request").get("mode"), "new")

    def test_created_call_response_failures_have_fixed_audit_and_public_codes(self):
        """LocationとMIME/SDPの失敗を区別し、下流header・SDPを転記しない。"""
        create = ET.parse(POLICIES / "realtime-create.xml")
        checks = create.findall("./outbound/choose/when/choose/when")
        self.assertEqual(len(checks), 2)
        self.assertEqual(checks[0].find('./set-variable[@name="gatewayAuditCode"]').get("value"), "REALTIME_CALL_LOCATION_INVALID")
        self.assertEqual(json.loads(checks[0].find("return-response/set-body").text), {"error": {"code": "RealtimeCallLocationInvalid"}})
        for node in checks:
            self.assertEqual(node.findall('./set-variable[@name="gatewayAuditStatus"]')[-1].get("value"), "@(502)")
            self.assertIsNotNone(node.find('./include-fragment[@fragment-id="core-gateway-telemetry"]'))
            response = node.find("return-response")
            self.assertEqual(response.find("set-status").get("code"), "502")
            self.assertEqual(response.find('./set-header[@name="Cache-Control"]/value').text, "no-store")
            self.assertNotIn("context.Response", ET.tostring(response, encoding="unicode"))
            self.assertIsNone(node.find("trace"))
        self.assertEqual(checks[1].findall('./set-variable[@name="gatewayAuditCode"]')[-1].get("value"), '@((string)context.Variables["realtimeAnswerStatus"])')
        status = create.find('./outbound/choose/when/set-variable[@name="realtimeAnswerStatus"]').get("value")
        self.assertIn('preserveContent: true', status)
        self.assertEqual(create.find('./outbound/choose/when/set-header[@name="Content-Type"]/value').text, "application/sdp")


    def test_created_call_is_reclaimed_before_returning_an_invalid_answer(self):
        """既知の通話だけを一度終了し、終了未確認の復旧情報を秘密本文と分ける。"""
        create = ET.parse(POLICIES / "realtime-create.xml")
        branch = create.findall("./outbound/choose/when/choose/when")[1]
        cleanup = branch.find("send-request")
        self.assertEqual(cleanup.attrib, {"mode": "new", "response-variable-name": "realtimeCleanupResponse", "timeout": "10", "ignore-error": "true"})
        self.assertEqual(cleanup.find("set-method").text, "POST")
        self.assertEqual(cleanup.find("set-body").text, "{}")
        self.assertEqual(cleanup.find("authentication-managed-identity").get("resource"), "https://cognitiveservices.azure.com")
        target = cleanup.find("set-url").text
        self.assertIn("__REALTIME_CLIENT_SECRETS_URL__", target)
        self.assertNotIn("context.Request", target)
        self.assertNotIn("context.Response", target)
        self.assertNotIn("realtimeEphemeralToken", ET.tostring(cleanup, encoding="unicode"))
        # Locationが不正でcall IDを確定できない場合は、未知のURLへ停止要求を送らない。
        self.assertIsNone(create.findall("./outbound/choose/when/choose/when")[0].find("send-request"))
        children = list(create.find("./outbound/choose/when"))
        ticket = create.find('./outbound/choose/when/set-variable[@name="realtimeCallTicket"]')
        answer = create.find('./outbound/choose/when/set-variable[@name="realtimeAnswerStatus"]')
        self.assertLess(children.index(ticket), children.index(answer))
        response = branch.find("return-response")
        self.assertIn("realtimeCleanupAccepted", response.find('set-header[@name="X-Core-Cleanup-Status"]/value').text)
        for name in ("X-Core-Call-Id", "X-Core-Call-Ticket"):
            self.assertIn('realtimeCleanupAccepted"] ? ""', response.find(f'set-header[@name="{name}"]/value').text)
        body = response.find("set-body").text
        self.assertIn("cleanupStatus", body)
        for private in ("realtimeCallId", "realtimeCallTicket", "context.Response"):
            self.assertNotIn(private, body)
        self.assertEqual(len(branch.findall('include-fragment[@fragment-id="core-gateway-telemetry"]')), 2)


class PolicyExpressionTests(unittest.TestCase):
    """Realtime接続開始・監視・停止のpolicyを検証するためのケースをまとめる。正常系と拒否・失敗系を分け、実機確認と混同しない。"""
    @classmethod
    def setUpClass(cls):
        if not PWSH: raise unittest.SkipTest("PowerShell with Roslyn is required")
        source = SOURCE.replace("__OFFER__", expression("realtime-create.xml", './inbound/set-variable[@name="realtimeSdp"]', "value"))
        source = source.replace("__SECRET_REQUEST__", expression("realtime-create.xml", "./inbound/send-request/set-body"))
        source = source.replace("__SECRET_TOKEN__", expression("realtime-create.xml", './inbound/set-variable[@name="realtimeEphemeralToken"]', "value"))
        raw_sdp = ET.parse(POLICIES / "realtime-create.xml").find("./inbound/set-body").text.strip()
        assert raw_sdp.startswith("@(") and raw_sdp.endswith(")")
        source = source.replace("__RAW_SDP__", raw_sdp[2:-1])
        source = source.replace("__CALL_LOCATION__", expression("realtime-create.xml", './outbound/choose/when/set-variable[@name="realtimeCallId"]', "value"))
        source = source.replace("__ANSWER_STATUS__", expression("realtime-create.xml", './outbound/choose/when/set-variable[@name="realtimeAnswerStatus"]', "value"))
        source = source.replace("__CLEANUP_URL__", expression("realtime-create.xml", './outbound/choose/when/choose/when/send-request/set-url').replace("__REALTIME_CLIENT_SECRETS_URL__", "https://registered-fixture.openai.azure.com/openai/v1/realtime/client_secrets"))
        source = source.replace("__CLEANUP_ACCEPTED__", expression("realtime-create.xml", './outbound/choose/when/choose/when/set-variable[@name="realtimeCleanupAccepted"]', "value"))
        source = source.replace("__CLEANUP_FAILURE_BODY__", expression("realtime-create.xml", './outbound/choose/when/choose[2]/when/return-response/set-body'))
        source = source.replace("__SIGN__", expression("realtime-create.xml", './outbound/choose/when/set-variable[@name="realtimeCallTicket"]', "value"))
        source = source.replace("__VERIFY__", expression("realtime-hangup.xml", './inbound/set-variable[@name="realtimeCallOwnerValid"]', "value"))
        source = source.replace("__OBSERVE__", expression("realtime-observer.xml", './set-variable[@name="realtimeObserverOwnerValid"]', "value"))
        with tempfile.TemporaryDirectory(prefix="realtime-policy-") as directory:
            folder = Path(directory); (folder / "policy.cs").write_text(source)
            (folder / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$references=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $references -CompilerOptions '/nowarn:1701'
[TicketFixture]::Run()
""")
            result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(folder / "run.ps1")], capture_output=True, text=True, timeout=60)
            if result.returncode: raise AssertionError("Actual realtime XML expressions did not compile/execute: " + result.stderr)
            cls.checks = json.loads(result.stdout)

    def test_only_matching_verified_owner_contract_and_call_is_authorized(self):
        self.assertTrue(self.checks["owner"])
        self.assertTrue(self.checks["otherContract"])
        for name in ("otherCaller", "otherCall", "pathInjection"):
            with self.subTest(name=name): self.assertFalse(self.checks[name])

    def test_optional_person_tokens_do_not_change_subscription_ownership(self):
        """購読が同じなら、API固有の追加tokenは音声ticketの所有者を変更しない。"""
        for name in ("otherVerifiedPrincipalSameLabels", "otherTenantSameLabels",
                     "missingVerifiedPrincipal", "ambiguousVerifiedPrincipal",
                     "observerOtherVerifiedPrincipalSameLabels", "observerOtherTenantSameLabels"):
            with self.subTest(name=name): self.assertTrue(self.checks[name])

    def test_verified_subscription_cannot_be_replaced_by_a_caller_label(self):
        for name in ("otherSubscriptionSameLabels","observerOtherSubscriptionSameLabels",
                     "missingVerifiedSubscription","emptyVerifiedSubscription"):
            with self.subTest(name=name): self.assertFalse(self.checks[name])

    def test_expired_modified_missing_and_forged_tickets_are_denied(self):
        for name in ("modified", "missing", "malformed", "oversized", "expired", "forgedPayload"):
            with self.subTest(name=name): self.assertFalse(self.checks[name])

    def test_offer_cannot_override_approved_model_or_session(self):
        self.assertTrue(self.checks["offerValid"]); self.assertTrue(self.checks["fixedSecretRequest"])
        self.assertTrue(self.checks["rawSdpUnchanged"])
        for name in ("offerExtraModel", "offerInvalid", "offerNotString"):
            with self.subTest(name=name): self.assertFalse(self.checks[name])

    def test_sideband_requires_the_signed_owner_and_exact_call_query(self):
        self.assertTrue(self.checks["observerOwner"])
        for name in ("observerModelInjection", "observerOtherCall", "observerDuplicateCall", "observerOtherCaller"):
            with self.subTest(name=name): self.assertFalse(self.checks[name])

    def test_ephemeral_response_is_validated_before_use(self):
        for name in ("missingSecretResponse", "timeoutSecretResponse", "validSecret", "failedSecretStatus",
                     "invalidSecrets", "malformedSecretResponses"):
            with self.subTest(name=name): self.assertTrue(self.checks[name])

    def test_failed_answer_cleanup_has_a_fixed_target_and_explicit_outcome(self):
        for name in ("cleanupFixedTarget", "cleanupIgnoresResponseHost", "cleanupMissingUnconfirmed",
                     "cleanupTimeoutUnconfirmed", "cleanupSuccessStatuses", "cleanupFailureStatuses", "cleanupFixedFailureBody"):
            with self.subTest(name=name): self.assertTrue(self.checks[name])

    def test_created_call_response_requires_allowlisted_mime_and_valid_bounded_sdp(self):
        for name in ("callLocationValid", "callLocationsInvalid", "validSdpAndPlain", "unsafeMimeClassified",
                     "invalidSdpRejected", "exactSdpByteLimit", "aboveOldSdpLimit", "utf8AboveOldSdpLimit"):
            with self.subTest(name=name): self.assertTrue(self.checks[name])


if __name__ == "__main__": unittest.main()
