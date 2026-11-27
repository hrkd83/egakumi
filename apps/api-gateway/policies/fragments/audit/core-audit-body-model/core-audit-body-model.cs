// 通常HTTPの監査用モデル名だけを読み、本文を保持して下流へ渡します。
// 同名XMLへこの式を展開し、登録一覧とモデル名を照合しません。
// 先行するHTTP操作選択の準備済み制約を使い、操作一覧を読み直しません。
// 内部設定の取得失敗は本文不備の空文字へ補完せず、HTTPの固定PolicyIdから500へ渡します。
var constraint = JObject.Parse((string)context.Variables["gatewayOperationConstraint"]);
var kind = (string)constraint?["kind"];
/* Live作成では、登録済みの固定配備名を監査の要求モデルとして扱います。 */
if (kind == "liveCreate") { return (string)constraint["model"] ?? ""; }
if (kind != "bodyModel" && kind != "textEmbeddings" && kind != "rerank" && kind != "statelessResponses") { return ""; }
try {
  var model = (string)context.Request.Body.As<JObject>(preserveContent: true)["model"];
  if (String.IsNullOrEmpty(model) || !System.Text.RegularExpressions.Regex.IsMatch(model, "^[A-Za-z0-9_.-]{1,160}$")) { return ""; }
  return model;
} catch { return ""; }
