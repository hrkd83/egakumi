// 提供元の応答は、入口認証や内部IDの引き渡しとは異なる外部入力です。
// 既知の要求IDヘッダー3名から単一値だけを取り出し、従来のGUID／req_形式を保ちます。
// 退避したIDは監査の照合に使い、利用者認証や資源の所有者確認には使いません。
var ids = new JObject();
if (context.Response != null) {
  foreach (var name in new string[] { "apim-request-id", "x-ms-request-id", "x-request-id" }) {
    if (!context.Response.Headers.ContainsKey(name) || context.Response.Headers[name].Length != 1) { continue; }
    var value = context.Response.Headers.GetValueOrDefault(name, "");
    Guid parsed;
    if (Guid.TryParse(value, out parsed) && parsed != Guid.Empty) { ids[name] = parsed.ToString("D"); }
    else if (System.Text.RegularExpressions.Regex.IsMatch(value, "^req_[A-Za-z0-9]{8,96}$")) { ids[name] = value; }
  }
}
return ids.ToString(Newtonsoft.Json.Formatting.None);
