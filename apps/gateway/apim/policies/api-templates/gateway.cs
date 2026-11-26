// 同名XMLのモデル別名・Agent/Responses保存指定と親要求IDを編集する本文式を管理します。
// 操作選択・単純な値取得・標準ポリシーの分岐はXMLへ維持します。
// ISO日時を含む文字列をDate型へ変えず、JSON.NETへ本文の構造解析を任せます。

// BEGIN model-alias-body
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
body["model"] = (string)context.Variables["gatewayEffectiveModel"];
return body.ToString(Newtonsoft.Json.Formatting.None);
// END model-alias-body

// BEGIN hosted-body
var rawBody = context.Request.Body.As<string>(preserveContent: true);
// 全文字列値へ~を一つ足してDate解析を避け、解析後に一つだけ戻します。
// 元の~・空文字・escaped文字列も同じ値に戻り、property名とコメントは変更しません。
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
if ((string)JObject.Parse((string)context.Variables["gatewayOperationConstraint"])["kind"] == "hostedAgentPost") {
  body["store"] = false;
  if (body.Property("conversation") != null) {
    body["conversation"] = JValue.CreateNull();
  }
  if (body.Property("background") != null) {
    body["background"] = false;
  }
}
if (!String.IsNullOrEmpty((string)JObject.Parse((string)context.Variables["gatewayAuditSettings"])["targetAgent"])) {
  var metadata = body["metadata"] as JObject ?? new JObject();
  metadata["core_parent_request_id"] = context.RequestId.ToString("D");
  body["metadata"] = metadata;
}
return body.ToString(Newtonsoft.Json.Formatting.None);
// END hosted-body

// BEGIN responses-body
var rawBody = context.Request.Body.As<string>();
// 全文字列値へ~を一つ足してDate解析を避け、解析後に一つだけ戻します。
// 元の~・空文字・escaped文字列も同じ値に戻り、property名とコメントは変更しません。
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
body["store"] = false;
if (body.Property("background") != null) {
  body["background"] = false;
}
if (body.Property("conversation") != null) {
  body["conversation"] = JValue.CreateNull();
}
return body.ToString(Newtonsoft.Json.Formatting.None);
// END responses-body
