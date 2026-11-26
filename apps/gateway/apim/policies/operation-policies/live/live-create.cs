// 同名XMLの要求変換式と応答変換式を管理します。
// BEGIN/ENDで区画を選び、各markerへ個別に展開します。ファイル全体は一つの式ではありません。

// BEGIN request
var readingSettings = false;
try {
  var body = context.Request.Body.As<JObject>(preserveContent: true);
  readingSettings = true;
  var constraint = JObject.Parse((string)context.Variables["gatewayOperationConstraint"]);
  readingSettings = false;
  var fields = new string[] { "sdp", "instructions", "voice" };
  if (body == null || body.Properties().Any(p => !fields.Contains(p.Name))) { return ""; }
  var sdp = body["sdp"];
  if (sdp?.Type != JTokenType.String || !((string)sdp).StartsWith("v=0\r\n")) { return ""; }
  var instructions = body["instructions"];
  if (instructions != null && (instructions.Type != JTokenType.String)) { return ""; }
  var voice = body["voice"];
  if (voice != null && voice.Type != JTokenType.String) { return ""; }
  var voiceName = voice == null ? "marin" : (string)voice;
  readingSettings = true;
  var voices = constraint["stringChoices"]?["voice"] as JArray;
  if (String.IsNullOrWhiteSpace(voiceName) || (voices != null && voices.Count > 0 && !voices.Any(v => (string)v == voiceName))) { return ""; }
  var model = (string)constraint["model"];
  readingSettings = false;
  var session = new JObject(
    new JProperty("model", model),
    new JProperty("delegation", new JObject(new JProperty("type", "client"))),
    new JProperty("audio", new JObject(new JProperty("output", new JObject(new JProperty("voice", voiceName))))));
  if (instructions != null) { session["instructions"] = instructions; }
  return new JObject(new JProperty("session", session),
    new JProperty("transport", new JObject(new JProperty("type", "webrtc"), new JProperty("sdp", (string)sdp)))).ToString(Newtonsoft.Json.Formatting.None);
} catch { if (readingSettings) { throw; } return ""; }
// END request

// BEGIN answer
try {
  var body = context.Response.Body.As<JObject>(preserveContent: true);
  var id = body?["session"]?["id"];
  var sdp = body?["transport"]?["sdp"];
  if (id?.Type != JTokenType.String || !System.Text.RegularExpressions.Regex.IsMatch((string)id, "^[A-Za-z0-9_-]{1,160}$")
      || sdp?.Type != JTokenType.String || !((string)sdp).StartsWith("v=0\r\n")) { return ""; }
  return new JObject(new JProperty("sessionId", (string)id), new JProperty("sdp", (string)sdp)).ToString(Newtonsoft.Json.Formatting.None);
} catch { return ""; }
// END answer
