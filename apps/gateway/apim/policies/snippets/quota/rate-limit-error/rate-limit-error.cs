// 目的：上限超過応答のRetry-Afterへ、標準ポリシーの値または既存の代替値を渡します。
// 回数枠を読める場合は既存の期間を使い、変数がない場合だけ60秒にします。
var fallback = context.Variables.ContainsKey("gatewayRateDecision") ? (int)JObject.Parse((string)context.Variables["gatewayRateDecision"])["renewalPeriodSeconds"] : 60;
var seconds = 0;
if (context.Variables.ContainsKey("gatewayRateRetryAfter") && Int32.TryParse(Convert.ToString(context.Variables["gatewayRateRetryAfter"]), out seconds) && seconds >= 1 && seconds <= 300) { return seconds.ToString(); }
return fallback.ToString();
