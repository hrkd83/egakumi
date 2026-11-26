// BEGIN sdp
// 目的：SDP offer の JSON 形式と必須の先頭行を確認し、後続へ渡す本文だけを選びます。
try {
    var body = context.Request.Body.As<Newtonsoft.Json.Linq.JObject>(preserveContent: true);
    if (body == null || body.Properties().Count() != 1 || body["sdp"] == null || body["sdp"].Type != Newtonsoft.Json.Linq.JTokenType.String) { return ""; }
    var sdp = (string)body["sdp"];
    if (String.IsNullOrEmpty(sdp) || !sdp.StartsWith("v=0") || sdp.Contains("\0")) { return ""; }
    return sdp;
} catch { return ""; }
// END sdp

// BEGIN client-secrets-request
// 目的：操作ごとのモデル設定から、Foundry の短期 token 取得に送る session JSON を作ります。
var settings = Newtonsoft.Json.Linq.JObject.Parse((string)context.Variables["gatewayOperationConstraint"]);
var session = new Newtonsoft.Json.Linq.JObject(
    new Newtonsoft.Json.Linq.JProperty("type", "realtime"),
    new Newtonsoft.Json.Linq.JProperty("model", (string)settings["model"]),
    new Newtonsoft.Json.Linq.JProperty("max_output_tokens", (int)(settings["initialOutputTokens"] ?? settings["maxOutputTokens"])),
    new Newtonsoft.Json.Linq.JProperty("output_modalities", new Newtonsoft.Json.Linq.JArray("audio")),
    new Newtonsoft.Json.Linq.JProperty("tools", new Newtonsoft.Json.Linq.JArray()),
    new Newtonsoft.Json.Linq.JProperty("tracing", Newtonsoft.Json.Linq.JValue.CreateNull()));
return new Newtonsoft.Json.Linq.JObject(
    new Newtonsoft.Json.Linq.JProperty("session", session),
    new Newtonsoft.Json.Linq.JProperty("expires_after", new Newtonsoft.Json.Linq.JObject(
        new Newtonsoft.Json.Linq.JProperty("anchor", "created_at"),
        new Newtonsoft.Json.Linq.JProperty("seconds", 60)))).ToString(Newtonsoft.Json.Formatting.None);
// END client-secrets-request

// BEGIN ephemeral-token
// 目的：短期 token の取得結果について、応答・形式・期限を確認し、この要求内で使う値を取り出します。
try {
    var response = context.Variables.ContainsKey("realtimeSecretResponse") ? context.Variables["realtimeSecretResponse"] as IResponse : null;
    if (response == null || response.StatusCode != 200 || response.Body == null) { return ""; }
    var text = response.Body.As<string>();
    if (String.IsNullOrEmpty(text) || text.Length > 16384) { return ""; }
    var body = Newtonsoft.Json.Linq.JObject.Parse(text);
    if (body["value"]?.Type != Newtonsoft.Json.Linq.JTokenType.String || body["expires_at"]?.Type != Newtonsoft.Json.Linq.JTokenType.Integer) { return ""; }
    var token = (string)body["value"];
    if (String.IsNullOrEmpty(token) || token.Length > 8192 || !System.Text.RegularExpressions.Regex.IsMatch(token, @"\A[\x21-\x7E]+\z") || (long)body["expires_at"] <= DateTimeOffset.UtcNow.ToUnixTimeSeconds()) { return ""; }
    return token;
} catch { return ""; }
// END ephemeral-token

// BEGIN call-id
// 目的：作成成功の Location から、所有者チケットと終了操作に使う call ID を取り出します。
var location = context.Response.Headers.GetValueOrDefault("Location", "");
var match = System.Text.RegularExpressions.Regex.Match(location, @"/realtime/calls/([A-Za-z0-9_-]{1,160})(?:\?|$)");
return match.Success ? match.Groups[1].Value : "";
// END call-id

// BEGIN ticket
// 目的：確認済みの所有者・call ID・期限を、HMAC 署名付きチケットへまとめます。
// 目的：この要求で受け取った所有者と、作成済みcall ID・期限をHMAC署名チケットへ入れます。
// 同名XMLのrealtimeCallTicket式を所有します。入口の本人認証をここでやり直しません。
var owner = JObject.Parse((string)context.Variables["realtimeOwnerContext"]);
var payload = new JObject(
    new JProperty("v", (int)owner["v"]),
    new JProperty("subscriptionId", (string)owner["subscriptionId"]),
    new JProperty("callId", (string)context.Variables["realtimeCallId"]),
    new JProperty("callerId", (string)owner["callerId"]),
    new JProperty("exp", DateTimeOffset.UtcNow.ToUnixTimeSeconds() + (int)context.Variables["realtimeSessionTtlSeconds"]));
// v3／v4の項目・順序・Base64url・HMAC方式を保ち、署名鍵をチケットへ含めません。
var encoded = Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(payload.ToString(Newtonsoft.Json.Formatting.None)))
    .TrimEnd('=').Replace('+', '-').Replace('/', '_');
var key = System.Text.Encoding.UTF8.GetBytes("{{core-realtime-ticket-key}}");
using (var hmac = new System.Security.Cryptography.HMACSHA256(key)) {
    var signature = Convert.ToBase64String(hmac.ComputeHash(System.Text.Encoding.UTF8.GetBytes(encoded)))
        .TrimEnd('=').Replace('+', '-').Replace('/', '_');
    return encoded + "." + signature;
}
// END ticket

// BEGIN answer-status
// 目的：成功応答の MIME と SDP 必須行を確認し、本文を記録せず固定の判定コードを返します。
try {
    var header = context.Response.Headers.GetValueOrDefault("Content-Type", "");
    var mediaType = header.Split(';')[0].Trim().ToLowerInvariant();
    if (String.IsNullOrEmpty(mediaType)) { return "REALTIME_CALL_MIME_MISSING"; }
    if (mediaType == "application/json" || mediaType.EndsWith("+json")) { return "REALTIME_CALL_MIME_JSON"; }
    if (mediaType == "text/html" || mediaType == "application/xhtml+xml") { return "REALTIME_CALL_MIME_HTML"; }
    if (mediaType != "application/sdp" && mediaType != "text/plain") { return "REALTIME_CALL_MIME_OTHER"; }
    var answer = context.Response.Body.As<string>(preserveContent: true);
    if (String.IsNullOrEmpty(answer) || answer.Contains("\0")) { return "REALTIME_CALL_SDP_INVALID"; }
    if (System.Text.Encoding.UTF8.GetByteCount(answer) > 524288000) { return "REALTIME_CALL_SDP_TOO_LARGE"; }
    var lines = answer.Split(new string[] { "\r\n", "\n" }, StringSplitOptions.None);
    if (lines.Length < 5 || lines[0] != "v=0"
        || !lines.Any(line => System.Text.RegularExpressions.Regex.IsMatch(line, @"\Ao=\S+ [0-9]+ [0-9]+ IN IP[46] \S+\z"))
        || !lines.Any(line => line.StartsWith("s=") && line.Length > 2)
        || !lines.Any(line => System.Text.RegularExpressions.Regex.IsMatch(line, @"\At=[0-9]+ [0-9]+\z"))
        || !lines.Any(line => System.Text.RegularExpressions.Regex.IsMatch(line, @"\Am=audio [0-9]+ \S+ \S+(?: \S+)*\z"))) { return "REALTIME_CALL_SDP_INVALID"; }
    return "VALID";
} catch { return "REALTIME_CALL_SDP_INVALID"; }
// END answer-status
