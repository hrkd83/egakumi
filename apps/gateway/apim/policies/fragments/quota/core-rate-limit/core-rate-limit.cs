// 目的：認証済みの購読または社員に適用する利用回数枠を準備します。
// 同名XMLへ展開し、gatewayRateDecisionを入口のrate-limit-by-keyへ渡します。
// 社員の本人情報の検証は先行する共通認証へ集約し、確認済みのIDを使います。
// 社員の利用枠と購読キーの認証は先行処理で確認済みです。無制限と取消・切断の免除は維持します。

var decision = JObject.Parse((string)context.Variables["gatewayDecision"]);
// 引き渡し失敗は先行するAPI処理で停止し、呼出者IDも共通認証から引き継いでいます。
var method = (string)decision["method"];
if (method == "user") {
  // 社員の認証成功は、共通認証と認証結果の引き渡しで確認済みです。
  // 共通認証と引き渡しで確認済みの社員IDを使い、本人情報を読み直して照合しません。
  // 従来のtid:oidをそのまま使うため、既存カウンターのキーは変わりません。
  var userIdentity = (string)decision["verifiedTenantId"] + ":" + (string)decision["verifiedPrincipalId"];
  // 回数・期間の型と範囲は共通認証で検査済みです。直前の社員用利用枠の取得が同じ値を渡しています。
  // 計数では準備済みのprofileを読み、確認済みのrequestRateを適用します。
  var userProfile = JObject.Parse((string)context.Variables["gatewaySubscriptionProfile"]);
  var rate = userProfile["requestRate"];
  var enabled = rate.Type == JTokenType.Object;

  // 認証済み社員も、購読キーと同じ取消・切断の計数免除を使います。
  return new JObject(new JProperty("scope", "user"), new JProperty("enabled", enabled && !(bool)context.Variables["gatewayRateLimitExempt"]),
    new JProperty("calls", enabled ? (int)rate["calls"] : 1), new JProperty("renewalPeriodSeconds", enabled ? (int)rate["renewalPeriodSeconds"] : 60),
    new JProperty("counterKey", "ai-gateway-rate-v3:user:" + Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(userIdentity))))
    .ToString(Newtonsoft.Json.Formatting.None);
}
var scope = "subscriptionKey";
// 共通認証と引き渡しで確認した同じ購読IDを使い、方式・IDの非空・呼出者の一致を再検査しません。
var identity = context.Subscription.Id;
var profileEnabled = "{{core-subscription-profile-enabled}}" == "true";
// 直前の利用枠準備が回数枠を含むprofileを必ず用意し、SQL取得の不備は先に停止しています。
var profile = JObject.Parse((string)context.Variables["gatewaySubscriptionProfile"]);
var profileRate = profile["requestRate"];
var hasProfileRate = profileRate.Type == JTokenType.Object;
JToken values;
bool keyEnabled;
if (profileEnabled && hasProfileRate) {
  // SQLの回数枠を優先する要求では、使わない埋込み設定を復元・検索しません。
  values = profileRate;
  keyEnabled = true;
} else {
  // 埋込み値が必要なキー要求だけ設定を復元し、購読別設定があれば優先します。
  // SQLの明示nullと免除時も、従来の内部回数・期間を準備し、計数の有効化は別に決めます。
  var settings = JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String("__RATE_LIMITS_BASE64__")));
  var selected = (JObject)settings[scope];
  var caller = (string)decision["callerId"];
  values = ((JArray)selected["overrides"]).FirstOrDefault(item => (string)item["callerId"] == caller) ?? selected;
  keyEnabled = !profileEnabled && (bool)selected["enabled"];
}
return new JObject(
  new JProperty("scope", scope),
  new JProperty("enabled", keyEnabled && !(bool)context.Variables["gatewayRateLimitExempt"]),
  new JProperty("calls", (int)values["calls"]),
  new JProperty("renewalPeriodSeconds", (int)values["renewalPeriodSeconds"]),
  new JProperty("counterKey", "ai-gateway-rate-v3:" + scope + ":" + Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(identity)))
).ToString(Newtonsoft.Json.Formatting.None);
