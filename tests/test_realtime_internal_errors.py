"""Realtimeの内部受取り不備を外部チケット403から分け、通話開始前の停止を確認する。"""
import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from arm_helpers import compiled_policy_value
from test_common_authentication import compile_inbound
from test_contract_rate_publication import ContractRatePublicationTests
from test_gateway_limits_telemetry import PWSH
from test_mcp_argument_validation import ERROR_CSHARP, mcp_error_pipeline
from test_realtime_policy import policy_tree, expression


OWNER_ID = "core-realtime-owner-context"
SETTINGS_ID = "core-realtime-session-settings"
TICKET_IDS = {"create": "core-realtime-ticket-generation", "hangup": "core-realtime-hangup-owner",
              "observer": "core-realtime-observer-owner"}


FIXTURE = ERROR_CSHARP.split("public static class McpFixture {", 1)[0].replace(
    "public class Request {public Body Body=new Body();}",
    "public class Url {public Dictionary<string,string[]> Query=new Dictionary<string,string[]>();}"
    "public class Request {public Body Body=new Body();public Url Url=new Url();"
    "public Dictionary<string,string[]> Headers=new Dictionary<string,string[]>();"
    "public Dictionary<string,string> MatchedParameters=new Dictionary<string,string>();}"
) + r'''
public static class RealtimeHeaderExtensions {
 public static string GetValueOrDefault(this Dictionary<string,string[]> values,string key,string fallback) {
  return values.ContainsKey(key)?String.Join(",",values[key]):fallback;
 }
}
public static class RealtimeErrorFixture {
 __METHODS__
 static dynamic CreateTicket(Context context) {__CREATE_TICKET__}
 static dynamic HangupTicket(Context context) {__HANGUP_TICKET__}
 static dynamic ObserverTicket(Context context) {__OBSERVER_TICKET__}
 static int Prepare(Context context) {__PREPARE__ return 200;}
 static int Error(Context context,string route) {__ERROR_SELECTORS__ throw new Exception("Unknown route");}
 public static string Run(string json) {
  var output=new JObject();
  foreach(JObject item in JArray.Parse(json)) {
   var c=new Context();c.Request.Body.Locked=true;
   c.Variables["gatewayAuthenticatedSubject"]=new JObject(new JProperty("status","verified"),
    new JProperty("kind","apimSubscription"),new JProperty("method","subscriptionKey"),
    new JProperty("callerId","subscription:fixture-subscription"),new JProperty("subscriptionId","fixture-subscription")).ToString(Formatting.None);
   c.Variables["gatewayDecision"]=new JObject(new JProperty("allowed",true),new JProperty("method","subscriptionKey"),
    new JProperty("callerId","subscription:fixture-subscription")).ToString(Formatting.None);
   c.Variables["gatewayOperationConstraint"]="{\"sessionTtlSeconds\":600}";
   c.Variables["gatewayUserAuthentication"]="OLD_USER_JSON_MUST_NOT_BE_READ";
   c.Variables["gatewayEntraAuthenticationValidated"]=false;
   foreach(var name in new [] {"gatewayDecision","gatewayOperationConstraint"}) {
    if(item["missing"]!=null&&(string)item["missing"]==name)c.Variables.Remove(name);
    if(item[name]!=null)c.Variables[name]=item[name].Type==JTokenType.Null?null:
      item[name].Type==JTokenType.String?(object)(string)item[name]:(object)item[name];
   }
   var propagated=false;var status=0;
   if(item["error"] is JObject error) {
    c.LastError.PolicyId=(string)error["policyId"];c.LastError.Reason=(string)error["reason"];
    status=Error(c,(string)item["route"]);
   } else {
    try {
     status=Prepare(c);
     if(item["ticketFault"]!=null) {
      var route=(string)item["route"];
      c.Variables["fixtureCurrentPolicyId"]=route=="create"?"core-realtime-ticket-generation":
       route=="hangup"?"core-realtime-hangup-owner":"core-realtime-observer-owner";
      var field=(string)item["ticketFault"];
      if(field=="invalid-owner")c.Variables["realtimeOwnerContext"]="{SECRET_INTERNAL_JSON";
      else c.Variables.Remove(field);
      if(route=="create")CreateTicket(c);else if(route=="hangup")HangupTicket(c);else ObserverTicket(c);
     }
    }
    catch {
     propagated=true;c.LastError.PolicyId=(string)c.Variables["fixtureCurrentPolicyId"];
     c.LastError.Reason="ExpressionValueEvaluationFailure";status=Error(c,(string)item["route"]);
    }
   }
   output[(string)item["id"]]=new JObject(new JProperty("status",status),new JProperty("propagated",propagated),
    new JProperty("policyId",c.LastError.PolicyId),new JProperty("requestId",c.RequestId.ToString()),
    new JProperty("hasBody",c.Variables.ContainsKey("fixtureResponseHasBody")&&(bool)c.Variables["fixtureResponseHasBody"]),
    new JProperty("reachedExternalBoundary",c.Variables.ContainsKey("fixtureExternalBoundary")),
    new JProperty("bodyReads",c.Request.Body.Reads),new JProperty("headers",JObject.FromObject(c.ResponseHeaders)),
    new JProperty("audits",new JArray(c.TraceMessages.Select(JObject.Parse))));
  }
  return output.ToString(Formatting.None);
 }
}'''


def runtime(template, parameters, cases):
    adapted=ET.Element("policies");inbound=ET.SubElement(adapted,"inbound")
    snippet=policy_tree("realtime-owner-context.xml").getroot()
    for node in snippet:
        if node.tag!="set-variable":continue
        ET.SubElement(inbound,"set-variable",{"name":"fixtureCurrentPolicyId","value":node.get("id")})
        inbound.append(copy.deepcopy(node))
    # 外部呼出し自体は実行しない。実XMLの受取りが成功してからこの境界へ進めるかを確認する。
    ET.SubElement(inbound,"set-variable",{"name":"fixtureExternalBoundary","value":"@(true)"})
    methods,prepare=compile_inbound(adapted)
    selectors=[]
    http_index=next(i for i,a in enumerate(parameters["apiDefinitions"]) if a["apiType"]=="http")
    ws_index=next(i for i,a in enumerate(parameters["apiDefinitions"]) if a["apiType"]=="websocket")
    parent=ET.fromstring(compiled_policy_value(template,parameters,http_index))
    for route in TICKET_IDS:
        if route=="observer":
            policy=ET.fromstring(compiled_policy_value(template,parameters,ws_index))
        else:
            policy=policy_tree("realtime-"+route+".xml").getroot()
            section=policy.find("on-error")
            for index,node in reversed(list(enumerate(list(section)))):
                if node.tag=="base":
                    section.remove(node)
                    for offset,child in enumerate(parent.find("on-error")):
                        section.insert(index+offset,copy.deepcopy(child))
        actual,body=mcp_error_pipeline(policy,template,parameters)
        prefix="Realtime"+route.title()+"Expression"
        rename=lambda value:re.sub(r"\bExpression(\d+)\b",lambda m:prefix+m[1],value)
        methods+="\n"+rename(actual)
        selectors.append("if(route=="+json.dumps(route)+") {"+rename(body)+" return 0;}")
    source=FIXTURE.replace("__METHODS__",methods).replace("__PREPARE__",prepare).replace("__ERROR_SELECTORS__","\n".join(selectors))
    for route, filename, xpath in (
        ("CREATE", "realtime-create.xml", './outbound/choose/when/set-variable[@name="realtimeCallTicket"]'),
        ("HANGUP", "realtime-hangup.xml", './inbound/set-variable[@name="realtimeCallOwnerValid"]'),
        ("OBSERVER", "realtime-observer.xml", './set-variable[@name="realtimeObserverOwnerValid"]'),
    ):
        source=source.replace("__"+route+"_TICKET__", expression(filename, xpath, "value"))
    with tempfile.TemporaryDirectory(prefix="realtime-internal-errors-") as directory:
        folder=Path(directory)
        (folder/"fixture.cs").write_text(source,encoding="utf-8")
        (folder/"cases.json").write_text(json.dumps(cases),encoding="utf-8")
        (folder/"run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll')|ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'fixture.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[RealtimeErrorFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""",encoding="utf-8")
        return subprocess.run([PWSH,"-NoProfile","-File",str(folder/"run.ps1")],capture_output=True,text=True,encoding="utf-8",timeout=60)


class RealtimeInternalErrorTests(unittest.TestCase):
    setUpClass=classmethod(ContractRatePublicationTests.setUpClass.__func__)

    def test_internal_receiver_failures_stop_before_external_calls_and_are_not_ticket_403(self):
        if not PWSH:self.skipTest("PowerShell Roslyn is required")
        cases=[];expected={}
        faults=[("missing-owner",{"missing":"gatewayDecision"},OWNER_ID),
                ("missing-settings",{"missing":"gatewayOperationConstraint"},SETTINGS_ID)]
        for variable,policy_id in (("gatewayDecision",OWNER_ID),("gatewayOperationConstraint",SETTINGS_ID)):
            for name,value in (("null",None),("wrong-type",42),("invalid-json","{SECRET_INTERNAL_JSON"),
                               ("json-null","null"),("json-array","[]"),("empty-object","{}")):
                faults.append((variable+"-"+name,{variable:value},policy_id))
        for value in ({"method":"user"},{"method":"user","callerId":""},{"callerId":"synthetic"}):
            faults.append(("owner-fields-"+str(len(faults)),{"gatewayDecision":json.dumps(value)},OWNER_ID))
        for route in TICKET_IDS:
            for name,patch,policy_id in faults:
                identifier=route+"-"+name;cases.append({"id":identifier,"route":route,**patch})
                expected[identifier]=(policy_id,"OPERATION_SETTINGS_INVALID" if policy_id==SETTINGS_ID else "AUTHENTICATION_PROCESSING_ERROR")
            cases.append({"id":route+"-normal","route":route})
        result=runtime(self.template,self.parameters,cases)
        self.assertEqual(result.returncode,0,result.stderr);values=json.loads(result.stdout)
        for identifier,(policy_id,code) in expected.items():
            with self.subTest(case=identifier):
                value=values[identifier];self.assertTrue(value["propagated"])
                self.assertEqual(value["policyId"],policy_id);self.assertEqual(value["status"],500)
                self.assertFalse(value["reachedExternalBoundary"]);self.assertFalse(value["hasBody"])
                self.assertEqual(value["headers"],{"apim-request-id":value["requestId"],"Cache-Control":"no-store"})
                gateway=[a for a in value["audits"] if a["event"]=="gateway-request"]
                self.assertEqual(len(gateway),1)
                self.assertEqual((gateway[0]["phase"],gateway[0]["code"],gateway[0]["status"]),("policy-error",code,500))
        for route in TICKET_IDS:
            value=values[route+"-normal"]
            self.assertEqual(value["status"],200);self.assertTrue(value["reachedExternalBoundary"])
            self.assertFalse(value["propagated"]);self.assertEqual(value["audits"],[])
        for value in values.values():self.assertEqual(value["bodyReads"],0)
        self.assertNotIn("SECRET_INTERNAL_JSON",json.dumps(values))

    def test_only_realtime_policy_ids_use_the_new_500_and_receipt_precedes_token_acquisition(self):
        if not PWSH:self.skipTest("PowerShell Roslyn is required")
        cases=[]
        for route,ticket_id in TICKET_IDS.items():
            for field in ("realtimeOwnerContext", "invalid-owner", "realtimeSessionTtlSeconds"):
                cases.append({"id":route+"-ticket-fault-"+field,"route":route,"ticketFault":field})
            for policy_id in (OWNER_ID,SETTINGS_ID,ticket_id,OWNER_ID+"-other","other-policy",None,
                              "core-entra-token-validation","core-rate-limit"):
                reason="TokenValidationFailed" if policy_id=="core-entra-token-validation" else "RateLimitExceeded" if policy_id=="core-rate-limit" else "ExpressionValueEvaluationFailure"
                cases.append({"id":route+"-"+str(policy_id),"route":route,"error":{"policyId":policy_id,"reason":reason}})
        result=runtime(self.template,self.parameters,cases)
        self.assertEqual(result.returncode,0,result.stderr);values=json.loads(result.stdout)
        for item in cases:
            value=values[item["id"]];policy_id=item["error"]["policyId"] if "error" in item else TICKET_IDS[item["route"]]
            expected=500 if policy_id in (OWNER_ID,SETTINGS_ID,TICKET_IDS[item["route"]]) else 401 if policy_id=="core-entra-token-validation" else 429 if policy_id=="core-rate-limit" else 0
            with self.subTest(case=item["id"]):
                self.assertEqual(value["status"],expected);self.assertFalse(value["hasBody"])
                if "ticketFault" in item:
                    self.assertTrue(value["propagated"]);self.assertEqual(value["policyId"],policy_id)
        create=policy_tree("realtime-create.xml").getroot()
        inbound=list(create.find("inbound"));token=create.find("inbound/send-request")
        self.assertEqual(inbound[0].tag,"base")
        for name in ("realtimeOwnerContext","realtimeSessionTtlSeconds"):
            self.assertLess(inbound.index(create.find('inbound/set-variable[@name="'+name+'"]')),inbound.index(token))
        for filename in ("realtime-create.xml","realtime-hangup.xml","realtime-observer.xml"):
            tree=policy_tree(filename)
            self.assertEqual(len(tree.findall('.//set-variable[@name="realtimeOwnerContext"]')),1)


if __name__=="__main__":unittest.main()
