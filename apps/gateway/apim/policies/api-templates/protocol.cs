// BEGIN model-selection
// 公開済みのWebSocket設定を復元し、指定モデルまたは既定モデルを後続へ渡します。
var encoded = "__WEBSOCKET_SETTINGS_BASE64__";
if (String.IsNullOrEmpty(encoded)) { return ""; }
/* 内部設定の復元不備は、専用on-errorで500へ分けます。 */
var settings = JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(encoded)));
/* defaultModelがない操作は、後続の既存固定query契約を使います。 */
if (settings.Property("defaultModel") == null) { return ""; }
var model = (string)settings["defaultModel"];
if (context.Request.Url.Query.ContainsKey("model")) {
  if (context.Request.Url.Query["model"].Length != 1) { return "!invalid-model!"; }
  model = context.Request.Url.Query["model"][0];
}
return model;
// END model-selection

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

// BEGIN audit-model-setting
// 先行処理が選んだWebSocketモデルを、既存の監査設定へ保存します。
var settings = JObject.Parse((string)context.Variables["gatewayAuditSettings"]);
settings["websocketModel"] = (string)context.Variables["gatewayProtocolModel"];
return settings.ToString(Newtonsoft.Json.Formatting.None);
// END audit-model-setting

// BEGIN query-validation
// 既存の公開クエリ契約に従い、項目名と値数を照合して転送前の判定を返します。
var encoded = "__WEBSOCKET_QUERY_BASE64__";
if (String.IsNullOrEmpty(encoded)) { return true; }
/* 内部設定を読めない場合は、入力の拒否と混ぜず専用on-errorへ渡します。 */
var expected = JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(encoded)));
var model = (string)context.Variables["gatewayProtocolModel"];
return context.Request.Url.Query.All(q => q.Value.Length == 1 && ((!String.IsNullOrEmpty(model) && q.Key == "model") || expected[q.Key] != null));
// END query-validation
