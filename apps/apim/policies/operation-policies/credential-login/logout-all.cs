// 同名XMLの確認済み呼出者の引渡しと、本人セッション要求の実処理を固定区画へ管理します。
// caller / session-requestを個別展開し、認証・入力形式・固定転送・停止応答を変えません。

// BEGIN caller
// 共通認証が確定した呼出者IDを受け取り、Brokerへ引き渡します。
// 成功印と分岐の確認を維持し、社員IDの形式や割当を再検査しません。
try {
 if(!context.Variables.ContainsKey("gatewayCommonAuthenticationValidated") || !(bool)context.Variables["gatewayCommonAuthenticationValidated"]) { return ""; }
 var user=context.Variables.ContainsKey("gatewayEntraAuthenticationValidated") && (bool)context.Variables["gatewayEntraAuthenticationValidated"];
 if(!user) { return context.Subscription==null ? "" : "subscription:"+context.Subscription.Id; }
 if(context.Subscription!=null || !context.Variables.ContainsKey("gatewayUserAuthentication")) { return ""; }
 var auth=JObject.Parse((string)context.Variables["gatewayUserAuthentication"]);
 return (string)auth["callerId"] ?? "";
} catch { return ""; }
// END caller

// BEGIN session-request
try {
  if(!context.Request.Headers.ContainsKey("X-Core-Connection-Session") || context.Request.Headers["X-Core-Connection-Session"].Length!=1) { return ""; }
  var token=context.Request.Headers.GetValueOrDefault("X-Core-Connection-Session","");
  if(!System.Text.RegularExpressions.Regex.IsMatch(token,"^cs2[.][a-f0-9]{64}[.][a-f0-9]{64}$")) { return ""; }
  var result=new JObject(new JProperty("connectionSession",token),new JProperty(context.Subscription==null ? "callerId" : "subscriptionId",context.Subscription==null ? (string)context.Variables["credentialCallerId"] : context.Subscription.Id));

  return result.ToString(Newtonsoft.Json.Formatting.None);
} catch { return ""; }
// END session-request
