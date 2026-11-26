// BEGIN owner
// 署名チケットの用途・ジョブ・利用範囲・所有者と発行時刻を照合し、確認済み情報を引き渡します。
try {
  /* 結果取得用チケットを一度取得し、一つ指定されたことを確認します。 */
  string[] ticketValues;
  if (!context.Request.Headers.TryGetValue("X-Core-Document-Ticket", out ticketValues) || ticketValues.Length != 1) { return ""; }
  var ticket = ticketValues[0];
  /* 公開URLの操作IDが、ハイフン付きUUIDのD形式であることを確認します。 */
  var id = context.Request.MatchedParameters.GetValueOrDefault("operationId", "");
  Guid operation;
  if (!Guid.TryParseExact(id, "D", out operation)) { return ""; }
  /* 未検証チケットの処理量を抑えるため、全体を4096文字以内に限定します。 */
  /* 書式はBase64urlの情報部分.署名です。署名43文字は、32バイトのHMAC-SHA256を末尾の=なしで符号化した固定長です。 */
  if (ticket.Length > 4096 || !System.Text.RegularExpressions.Regex.IsMatch(ticket, @"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$")) { return ""; }
  var parts = ticket.Split('.');
  string expected;
  /* 開始操作と同じ鍵・Document専用の接頭辞で署名を再計算し、別用途のチケットと区別します。 */
  using (var hmac = new System.Security.Cryptography.HMACSHA256(System.Text.Encoding.UTF8.GetBytes("{{core-realtime-ticket-key}}"))) {
    expected = Convert.ToBase64String(hmac.ComputeHash(System.Text.Encoding.UTF8.GetBytes("document-analysis-v1:" + parts[0]))).TrimEnd('=').Replace('+', '-').Replace('/', '_');
  }
  /* 署名全体の長さが一致することを確認してから、同じ範囲の文字を照合します。 */
  if (expected.Length != parts[1].Length) { return ""; }
  /* 不一致位置で途中終了せず、全文字の差を集めて署名の一致を判定します。 */
  var difference = 0;
  for (var i = 0; i < expected.Length; i++) { difference |= expected[i] ^ parts[1][i]; }
  if (difference != 0) { return ""; }
  /* 署名を確認した情報部分のBase64urlを復元し、UTF-8のJSONとして読み取ります。 */
  var encoded = parts[0].Replace('-', '+').Replace('_', '/');
  encoded += new string('=', (4 - encoded.Length % 4) % 4);
  var claims = JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(encoded)));
  string owner;
  try {
    /* 上位APIが引き渡し成功を確認済みの認証情報を、チケットの所有者照合に使います。 */
    var decision = JObject.Parse((string)context.Variables["gatewayDecision"]);
    var caller = (string)decision["callerId"];
    if ((string)decision["method"] == "subscriptionKey") {
      /* 共通処理の認証済みIDから接頭辞だけを除き、既存チケットの購読ID表記を維持します。 */
      owner = caller.Substring("subscription:".Length);
    } else if ((string)decision["method"] == "user") {
      /* Bearerの社員IDは共通処理で検証・整形済みなので、そのまま所有者として使います。 */
      owner = caller;
    } else { return "AUTHENTICATION_PROCESSING_ERROR"; }
    /* 所有者IDを引き継げない場合は、空の所有者で照合せず内部不備として止めます。 */
    if (String.IsNullOrEmpty(owner)) { return "AUTHENTICATION_PROCESSING_ERROR"; }
  } catch {
    /* 内部認証情報の引き渡し不備だけを、後続の500判定と監査へ渡します。 */
    return "AUTHENTICATION_PROCESSING_ERROR";
  }
  /* 開始時と同じ接続先・API・所有者から、チケットの利用範囲を確認する照合値を作ります。 */
  string scope;
  using (var hash = System.Security.Cryptography.SHA256.Create()) {
    scope = Convert.ToBase64String(hash.ComputeHash(System.Text.Encoding.UTF8.GetBytes((string)context.Variables["documentBaseUrl"] + "\n" + context.Api.Id + "\n" + owner)));
  }
  /* 対応するチケットの形式・用途と、現在の解析ジョブ・利用範囲が一致することを確認します。 */
  if ((int?)claims["v"] != 1 || (string)claims["aud"] != "document-analysis-v1"
      || (string)claims["operationId"] != operation.ToString("D") || (string)claims["scope"] != scope) { return ""; }
  /* モデル名をURLの一つの要素として使える最低条件を確認します。対応モデルの利用可否は提供元が判定します。 */
  var model = claims["model"];
  if (model?.Type != JTokenType.String || String.IsNullOrWhiteSpace((string)model)
      || (string)model == "." || (string)model == "..") { return ""; }
  /* UTCのUnix秒で発行時刻を確認し、発行側との時計差として30秒までの未来時刻を許容します。 */
  var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
  var issued = (long?)claims["iat"];
  var expires = (long?)claims["exp"];
  /* 時刻の欠落・時計差の超過・発行から24時間の有効期間との不整合は、チケット不正として扱います。 */
  if (issued == null || expires == null || issued > now + 30 || expires != issued + 86400) { return ""; }
  return claims.ToString(Newtonsoft.Json.Formatting.None);
} catch { return ""; }
// END owner

// BEGIN owner-status
// チケット確認の結果から、内部不備・アクセス拒否・期限切れ・取得許可を選びます。
var value = (string)context.Variables["documentOwner"];
if (value == "AUTHENTICATION_PROCESSING_ERROR") { return 500; }
if (String.IsNullOrEmpty(value)) { return 403; }
/* 取得許可を決める時点のUTC秒で期限を確認し、期限に到達したら410で止めます。時計差30秒の許容で期限は延長しません。 */
/* この期限はGatewayチケットの期限です。提供元で結果が取得できない場合の404は、後段の応答処理で扱います。 */
return (long)JObject.Parse(value)["exp"] <= DateTimeOffset.UtcNow.ToUnixTimeSeconds() ? 410 : 200;
// END owner-status

// BEGIN poll-evaluation
// 提供元の状態と結果を既存の公開応答へ変換し、内部設定の読取り不備を区別します。
var status = 502; var result = ""; var retryAfter = 2;
var settingsReadInProgress = false;
try {
  var response = context.Response;
  if (response == null) { status = 504; }
  else if (response.StatusCode == 429) { status = 429; }
  else if (response.StatusCode == 404) { status = 404; }
  else if (response.StatusCode == 410) { status = 410; }
  else if (response.StatusCode == 200) {
    /* API側で準備済みの操作設定から、結果サイズの上限と対応API版を読みます。 */
    /* この取得・解析と既存サイズ値の変換失敗だけを、基盤内部の設定不備として区別します。 */
    settingsReadInProgress = true;
    var settings = JObject.Parse((string)context.Variables["gatewayOperationConstraint"]);
    var maxResponseBytes = (int)settings["maxResponseBytes"];
    /* 正式な操作設定で必須のAPI版を先に取得し、外部版の欠落とnull同士で一致させません。 */
    var version = settings["apiVersion"];
    if (version?.Type != JTokenType.String || String.IsNullOrWhiteSpace((string)version)) {
      throw new Exception("Document operation API version is unavailable");
    }
    var apiVersion = (string)version;
    settingsReadInProgress = false;
    var bytes = response.Body.As<byte[]>(preserveContent: true);
    if (bytes.Length <= maxResponseBytes) {
      var body = response.Body.As<JObject>();
      var current = (string)body?["status"];
      if (current == "running" || current == "notStarted") {
        status = 202;
        result = new JObject(new JProperty("operationId", (string)context.Variables["documentOperationId"]), new JProperty("model", (string)context.Variables["documentModel"]), new JProperty("status", "running"), new JProperty("requestId", context.RequestId.ToString())).ToString(Newtonsoft.Json.Formatting.None);
      } else if (current == "failed" || current == "canceled") { status = 422; }
      else if (current == "succeeded") {
        var analysis = body["analyzeResult"] as JObject;
        if (analysis != null && (string)analysis["apiVersion"] == apiVersion && (string)analysis["modelId"] == (string)context.Variables["documentModel"] && analysis["content"]?.Type == JTokenType.String && analysis["pages"] is JArray) {
          var projected = new JObject(new JProperty("analyzeResult", analysis), new JProperty("requestId", context.RequestId.ToString())).ToString(Newtonsoft.Json.Formatting.None);
          if (System.Text.Encoding.UTF8.GetByteCount(projected) <= maxResponseBytes) { status = 200; result = projected; }
        }
      }
    }
  }
  int delay;
  if (response != null && Int32.TryParse(response.Headers.GetValueOrDefault("Retry-After", ""), out delay) && delay > 0 && delay <= 86400) { retryAfter = delay; }
} catch { status = settingsReadInProgress ? 500 : 502; result = ""; }
if (String.IsNullOrEmpty(result)) {
  var code = status == 404 ? "DocumentOperationNotFound" : status == 410 ? "DocumentOperationExpired" : status == 422 ? "DocumentRejected" : status == 429 ? "DocumentAnalysisThrottled" : "DocumentAnalysisUnavailable";
  result = new JObject(new JProperty("error", new JObject(new JProperty("code", code))), new JProperty("requestId", context.RequestId.ToString())).ToString(Newtonsoft.Json.Formatting.None);
}
return new JObject(new JProperty("status", status), new JProperty("result", result), new JProperty("retryAfter", retryAfter)).ToString(Newtonsoft.Json.Formatting.None);
// END poll-evaluation
