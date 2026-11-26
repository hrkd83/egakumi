// BEGIN input
// 取引ID・stateと同じ取引のCookieを確認し、Brokerへ渡す本文を作ります。
try {
 if (context.Request.Url.Query.Count != 2 || context.Request.Url.Query.Keys.Any(k => k != "transactionId" && k != "state") || context.Request.Url.Query.Any(k => k.Value.Length != 1)) { return ""; }
 var tx=context.Request.Url.Query.GetValueOrDefault("transactionId", ""); var state=context.Request.Url.Query.GetValueOrDefault("state", "");
 if(!System.Text.RegularExpressions.Regex.IsMatch(tx,"^[a-f0-9]{64}$") || !System.Text.RegularExpressions.Regex.IsMatch(state,"^[a-f0-9]{64}$")) { return ""; }
 var cookies=context.Request.Headers.GetValueOrDefault("Cookie", "").Split(';').Select(c=>c.Trim()).Where(c=>c.StartsWith("__Host-CoreCredentialLogin=",StringComparison.Ordinal)).ToArray();
 if(cookies.Length!=1) { return ""; } var cookie=cookies[0].Substring("__Host-CoreCredentialLogin=".Length);
 if(!System.Text.RegularExpressions.Regex.IsMatch(cookie,"^[a-f0-9]{64}[.][a-f0-9]{64}$") || cookie.Substring(0,64)!=tx) { return ""; }
 return new JObject(new JProperty("transactionId",tx),new JProperty("state",state),new JProperty("cookie",cookie)).ToString(Newtonsoft.Json.Formatting.None);
} catch { return ""; }
// END input

// BEGIN transaction
// Brokerの取引応答にある接続と、登録済みprovider・本人確認先を照合します。
try {
 var response=context.Variables.ContainsKey("credentialContextResponse") ? context.Variables["credentialContextResponse"] as IResponse : null;
 if(response==null || response.StatusCode!=200 || response.Body==null) { return ""; }
 var bytes=response.Body.As<byte[]>(preserveContent:true); if(bytes.Length>16384) { return ""; }
 var result=JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes)); Guid tenant,owner;
 if(!Guid.TryParse((string)result["tenantId"],out tenant) || tenant==Guid.Empty || !Guid.TryParse((string)result["ownerId"],out owner) || owner==Guid.Empty) { return ""; }
 if(!System.Text.RegularExpressions.Regex.IsMatch((string)result["connectionId"]??"","^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$") || !System.Text.RegularExpressions.Regex.IsMatch((string)result["callbackTicket"]??"","^[a-f0-9]{64}$")) { return ""; }
 var config=JObject.Parse((string)context.Variables["credentialLoginSettings"]);
 var providers=((JArray)config["providers"]).Where(p=>(string)p["name"]==(string)result["providerName"] && (string)p["oauthTenantId"]==tenant.ToString("D") && (string)p["identityEndpoint"]==(string)result["identityEndpoint"] && (string)p["identityIdProperty"]==(string)result["identityIdProperty"]).ToArray();
 return providers.Length==1 ? result.ToString(Newtonsoft.Json.Formatting.None) : "";
} catch { return ""; }
// END transaction

// BEGIN owner
// CMのトークンで確認したGraph本人IDを、取引の期待する所有者と照合します。
try {
 var response=context.Variables.ContainsKey("credentialIdentityResponse") ? context.Variables["credentialIdentityResponse"] as IResponse : null;
 if(response==null || response.StatusCode!=200 || response.Body==null) { return ""; }
 var bytes=response.Body.As<byte[]>(preserveContent:true); if(bytes.Length>65536) { return ""; }
 var selected=JObject.Parse((string)context.Variables["credentialTransaction"]);
 var value=JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes))[(string)selected["identityIdProperty"]]; Guid owner;
 return value?.Type==JTokenType.String && Guid.TryParse((string)value,out owner) && owner!=Guid.Empty && owner.ToString("D")== (string)selected["ownerId"] ? owner.ToString("D") : "";
} catch { return ""; }
// END owner
