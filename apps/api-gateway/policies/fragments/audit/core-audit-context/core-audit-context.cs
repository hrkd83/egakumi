// 同名XMLの親要求IDの観測式とモデル名の選択式を管理します。
// 固定のBEGIN/END区画をそれぞれのmarkerへ展開し、ファイル全体を一つの式として使いません。

// BEGIN trace
var result = new JObject(new JProperty("status", "none"));
try {
  var name = "X-Core-Agent-Parent-Id";
  if (!context.Request.Headers.ContainsKey(name)) { return result.ToString(); }
  Guid parent;
  if (context.Request.Headers[name].Length != 1 || !Guid.TryParse(context.Request.Headers.GetValueOrDefault(name, ""), out parent) || parent == Guid.Empty) {
    result["status"] = "invalid"; return result.ToString();
  }
  var decision = JObject.Parse((string)context.Variables["gatewayDecision"]);
  var actor = (string)decision["callerId"];
  if (String.IsNullOrWhiteSpace(actor)) { return result.ToString(); }
  result["status"] = "candidate";
  result["claimedParentId"] = parent.ToString("D");
  result["actorId"] = actor;
} catch { result = new JObject(new JProperty("status", "invalid")); }
return result.ToString(Newtonsoft.Json.Formatting.None);
// END trace

// BEGIN model
try {
  var settings = JObject.Parse((string)context.Variables["gatewayAuditSettings"]);
  var controls = JObject.Parse((string)context.Variables["gatewayControlSettings"]);
  var operation = String.IsNullOrEmpty((string)controls["operationId"]) ? context.Operation.Id : (string)controls["operationId"];
  var constraint = controls["operationConstraints"][operation];
  var kind = (string)constraint?["kind"];
  var model = (string)settings["websocketModel"] ?? "";
  if (context.Variables.ContainsKey("gatewayAuditBodyModel")) { model = (string)context.Variables["gatewayAuditBodyModel"]; }
  if (kind == "pathModel") { model = context.Request.MatchedParameters.GetValueOrDefault((string)constraint["parameter"], ""); }
  if (constraint?["model"]?.Type == JTokenType.String) { model = (string)constraint["model"]; }
  if (String.IsNullOrEmpty(model) || !System.Text.RegularExpressions.Regex.IsMatch(model, "^[A-Za-z0-9_.-]{1,160}$")) { return ""; }
  return model;
} catch { return ""; }
// END model
