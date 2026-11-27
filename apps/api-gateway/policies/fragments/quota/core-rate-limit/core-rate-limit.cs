// 確認したEntraユーザーの利用枠と従来のカウンターを使います。
var decision = JObject.Parse((string)context.Variables["gatewayDecision"]);
if ((string)decision["method"] != "user") {
  return "{\"enabled\":false}";
}
var limits = JObject.Parse((string)context.Variables["gatewayUserLimits"]);
var rate = limits["requestRate"];
var enabled = rate.Type == JTokenType.Object;
var identity = (string)decision["verifiedTenantId"] + ":" + (string)decision["verifiedPrincipalId"];
return new JObject(
  new JProperty("scope", "user"),
  new JProperty("enabled", enabled && !(bool)context.Variables["gatewayRateLimitExempt"]),
  new JProperty("calls", enabled ? (int)rate["calls"] : 1),
  new JProperty("renewalPeriodSeconds", enabled ? (int)rate["renewalPeriodSeconds"] : 60),
  new JProperty("counterKey", "ai-gateway-rate-v3:user:" + Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(identity)))
).ToString(Newtonsoft.Json.Formatting.None);
