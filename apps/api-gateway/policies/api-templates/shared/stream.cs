// BEGIN authentication-result
// 共通認証の購読または社員情報を、操作制約と監査が使う認証結果へ引き継ぎます。
var deny = new JObject(new JProperty("allowed", false));
JObject decision;
if (context.Subscription != null) {
  /* 購読とキー用ヘッダーの形式は共通認証で確認済みです。検証済み購読IDを呼出主体へ引き継ぎます。 */
  decision = new JObject(new JProperty("allowed", true),
    new JProperty("callerId", "subscription:" + context.Subscription.Id), new JProperty("method", "subscriptionKey"));
} else {
  try {
    /* 購読なしは直前の分岐で確定し、共通認証の完了は先行する要求準備で確認済みです。 */
    if (!context.Variables.ContainsKey("gatewayEntraAuthenticationValidated") || !(bool)context.Variables["gatewayEntraAuthenticationValidated"]) { return deny.ToString(); }
    var user = JObject.Parse((string)context.Variables["gatewayUserAuthentication"]);
    /* 形式番号と社員IDは共通認証で確認・整形済みです。内部JSONから確認済みの値を引き継ぎます。 */
    var tenant = (string)user["tenantId"];
    var principal = (string)user["principalId"];
    /* 呼出者と利用枠の帰属先も確認済みなので、IDを作り直さず、それぞれの値を引き継ぎます。 */
    var caller = (string)user["callerId"];
    var allocation = (string)user["allocationId"];
    decision = new JObject(new JProperty("allowed", true), new JProperty("method", "user"),
      new JProperty("callerId", caller), new JProperty("allocationId", allocation),
      new JProperty("verifiedTenantId", tenant), new JProperty("verifiedPrincipalId", principal),
      new JProperty("verifiedClientId", (string)user["clientId"] ?? ""));
  } catch { return deny.ToString(); }
}
return decision.ToString(Newtonsoft.Json.Formatting.None);
// END authentication-result
