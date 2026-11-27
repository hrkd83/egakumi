// BEGIN broker-result
// Brokerの応答は結果コード・下流トークン・検査済みclaimsだけを使用します。
var result = new JObject(new JProperty("status", 503));
try {
    var response = context.Variables.GetValueOrDefault<IResponse>("gatewayGraphOboResponse", null);
    if (response == null || response.Body == null) { return result.ToString(); }
    var bytes = response.Body.As<byte[]>();
    if (bytes.Length > 32768) { return result.ToString(); }
    var body = JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes));
    var code = (string)body["error"]?["code"];
    if (response.StatusCode == 200) {
        var token = (string)body["accessToken"];
        if (body["accessToken"]?.Type == JTokenType.String
            && System.Text.RegularExpressions.Regex.IsMatch(token, @"\A[A-Za-z0-9_.~+/=-]{1,24000}\z")) {
            result["status"] = 200;
            result["accessToken"] = token;
        }
    } else if (response.StatusCode == 401 && code == "GraphReauthenticationRequired") {
        result["status"] = 401;
        var claims = (string)body["claims"] ?? "";
        if (System.Text.RegularExpressions.Regex.IsMatch(claims, @"\A[A-Za-z0-9+/]{1,4096}={0,2}\z")) {
            result["claims"] = claims;
        }
    } else if (response.StatusCode == 403 && code == "GraphAccessDenied") {
        result["status"] = 403;
    }
} catch { }
return result.ToString(Newtonsoft.Json.Formatting.None);
// END broker-result

// BEGIN backend-result
// GET /meの本人を、共通認証で確定したobject IDに束縛します。
var result = new JObject(new JProperty("status", 503));
try {
    var status = context.Response.StatusCode;
    if (status == 200 && context.Response.Body != null) {
        var bytes = context.Response.Body.As<byte[]>(preserveContent: true);
        if (bytes.Length > 65536) { return result.ToString(); }
        var profile = JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes));
        var owner = (string)JObject.Parse((string)context.Variables["gatewayDecision"])["verifiedPrincipalId"];
        Guid actual;
        result["status"] = profile["id"]?.Type == JTokenType.String
            && Guid.TryParse((string)profile["id"], out actual) && actual.ToString("D") == owner ? 200 : 403;
    } else if (status == 429) {
        result["status"] = 429;
        var delay = context.Response.Headers.GetValueOrDefault("Retry-After", "");
        if (System.Text.RegularExpressions.Regex.IsMatch(delay, @"\A[0-9]{1,9}\z")) {
            result["retryAfter"] = delay;
        }
    } else if (status == 401 || status == 403) {
        result["status"] = status;
        var headers = context.Response.Headers.GetValueOrDefault("WWW-Authenticate", "");
        if (headers.Length > 8192 || !headers.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase)) {
            return result.ToString();
        }
        var error = System.Text.RegularExpressions.Regex.Matches(headers, "(?:^Bearer |, *)error=\"insufficient_claims\"(?=,|$)", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
        var challenge = System.Text.RegularExpressions.Regex.Matches(headers, "(?:^Bearer |, *)claims=\"([A-Za-z0-9+/]{1,4096}={0,2})\"(?=,|$)", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
        if (error.Count == 1 && challenge.Count == 1) {
            var decoded = Convert.FromBase64String(challenge[0].Groups[1].Value);
            if (decoded.Length <= 3072) {
                var claims = JObject.Parse(System.Text.Encoding.UTF8.GetString(decoded));
                var access = claims["access_token"] as JObject;
                if (claims.Count == 1 && access != null && access.Count > 0) {
                    result["claims"] = Convert.ToBase64String(decoded);
                    result["status"] = 401;
                }
            }
        }
    }
} catch { }
return result.ToString(Newtonsoft.Json.Formatting.None);
// END backend-result
