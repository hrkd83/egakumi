// BEGIN operation-id
// 受付先URLが固定提供元・モデル・API版と一致することを確認し、処理IDを取り出します。
try {
  var response = context.Response;
  if (response == null || response.StatusCode != 202) { return ""; }
  var baseUrl = (string)context.Variables["documentBaseUrl"];
  var location = new Uri(response.Headers.GetValueOrDefault("Operation-Location", ""));
  var expected = new Uri(baseUrl);
  /* 固定パスの構造を先に確認し、モデルの要素だけ一度復号して照合します。 */
  /* 符号化された / をパス全体の区切りへ戻さず、%2F / %2f の表記差は同じモデルとして扱います。 */
  var path = location.AbsolutePath.Split('/');
  if (location.Scheme != "https" || !String.Equals(location.Authority, expected.Authority, StringComparison.OrdinalIgnoreCase) || location.UserInfo.Length != 0 || location.Fragment.Length != 0 || location.Query != "?api-version=__DOCUMENT_API_VERSION__"
      || path.Length != 6 || path[1] != "documentintelligence" || path[2] != "documentModels" || path[4] != "analyzeResults"
      || !String.Equals(Uri.UnescapeDataString(path[3]), (string)context.Variables["documentModel"], StringComparison.Ordinal)) { return ""; }
  Guid id;
  if (!Guid.TryParseExact(path[5], "D", out id)) { return ""; }
  return id.ToString("D");
} catch { return ""; }
// END operation-id

// BEGIN accepted
// 確認済み所有者へ束縛したHMACチケットと、公開する受付情報を作ります。
try {
  var id = (string)context.Variables["documentOperationId"];
  if (String.IsNullOrEmpty(id)) { return ""; }
  string owner;
  try {
    /* 上位APIが引き渡し成功を確認済みの認証情報を、チケットの所有者IDの生成に使います。 */
    var decision = JObject.Parse((string)context.Variables["gatewayDecision"]);
    var caller = (string)decision["callerId"];
    if ((string)decision["method"] == "subscriptionKey") {
      /* 共通処理の認証済みIDから接頭辞だけを除き、既存チケットの購読ID表記を維持します。 */
      owner = caller.Substring("subscription:".Length);
    } else if ((string)decision["method"] == "user") {
      /* Bearerの社員IDは共通処理で検証・整形済みなので、そのまま所有者として使います。 */
      owner = caller;
    } else { return "AUTHENTICATION_PROCESSING_ERROR"; }
    /* 所有者IDを引き継げない場合は、空の所有者でチケットを作らず内部不備として止めます。 */
    if (String.IsNullOrEmpty(owner)) { return "AUTHENTICATION_PROCESSING_ERROR"; }
  } catch {
    /* 内部認証情報の引き渡し不備だけを、後続の500判定と監査へ渡します。 */
    return "AUTHENTICATION_PROCESSING_ERROR";
  }
  var model = (string)context.Variables["documentModel"];
  /* 受付を確認して発行する時刻から、チケットの24時間の有効期間を数えます。 */
  var issued = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
  /* 結果の保持は提供元の条件に従い、取得できない結果は状態照会の404で扱います。 */
  var expires = issued + 86400;
  string scope;
  using (var hash = System.Security.Cryptography.SHA256.Create()) {
    scope = Convert.ToBase64String(hash.ComputeHash(System.Text.Encoding.UTF8.GetBytes((string)context.Variables["documentBaseUrl"] + "\n" + context.Api.Id + "\n" + owner)));
  }
  var claims = new JObject(new JProperty("v", 1), new JProperty("aud", "document-analysis-v1"), new JProperty("operationId", id), new JProperty("model", model), new JProperty("scope", scope), new JProperty("iat", issued), new JProperty("exp", expires));
  var encoded = Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(claims.ToString(Newtonsoft.Json.Formatting.None))).TrimEnd('=').Replace('+', '-').Replace('/', '_');
  string signature;
  using (var hmac = new System.Security.Cryptography.HMACSHA256(System.Text.Encoding.UTF8.GetBytes("{{core-realtime-ticket-key}}"))) {
    /* 同じ鍵のRealtimeチケットと、署名対象を明確に分離します。 */
    signature = Convert.ToBase64String(hmac.ComputeHash(System.Text.Encoding.UTF8.GetBytes("document-analysis-v1:" + encoded))).TrimEnd('=').Replace('+', '-').Replace('/', '_');
  }
  return new JObject(new JProperty("operationId", id), new JProperty("model", model), new JProperty("status", "running"), new JProperty("statusPath", "/tools/documents/v1/operations/" + id), new JProperty("ticket", encoded + "." + signature), new JProperty("expiresAt", DateTimeOffset.FromUnixTimeSeconds(expires).UtcDateTime.ToString("o")), new JProperty("requestId", context.RequestId.ToString())).ToString(Newtonsoft.Json.Formatting.None);
} catch { return ""; }
// END accepted

// BEGIN status
// 提供元の状態と受付結果から、従来の公開HTTP状態を選びます。
var response = context.Response;
if (response == null) { return 504; }
if (response.StatusCode == 429) { return 429; }
if (response.StatusCode == 413) { return 413; }
if (response.StatusCode == 400 || response.StatusCode == 415 || response.StatusCode == 422) { return 422; }
var accepted = (string)context.Variables["documentAccepted"];
if (accepted == "AUTHENTICATION_PROCESSING_ERROR") { return 500; }
return String.IsNullOrEmpty(accepted) ? 502 : 202;
// END status

// BEGIN retry-after
// 提供元の再試行秒を従来の整数範囲で受け取り、欠落時は既定値を使います。
int delay;
return context.Response != null && Int32.TryParse(context.Response.Headers.GetValueOrDefault("Retry-After", ""), out delay) && delay > 0 && delay <= 86400 ? delay : 2;
// END retry-after
