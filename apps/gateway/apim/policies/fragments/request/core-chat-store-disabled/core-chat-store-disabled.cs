// 目的：FoundryのChat要求へ、保存しないstore=falseを必ず設定します。
// この同名C#をcore-chat-store-disabled.xmlへ展開してAzureの同名部品を作ります。
// 呼び出し対象はAPI・操作・実効接続先から配備時に選び、モデル一覧は使いません。

JObject body;
try {
  // 本文は一度だけ消費して読みます。前段のサイズ・利用枠確認とaliasの反映は完了しています。
  var rawBody = context.Request.Body.As<string>();
  // JSON文字列値だけ~を一つ足してDate解析を避け、解析後に一つだけ戻します。
  // 元の~・空文字・escapeを保持し、property名とコメントは原文のままです。
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
  body = JObject.Parse(protectedBody);
  foreach (var value in body.Descendants().OfType<JValue>()) {
    if (value.Type == JTokenType.String) { value.Value = ((string)value).Substring(1); }
  }
} catch {
  // JSONオブジェクトを読めない入力は空文字を返し、親フローが監査と400応答を行います。
  return "";
}
if (body == null) { return ""; }

// 指定の有無・型・値によらずBooleanのfalseへ揃え、他の項目は変更しません。
body["store"] = false;
// 返す文字列は今回のset-bodyへの受け渡しだけに使い、監査には出力しません。
return body.ToString(Newtonsoft.Json.Formatting.None);
