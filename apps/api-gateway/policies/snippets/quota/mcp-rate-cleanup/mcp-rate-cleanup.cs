// 目的：終了・既存要求の取消だけを回数枠から免除し、新しいtool呼出しは数えます。
// POST本文の保持と取消形式の検査を維持し、下流の本人・所有者確認は省略しません。
if (context.Request.Method == "DELETE") { return true; }
if (context.Request.Method != "POST") { return false; }
try {
  var body = context.Request.Body.As<JObject>(preserveContent: true);
  if (body == null || (string)body["jsonrpc"] != "2.0" || (string)body["method"] != "notifications/cancelled"
      || body.Properties().Any(item => !(new string[] { "jsonrpc", "method", "params" }).Contains(item.Name))) { return false; }
  var args = body["params"] as JObject;
  if (args == null || args.Properties().Any(item => !(new string[] { "requestId", "reason" }).Contains(item.Name))) { return false; }
  var id = args["requestId"];
  if (id == null || (id.Type != JTokenType.Integer && (id.Type != JTokenType.String || String.IsNullOrWhiteSpace((string)id) || ((string)id).Length > 512))) { return false; }
  return args["reason"] == null || (args["reason"].Type == JTokenType.String && ((string)args["reason"]).Length <= 1024);
} catch { return false; }
