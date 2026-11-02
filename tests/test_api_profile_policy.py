"""統合APIの実C#ポリシーを実行し、購読と操作別の入力制約を確認する。

ネットワークやAzureを呼ばず、profile選択・購読の認可・WSモデル選択と
選択後の既存上限検査を、本番XMLから取り出した式で検証する。
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_api_protocols import CSHARP as EXISTING_CSHARP, actual_expression, bound_coverage, configuration
from test_gateway_limits_telemetry import LIMIT_EXPRESSION, PWSH


CORE = Path(__file__).resolve().parents[1]
CLASSES = EXISTING_CSHARP.split("public static class ExpandedPolicyFixture")[0].replace(
    'public class Body { public string Data; public int Reads; public T As<T>(bool preserveContent=false) { Reads++; return (T)(object)JObject.Parse(Data); } }',
    '''public class Body {
 public string Data; public int Reads;
 public T As<T>(bool preserveContent=false) {
  Reads++;
  if (!preserveContent) throw new Exception("Request body was consumed");
  return typeof(T)==typeof(byte[]) ? (T)(object)Encoding.UTF8.GetBytes(Data) : (T)(object)JObject.Parse(Data);
 }
}''')

SOURCE = CLASSES + r'''
public static class IntegratedProfileFixture {
 public static string Profile(Context context) { __PROFILE__ }
 public static string Auth(Context context) { __AUTH__ }
 public static string Limits(Context context) { __LIMITS__ }
 public static string Model(Context context) { __MODEL__ }
 public static string ProtocolAuth(Context context) { __PROTOCOL_AUTH__ }
 public static bool Query(Context context) { __QUERY__ }
 public static string Run(string input) {
  var results = new JObject();
  foreach (JObject item in JArray.Parse(input)) {
   var c = new Context();
   c.Operation.Id = "chat-completions";
   c.Request.Body = new Body {Data=item["rawBody"]!=null?(string)item["rawBody"]:(item["body"]??new JObject()).ToString(Formatting.None)};
   c.Variables["config"] = item["config"].ToString(Formatting.None);
   c.Variables["websocket"] = (item["websocket"]??new JObject()).ToString(Formatting.None);
   c.Variables["gatewayKeyHeader"] = "api-key";
   foreach(var entry in ((JObject)(item["headers"]??new JObject())).Properties()) c.Request.Headers[entry.Name]=entry.Value is JArray?entry.Value.Select(x=>(string)x).ToArray():new[] {(string)entry.Value};
   foreach(var entry in ((JObject)(item["query"]??new JObject())).Properties()) c.Request.Url.Query[entry.Name]=entry.Value is JArray?entry.Value.Select(x=>(string)x).ToArray():new[] {(string)entry.Value};
   if(item["key"]!=null) { c.Subscription=new Named {Id=(string)item["key"]};c.Product=new Named {Id="tool-users"};c.Request.Headers["api-key"]=new[] {"fixture-value"}; }
   var result=new JObject();
   if((string)item["mode"]=="ws") {
    var model=Model(c);c.Variables["gatewayProtocolModel"]=model;
    result["model"]=model;
    result["decision"]=JObject.Parse(ProtocolAuth(c));
    result["queryAllowed"]=Query(c);
   } else {
    var selection=JObject.Parse(Profile(c));c.Variables["gatewayProfileSelection"]=selection.ToString(Formatting.None);
    result["profile"]=selection;
    if((bool)selection["allowed"]) {
     result["decision"]=JObject.Parse(Auth(c));
     c.Variables["gatewayOperationConstraint"]=selection["constraint"].ToString(Formatting.None);
     result["limits"]=JObject.Parse(Limits(c));
    }
   }
   result["reads"]=c.Request.Body.Reads;
   result["bodyAfter"]=c.Request.Body.Data;
   results[(string)item["id"]]=result;
  }
  return results.ToString(Formatting.None);
 }
}
'''


def case(name, **patch):
    profiles = [{"profileId": profile, "kind": "bodyModel", "bodyFormat": "json", "maxRequestBytes": size,
                 "allowedModels": models, "modalities": modalities,
                 "maxOutputTokens": 32, "outputTokenParameter": "max_completion_tokens"}
                for profile, size, models, modalities in (
                    ("chat", 512, ["shared"], ["text"]),
                    ("vision", 2048, ["shared"], ["text", "image"]),
                    ("audio-chat", 2048, ["audio"], ["text", "audio"]),
                    ("pdf", 2048, ["shared"], ["text", "file"]))]
    config = {"mode": "subscriptionKey",
              "operationConstraints": {"chat-completions": {"kind": "bodyModel", "defaultProfile": "chat", "profiles": profiles}}}
    item = {"id": name, "config": config, "key": "subscription-fixture",
            "body": {"model": "shared", "messages": [{"role": "user", "content": "synthetic"}], "max_completion_tokens": 16}}
    item.update(patch)
    return item


class ProfilePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PWSH:
            raise unittest.SkipTest("Actual C# policy execution requires PowerShell")
        cases = [case("default"), case("explicit", headers={"X-Egakumi-Profile": "vision"}),
                 case("unknown", headers={"X-Egakumi-Profile": "unknown"}),
                 case("duplicate", headers={"X-Egakumi-Profile": ["chat", "vision"]}),
                 case("wrong-model", headers={"X-Egakumi-Profile": "vision"}, body={"model": "audio"}),
                 case("huge-malformed", rawBody="{" + "x" * 2049)]
        # 実際の登録例から取り出したprofileで、モデルと出力項目の組を実行検査します。
        apis = {a["name"]: a for a in configuration.normalize_configuration(bound_coverage(), base_directory=CORE / "config")["apiDefinitions"]}
        inference = apis["openai-inference-v1"]["authorization"]
        for model in ("DeepSeek-V4-Flash", "DeepSeek-V4-Pro", "Mistral-Large-3", "grok-4.7"):
            cases.append(case(model, config=inference, headers={"X-Egakumi-Profile": "compatible"}, body={"model": model, "max_tokens": 64}))
        cases.append(case("compatible-wrong-token-field", config=inference, headers={"X-Egakumi-Profile": "compatible"}, body={"model": "grok-4.7", "max_completion_tokens": 64}))
        cases.append(case("router", config=inference, headers={"X-Egakumi-Profile": "router"}, body={"model": "model-router", "max_completion_tokens": 64}))
        flux = apis["blackforest-images-v1"]["authorization"]
        flux = dict(flux, operationConstraints={"chat-completions": flux["operationConstraints"]["flux2-generate"]})
        cases.append(case("flux-pro", config=flux, headers={"X-Egakumi-Profile": "pro"}, body={"model": "FLUX.2-pro", "prompt": "synthetic"}))
        cases.append(case("flux-wrong-profile", config=flux, headers={"X-Egakumi-Profile": "flex"}, body={"model": "FLUX.2-pro", "prompt": "synthetic"}))
        # 公開JSON上限にWAFの2000KiB検査枠から余裕を取り、APIMの等号/超過を実式で検証します。
        maximum = flux["operationConstraints"]["chat-completions"]["profiles"][0]["maxRequestBytes"]
        for excess in (0, 1):
            body = json.dumps({"model": "FLUX.2-flex", "prompt": ""}, separators=(",", ":"))
            body = body.replace('"prompt":""', '"prompt":"' + "a" * (maximum + excess - len(body.encode("utf-8"))) + '"')
            cases.append(case("flux-byte-limit-" + str(excess), config=flux, headers={"X-Egakumi-Profile": "flex"}, rawBody=body))
        image = {"model": "shared", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}], "max_completion_tokens": 16}
        for name, headers in (("image-default-denied", {}), ("image-authorized", {"X-Egakumi-Profile": "vision"})):
            cases.append(case(name, headers=headers, body=image))
        cases.append(case("audio-default-denied", body={"model": "shared", "modalities": ["text", "audio"], "max_completion_tokens": 16}))
        cases.append(case("file-default-denied", body={"model": "shared", "messages": [{"content": [{"type": "file", "file": {"file_data": "AA=="}}]}]}))
        cases.append(case("selected-size", body={"model": "shared", "messages": [{"content": "x" * 600}], "max_completion_tokens": 16}))
        for name, query, models in (("ws-default", {}, ["standard"]), ("ws-mini", {"model": "mini"}, ["standard", "mini"]),
                                    ("ws-mini-denied", {"model": "mini"}, ["standard"]),
                                    ("ws-unknown", {"model": "unknown"}, ["standard", "mini"]),
                                    ("ws-duplicate", {"model": ["standard", "mini"]}, ["standard", "mini"]),
                                    ("ws-unknown-query", {"model": "mini", "backend": "other"}, ["mini"])):
            item = case(name, mode="ws", query=query, websocket={"allowedModels": models, "defaultModel": "standard", "queryParameters": {"api-version": "fixed"}})
            cases.append(item)
        source = SOURCE.replace("__PROFILE__", actual_expression("gateway.xml", "gatewayProfileSelection"))
        source = source.replace("__AUTH__", actual_expression("gateway.xml", "gatewayDecision")).replace("__LIMITS__", LIMIT_EXPRESSION)
        model = actual_expression("protocol.xml", "gatewayProtocolModel").replace(
            'JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(encoded)))', 'JObject.Parse((string)context.Variables["websocket"])').replace('var encoded = "__WEBSOCKET_SETTINGS_BASE64__";', 'var encoded = "fixture";')
        query = actual_expression("protocol.xml", "gatewayProtocolQueryAllowed").replace(
            'JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(encoded)))', 'JObject.Parse((string)context.Variables["websocket"])["queryParameters"] as JObject').replace('var encoded = "__WEBSOCKET_QUERY_BASE64__";', 'var encoded = "fixture";')
        source = source.replace("__MODEL__", model).replace("__PROTOCOL_AUTH__", actual_expression("protocol.xml", "gatewayDecision")).replace("__QUERY__", query)
        with tempfile.TemporaryDirectory(prefix="core-profile-policy-") as temporary:
            path = Path(temporary)
            (path / "policy.cs").write_text(source)
            (path / "cases.json").write_text(json.dumps(cases))
            (path / "run.ps1").write_text("""$ErrorActionPreference='Stop'
$refs=@(Get-ChildItem (Join-Path $PSHOME 'ref/*.dll') | ForEach-Object FullName)+@([Newtonsoft.Json.Linq.JObject].Assembly.Location)
Add-Type -TypeDefinition (Get-Content -Raw (Join-Path $PSScriptRoot 'policy.cs')) -ReferencedAssemblies $refs -CompilerOptions '/nowarn:1701'
[IntegratedProfileFixture]::Run((Get-Content -Raw (Join-Path $PSScriptRoot 'cases.json')))
""")
            run = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path / "run.ps1")],
                                 capture_output=True, text=True, timeout=60,
                                 env={**os.environ, "DOTNET_BUNDLE_EXTRACT_BASE_DIR": str(path / "dotnet")})
            if run.returncode:
                raise AssertionError("Actual integrated policy execution failed:\n" + run.stderr)
            cls.results = json.loads(run.stdout)

    def test_profile_selection_preserves_the_authenticated_caller(self):
        self.assertTrue(self.results["default"]["decision"]["allowed"])
        self.assertTrue(self.results["explicit"]["decision"]["allowed"])
        self.assertEqual(self.results["explicit"]["decision"]["profileId"], "vision")
        self.assertEqual(self.results["default"]["decision"]["callerId"], self.results["explicit"]["decision"]["callerId"])

    def test_model_and_modality_cannot_bypass_default_or_selected_profile(self):
        for name in ("unknown", "duplicate", "wrong-model", "image-default-denied", "audio-default-denied", "file-default-denied"):
            self.assertFalse(self.results[name]["profile"]["allowed"], name)
        self.assertTrue(self.results["image-authorized"]["profile"]["allowed"])

    def test_bytes_are_bounded_before_json_and_again_for_selected_profile(self):
        self.assertEqual(self.results["huge-malformed"]["profile"]["status"], 413)
        self.assertEqual(self.results["huge-malformed"]["reads"], 1)
        self.assertTrue(self.results["selected-size"]["profile"]["allowed"])
        self.assertEqual(self.results["selected-size"]["limits"]["status"], 413)

    def test_ws_selection_enforces_the_api_model_and_query_allowlists(self):
        for name in ("ws-default", "ws-mini"):
            self.assertTrue(self.results[name]["decision"]["allowed"], name)
            self.assertTrue(self.results[name]["queryAllowed"], name)
        for name in ("ws-mini-denied", "ws-unknown", "ws-duplicate"):
            self.assertEqual(self.results[name]["model"], "!invalid-model!")
            self.assertFalse(self.results[name]["queryAllowed"])
        self.assertFalse(self.results["ws-unknown-query"]["queryAllowed"])
        for name, result in self.results.items():
            if name.startswith("ws-"):
                self.assertEqual(result["reads"], 0)

    def test_added_chat_and_flux_models_use_their_own_profile_contracts(self):
        for name in ("DeepSeek-V4-Flash", "DeepSeek-V4-Pro", "Mistral-Large-3", "grok-4.7", "router", "flux-pro"):
            self.assertTrue(self.results[name]["profile"]["allowed"], name)
            self.assertTrue(self.results[name]["limits"]["allowed"], name)
        self.assertFalse(self.results["compatible-wrong-token-field"]["limits"]["allowed"])
        self.assertFalse(self.results["flux-wrong-profile"]["profile"]["allowed"])

    def test_published_json_boundary_retains_waf_inspection_headroom(self):
        accepted = self.results["flux-byte-limit-0"]
        self.assertTrue(accepted["profile"]["allowed"])
        self.assertTrue(accepted["limits"]["allowed"])
        self.assertEqual(self.results["flux-byte-limit-1"]["profile"]["status"], 413)
        for name in ("blackforest-images-v1", "openai-inference-v1"):
            definition = json.loads((CORE / "apps/gateway/apim/apis" / (name + ".json")).read_text())
            def inspect(value):
                if isinstance(value, dict):
                    if value.get("maxRequestBytes", 0) > 32768:
                        self.assertLessEqual(value["maxRequestBytes"], 2_000_000)
                        self.assertGreater(2000 * 1024 - value["maxRequestBytes"], 1024)
                    for child in value.values(): inspect(child)
                elif isinstance(value, list):
                    for child in value: inspect(child)
            inspect(definition)


if __name__ == "__main__":
    unittest.main()
