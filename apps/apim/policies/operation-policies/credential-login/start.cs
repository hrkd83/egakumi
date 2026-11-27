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
