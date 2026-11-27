// 生成MCPテンプレート固有の認証結果引渡しとPII引数検査を管理します。
// 各BEGIN/END区画を同名mcp.xmlの固定markerへ展開し、HTTP専用fragmentへ共有しません。

// BEGIN gateway-decision
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
// END gateway-decision

// BEGIN pii-arguments
JObject message;
string name;
try {
  var rawBody = context.Request.Body.As<string>(preserveContent: true);
  // JSON文字列値だけ先頭へ~を退避用に足し、日時の自動解析を防ぎます。
  // property名・コメントは原文を保持し、escapeと残るJSON構造はJSON.NETが読みます。
  var propertySuffix = new System.Text.RegularExpressions.Regex(@"\G\s*:",
    System.Text.RegularExpressions.RegexOptions.CultureInvariant, TimeSpan.FromSeconds(2));
  var quotedValues = new System.Text.RegularExpressions.Regex(
    @"""(?:\\[\s\S]|[^""\\])*""|'(?:\\[\s\S]|[^'\\])*'|/\*[\s\S]*?\*/|//[^\r\n]*",
    System.Text.RegularExpressions.RegexOptions.CultureInvariant, TimeSpan.FromSeconds(2));
  var protectedBody = quotedValues.Replace(rawBody, match => {
      if ((match.Value[0] != '"' && match.Value[0] != '\'')
        || propertySuffix.Match(rawBody, match.Index + match.Length).Success) { return match.Value; }
      return match.Value.Substring(0, 1) + "~" + match.Value.Substring(1);
    });
  message = JObject.Parse(protectedBody);
  foreach (var value in message.Descendants().OfType<JValue>()) {
    if (value.Type == JTokenType.String) { value.Value = ((string)value).Substring(1); }
  }
  if ((string)message?["method"] != "tools/call" || message["id"]?.Type != JTokenType.Integer) { return ""; }
  name = (string)message["params"]?["name"];
} catch { return ""; }
/* 内部設定の読取り不備だけを、専用on-errorの基盤内エラーへ渡します。 */
var config = JObject.Parse((string)context.Variables["gatewayControlSettings"]);
try {
  var constraint = String.IsNullOrEmpty(name) ? null : config["operationConstraints"]?[name] as JObject;
  if ((string)constraint?["kind"] != "pii") { return ""; }
  var arguments = message["params"]?["arguments"] as JObject;
  var valid = arguments != null && arguments.Properties().Count() == 2
    && arguments["text"]?.Type == JTokenType.String && !String.IsNullOrWhiteSpace((string)arguments["text"])
    && arguments["language"]?.Type == JTokenType.String
    && !String.IsNullOrWhiteSpace((string)arguments["language"])
    && (((JArray)constraint["allowedLanguages"]).Count == 0 || ((JArray)constraint["allowedLanguages"]).Any(v => (string)v == (string)arguments["language"]))
    && System.Text.Encoding.UTF8.GetByteCount(arguments.ToString(Newtonsoft.Json.Formatting.None)) <= (int)constraint["maxRequestBytes"];
  if (valid) { return ""; }
  return new JObject(new JProperty("jsonrpc", "2.0"), new JProperty("id", message["id"]),
    new JProperty("error", new JObject(new JProperty("code", -32602), new JProperty("message", "Invalid tool arguments")))).ToString(Newtonsoft.Json.Formatting.None);
} catch { return ""; }
// END pii-arguments
