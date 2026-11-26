// BEGIN caller
// 目的：先行の共通認証・認証結果の引き渡しで確定した呼出者IDを、CMの接続照合へ渡します。
// 同名XMLのcredentialCallerId式だけを所有し、Azure名core-credential-connectionを維持します。
// HTTP・CM付き外部MCPでは、引き渡し失敗はCMへ進む前に500で停止します。
// 社員JSONの再解析・GUID再整形・認証成功印・ID一致の再確認は省き、確認済みcallerIdを使います。
// 接続の所有者・本人セッション・Graph本人ID・期限・失効の照合は、XMLの後続処理で維持します。
try {
    return (string)JObject.Parse((string)context.Variables["gatewayDecision"])["callerId"] ?? "";
} catch {
    // 空IDは接続選択が内部500へ分け、呼出し元APIの監査・応答で止めます。別の認証へ切り替えません。
    return "";
}
// END caller

// BEGIN selection
// 接続設定と本人セッションの有無を確認し、Brokerへ送る接続候補を選びます。
var result=new JObject(new JProperty("allowed",false),new JProperty("status",403));
try {
 /* 空IDの既存停止条件だけを内部500へ分けます。接続設定の読取りや外部呼出しへ進みません。 */
 if(String.IsNullOrEmpty((string)context.Variables["credentialCallerId"])) { result["status"]=500; return result.ToString(); }
 var configured=JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String((string)context.Variables["gatewayCredentialConnection"])));
 if(configured["enabled"]?.Type!=JTokenType.Boolean || !(bool)configured["enabled"]) { return result.ToString(); }
 var name=(string)configured["providerName"]; var mode=(string)configured["mode"];
 result["mode"]=mode;
 if(mode=="service") {
  var connection=(string)configured["connectionName"];
  result["providerName"]=name; result["connectionId"]=connection; result["allowed"]=true; return result.ToString();
 }
 result["profileName"]=name;
 if(!context.Request.Headers.ContainsKey("X-Core-Connection-Session")){result["status"]=409;return result.ToString();}
 if(context.Request.Headers["X-Core-Connection-Session"].Length!=1) { return result.ToString(); }
 if(!System.Text.RegularExpressions.Regex.IsMatch(context.Request.Headers.GetValueOrDefault("X-Core-Connection-Session",""),"^cs2[.][a-f0-9]{64}[.][a-f0-9]{64}$")) { result["status"]=409; return result.ToString(); }
 result["allowed"]=true; return result.ToString();
} catch { return result.ToString(); }
// END selection

// BEGIN authorize-result
// 固定Brokerの認可応答を確認し、接続所有者・provider・期限と利用可否を引き継ぎます。
var previous=JObject.Parse((string)context.Variables["gatewayConnectionSelection"]);
var denied=new JObject(new JProperty("allowed",false),new JProperty("status",503),new JProperty("mode","personal"),new JProperty("profileName",previous["profileName"]));
try {
 var response=context.Variables.ContainsKey("gatewayConnectionSessionResponse") ? context.Variables["gatewayConnectionSessionResponse"] as IResponse : null;
 if(response==null) { return denied.ToString(); }
 if(response.StatusCode!=200){denied["status"]=response.StatusCode==409 ? 409 : response.StatusCode==403 ? 403 : 503; return denied.ToString();}
 if(response.Body==null) { return denied.ToString(); }
 var bytes=response.Body.As<byte[]>(preserveContent:true); if(bytes.Length>16384) { return denied.ToString(); }
 var result=JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes));
 if(result["allowed"]?.Type!=JTokenType.Boolean) { return denied.ToString(); }
 if(!(bool)result["allowed"]) {
  denied["status"]=result["status"]?.Type==JTokenType.Integer && (int)result["status"]==403 ? 403 : 409;
  return denied.ToString();
 }
 Guid tenant,owner;
 if(!Guid.TryParse((string)result["tenantId"],out tenant) || tenant==Guid.Empty || !Guid.TryParse((string)result["ownerId"],out owner) || owner==Guid.Empty || !System.Text.RegularExpressions.Regex.IsMatch((string)result["connectionId"]??"","^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")) { return denied.ToString(); }
 var caller=(string)context.Variables["credentialCallerId"];
 if(caller.StartsWith("user:") && caller!="user:"+tenant.ToString("D")+":"+owner.ToString("D")) { return denied.ToString(); }
 var config=JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String((string)context.Variables["gatewayConnectionProviderSettings"])));
 JArray providers=(JArray)config["providers"];
 string providerName=(string)result["providerName"], profileName=(string)previous["profileName"], tenantId=tenant.ToString("D");
 string identityEndpoint=(string)result["identityEndpoint"], identityIdProperty=(string)result["identityIdProperty"];
 int matches=0;
 foreach(JToken provider in providers) {
  if((string)provider["name"]!=providerName) { continue; }
  if((string)provider["sourceName"]!=profileName) { continue; }
  if((string)provider["oauthTenantId"]!=tenantId) { continue; }
  if((string)provider["identityEndpoint"]!=identityEndpoint) { continue; }
  if((string)provider["identityIdProperty"]!=identityIdProperty) { continue; }
  matches++; if(matches>1) { return denied.ToString(); }
 }
 if(matches!=1) { return denied.ToString(); }
 result["mode"]="personal"; result["profileName"]=previous["profileName"]; result["status"]=200; return result.ToString(Newtonsoft.Json.Formatting.None);
} catch { return denied.ToString(Newtonsoft.Json.Formatting.None); }
// END authorize-result

// BEGIN refresh-unavailable
// 資格情報の再取得が成立しなかった場合に、接続の利用可否と既存のエラー状態を更新します。
var selection=JObject.Parse((string)context.Variables["gatewayConnectionSelection"]); selection["allowed"]=false; selection["status"]=503;
try {
  var response=context.Variables.ContainsKey("gatewayConnectionStateResponse") ? context.Variables["gatewayConnectionStateResponse"] as IResponse : null;
  if(response!=null && response.StatusCode==200 && response.Body!=null) {
    var bytes=response.Body.As<byte[]>(preserveContent:true);
    if(bytes.Length<=16384) { var state=JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes));
      if((string)state["status"]=="reauthentication_required" && state["reauthenticationRequired"]?.Type==JTokenType.Boolean && (bool)state["reauthenticationRequired"]) { selection["status"]=409; }
    }
  }
} catch { }
return selection.ToString(Newtonsoft.Json.Formatting.None);
// END refresh-unavailable

// BEGIN claims-required
// Graphの認証応答から、追加の本人認証が必要かどうかを既存条件で判定します。
try {
  var response=context.Variables.ContainsKey("gatewayConnectionIdentityResponse") ? context.Variables["gatewayConnectionIdentityResponse"] as IResponse : null;
  if(response==null || response.StatusCode!=403) { return false; }
  var challenge=response.Headers.GetValueOrDefault("WWW-Authenticate","");
  if(challenge.Length<=8192 && challenge.IndexOf("insufficient_claims",StringComparison.OrdinalIgnoreCase)>=0) { return true; }
  if(response.Body==null) { return false; }
  var bytes=response.Body.As<byte[]>(preserveContent:true); if(bytes.Length>65536) { return false; }
  var body=JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes));
  return (string)body["error"]?["code"]=="insufficient_claims";
} catch { return false; }
// END claims-required

// BEGIN identity-status
// Graph本人確認の応答と接続所有者を照合し、成功・再認証・拒否・提供元不備を分類します。
try {
  var response = context.Variables.ContainsKey("gatewayConnectionIdentityResponse") ? context.Variables["gatewayConnectionIdentityResponse"] as IResponse : null;
  if (response == null) { return 503; }
  if (response.StatusCode != 200) { return response.StatusCode == 401 ? 409 : response.StatusCode == 403 ? 403 : 503; }
  if (response.Body == null) { return 503; }
  var bytes = response.Body.As<byte[]>(preserveContent: true); if (bytes.Length > 65536) { return 503; }
  var identity = JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes)); var selection = JObject.Parse((string)context.Variables["gatewayConnectionSelection"]);
  var value = identity[(string)selection["identityIdProperty"]]; Guid owner;
  return value?.Type == JTokenType.String && Guid.TryParse((string)value, out owner) && owner != Guid.Empty && owner.ToString("D") == (string)selection["ownerId"] ? 200 : 403;
} catch { return 503; }
// END identity-status
