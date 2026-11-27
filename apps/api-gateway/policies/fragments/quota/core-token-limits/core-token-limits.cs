// BEGIN enabled
var decision = JObject.Parse((string)context.Variables["gatewayDecision"]);
if ((string)decision["method"] != "user") {
  return false;
}
// 配備時に生成した対象操作一覧を使います。不正な内部設定はon-errorへ渡します。
var settings = JObject.Parse((string)context.Variables["gatewayControlSettings"]);
var operations = (JArray)settings["tokenLimitOperationIds"];
var supported = operations.Any(value => (string)value == context.Operation.Id) && !(bool)context.Variables["gatewayRateLimitExempt"];
var limits = JObject.Parse((string)context.Variables["gatewayUserLimits"]);
return supported
  && (limits["tokensPerMinute"].Type != JTokenType.Null || limits["tokenQuota"].Type != JTokenType.Null);
// END enabled

// BEGIN counter
// 認証済みEntraユーザーの従来のtid:oidカウンターを使います。
var decision = JObject.Parse((string)context.Variables["gatewayDecision"]);
var identity = (string)decision["verifiedTenantId"] + ":" + (string)decision["verifiedPrincipalId"];
return "core-tokens-v1:user:" + identity;
// END counter
