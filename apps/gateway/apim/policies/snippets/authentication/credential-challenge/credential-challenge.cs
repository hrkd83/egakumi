// 同名XMLの認証案内応答の検査、429の待ち秒、公開応答本文を管理します。
// 固定Broker送信・短い状態投影・ヘッダー・要求停止の順序はXMLへ維持します。

// BEGIN result
// 非200は本文を読まず状態を分類します。
var denied = new JObject(new JProperty("status", 503));
try {
  var response = context.Variables.ContainsKey("gatewayConnectionChallengeResponse") ? context.Variables["gatewayConnectionChallengeResponse"] as IResponse : null;
  if (response == null) { return denied.ToString(); }
  if (response.StatusCode != 200) {
    denied["status"] = response.StatusCode == 403 ? 403 : response.StatusCode == 429 ? 429 : 503;
    return denied.ToString();
  }
  if (response.Body == null) { return denied.ToString(); }
  var bytes = response.Body.As<byte[]>(preserveContent: true);
  if (bytes.Length > 16384) { return denied.ToString(); }
  var value = JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes));
  var origin = System.Text.Encoding.UTF8.GetString(Convert.FromBase64String("__CREDENTIAL_LOGIN_ORIGIN_BASE64__"));
  var tx = (string)value["transactionId"]; var poll = (string)value["pollSecret"];
  var provisional = (string)value["provisionalConnectionSession"];
  var login = (string)value["loginUrl"]; var statusUrl = (string)value["statusUrl"];
  if ((string)value["status"] != "need_login" || !System.Text.RegularExpressions.Regex.IsMatch(tx ?? "", "^[a-f0-9]{64}$")
      || !System.Text.RegularExpressions.Regex.IsMatch(poll ?? "", "^[a-f0-9]{64}$")
      || value["protocolVersion"]?.Type != JTokenType.Integer || (int)value["protocolVersion"] != 2
      || !System.Text.RegularExpressions.Regex.IsMatch(provisional ?? "", "^cs2[.][a-f0-9]{64}[.][a-f0-9]{64}$")
      || value["expiresIn"]?.Type != JTokenType.Integer || (int)value["expiresIn"] < 60 || (int)value["expiresIn"] > 600) { return denied.ToString(); }
  var prefix = origin + "/auth/connections/browser/launch#transactionId=" + tx + "&launch=";
  if (login == null || !login.StartsWith(prefix, StringComparison.Ordinal)
      || !System.Text.RegularExpressions.Regex.IsMatch(login.Substring(prefix.Length), "^[a-f0-9]{64}$")
      || statusUrl != origin + "/auth/connections/status?transactionId=" + tx) { return denied.ToString(); }
  var auth = new JObject(new JProperty("protocolVersion", 2), new JProperty("provisionalConnectionSession", provisional), new JProperty("status", "need_login"), new JProperty("loginUrl", login),
    new JProperty("transactionId", tx), new JProperty("pollSecret", poll),
    new JProperty("statusUrl", statusUrl), new JProperty("expiresIn", (int)value["expiresIn"]));
  return new JObject(new JProperty("status", 409), new JProperty("authentication", auth)).ToString(Newtonsoft.Json.Formatting.None);
} catch { return denied.ToString(Newtonsoft.Json.Formatting.None); }
// END result

// BEGIN retry-after
if ((int)JObject.Parse((string)context.Variables["gatewayConnectionChallengeResult"])["status"] != 429) { return ""; }
var response = context.Variables.ContainsKey("gatewayConnectionChallengeResponse") ? context.Variables["gatewayConnectionChallengeResponse"] as IResponse : null;
var retry = response != null && response.StatusCode == 429 ? response.Headers.GetValueOrDefault("Retry-After", "") : ""; var seconds = 0;
return System.Text.RegularExpressions.Regex.IsMatch(retry, "^[0-9]{1,3}\\z") && Int32.TryParse(retry, out seconds) && seconds >= 1 && seconds <= 300 ? seconds.ToString() : "60";
// END retry-after

// BEGIN response-body
var result = JObject.Parse((string)context.Variables["gatewayConnectionChallengeResult"]);
var status = (int)result["status"];
var body = new JObject(new JProperty("error", new JObject(new JProperty("code", status == 409 ? "ConnectionRequired" : status == 403 ? "ConnectionNotAuthorized" : status == 429 ? "ConnectionRateLimited" : "ConnectionUnavailable"))));
if (status == 409 && result["authentication"] != null) { body["authentication"] = result["authentication"]; }
return body.ToString(Newtonsoft.Json.Formatting.None);
// END response-body
