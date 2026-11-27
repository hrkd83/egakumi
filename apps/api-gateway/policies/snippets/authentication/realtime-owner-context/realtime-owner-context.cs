// 目的：先行認証が確認したcallerを、通話チケットの発行・照合へ引き渡します。
// この要求のdecisionから受け取り、社員JSON・認証印・GUID・購読との一致を再検査しません。
var decision = JObject.Parse((string)context.Variables["gatewayDecision"]);
var caller = (string)decision["callerId"];
var method = (string)decision["method"];
// 必要な内部データを受け取れなければ、呼出元がPolicyIdを限定して安全な500で停止します。
if (String.IsNullOrEmpty(caller)) { throw new Exception("Realtime owner context is unavailable"); }
string subscriptionId;
int version;
// 認証をやり直す分岐ではなく、既存の社員v4／購読v3チケットの表現を選びます。
if (method == "user") {
    version = 4;
    subscriptionId = "";
} else if (method == "subscriptionKey") {
    version = 3;
    subscriptionId = caller.Substring("subscription:".Length);
    if (String.IsNullOrEmpty(subscriptionId)) { throw new Exception("Realtime owner context is unavailable"); }
} else {
    throw new Exception("Realtime owner context is unavailable");
}
return new JObject(new JProperty("v", version), new JProperty("subscriptionId", subscriptionId),
    new JProperty("callerId", caller)).ToString(Newtonsoft.Json.Formatting.None);
