// 同名XMLのPII入力検査、内部設定、固定要求、待ち時間ヘッダー、固定結果と監査を管理します。
// 固定のBEGIN/END区画を各markerへ展開し、単式のXML処理はこのファイルへ移しません。

// BEGIN input
// 準備済み内部設定と既存のサイズ・言語値の取得失敗は、入力不備のcatchへ入れず専用PolicyIdのon-errorへ渡します。
var settings = JObject.Parse((string)context.Variables["piiSettings"]);
var maxRequestBytes = (long)settings["maxRequestBytes"];
var languages = settings["allowedLanguages"] as JArray;
try {
  if (context.Request.Body == null) { return 400; }
  var bytes = context.Request.Body.As<byte[]>(preserveContent: true);
  /* Bound the received bytes before parsing. JSON Unicode escapes may
     use six bytes for one ASCII character in the compact representation. */
  if (bytes.Length > 6L * maxRequestBytes) { return 413; }
  if (context.Request.Url.Query.Count != 0 || !String.Equals(context.Request.Headers.GetValueOrDefault("Content-Type", "").Split(';')[0].Trim(), "application/json", StringComparison.OrdinalIgnoreCase)) { return 400; }
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
  var body = JObject.Parse(protectedBody);
  foreach (var value in body.Descendants().OfType<JValue>()) {
    if (value.Type == JTokenType.String) { value.Value = ((string)value).Substring(1); }
  }
  if (body == null || body.Properties().Count() != 2) { return 400; }
  if (body["text"]?.Type != JTokenType.String || String.IsNullOrWhiteSpace((string)body["text"]) || body["language"]?.Type != JTokenType.String) { return 400; }
  if (String.IsNullOrWhiteSpace((string)body["language"]) || (languages != null && languages.Count > 0 && !languages.Any(v => (string)v == (string)body["language"]))) { return 400; }
  /* Match the native MCP argument budget. Compact JSON removes syntax
     whitespace and decodes JSON escapes, without normalizing, trimming
     or truncating the text value passed to Language. */
  if (System.Text.Encoding.UTF8.GetByteCount(body.ToString(Newtonsoft.Json.Formatting.None)) > maxRequestBytes) { return 413; }
  /* Exact TextElements_v8 limits belong to Language. StringInfo is not an
     allowed APIM policy type; UTF-16 Length must not reject valid emoji or
     combining sequences. The independent compact UTF-8 bound is above. */
  return 200;
} catch { return 400; }
// END input

// BEGIN request-settings
var settings = JObject.Parse((string)context.Variables["piiSettings"]);
var modelVersion = settings["modelVersion"];
if (modelVersion?.Type != JTokenType.String || String.IsNullOrWhiteSpace((string)modelVersion)) { throw new Exception(); }
return (string)modelVersion;
// END request-settings

// BEGIN request
var rawBody = context.Request.Body.As<string>();
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
var input = JObject.Parse(protectedBody);
foreach (var value in input.Descendants().OfType<JValue>()) {
  if (value.Type == JTokenType.String) { value.Value = ((string)value).Substring(1); }
}
return new JObject(
  new JProperty("kind", "PiiEntityRecognition"),
  new JProperty("analysisInput", new JObject(new JProperty("documents", new JArray(new JObject(
    new JProperty("id", (string)context.Variables["piiDocumentId"]),
    new JProperty("language", (string)input["language"]),
    new JProperty("text", (string)input["text"])
  ))))),
  new JProperty("parameters", new JObject(
    new JProperty("loggingOptOut", true),
    new JProperty("modelVersion", (string)context.Variables["piiRequestModelVersion"]),
    new JProperty("stringIndexType", "TextElements_v8")
  ))
).ToString(Newtonsoft.Json.Formatting.None);
// END request

// BEGIN retry
var value = context.Response.Headers.GetValueOrDefault("Retry-After", "");
if (context.Response.StatusCode != 429) { return ""; }
if (System.Text.RegularExpressions.Regex.IsMatch(value, "^[0-9]{1,6}$")) { return value; }
DateTimeOffset retryAt;
return DateTimeOffset.TryParse(value, out retryAt) ? retryAt.UtcDateTime.ToString("R") : "";
// END retry

// BEGIN result
var requestId = context.RequestId.ToString();
var status = context.Response.StatusCode;
var code = "PiiUpstreamFailure";
if (status == 429) { code = "PiiThrottled"; }
else if (status == 413) { code = "PiiInputTooLarge"; }
else if (status == 504) { code = "PiiTimeout"; }
else { status = 502; }
var readingSettings = false;
try {
  if (context.Response.StatusCode == 200) {
    readingSettings = true;
    var settings = JObject.Parse((string)context.Variables["piiSettings"]);
    readingSettings = false;
    var rawBody = context.Response.Body.As<string>();
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
    var body = JObject.Parse(protectedBody);
    foreach (var value in body.Descendants().OfType<JValue>()) {
      if (value.Type == JTokenType.String) { value.Value = ((string)value).Substring(1); }
    }
    var results = body?["results"] as JObject;
    var documents = results?["documents"] as JArray;
    var errors = results?["errors"] as JArray;
    var expectedId = (string)context.Variables["piiDocumentId"];
    if ((string)body?["kind"] != "PiiEntityRecognitionResults" || results == null || documents == null || errors == null) {
      code = "PiiInvalidUpstreamResult";
    } else if (errors.Count == 1 && documents.Count == 0 && (string)errors[0]["id"] == expectedId) {
      /* InvalidDocument is not a documented length-specific code. Never
         infer 413 from free-text messages or expose those messages. */
      status = 422; code = "PiiDocumentRejected";
    } else if (errors.Count != 0 || documents.Count != 1 || (string)documents[0]["id"] != expectedId) {
      code = "PiiInvalidUpstreamResult";
    } else {
      // 外部の形・件数・IDの短絡条件を通過した位置で、正式な内部必須モデル版を取得します。
      var upstreamModelVersion = (string)results["modelVersion"];
      readingSettings = true;
      var modelVersion = settings["modelVersion"];
      if (modelVersion?.Type != JTokenType.String || String.IsNullOrWhiteSpace((string)modelVersion)) { throw new Exception(); }
      var expectedModelVersion = (string)modelVersion;
      readingSettings = false;
      if (upstreamModelVersion != expectedModelVersion || documents[0]["redactedText"]?.Type != JTokenType.String || !(documents[0]["entities"] is JArray) || !(documents[0]["warnings"] is JArray)) {
        code = "PiiInvalidUpstreamResult";
      } else {
        var document = documents[0];
        var warnings = (JArray)document["warnings"];
        if (warnings.Any(w => (string)w["code"] == "DocumentTruncated")) {
          status = 413; code = "PiiInputTooLarge";
        } else {
          readingSettings = true;
          var allowed = (JArray)settings["allowedWarningCodes"];
          readingSettings = false;
          var warningCodes = new JArray(warnings.Where(w => w["code"]?.Type == JTokenType.String && allowed.Any(v => (string)v == (string)w["code"])).Select(w => (string)w["code"]).Distinct());
          var result = new JObject(
            new JProperty("redactedText", (string)document["redactedText"]),
            new JProperty("language", (string)context.Variables["piiLanguage"]),
            new JProperty("detectedCount", ((JArray)document["entities"]).Count),
            new JProperty("modelVersion", (string)results["modelVersion"]),
            new JProperty("warningCodes", warningCodes),
            new JProperty("requestId", requestId)
          );
          return new JObject(new JProperty("status", 200), new JProperty("body", result)).ToString(Newtonsoft.Json.Formatting.None);
        }
      }
    }
  }
} catch { if (readingSettings) { throw; } status = 502; code = "PiiInvalidUpstreamResult"; }
return new JObject(new JProperty("status", status), new JProperty("body", new JObject(new JProperty("error", new JObject(new JProperty("code", code))), new JProperty("requestId", requestId)))).ToString(Newtonsoft.Json.Formatting.None);
// END result

// BEGIN output
// 内部結果を応答本文へ戻す最後の解析も、日時を含む文字列を保持します。
var rawBody = (string)context.Variables["piiResult"];
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
var body = JObject.Parse(protectedBody);
foreach (var value in body.Descendants().OfType<JValue>()) {
  if (value.Type == JTokenType.String) { value.Value = ((string)value).Substring(1); }
}
return body["body"].ToString(Newtonsoft.Json.Formatting.None);
// END output

// BEGIN audit-settings
var settings = JObject.Parse((string)context.Variables["piiSettings"]);
return (string)settings["modelVersion"];
// END audit-settings

// BEGIN audit
var result = JObject.Parse((string)context.Variables["piiResult"]);
var record = new JObject(new JProperty("requestId", context.RequestId.ToString()), new JProperty("status", context.Response.StatusCode), new JProperty("inputBytes", (int)context.Variables["piiInputBytes"]), new JProperty("documentCount", 1), new JProperty("upstreamAttempt", 1), new JProperty("modelVersion", (string)context.Variables["piiAuditModelVersion"]));
if ((int)result["status"] == 200) { record["detectedCount"] = (int)result["body"]["detectedCount"]; }
return record.ToString(Newtonsoft.Json.Formatting.None);
// END audit
