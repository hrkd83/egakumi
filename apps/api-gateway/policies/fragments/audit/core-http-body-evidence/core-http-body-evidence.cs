// BEGIN request-body
// 正確なバイト列を中継へ渡すため、APIMの文字列本文ではBase64を使います。!で始まる値は未採取理由です。
const int maxBodyBytes = 16 * 1024 * 1024;
if (context.Request.Method == "GET") {
  return "";
}
long declaredBytes;
if (long.TryParse(context.Request.Headers.GetValueOrDefault("Content-Length", ""), out declaredBytes)
    && declaredBytes > maxBodyBytes) {
  return "!transport-size";
}
var requestBytes = context.Request.Body?.As<byte[]>(preserveContent: true) ?? new byte[0];
return requestBytes.Length <= maxBodyBytes ? Convert.ToBase64String(requestBytes) : "!transport-size";
// END request-body

// BEGIN payload
if (!(bool)context.Variables["gatewayEvidenceResponse"]) {
  return (string)context.Variables["gatewayEvidenceRequestBody"];
}
const int maxBodyBytes = 16 * 1024 * 1024;
if (context.Variables.ContainsKey("gatewayEvidencePreparedStatus")) {
  var prepared = System.Text.Encoding.UTF8.GetBytes((string)context.Variables["gatewayEvidencePreparedResponse"]);
  return prepared.Length <= maxBodyBytes ? Convert.ToBase64String(prepared) : "!transport-size";
}
if (context.Response.StatusCode == 204 || context.Response.StatusCode == 304) {
  return "";
}
var contentType = context.Response.Headers.GetValueOrDefault("Content-Type", "");
var mediaType = contentType.Split(';')[0].Trim();
long declaredBytes;
var hasLength = long.TryParse(context.Response.Headers.GetValueOrDefault("Content-Length", ""), out declaredBytes)
  && declaredBytes >= 0;
if (mediaType.Equals("text/event-stream", StringComparison.OrdinalIgnoreCase)
    || (!hasLength && (mediaType.StartsWith("audio/", StringComparison.OrdinalIgnoreCase)
        || (context.Operation.Id == "tts" && context.Response.StatusCode < 400)))) {
  return "!streaming";
}
if (hasLength && declaredBytes > maxBodyBytes) {
  return "!transport-size";
}
var responseBytes = context.Response.Body?.As<byte[]>(preserveContent: true) ?? new byte[0];
return responseBytes.Length <= maxBodyBytes ? Convert.ToBase64String(responseBytes) : "!transport-size";
// END payload

// BEGIN ack
var acknowledgement = context.Variables.GetValueOrDefault<IResponse>("gatewayEvidenceRelayResult", null);
if (acknowledgement == null) {
  return "unavailable";
}
if ((bool)context.Variables["gatewayEvidenceResponse"]) {
  return acknowledgement.StatusCode == 204 ? "acknowledged" : "unavailable";
}
if (acknowledgement.StatusCode != 201 || acknowledgement.Body == null) {
  return "unavailable";
}
try {
  var body = acknowledgement.Body.As<JObject>();
  var id = (string)body["captureId"];
  var nextSequence = (int?)body["nextSequence"];
  if (body["captureId"]?.Type == JTokenType.String
      && body["nextSequence"]?.Type == JTokenType.Integer
      && System.Text.RegularExpressions.Regex.IsMatch(id ?? "", "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
      && nextSequence >= 1 && nextSequence <= 128) {
    return new JObject(new JProperty("captureId", id), new JProperty("nextSequence", nextSequence))
      .ToString(Newtonsoft.Json.Formatting.None);
  }
} catch {
  return "unavailable";
}
return "unavailable";
// END ack
